"""
Provider-agnostic STT with automatic fallback (docs/PRD.md §5: "silence
is the failure mode"). Uses livekit-agents' stt.FallbackAdapter rather
than a hand-rolled retry loop.

STT_PROVIDER picks which is tried first ("groq", default, or "openai");
the other is always the fallback.
"""

import os

from livekit.agents import stt as stt_base
from livekit.agents import vad as vad_base
from livekit.plugins import groq
from livekit.plugins import openai as openai_plugin

# Whisper's prompt biases language detection toward whatever script it's
# written in (short Arabic audio was defaulting to English) and carries
# the "Sarjy" name hint (misheard as "Sergey" without it). No "مرحباً"
# here on purpose — a real early "hello" once got mis-transcribed as
# exactly that word when it sat in this prompt. Shared by both providers
# below so it can't drift out of sync between them.
_STT_NAME_HINT = "Hi, I'm Sarjy, your voice assistant. أنا سرجي، مساعدك الصوتي."

# Groq only. Tested all 10 original vocabulary words vs. various trims:
# "نعم" is the only one that ever fixed a real mishearing (نعم -> نام
# without it); the rest made no measured difference either way. The full
# list also caused the worst hallucination found in testing — silence
# echoing "نعم، لا، بكراغ" together. Trimmed to نعم-only: same accuracy,
# smaller hallucination surface. Doesn't fully fix it though — نعم itself
# still occasionally hallucinates onto noise; that's inherent to keeping
# the one word that's actually needed, and Groq has no safer mechanism
# to move it to (see _STT_KEYWORDS below for why OpenAI does).
_STT_PROMPT = f"{_STT_NAME_HINT} نعم."

# OpenAI's gpt-transcribe takes vocabulary via a real `keywords` field,
# not folded into free-text `prompt` like Whisper — a structurally safer
# mechanism (no hallucination observed on this model in testing either
# way), so kept at the full word list rather than trimmed like Groq's.
# Fallback-only; Groq has no equivalent field.
_STT_KEYWORDS = [
    "نعم",
    "لا",
    "بكرا",
    "اليوم",
    "المغرب",
    "الظهر",
    "العصر",
    "الفجر",
    "العشاء",
    "اجتماع",
]


def _groq_stt() -> stt_base.STT:
    # detect_language=True: groq.STT defaults to language="en" otherwise,
    # forcing every utterance through as English regardless of content.
    # whisper-large-v3-turbo over the full model: same mis-detection rate
    # on short/ambiguous audio, but the full model romanizes into Latin
    # script on a wrong guess ("Nam.") while turbo stays in Arabic ("نام.").
    return groq.STT(model="whisper-large-v3-turbo", detect_language=True, prompt=_STT_PROMPT)


def _openai_stt() -> stt_base.STT:
    # gpt-transcribe: the only OpenAI model this plugin treats as
    # supporting a language list and structured keywords.
    return openai_plugin.STT(
        model="gpt-transcribe",
        language=["ar", "en"],
        prompt=_STT_NAME_HINT,
        keywords=_STT_KEYWORDS,
    )


def build_stt(vad: vad_base.VAD) -> stt_base.STT:
    # Neither provider streams natively (one-shot recognize() per
    # utterance) — FallbackAdapter needs streaming-capable STTs unless
    # given a VAD, in which case it wraps each in stt.StreamAdapter.
    providers = {"groq": _groq_stt, "openai": _openai_stt}
    primary = os.getenv("STT_PROVIDER", "groq").lower()
    if primary not in providers:
        primary = "groq"
    order = [primary, *[name for name in providers if name != primary]]
    return stt_base.FallbackAdapter([providers[name]() for name in order], vad=vad)
