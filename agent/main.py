"""
Sarjy voice agent worker — entry point.

Wires VAD -> turn detection -> STT -> LLM -> TTS with tool calls,
memory, and latency instrumentation. Day-1 goal: a working EN+AR voice
loop, deployed. Stubbed pieces are TODO-tagged per docs/PRD.md.
"""

import asyncio
import logging
import time
import uuid
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
    inference,
    llm,
)
from livekit.plugins import noise_cancellation, silero
from livekit.rtc import AudioFrame

import memory
import tts_cache
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

# TTS phrase cache candidates (docs/PRD.md §4) — the only text in this
# codebase that's genuinely fixed turn to turn. Everything else the
# agent says is LLM-generated and varies per turn, so it was never a
# cache candidate; caching it would mean matching on content that's
# different every time, which is a cache that never hits.
_CACHEABLE_PHRASES = {_FALLBACK_MESSAGE}


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


def _publish_stage(latency: LatencyTracker, stage: str, seconds: float | None) -> None:
    # MetricsReport fields (and our own time.monotonic() timings) are in
    # seconds; some fields are absent for turns they don't apply to (e.g.
    # tts_node_ttfb on a tool-only reply) — skip those rather than
    # publishing a misleading 0.
    if seconds is None:
        return
    asyncio.create_task(latency.publish(stage, seconds * 1000))


async def _cached_tts_node(
    agent: Agent, text: AsyncIterable[str], model_settings: ModelSettings
) -> AsyncIterator[AudioFrame]:
    text_iter = text.__aiter__()
    try:
        first = await text_iter.__anext__()
    except StopAsyncIteration:
        return

    if first not in _CACHEABLE_PHRASES:
        # The overwhelming common case (an LLM-generated reply, never a
        # cache candidate) — one unavoidable peek at the first chunk to
        # rule it out, then straight through with no buffering added.
        async def _passthrough() -> AsyncIterator[str]:
            yield first
            async for chunk in text_iter:
                yield chunk

        async for frame in Agent.default.tts_node(agent, _passthrough(), model_settings):
            yield frame
        return

    # A fixed phrase always arrives as a single complete chunk (it's
    # yielded that way at the source — main.py's own fallback path, e.g.
    # — never streamed), so confirm the stream actually ends here before
    # trusting the cache. Without this, a real multi-chunk reply that
    # merely *starts* with this exact text would get cut off.
    rest = [chunk async for chunk in text_iter]
    if rest:

        async def _replay() -> AsyncIterator[str]:
            yield first
            for chunk in rest:
                yield chunk

        async for frame in Agent.default.tts_node(agent, _replay(), model_settings):
            yield frame
        return

    cached = await tts_cache.get(first)
    if cached is not None:
        yield cached
        return

    async def _single() -> AsyncIterator[str]:
        yield first

    frames = []
    async for frame in Agent.default.tts_node(agent, _single(), model_settings):
        frames.append(frame)
        yield frame
    await tts_cache.store(first, frames)


class SarjyAgent(Agent):
    def __init__(self, latency: LatencyTracker) -> None:
        self._latency = latency
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
                "availability first. The tools only take a duration in "
                "minutes, not an end time — if the user gave both a start "
                'and an end ("between 1 and 2", "from 3 to 4:30"), '
                "compute the duration yourself, don't ask for it again; "
                "only ask if a duration truly can't be worked out from "
                "what they said. Once you know the time and duration, "
                "state them back in one short sentence and wait for the "
                "user to confirm before calling book_calendar_event — "
                "never book in the same turn you first mention a resolved "
                "time. This is a voice conversation with no keyboard: "
                "never ask the user to type anything, and never require "
                'one exact word — any clear spoken yes ("confirm", '
                '"yes", "go ahead", "book it", "نعم", "احجزها") '
                "counts; a clear no or a change of details means don't "
                "book yet."
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

    def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterator[AudioFrame]:
        return _cached_tts_node(self, text, model_settings)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # Semantic recall, injected right before the LLM sees this turn
        # (docs/PRD.md §5) — not the full transcript, a scoped block.
        text = new_message.text_content
        if not text:
            return
        start = time.monotonic()
        facts = await memory.retrieve(self.session.userdata, text)
        _publish_stage(self._latency, "memory", time.monotonic() - start)
        if facts:
            turn_ctx.add_message(
                role="system",
                content="Known about this user, from past conversations:\n"
                + "\n".join(f"- {f}" for f in facts),
            )


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per worker process, not per session.
    proc.userdata["vad"] = silero.VAD.load()


