"""
Sarjy voice agent worker — entry point.

Wires VAD -> turn detection -> STT -> LLM -> TTS with tool calls,
memory, and latency instrumentation. Day-1 goal: a working EN+AR voice
loop, deployed. Stubbed pieces are TODO-tagged per docs/PRD.md.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    RoomInputOptions,
    STTContextOptions,
    WorkerOptions,
    cli,
    inference,
    llm,
)
from livekit.plugins import noise_cancellation, silero
from livekit.rtc import AudioFrame

import conversations
import memory
import tts_cache
import web_notify
from affirmatives import confirmation_policy
from arabic_normalize import MAX_PATTERN_LEN, normalize_for_speech
from db import ensure_user
from language_detect import (
    LanguageTracker,
    SpeechMetadata,
    describe_for_llm,
    detect_code_switch,
    language_directive,
)
from latency import LatencyTracker
from llm_adapter import build_llm
from stt_adapter import build_stt, retune_for_language
from tools import (
    check_calendar_availability,
    confirm_pending_action,
    get_prayer_time,
    list_calendar_events,
    propose_booking,
    propose_cancellation,
    propose_edit_event,
    undo_last_action,
)
from tts_adapter import build_tts

load_dotenv()
logger = logging.getLogger("sarjy-agent")

# Must match tools.py's _DEFAULT_TZ — the model needs "today"/"tomorrow" to
# resolve to the same calendar date the booking tools will parse.
_DEFAULT_TZ = ZoneInfo("Asia/Riyadh")

# asyncio.create_task() only holds a weak reference to the task it returns —
# without something else holding a strong reference, the event loop is free
# to garbage-collect it mid-execution, silently dropping whatever it was
# doing (confirmed live: a fact-extraction call cut short this way, storing
# "name is Owen" but losing "favorite color is blue" from the very next
# sentence). Every fire-and-forget task in this file goes through here so
# none of them are silently GC'd mid-flight.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Some models occasionally write a tool call as literal text (e.g.
# "<function=check_calendar_availability>{...}</function>", originally
# seen from Groq's now-removed llama-3.3-70b-versatile — see
# llm_adapter.py) instead of using the real tool-calling mechanism — it
# gets spoken and shown to the user as garbage, and nothing actually gets
# called. Real tool calls live in ChatChunk.delta.tool_calls, a separate
# field from .content, so this only ever touches malformed text that
# leaked into content — it can't affect a correctly-formed call. Kept as
# a defensive net regardless of which model is currently active.
_LEAK_MARKER = "<function="

# Graceful degradation (docs/PRD.md §5: "silence is the failure mode").
# An unhandled exception anywhere in the LLM stream (a rate limit, a
# malformed tool call, any other provider failure) would otherwise
# propagate out and kill the whole turn with zero reply, spoken or shown.
# This is the outer boundary that keeps any of them from going completely
# silent, not a fix for one specific cause.
# One entry per language, not one bilingual string. The old single string said
# the same sentence in English and then in Arabic — the exact
# say-it-twice-as-a-translation pattern the agent's own instructions forbid,
# modelled by the agent itself, and it lands in history as an assistant turn
# where it becomes a bilingual anchor for every reply that follows. The `None`
# entry is still bilingual on purpose: it is the one case where the language
# genuinely isn't known yet, and guessing wrong there means apologising in a
# language the user may not speak.
_FALLBACK_MESSAGES: dict[str | None, str] = {
    "en": "Sorry, I ran into a problem there — could you try again?",
    "ar": "عذرًا، واجهت مشكلة، ممكن تجرب مرة ثانية؟",
    None: (
        "Sorry, I ran into a problem there — could you try again? "
        "عذرًا، واجهت مشكلة، ممكن تجرب مرة ثانية؟"
    ),
}

# docs/HANDOFF.md's diagnosed "early speech can be silently dropped" gap
# (task #16): livekit-agents replaces user audio with silence before STT
# for aec_warmup_duration seconds (default 3s, see AgentSession below)
# after the agent starts speaking, to stop it hearing its own echo — real
# protection, not a bug, but it means genuine speech that overlaps the
# greeting's first few seconds never reaches STT at all. transcription_timeout
# (below) is the SDK's own answer: it fires when VAD saw speech but no
# transcript showed up in time, covering this case and any other STT gap
# (a slow/failed provider) the same way — this message plays instead of
# leaving the user talking into silence.
# Per-language for the same reason as _FALLBACK_MESSAGES above.
_MISSED_SPEECH_MESSAGES: dict[str | None, str] = {
    "en": "Sorry, I didn't catch that — could you say it again?",
    "ar": "عذرًا، لم ألتقط كلامك، ممكن تعيد؟",
    None: "Sorry, I didn't catch that — could you say it again? عذرًا، لم ألتقط كلامك، ممكن تعيد؟",
}

# Deliberately longer than transcription_timeout (2.5s) so the SDK's own
# net always fires first when it is armed; this only ever speaks for a turn
# that net never covered. See the watchdog in entrypoint() for why one exists.
_MISSED_SPEECH_WATCHDOG_S = 4.0

# TTS phrase cache candidates (docs/PRD.md §4) — the only text in this
# codebase that's genuinely fixed turn to turn. Everything else the
# agent says is LLM-generated and varies per turn, so it was never a
# cache candidate; caching it would mean matching on content that's
# different every time, which is a cache that never hits.
_CACHEABLE_PHRASES = {*_FALLBACK_MESSAGES.values(), *_MISSED_SPEECH_MESSAGES.values()}


async def _strip_leaked_tool_syntax(
    chunks: AsyncIterable[llm.ChatChunk],
    language: str | None = None,
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
            delta=llm.ChoiceDelta(
                role="assistant",
                content=_FALLBACK_MESSAGES.get(language, _FALLBACK_MESSAGES[None]),
            ),
        )
        return

    if pending:
        yield llm.ChatChunk(id=last_id, delta=llm.ChoiceDelta(role="assistant", content=pending))


# A reply-wide sticky "has this shown Arabic yet" flag was the first fix
# here and was itself wrong: a genuinely mixed reply that opens in Arabic
# and later switches to a whole separate English sentence would still
# Arabic-ize a bare time in that sentence, purely because the reply had
# touched Arabic earlier in an unrelated clause — confirmed live (and
# raised again testing a mixed reply: an English clause right after an
# Arabic one still got Arabic-ized, since a flat character-count lookback
# alone still reached back across the sentence break into the Arabic
# clause). What should decide a given number is the language of its own
# sentence, not the reply as a whole and not just "N characters back"
# blind to what's in them — so this resets at the most recent sentence
# boundary (./!/?/؟) in the recently-emitted text before checking it for
# Arabic script, in addition to capping its raw length as a backstop for
# a pathologically long, unpunctuated sentence.
_ARABIC_CONTEXT_WINDOW = MAX_PATTERN_LEN * 2
_SENTENCE_END = re.compile(r"[.!?؟]")


def _carry_forward(combined: str) -> str:
    # What the *next* window inherits as context — reset at the most
    # recent sentence boundary (a later, separate sentence shouldn't
    # remember an earlier one's language), length-capped as a backstop for
    # a pathologically long, unpunctuated sentence. Deliberately NOT used
    # to judge the window that produced `combined` itself — a window whose
    # own trailing character happens to be that sentence's period (the
    # common case: the period arrives in the same flush as what precedes
    # it) would otherwise have its own content chopped away by its own
    # trailing punctuation right before being checked, discarding the very
    # context it needed to judge itself correctly. That exact bug shipped
    # first: "17:00." flushed as one piece, and the period at the end
    # wiped out "...الساعة" right before the has-Arabic check ran on it.
    boundaries = list(_SENTENCE_END.finditer(combined))
    if boundaries:
        combined = combined[boundaries[-1].end() :]
    return combined[-_ARABIC_CONTEXT_WINDOW:]


async def _normalize_arabic_tts_stream(text: AsyncIterable[str]) -> AsyncIterator[str]:
    # Same hold-back-a-tail technique as _strip_leaked_tool_syntax above,
    # here so a raw ISO date/time split across two streamed chunks still
    # gets caught rather than slipping through unmatched.
    #
    # normalize_for_speech only ever produces Arabic words — right for an
    # Arabic reply's raw ISO date/time (its whole purpose, per
    # arabic_normalize.py's docstring), wrong for an English one: an
    # English reply mentioning "5:00" has no Arabic period-marker word
    # for _speak_bare_time to recognize, so it fell through to a bare
    # hour-based guess and produced "5 صباحًا" (an Arabic AM/PM marker)
    # sitting inside an otherwise-English sentence — confirmed live. Only
    # normalize a window whose own context (everything carried forward
    # from earlier windows, since the last sentence boundary, plus the
    # window itself) actually contains Arabic script — see _carry_forward.
    pending = ""
    recent = ""
    async for chunk in text:
        pending += chunk
        safe_len = max(0, len(pending) - MAX_PATTERN_LEN)
        if safe_len:
            window = pending[:safe_len]
            combined = recent + window
            yield normalize_for_speech(window) if _ARABIC_SCRIPT.search(combined) else window
            recent = _carry_forward(combined)
            pending = pending[safe_len:]
    if pending:
        combined = recent + pending
        yield normalize_for_speech(pending) if _ARABIC_SCRIPT.search(combined) else pending


_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def _detect_language(text: str) -> str:
    # Confirmed live: Groq's whisper-large-v3-turbo never reports a
    # language on user_input_transcribed at all, even with
    # detect_language=True — Groq's own API supports verbose_json with a
    # real language field (tested directly against the API), but
    # livekit-plugins-openai (which groq.STT subclasses; Groq's endpoint
    # is OpenAI-compatible) only requests that format for the literal
    # model name "whisper-1", which Groq never uses. Every turn_traces
    # row was landing as "unknown" as a result. This scans the transcript
    # text itself instead — scoped to exactly the two languages this
    # project supports, not general language ID.
    if _ARABIC_SCRIPT.search(text):
        return "ar"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return "unknown"


def _publish_stage(latency: LatencyTracker, stage: str, seconds: float | None) -> None:
    # MetricsReport fields (and our own time.monotonic() timings) are in
    # seconds; some fields are absent for turns they don't apply to (e.g.
    # tts_node_ttfb on a tool-only reply) — skip those rather than
    # publishing a misleading 0.
    if seconds is None:
        return
    _fire_and_forget(latency.publish(stage, seconds * 1000))


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
        # rule it out, then straight through with no buffering added
        # beyond _normalize_arabic_tts_stream's own small hold-back (the
        # only path a raw ISO date/time could actually appear on; the
        # two cacheable phrases below are fixed strings that never
        # contain one, so they skip normalization rather than needing it).
        async def _passthrough() -> AsyncIterator[str]:
            yield first
            async for chunk in text_iter:
                yield chunk

        async for frame in Agent.default.tts_node(
            agent, _normalize_arabic_tts_stream(_passthrough()), model_settings
        ):
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
    def __init__(self, latency: LatencyTracker, language: LanguageTracker) -> None:
        self._latency = latency
        # Rolling per-conversation language estimate, shared with entrypoint()
        # so the STT-reported language recorded on user_input_transcribed and
        # the retuning driven from it are the same object. See
        # LanguageTracker's docstring for why the estimate is rolling rather
        # than per-turn.
        self._language = language
        # Set by on_user_turn_completed, read by llm_node — see llm_node's
        # own comment for why a tool-call follow-up needs a fresh,
        # stronger reminder rather than trusting the copy already earlier
        # in context.
        self._last_language_meta: SpeechMetadata | None = None
        now = datetime.now(_DEFAULT_TZ)
        super().__init__(
            instructions=(
                f"Right now it is {now.strftime('%A, %Y-%m-%d, %H:%M')} "
                "(Asia/Riyadh time) — use this to resolve 'today', "
                "'tomorrow', or a weekday name into an actual calendar "
                "date yourself before calling propose_booking, "
                "check_calendar_availability, propose_cancellation, or "
                "propose_edit_event; never ask the user to restate a "
                "date/time that's already unambiguous just because you "
                "need to convert it to ISO 8601 yourself. "
                "You are Sarjy, a helpful bilingual (Arabic/English) voice "
                "assistant. Judge the language of the user's last message "
                "as a whole and reply in one of three modes: mostly "
                "Arabic — reply in Arabic; mostly English — reply in "
                "English; meaningfully mixed, real code-switching rather "
                "than a single borrowed word — reply naturally in that "
                "same kind of mixed Arabic/English a bilingual speaker "
                "would actually use. Don't mirror language word-by-word "
                "or force every term to match what they said; the goal "
                "is natural bilingual conversation, not literal "
                "mirroring. Never say the same sentence twice in two "
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
                "after they return. Tool results carry machine-formatted "
                "dates and times (ISO strings, 24-hour clock, an 'ISO:' "
                "tag meant only for your own chaining into the next tool "
                "call) — never speak these back to the user verbatim in "
                "either language. Always restate them as a natural "
                "spoken sentence in whichever language you're replying "
                'in ("الساعة أربعة وثلاث دقائق فجرًا", not "04:03"; '
                '"today" or the actual date spoken naturally, not '
                '"2026-08-13"). Booking, cancelling, and editing all go '
                "through propose_booking/propose_cancellation/"
                "propose_edit_event first, then confirm_pending_action — "
                "the actual write only happens on that second call, so "
                "never treat a proposal as done until you've confirmed "
                "it. propose_booking takes a duration in minutes, not an "
                'end time — if the user gave both ("between 1 and 2"), '
                "compute the duration yourself, don't ask for it again; "
                "only ask first if it truly can't be worked out. As soon "
                "as the user gives a specific start time for a new "
                "booking, even before you have a title, call "
                "check_calendar_availability right away using that time "
                "(assume a 30-minute duration if none was given yet) — a "
                "conflict makes the title irrelevant, so find that out "
                "before asking for one; only ask for the title once you "
                "know the slot is actually free. For "
                "cancelling or editing, get the exact event time first "
                "(list_calendar_events or check_calendar_availability if "
                "you don't already know it) before proposing. Editing "
                "(renaming, rescheduling, or changing the duration of an "
                "event that already exists) always goes through "
                "propose_edit_event, never a cancel-then-book pair — that "
                "would lose the original event's identity and read to "
                "the user as two separate actions instead of one change. "
                "After any propose call, relay what it describes to the "
                "user in one short sentence and wait for their actual "
                "next turn. " + confirmation_policy() + " Never call confirm_pending_action "
                "without a live proposal from this same conversation, and "
                "never in the same turn the proposal was first made. "
                "After any booking, cancellation, or edit actually goes "
                "through, always say back what changed — the title and "
                "the time, spoken naturally — and mention it can be "
                "undone; speech recognition can mishear a confirmation, "
                "so hearing what changed is the user's only way to catch "
                "that. If they say the change was wrong, or that they "
                "never confirmed it, call undo_last_action instead of "
                "arguing or re-asking."
            ),
            tools=[
                get_prayer_time,
                check_calendar_availability,
                list_calendar_events,
                propose_booking,
                propose_cancellation,
                propose_edit_event,
                confirm_pending_action,
                undo_last_action,
            ],
        )

    def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterator[llm.ChatChunk]:
        # Re-injects a stronger, imperative language reminder right before
        # the *follow-up* generation that comes after a tool call —
        # llm_node fires once per LLM call within a turn, not once per
        # turn, so a booking confirmation is two calls: one that decides
        # to call confirm_pending_action, one that turns its result into
        # the spoken reply. on_user_turn_completed's own tag sits right
        # before the user's message; by the second call it's separated
        # from the actual generation point by the function_call and
        # function_call_output items, and confirmed live (reproduced
        # directly against the real model/prompt/tools) that distance was
        # enough for an earlier stretch of Arabic history to pull the
        # reply back into Arabic despite the tag still technically being
        # in context. Re-adding the same passive tag right here still
        # wasn't enough in that same reproduction — only an explicit
        # imperative ("you must reply in X") actually overrode it, hence
        # language_directive rather than describe_for_llm here. Only
        # fires when the tail is genuinely a tool result, not on every
        # call — an already-close tag needs no reinforcement.
        if (
            self._last_language_meta
            and chat_ctx.items
            and isinstance(chat_ctx.items[-1], llm.FunctionCallOutput)
            and (directive := language_directive(self._last_language_meta))
        ):
            chat_ctx.add_message(role="system", content=directive)
        return _strip_leaked_tool_syntax(
            Agent.default.llm_node(self, chat_ctx, tools, model_settings),
            # If this generation fails outright, the apology should at least be
            # in the language the user is speaking rather than in both.
            language=self._language.estimate(),
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

        # The provider's own answer for what language the audio was, when it
        # gave one — recorded by _on_user_input_transcribed as the transcript
        # arrived. It beats counting script in the transcript because it is
        # the only one of the two signals that survives a mis-transcription:
        # English audio returned in Arabic script reads as "Arabic" to a
        # script counter, and the reply instruction built from that is what
        # answers an English speaker in Arabic.
        reported = self._language.take_reported()
        meta = detect_code_switch(text, reported_language=reported)
        self._last_language_meta = meta
        self._language.observe(meta)
        if meta.transcript_disagrees:
            logger.warning(
                "language: STT reported %s but the transcript is %s — probable "
                "mis-transcription; replying in %s",
                meta.reported_language,
                meta.script_language,
                meta.primary_language,
            )

        # Retunes STT for the *next* turn from the rolling estimate, not from
        # this one turn — see retune_for_language's and LanguageTracker's
        # docstrings for why tuning the decoder off the transcript it produced
        # is a feedback loop rather than an adaptation.
        retune_for_language(self.session.stt, self._language.estimate())

        start = time.monotonic()
        facts = await memory.retrieve(self.session.userdata, text)
        _publish_stage(self._latency, "memory", time.monotonic() - start)
        if facts:
            turn_ctx.add_message(
                role="system",
                content="Known about this user, from past conversations:\n"
                + "\n".join(f"- {f}" for f in facts),
            )

        # Language tag goes in LAST, after the facts block, so it is the final
        # thing before the user's own message. memory.py normalizes each fact
        # into "whichever language reads best", so the facts block routinely
        # carries Arabic sentences — and it used to be injected *after* the
        # language tag, i.e. between the instruction and the message it
        # applied to, putting an Arabic anchor closer to generation than the
        # instruction telling the model to reply in English. Injected on every
        # turn, mixed or not — see describe_for_llm's docstring for why a
        # single-language turn needs this too.
        if lang_context := describe_for_llm(meta):
            turn_ctx.add_message(role="system", content=lang_context)


# docs/PRD.md §5's second promise for the memory pillar ("memory feeds ASR
# vocabulary hints"), previously unbuilt. Remembered facts are the only
# source of user-specific proper nouns this system has — a colleague's
# name or a recurring meeting title is exactly what STT mangles and
# exactly what memory already stores.
#
# Honest scope: this reaches whichever configured STT actually advertises
# the keyterms capability. Groq/Whisper does not (it has only a free-text
# prompt, and stt_adapter.py documents why that prompt is deliberately
# minimal), so today this takes effect on the OpenAI fallback and would
# start applying to the primary if the primary ever changes to a provider
# with a real term list.
_KEYTERM_STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "has",
    "have",
    "at",
    "on",
    "in",
    "to",
    "of",
    "and",
    "or",
    "user",
    "his",
    "her",
    "their",
    "every",
    "all",
    "for",
    "with",
    "likes",
    "prefers",
    "name",
    "meeting",
    "meetings",
    "favorite",
}
_MAX_KEYTERMS = 20


def _keyterms_from_facts(facts: list[str]) -> list[str]:
    """Distinctive terms worth biasing STT toward — proper nouns, not whole facts.

    Latin tokens are taken only when capitalized (a name, not a common word);
    Arabic has no case, so those are length-filtered instead. Both are crude,
    which is why the result is capped and why nothing downstream depends on it
    being right — a wrong keyterm biases one word, it doesn't break a turn.
    """
    seen: dict[str, None] = {}
    for fact in facts:
        for raw in fact.split():
            token = raw.strip(".,!?;:'\"()[]—-،؛؟")
            if len(token) < 3 or token.lower() in _KEYTERM_STOPWORDS:
                continue
            if (
                _ARABIC_SCRIPT.search(token)
                or token[0].isupper()
                and token.isascii()
                and token.isalpha()
            ):
                seen.setdefault(token, None)
    return list(seen)[:_MAX_KEYTERMS]


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

    # First thing done with this identity, before anything else in this
    # function touches Postgres — every user_id-keyed table FK's this row,
    # so nothing downstream needs its own lazy-create anymore.
    try:
        await ensure_user(user_id)
    except Exception:
        logger.exception("db: failed to ensure user row — memory/sessions may fail this turn")

    latency = LatencyTracker(room=ctx.room, session_id=ctx.room.name)
    # Shared by the session event handlers below (which record the language the
    # STT provider reported) and SarjyAgent (which reads it back per turn and
    # retunes the STT from it).
    language_tracker = LanguageTracker()

    # Retrieved before the session is built, not after start(), because it
    # now feeds two things: the returning-user greeting (as before) and the
    # STT keyterm list below, which is a construction-time option. Failure
    # here must not take the session with it — an unreachable Postgres or
    # Redis should cost memory, not the whole conversation.
    try:
        known_facts = await memory.retrieve(user_id, "facts about the user")
    except Exception:
        logger.exception("memory: initial retrieve failed, starting without known facts")
        known_facts = []

    # web/server/api/token.get.ts sets this participant attribute only when
    # the user picked "continue" on a specific past conversation in the UI
    # — absent on every fresh connection. resume_context scopes the lookup
    # to user_id too, so a browser can't reopen another user's session
    # just by guessing an id, and distinguishes "no such session" from "a
    # real one with no summary yet" (a plain nullable return couldn't).
    resume_summary = None
    db_session_id: str | None = None
    if resume_session_id := participant.attributes.get("resume_session_id"):
        try:
            found, resume_summary = await conversations.resume_context(resume_session_id, user_id)
            if found:
                # Reopens the same conversation — new messages append to
                # this row, not a new one, which is what lets sending a
                # message in a resumed conversation move it back to the
                # top of the web UI's most-recently-active sort.
                db_session_id = resume_session_id
        except Exception:
            logger.exception("conversations: failed to fetch resume context")

    # New row per LiveKit session, not per user — this is the "one browsable
    # conversation" unit web/server/api/sessions*.ts reads back. Covers
    # both a genuinely fresh connection and a resume_session_id that
    # turned out stale/foreign (resume_context found nothing for it above)
    # — either way, falls back to a real session rather than silently
    # going conversation-history-less for the rest of this call. Failure
    # here shouldn't cost the whole conversation either, same reasoning as
    # known_facts above; a session that fails to open just doesn't get
    # persisted or summarized, everything else still works.
    if db_session_id is None:
        try:
            db_session_id, session_row = await conversations.start_session(user_id)
            # A brand-new row, not a resumed one — this is the "a new
            # conversation just appeared" moment the sidebar's live-update
            # channel exists for (web/server/routes/ws.ts). A resumed
            # session already has a row every open tab's already seen; its
            # own sidebar-relevant moment is end_session below instead.
            _fire_and_forget(web_notify.notify_session_upserted(user_id, session_row))
        except Exception:
            logger.exception("conversations: failed to open a session row")

    if db_session_id is not None:
        # The web side has no other way to learn a brand-new conversation's
        # id (it only ever sends resume_session_id, never receives one back)
        # — without this, clicking Stop on a fresh conversation had nowhere
        # sensible to navigate to, even though the row (and its messages)
        # already existed. Also fires for a resumed conversation (redundant
        # with what the web side already has via the route, but harmless)
        # rather than special-casing which branch set db_session_id above.
        # Fire-and-forget, not awaited — nothing below this point depends
        # on it having landed (the frontend only needs it by the time
        # someone clicks Stop, comfortably later), so there's no reason to
        # hold up session.start()/the greeting on a data-channel round
        # trip. Originally awaited here and confirmed live as one real
        # contributor to "starting a conversation takes longer now."
        async def _publish_session_id() -> None:
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps({"sessionId": db_session_id}).encode("utf-8"),
                    topic="session",
                )
            except Exception:
                logger.exception("failed to publish session id to room")

        _fire_and_forget(_publish_session_id())

        # Runs on every shutdown path (Stop button, disconnect, crash) —
        # add_shutdown_callback, not a bare fire-and-forget task, because
        # this needs to survive past the point the job process starts
        # tearing down, which a plain asyncio.create_task does not.
        async def _close_session() -> None:
            try:
                session_row = await conversations.end_session(db_session_id)
                # The summary (or a rename via a future edit) just landed —
                # every open tab's sidebar should reflect it without
                # needing its own manual reload.
                await web_notify.notify_session_upserted(user_id, session_row)
            except Exception:
                logger.exception("conversations: failed to close/summarize session")

        ctx.add_shutdown_callback(_close_session)

    # Distinctive proper nouns from this user's remembered facts — the only
    # user-specific vocabulary this system has, and exactly what STT mangles.
    keyterms = _keyterms_from_facts(known_facts)

    # TODO(day 2): swap LLM/TTS per request by language (docs/PRD.md §3-4).
    session = AgentSession[str](
        userdata=user_id,  # tools.py reads this via RunContext.userdata
        vad=ctx.proc.userdata["vad"],
        stt=build_stt(ctx.proc.userdata["vad"], keyterms=keyterms),
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
        # Fires when VAD saw the user speak but no transcript showed up in
        # time — covers both a slow/failed STT call and audio that was
        # deliberately withheld from STT during aec_warmup_duration's
        # silence-substitution window (task #16: "early speech can be
        # silently dropped"). Timed from end-of-speech, not speech start
        # (confirmed in livekit-agents' audio_recognition.py) — eval/run.py's
        # own p50/p95 numbers put real stt latency at 1113/1396ms, so 2.5s
        # is comfortably above worst-case-but-still-arriving before firing
        # on a genuine drop.
        transcription_timeout=2.5,
        # Static terms only — the framework's LLM-based keyterm detection is
        # deliberately left off, since it adds a per-turn model call to a
        # pipeline whose latency is already the weakest measured pillar.
        #
        # Kept alongside build_stt's own `keyterms` argument, not replaced by
        # it: this option only reaches a provider that advertises the framework
        # keyterms capability (none of the three configured here do), so on its
        # own it was wiring PRD §5's "memory feeds ASR vocabulary hints" to
        # nothing. build_stt passes the same terms straight to the providers
        # that actually take them. This stays so the wiring is already correct
        # if a future provider does advertise the capability.
        stt_context_options=STTContextOptions(keyterms=keyterms),
    )

    # Backstop for the "user spoke, got nothing back at all" issue.
    #
    # The SDK's own transcription_timeout above is the primary net, and
    # reading livekit-agents' source confirms it is armed on *VAD*
    # end-of-speech and only disarmed by a final transcript with non-empty
    # text (audio_recognition.py: _arm_transcription_timeout /
    # _mark_turn_transcribed) — so an empty Whisper result, which
    # stt.StreamAdapter drops before it ever reaches that layer, is already
    # covered. What is *not* covered is the case where that timer was never
    # armed for this turn at all: _arm_transcription_timeout returns early
    # while _turn_transcript_received is still set from the previous turn,
    # and that flag is only cleared during end-of-turn cleanup. A second
    # utterance arriving inside that window has no net.
    #
    # This watchdog is driven by user_state_changed instead, which is a
    # different signal path, and deliberately waits longer than
    # transcription_timeout so the SDK's own net always gets first refusal.
    _missed_speech_handle: asyncio.TimerHandle | None = None
    _transcript_seen = True  # nothing is pending before the first user turn

    # Per-turn speech duration, recorded so the deferred confirmation-evidence
    # gate (docs/ISSUE_ANALYSIS.md §2) can eventually be decided on a measured
    # distribution rather than a third guess. Recording only — nothing reads
    # this to make a decision, deliberately: the previous attempt at that gate
    # was removed for thresholding a signal nobody had characterised, and the
    # fix for that is data, not a better guess.
    #
    # It is a *proxy*, and the name says so. livekit-agents exposes no
    # per-turn VAD speech duration on the success path — `speech_duration`
    # rides only on UserTranscriptionTimeoutEvent, i.e. the failure path — so
    # this measures wall-clock between the user_state_changed transitions
    # instead. That includes Silero's own hangover (min_silence_duration,
    # 0.55s default) plus event dispatch, so it reads high by a roughly
    # constant amount. Constant is what matters here: both populations being
    # compared (a genuine short confirmation vs. an affirmative hallucinated
    # onto noise) carry the same offset, so the *separation* between them is
    # measurable even though neither absolute value is.
    _speech_started_at: float | None = None

    def _publish_speech_duration_proxy() -> None:
        nonlocal _speech_started_at
        if _speech_started_at is None:
            return
        _publish_stage(latency, "speech_duration_proxy", time.monotonic() - _speech_started_at)
        _speech_started_at = None

    def _missed_speech_message() -> str:
        # In whichever language the conversation has settled into; bilingual
        # only while it hasn't. Both call sites fire precisely when nothing was
        # transcribed, so there is no current turn to read a language off —
        # the rolling estimate is the only signal available here, which is one
        # more reason it is worth keeping.
        return _MISSED_SPEECH_MESSAGES.get(
            language_tracker.estimate(), _MISSED_SPEECH_MESSAGES[None]
        )

    def _cancel_missed_speech_watchdog() -> None:
        nonlocal _missed_speech_handle
        if _missed_speech_handle is not None:
            _missed_speech_handle.cancel()
            _missed_speech_handle = None

    def _note_transcript_seen() -> None:
        # A flag as well as a cancel, because event ordering isn't
        # guaranteed: a transcript can land before the VAD state has
        # finished transitioning out of "speaking", and cancelling a timer
        # that hasn't been armed yet would leave the later arm running with
        # nothing to stop it — a spurious "I didn't catch that" over a turn
        # that was heard perfectly well.
        nonlocal _transcript_seen
        _transcript_seen = True
        _cancel_missed_speech_watchdog()

    def _on_missed_speech_deadline() -> None:
        nonlocal _missed_speech_handle
        _missed_speech_handle = None
        if _transcript_seen:
            return
        # Only speak when the agent is genuinely idle. Any other state
        # ("thinking", "speaking") means this turn is already being handled
        # — a slow LLM is not a dropped utterance.
        if session.agent_state != "listening":
            logger.debug("watchdog: skipped, agent_state=%s", session.agent_state)
            return
        logger.warning(
            "watchdog: user speech ended with no transcript and no SDK timeout — "
            "replying with the missed-speech prompt"
        )
        session.say(_missed_speech_message())

    @session.on("user_state_changed")
    def _on_user_state_changed(ev) -> None:
        nonlocal _missed_speech_handle, _transcript_seen, _speech_started_at
        if ev.new_state == "speaking":
            # VAD did fire for this turn — which is itself the diagnostic
            # separating "STT dropped it" from "VAD never saw it" for the
            # silent-utterance issue (docs/KNOWN_ISSUES.md #4).
            logger.debug("watchdog: user speech started")
            _transcript_seen = False
            _speech_started_at = time.monotonic()
            _cancel_missed_speech_watchdog()
            return

        if ev.old_state == "speaking":
            _publish_speech_duration_proxy()
            if _transcript_seen:
                return
            _cancel_missed_speech_watchdog()
            _missed_speech_handle = asyncio.get_running_loop().call_later(
                _MISSED_SPEECH_WATCHDOG_S, _on_missed_speech_deadline
            )

    @session.on("user_transcription_timeout")
    def _on_user_transcription_timeout(ev) -> None:
        # The SDK's own net handled this turn — suppress the watchdog so the
        # apology is spoken once, not twice.
        _note_transcript_seen()
        logger.warning("user spoke (%.2fs) but no transcript arrived in time", ev.speech_duration)
        session.say(_missed_speech_message())

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev) -> None:
        if ev.transcript:
            _note_transcript_seen()
        # Provider-reported language wins when one is actually given; a
        # script-based guess off the transcript text otherwise (see
        # _detect_language above). Both the stock Groq plugin and OpenAI's
        # gpt-transcribe report nothing here (docs/KNOWN_ISSUES.md #7) —
        # VerboseGroqSTT and ElevenLabs Scribe both do, which is what makes
        # this branch worth feeding into the language policy and not just into
        # a trace column.
        language_tracker.note_reported(ev.language)
        if ev.language:
            latency.set_language(ev.language)
        elif ev.transcript:
            latency.set_language(_detect_language(ev.transcript))

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
                _fire_and_forget(_remember(user_id, text))
                if db_session_id is not None:
                    _fire_and_forget(_add_message_and_notify(user_id, db_session_id, "user", text))
        elif ev.item.role == "assistant":
            _publish_stage(latency, "llm_first_token", ev.item.metrics.get("llm_node_ttft"))
            _publish_stage(latency, "tts_first_byte", ev.item.metrics.get("tts_node_ttfb"))
            _publish_stage(latency, "total", ev.item.metrics.get("e2e_latency"))
            if db_session_id is not None and (text := ev.item.text_content):
                _fire_and_forget(_add_message_and_notify(user_id, db_session_id, "assistant", text))

    await session.start(
        agent=SarjyAgent(latency=latency, language=language_tracker),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Krisp noise cancellation — near-free on LiveKit Cloud.
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # known_facts was retrieved before the session was constructed (it also
    # feeds stt_context_options above). on_user_turn_completed only fires
    # from the second turn onward — the greeting is generated before any
    # user turn exists, so a returning user needs that one up-front call.
    # Broad query ("facts about the user", not just "the user's name") so
    # a returning user who never stated their name still counts as known.
    if known_facts:
        # extract_facts() (memory.py) normalizes each fact into whichever
        # language reads best — reused here as a free, already-available
        # signal for which language this user tends to speak, rather than
        # tracking a separate explicit preference.
        # detect_code_switch(), not _detect_language() — the latter is
        # Arabic-first by design (a single Arabic character anywhere tags
        # the whole string "ar"), which is fine for the coarse turn_traces
        # presence flag it exists for, but wrong here: one Arabic proper
        # noun in an otherwise-English set of remembered facts would have
        # greeted an English-speaking user in Arabic. This counts tokens
        # and picks whichever language actually dominates.
        preferred_language = (
            "Arabic"
            if detect_code_switch(" ".join(known_facts)).primary_language == "ar"
            else "English"
        )
        greeting_instructions = (
            f"Greet this returning user briefly in {preferred_language} — they "
            "already know you're bilingual, so don't re-explain that. You "
            "already know this about them from past conversations: "
            + "; ".join(known_facts)
            + ". If their name is among these, greet them by name instead of generically."
        )
        # "ar", not None. Under the old shared bilingual prompt, None was the
        # only way to keep Arabic biasing on, because the prompt had exactly
        # two states (with and without a "نعم" suffix). Each language now has
        # its own prompt, so an Arabic-leaning returning user can actually be
        # given the Arabic one instead of the bilingual compromise.
        preferred_language_code = "en" if preferred_language == "English" else "ar"
    else:
        greeting_instructions = "Greet the user briefly in English, mention you also speak Arabic."
        preferred_language_code = "en"

    # Seeds STT's per-turn retuning (stt_adapter.py) before the user's own
    # first turn even happens, rather than leaving it at build_stt()'s
    # static default — the greeting itself is real signal for which
    # language this turn is likely to come back in (a returning user's
    # actual known language, or the fixed "greet in English" instruction
    # for a new one), not a guess. Turn 1 was otherwise the one gap this
    # retuning couldn't close on its own: on_user_turn_completed only
    # fires from turn 2 onward, so nothing had retuned it yet.
    language_tracker.seed(preferred_language_code)
    retune_for_language(session.stt, preferred_language_code)

    if resume_summary:
        # Additive, not a replacement for the known_facts greeting above —
        # facts are about the user in general, this is specifically "you
        # picked up a particular past conversation," which needs its own
        # acknowledgment (e.g. "picking up where we left off...").
        greeting_instructions += (
            " The user just chose to continue a specific earlier conversation. "
            f"Here's what it was about: {resume_summary} Acknowledge picking it "
            "back up (briefly — don't recite the summary verbatim) before asking "
            "how you can help."
        )

    await session.generate_reply(instructions=greeting_instructions)


async def _remember(user_id: str, transcript: str) -> None:
    try:
        # Same query on_user_turn_completed's own retrieve() call already
        # made for this exact transcript — retrieve()'s Redis cache makes
        # this effectively free, not a second real lookup. Without this,
        # extract_facts() has nothing to compare a correction against and
        # can only ever add a fact, never supersede the one it corrects
        # (memory.py's own docstring).
        existing = await memory.retrieve(user_id, transcript)
        to_add, to_remove = await memory.extract_facts(user_id, transcript, existing)
        await memory.store(user_id, to_add, remove=to_remove)
    except Exception:
        logger.exception("memory: failed to extract/store facts")


async def _add_message_and_notify(user_id: str, session_id: str, role: str, content: str) -> None:
    # One fire-and-forget task covering both, not two separate ones — the
    # push needs the row add_message() just inserted (its real id and
    # created_at, for SelectedConversationTranscript.vue's dedupe-by-id),
    # so notifying has to happen after the insert actually lands, not
    # just after it's been kicked off.
    try:
        message = await conversations.add_message(session_id, role, content)
        await web_notify.notify_message_added(user_id, session_id, message)
    except Exception:
        logger.exception("conversations: failed to add message / notify")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
