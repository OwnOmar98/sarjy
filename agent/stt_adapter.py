"""
Provider-agnostic STT with automatic fallback (docs/PRD.md §5: "silence
is the failure mode" — a rate-limited/down STT provider must not go
silent). Uses livekit-agents' own stt.FallbackAdapter, not a hand-rolled
retry loop — it already catches provider errors (rate limits included)
and handles the retry/availability bookkeeping correctly.

STT_PROVIDER picks which one is tried first ("groq", default, or
"openai"); whichever isn't primary is always the automatic fallback.
"""

import os

from livekit.agents import stt as stt_base
from livekit.agents import vad as vad_base
from livekit.plugins import groq
from livekit.plugins import openai as openai_plugin

# Bilingual sample: Whisper's prompt biases both vocabulary and, on
# short/ambiguous audio, the detected language toward whatever the
# prompt is written in — an English-only prompt was pulling short Arabic
# utterances toward an English detection. Also carries the "Sarjy"
# vocabulary hint (misheard as "Sergey" otherwise) and a few short words
# that fixed real word-level transcription errors ("نعم" heard as "نام").
#
# No "مرحباً" ("hello") here deliberately — confirmed live, it isn't
# hypothetical: a real "hello," said early (before the agent had even
# spoken), came back mis-transcribed as exactly this word, sitting right
# next to "Sarjy" in the old prompt. It's a near-perfect collision —
# biasing toward the single Arabic word semantically closest to the most
# likely thing anyone says first is asking for exactly this failure.
_STT_PROMPT = (
    "Hi, I'm Sarjy, your voice assistant. أنا سرجي، مساعدك الصوتي. "
    "نعم، لا، بكرا، اليوم، المغرب، الظهر، العصر، الفجر، العشاء، اجتماع."
)


def _groq_stt() -> stt_base.STT:
    # detect_language=True matters more than it looks: groq.STT defaults
    # to language="en", detect_language=False, which forces every
    # utterance through Whisper as English regardless of what was
    # actually said.
    #
    # whisper-large-v3-turbo, not the full whisper-large-v3: both
    # mis-detect language equally often on short/ambiguous words, but on
    # a wrong-language detection the full model tends to *romanize* into
    # Latin script ("Nam.") while turbo stays in Arabic script ("نام.")
    # — full model, no accuracy win and more latency.
    return groq.STT(model="whisper-large-v3-turbo", detect_language=True, prompt=_STT_PROMPT)


def _openai_stt() -> stt_base.STT:
    # gpt-transcribe, not gpt-4o-mini-transcribe — the only OpenAI model
    # this plugin treats as supporting a language list. No accuracy win
    # over Groq as a primary, but a reasonable fallback when Groq itself
    # is unavailable.
    return openai_plugin.STT(model="gpt-transcribe", language=["ar", "en"], prompt=_STT_PROMPT)


def build_stt(vad: vad_base.VAD) -> stt_base.STT:
    # Neither Groq's nor OpenAI's Whisper-style STT streams natively —
    # both are one-shot recognize() calls per utterance. FallbackAdapter
    # requires streaming-capable STTs unless given a VAD, in which case
    # it wraps each with stt.StreamAdapter (VAD segments the audio,
    # recognize() runs per segment) — the same shape AgentSession already
    # uses this VAD for elsewhere, just reused here.
    providers = {"groq": _groq_stt, "openai": _openai_stt}
    primary = os.getenv("STT_PROVIDER", "groq").lower()
    if primary not in providers:
        primary = "groq"
    order = [primary, *[name for name in providers if name != primary]]
    return stt_base.FallbackAdapter([providers[name]() for name in order], vad=vad)