def _normalize_user_id(identity: str) -> str:
    # facts.user_id and calendar_events.user_id are both uuid columns
    # (db/schema.sql) — the real frontend always sends a real UUID
    # (useSarjyRoom.ts's crypto.randomUUID()), but nothing stops some
    # other client from joining with an arbitrary identity string, and
    # that would otherwise crash the *entire* session the moment
    # memory.py/tools.py first hit Postgres with it — the exact kind of
    # silent-failure this codebase guards against everywhere else.
    # Deterministic, so the same non-UUID identity still maps to the
    # same row every session rather than a fresh one each time.
    try:
        return str(uuid.UUID(identity))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # The web side mints a stable per-browser id (localStorage) and passes
    # it as the LiveKit identity — that's what memory keys facts on across
    # sessions (web/app/composables/useSarjyRoom.ts, web/server/api/token.get.ts).
    participant = await ctx.wait_for_participant()
    user_id = _normalize_user_id(participant.identity)

    latency = LatencyTracker(room=ctx.room, session_id=ctx.room.name)

    # TODO(day 2): swap LLM/TTS per request by language (docs/PRD.md §3-4).
    session = AgentSession[str](
        userdata=user_id,  # tools.py reads this via RunContext.userdata
        vad=ctx.proc.userdata["vad"],
        stt=build_stt(ctx.proc.userdata["vad"]),
        llm=build_llm(),
        tts=build_tts(),
        # inference.TurnDetector() is AgentSession's own default when
        # turn_detection is omitted — set explicitly rather than relying
        # on that implicit default, since it's core to the AR/EN pillar
        # (docs/PRD.md "Quality/fidelity") and shouldn't silently change
        # out from under us on an SDK upgrade. Cloud "v1" model (server-
        # calibrated per-language thresholds, ar/en both covered) when
        # LIVEKIT_API_KEY/SECRET are present, as they are here; degrades
        # to the local "v1-mini" model only if the gateway call fails.
        #
        # Preemptive generation starts an early reply before end-of-turn
        # is confident, which can produce a reply for a turn the user
        # hadn't actually finished — off until validated against this.
        turn_handling={
            "preemptive_generation": {"enabled": False},
            "turn_detection": inference.TurnDetector(),
        },
    )

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev) -> None:
        # Best-effort language tag for turn_traces (latency.py) — not
        # every STT provider/model reports one (stt_adapter.py).
        if ev.language:
            latency.set_language(ev.language)

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev) -> None:
        if not isinstance(ev.item, llm.ChatMessage):
            return

        if ev.item.role == "user":
            latency.next_turn()
            _publish_stage(latency, "endpointing", ev.item.metrics.get("end_of_turn_delay"))
            _publish_stage(latency, "stt", ev.item.metrics.get("transcription_delay"))
            # conversation_item_added fires with history's actual
            # (post-truncation) content, but restricting fact extraction
            # to the user's own speech sidesteps the barge-in question
            # entirely — user audio is never truncated, only an
            # interrupted assistant reply is.
            text = ev.item.text_content
            if text:
                asyncio.create_task(_remember(user_id, text))
        elif ev.item.role == "assistant":
            _publish_stage(latency, "llm_first_token", ev.item.metrics.get("llm_node_ttft"))
            _publish_stage(latency, "tts_first_byte", ev.item.metrics.get("tts_node_ttfb"))
            _publish_stage(latency, "total", ev.item.metrics.get("e2e_latency"))

    await session.start(
        agent=SarjyAgent(latency=latency),
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
