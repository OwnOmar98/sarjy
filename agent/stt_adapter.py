"""
Provider-agnostic STT with automatic fallback (docs/PRD.md §5: "silence
is the failure mode"). Uses livekit-agents' stt.FallbackAdapter rather
than a hand-rolled retry loop.

STT_PROVIDER picks which is tried first ("openai" (default), "elevenlabs", or
"groq"); the others follow as fallbacks, ordered by measured accuracy (see
_PROVIDERS below), not alphabetically or historically.

Default changed from "groq" after eval/stt_compare.py measured all three
against the same fixtures (docs/KNOWN_ISSUES.md #3): openai (gpt-transcribe)
mean WER 0.009 vs. groq's 0.285, and 0.077 vs. 0.231 on the code-switched
clip specifically — the case this product is actually built for. The cost is
latency, roughly 503ms -> 1291ms p50, which is real but is a tuning knob
(STT_PROVIDER=groq) rather than a reason to ship the worse transcript by
default.

Two things changed here after tracing the wrong-language and hallucinated-
confirmation failures back through the pipeline, both of which had their root
in this file:

**The prompt no longer contains "نعم".** It was there because dropping it
regressed a real "نعم" to "نام", reproducibly — but that is only a regression
if the transcript has to say "نعم" literally, and it doesn't: confirmation is
judged by the model calling `confirm_pending_action`, and the model is now
told that the known mishearings count as a yes (see `affirmatives.py`). Biasing
the decoder globally to fix a word the intent layer can recognise anyway was
paying for it in exactly the currency it couldn't afford — an affirmative that
Whisper will emit over silence. The word is gone from the decoder; the
recognition it was protecting moved one layer down.

**The prompt is now language-specific in full, not just in its suffix.** The
old prompt was `"Hi, I'm Sarjy, your voice assistant. أنا سرجي، مساعدك الصوتي."`
— eight English words to four Arabic — and per-turn retuning only ever added
or removed the "نعم" on the end. Whisper's language detection is biased by the
script of its prompt, so an Arabic speaker was being nudged toward English on
every single utterance and nothing in the retuning could reach it. Each
language now gets its own prompt, with the bilingual one kept only for when
the conversation genuinely hasn't settled.
"""

import logging
import os

from livekit.agents import stt as stt_base
from livekit.agents import vad as vad_base

# Sentinel for "don't pass this argument at all" — the plugins distinguish an
# omitted option from an empty one, and an empty keyterm list is not the same
# request as no keyterm list.
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import groq
from livekit.plugins import openai as openai_plugin

from groq_verbose_stt import VerboseGroqSTT

logger = logging.getLogger("sarjy-agent.stt_adapter")

# The name hint alone is load-bearing and stays: "Sarjy" is heard as "Sergey"
# without it. What each variant does *not* do is carry the other language's
# script — that's the whole point of having three of them. No "مرحباً" in the
# Arabic one on purpose: a real early "hello" once got mis-transcribed as
# exactly that word when it sat in the prompt.
_PROMPTS: dict[str | None, str] = {
    "en": "Hi, I'm Sarjy, your voice assistant.",
    "ar": "أنا سرجي، مساعدك الصوتي.",
    None: "Hi, I'm Sarjy, your voice assistant. أنا سرجي، مساعدك الصوتي.",
}

