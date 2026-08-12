"""
Sarjy voice agent worker — entry point.

Wires VAD -> turn detection -> STT -> LLM -> TTS with tool calls,
memory, and latency instrumentation. Day-1 goal: a working EN+AR voice
loop, deployed. Stubbed pieces are TODO-tagged per docs/PRD.md.
"""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import noise_cancellation, silero

import memory
from latency import LatencyTracker
from llm_adapter import build_llm
from stt_adapter import build_stt
from tools import (
    book_calendar_event,
    check_calendar_availability,
    get_prayer_time,
    list_calendar_events,
)
from tts_adapter import build_tts

load_dotenv()
logger = logging.getLogger("sarjy-agent")

# Groq/llama-3.3-70b-versatile occasionally writes a tool call as literal
# text (e.g. "<function=check_calendar_availability>{...}</function>")
# instead of using the real tool-calling mechanism — it gets spoken and
# shown to the user as garbage, and nothing actually gets called. Real
# tool calls live in ChatChunk.delta.tool_calls, a separate field from
# .content, so this only ever touches malformed text that leaked into
# content — it can't affect a correctly-formed call.
_LEAK_MARKER = "<function="

# Graceful degradation (docs/PRD.md §5: "silence is the failure mode").
# An unhandled exception anywhere in the LLM stream (a rate limit, a
# malformed tool call, any other provider failure) would otherwise
# propagate out and kill the whole turn with zero reply, spoken or shown.
# This is the outer boundary that keeps any of them from going completely
# silent, not a fix for one specific cause.
_FALLBACK_MESSAGE = (
    "Sorry, I ran into a problem there — could you try again? "
    "عذرًا، واجهت مشكلة، ممكن تجرب مرة ثانية؟"
)


async def _strip_leaked_tool_syntax(
    chunks: AsyncIterable[llm.ChatChunk],
) -> AsyncIterator[llm.ChatChunk]:
    pending = ""
    last_id = "sanitized"
    try:
        async for chunk in chunks:
            if not isinstance(chunk, llm.ChatChunk) or not chunk.delta or not chunk.delta.content:
                yield chunk
                continue

            last_id = chunk.id
            pending += chunk.delta.content
            if _LEAK_MARKER in pending:
                safe_text, _, _ = pending.partition(_LEAK_MARKER)
                if safe_text:
                    chunk.delta.content = safe_text
                    yield chunk
                logger.warning("llm_node: stripped a leaked tool-call-as-text response")
                return

            # Hold back a tail long enough to still catch a marker split
            # across chunk boundaries; flush the rest now so streaming
            # stays smooth.
            safe_len = max(0, len(pending) - (len(_LEAK_MARKER) - 1))
            if safe_len:
                chunk.delta.content = pending[:safe_len]
                yield chunk
                pending = pending[safe_len:]
    except Exception:
        # Exception, not BaseException — asyncio.CancelledError (e.g. the
        # user hitting Stop mid-generation) must keep propagating, not
        # get turned into a spoken apology after the session's gone.
        logger.exception("llm_node: LLM generation failed, falling back to an apology")
        if pending:
            # Whatever was already held back for marker-boundary
            # detection would otherwise be silently dropped.
            yield llm.ChatChunk(
                id=last_id, delta=llm.ChoiceDelta(role="assistant", content=pending)
            )
        yield llm.ChatChunk(
            id="llm-error-fallback",
            delta=llm.ChoiceDelta(role="assistant", content=_FALLBACK_MESSAGE),
        )
        return

    if pending:
        yield llm.ChatChunk(id=last_id, delta=llm.ChoiceDelta(role="assistant", content=pending))


class SarjyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are Sarjy, a helpful bilingual (Arabic/English) voice "
                "assistant. Reply in whichever language the user's last "
                "message actually used — pure Arabic in, pure Arabic back; "
                "pure English in, pure English back. If their message "
                "itself mixed languages mid-sentence, mirroring that mix "
                "is fine. Never say the same sentence twice in two "
                "languages as a translation — that is not what matching "
                "the user's language means, and it's not something a "
                "real bilingual speaker would do. Keep responses short — "
                "you're spoken aloud, not read. Only call a tool when the "
                "user's request clearly calls for it — never call one "
                "just because it's available. Never invent a date, time, "
                "or title; if a detail you need is missing or the "
                "user's request was unclear, ask instead of guessing. "
                "For get_prayer_time and check_calendar_availability, "
                "call immediately without narrating first — explain only "
                "after they return. Booking is different: check "
                "availability first; if the user hasn't said how long "
                "the meeting should be, ask, don't assume a duration; "
                "once you know the time and duration, state them back in "
                "one short sentence and wait for the user to actually "
                "confirm before calling book_calendar_event — never book "
                "in the same turn you first mention a resolved time."
            ),
            tools=[
                get_prayer_time,
                check_calendar_availability,
                list_calendar_events,
                book_calendar_event,
            ],
        )

    def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterator[llm.ChatChunk]:
        return _strip_leaked_tool_syntax(
            Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        )

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # Semantic recall, injected right before the LLM sees this turn
        # (docs/PRD.md §5) — not the full transcript, a scoped block.
        text = new_message.text_content
        if not text:
            return
        facts = await memory.retrieve(self.session.userdata, text)
        if facts:
            turn_ctx.add_message(
                role="system",
                content="Known about this user, from past conversations:\n"
                + "\n".join(f"- {f}" for f in facts),
            )


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per worker process, not per session.
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # The web side mints a stable per-browser id (localStorage) and passes
    # it as the LiveKit identity — that's what memory keys facts on across
    # sessions (web/app/composables/useSarjyRoom.ts, web/server/api/token.get.ts).
    participant = await ctx.wait_for_participant()
    user_id = participant.identity

    latency = LatencyTracker(room=ctx.room)

    # TODO(day 2): swap LLM/TTS per request by language (docs/PRD.md §3-4).
    session = AgentSession[str](
        userdata=user_id,  # tools.py reads this via RunContext.userdata
        vad=ctx.proc.userdata["vad"],
        stt=build_stt(ctx.proc.userdata["vad"]),
        llm=build_llm(),
        tts=build_tts(),
        # TODO(day 2-3): multilingual turn detector — measure AR/EN
        # code-switching, don't assume (docs/PRD.md "Quality/fidelity").
        #
        # Preemptive generation starts an early reply before end-of-turn
        # is confident, which can produce a reply for a turn the
        # user hadn't actually finished — off until validated against
        # the real turn detector above.
        turn_handling={"preemptive_generation": {"enabled": False}},
    )

    latency.attach(session)

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev) -> None:
        # User-role only: conversation_item_added fires with history's
        # actual (post-truncation) content, but restricting to the user's
        # own speech sidesteps the barge-in question entirely — user audio
        # is never truncated, only an interrupted assistant reply is.
        if not isinstance(ev.item, llm.ChatMessage) or ev.item.role != "user":
            return
        text = ev.item.text_content
        if text:
            asyncio.create_task(_remember(user_id, text))

    await session.start(
        agent=SarjyAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Krisp noise cancellation — near-free on LiveKit Cloud.
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    greeting_instructions = "Greet the user briefly in English, mention you also speak Arabic."
    # on_user_turn_completed (above) only fires from the second turn
    # onward — the greeting is generated before any user turn exists, so
    # a returning user needs this same retrieve() call run once up front.
    known_facts = await memory.retrieve(user_id, "the user's name")
    if known_facts:
        greeting_instructions += (
            " You already know this about them from past conversations: "
            + "; ".join(known_facts)
            + ". If their name is among these, greet them by name instead of generically."
        )

    await session.generate_reply(instructions=greeting_instructions)


async def _remember(user_id: str, transcript: str) -> None:
    try:
        facts = await memory.extract_facts(user_id, transcript)
        await memory.store(user_id, facts)
    except Exception:
        logger.exception("memory: failed to extract/store facts")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