# OpenAI's gpt-transcribe takes vocabulary via a real `keywords` field rather
# than folded into free-text `prompt` like Whisper — a structurally safer
# mechanism (no hallucination observed on this model in testing either way),
# so the full word list is fine here. Deliberately no "نعم": the affirmative
# is recognised at the intent layer now (see the module docstring), and there
# is no reason to keep a hallucination-shaped word in any provider's bias list
# when nothing downstream needs the literal token.
_STT_KEYWORDS = [
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

# Scribe v1/v2 are batch (one-shot recognize, wrapped in StreamAdapter like
# the other two); scribe_v2_realtime streams natively, which removes that
# wrapper — attractive for docs/KNOWN_ISSUES.md #4, but `keyterms` is
# batch-only in this plugin version, and keyterms are how remembered proper
# nouns reach the primary STT at all. Batch is the default for that reason;
# override to compare.
_ELEVENLABS_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")

_MAX_ELEVENLABS_KEYTERMS = 100


def _groq_stt(keyterms: list[str]) -> stt_base.STT:
    # detect_language=True: groq.STT defaults to language="en" otherwise,
    # forcing every utterance through as English regardless of content. It
    # also clears the plugin's `languages` list, which is what lets
    # verbose_json come back with Whisper's own answer for the language.
    #
    # whisper-large-v3-turbo over the full model: same mis-detection rate on
    # short/ambiguous audio, but the full model romanizes into Latin script on
    # a wrong guess ("Nam.") while turbo stays in Arabic ("نام.").
    #
    # VerboseGroqSTT, not groq.STT: same model and options, but it asks for
    # verbose_json, so the detected language and Whisper's own hallucination
    # diagnostics survive instead of being discarded by the plugin. Groq has
    # no keyterm field, so `keyterms` is unused here — see build_stt.
    del keyterms
    return VerboseGroqSTT(
        model="whisper-large-v3-turbo", detect_language=True, prompt=_PROMPTS[None]
    )


def _elevenlabs_stt(keyterms: list[str]) -> stt_base.STT | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    from livekit.plugins import elevenlabs

    # Passed explicitly: the plugin's own env fallback reads ELEVEN_API_KEY,
    # not ELEVENLABS_API_KEY, so it raises even with our var set unless the
    # key is handed to it directly. Same reason as tts_adapter.py.
    #
    # No language_code: leaving it unset is what turns on Scribe's own
    # language detection (the plugin adds include_language_detection=true),
    # and that detected code is the audio-derived language signal the rest of
    # the pipeline now prefers over guessing from the transcript's script.
    #
    # tag_audio_events: non-speech comes back tagged ("(noise)") instead of
    # being decoded into whatever word the model finds most likely — a
    # structural answer to hallucinating an affirmative onto silence rather
    # than a threshold guess at one.
    return elevenlabs.STT(
        api_key=api_key,
        model=_ELEVENLABS_MODEL,
        tag_audio_events=True,
        keyterms=keyterms[:_MAX_ELEVENLABS_KEYTERMS] if keyterms else NOT_GIVEN,
    )


def _openai_stt(keyterms: list[str]) -> stt_base.STT | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    # gpt-transcribe: the only OpenAI model this plugin treats as
    # supporting a language list and structured keywords.
    return openai_plugin.STT(
        model="gpt-transcribe",
        language=["ar", "en"],
        prompt=_PROMPTS[None],
        keywords=[*_STT_KEYWORDS, *keyterms],
    )


# Ordered by eval/stt_compare.py's measured mean WER (openai 0.009, elevenlabs
# 0.147, groq 0.285) — this is also the fallback order once a primary is
# picked, so if the primary fails mid-call the next attempt is the
# next-most-accurate provider, not an arbitrary one.
_PROVIDERS = {
    "openai": _openai_stt,
    "elevenlabs": _elevenlabs_stt,
    "groq": _groq_stt,
}

PROVIDER_NAMES = tuple(_PROVIDERS)


def build_provider(name: str, keyterms: list[str] | None = None) -> stt_base.STT | None:
    """One provider on its own, or None when its key isn't configured.

    Public so `eval/stt_compare.py` can put the providers side by side on the
    same audio without a LiveKit room and without a FallbackAdapter in the way
    — the comparison is the point, and it has to be the *same* construction the
    agent actually runs, not a re-implementation of it.
    """
    builder = _PROVIDERS.get(name)
    if builder is None:
        raise ValueError(f"unknown STT provider {name!r}; expected one of {PROVIDER_NAMES}")
    return builder(keyterms or [])


def build_stt(vad: vad_base.VAD, keyterms: list[str] | None = None) -> stt_base.STT:
    """Build the STT stack, primary first.

    `keyterms` are the distinctive proper nouns pulled from this user's
    remembered facts (main.py's `_keyterms_from_facts`). They used to reach
    only whichever provider advertised the framework's `keyterms` capability,
    which was neither of the configured ones — so PRD §5's "memory feeds ASR
    vocabulary hints" was wired to a provider that never ran. Passing them in
    at construction reaches ElevenLabs and OpenAI directly. Groq/Whisper still
    has no term list to put them in; that is a provider limit, not a wiring
    one, and it is one of the reasons ElevenLabs is worth measuring as primary.
    """
    terms = keyterms or []
    primary = os.getenv("STT_PROVIDER", "openai").lower()
    if primary not in _PROVIDERS:
        logger.warning("stt_adapter: unknown STT_PROVIDER=%r, falling back to openai", primary)
        primary = "openai"
    order = [primary, *[name for name in _PROVIDERS if name != primary]]

    instances: list[stt_base.STT] = []
    for name in order:
        try:
            instance = build_provider(name, terms)
        except Exception:
            logger.exception("stt_adapter: failed to build %s STT, skipping it", name)
            continue
        if instance is not None:
            instances.append(instance)

    if not instances:
        raise RuntimeError(
            "no STT provider could be built — set GROQ_API_KEY, ELEVENLABS_API_KEY, "
            "or OPENAI_API_KEY"
        )
    logger.info("stt_adapter: providers in order: %s", [type(i).__name__ for i in instances])
    if len(instances) == 1:
        return instances[0]

    # Neither Groq nor batch Scribe streams natively (one-shot recognize() per
    # utterance) — FallbackAdapter needs streaming-capable STTs unless given a
    # VAD, in which case it wraps each in stt.StreamAdapter.
    return stt_base.FallbackAdapter(instances, vad=vad)


def _iter_real_stts(stt: stt_base.STT):
    """Every underlying provider STT behind whatever wrappers are in play.

    Reaches into FallbackAdapter's private `_stt_instances` — there's no public
    passthrough for update_options on the adapter itself, only on each real
    provider STT (each is StreamAdapter-wrapped when the adapter was given a
    VAD, since not every provider here streams natively).
    """
    for instance in getattr(stt, "_stt_instances", [stt]):
        yield getattr(instance, "wrapped_stt", instance)


def retune_for_language(stt: stt_base.STT, language: str | None) -> None:
    """Point the prompt-based providers at one language for the *next* turn.

    Called from main.py with `LanguageTracker.estimate()` — a rolling read over
    the last few turns, not the language of the turn that just happened. That
    distinction is the fix, not a detail: retuning off a single turn is a
    feedback loop, because the signal being measured (the transcript) is
    downstream of the thing being tuned (the decoder bias). One turn wrongly
    read as Arabic used to bias the next turn toward Arabic, which made the
    next transcript more likely to come back Arabic again, with nothing in the
    loop able to pull it back. `None` means the conversation hasn't settled and
    restores the bilingual prompt.

    `detect_language=True` stays on unconditionally. An earlier version
    hard-pinned `language="en"` (detect_language=False) when the last turn was
    clean English, and it broke the very next turn if that one genuinely
    code-switched: the Arabic half went through a recognizer that could not
    output Arabic at all. Prompt script is the bias worth tuning; the
    auto-detection itself should never turn off — and now that the detected
    language is actually read back out of the response (groq_verbose_stt.py),
    turning it off would also blind the language policy downstream.

    ElevenLabs is deliberately untouched: Scribe does its own language
    detection server-side and reports it back, so there is no prompt to bias
    and nothing here to improve on.
    """
    prompt = _PROMPTS.get(language, _PROMPTS[None])
    for real in _iter_real_stts(stt):
        # groq.STT subclasses openai_plugin.STT, so this order matters.
        if isinstance(real, groq.STT):
            options = {"detect_language": True, "prompt": prompt}
        elif isinstance(real, openai_plugin.STT):
            # Prompt only. gpt-transcribe is configured with an explicit
            # language *list* (["ar", "en"]), and passing detect_language=True
            # to update_options clears that list — which would trade a
            # two-language constraint for open-ended detection on a model
            # that supports the constraint natively.
            options = {"prompt": prompt}
        else:
            continue
        try:
            real.update_options(**options)
        except Exception:
            logger.exception("stt_adapter: failed to retune %s for language=%s", real, language)
