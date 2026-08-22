"""Groq Whisper STT that asks for `verbose_json` instead of `json`.

`livekit-plugins-openai` requests `verbose_json` only for the literal model
name `"whisper-1"` (see its `_recognize_impl`), and Groq never uses that name
— so on the stock plugin every Groq transcription comes back as bare text and
three things Whisper already computed are thrown away before anyone sees them:

- **`language`** — the model's own answer for what language the audio was in.
  This is the only language signal in the system derived from audio rather
  than from the transcript's script, which makes it the only one that
  survives a mis-transcription. docs/KNOWN_ISSUES.md #7 records this field as
  unavailable; it is available, it just wasn't being asked for.
- **`compression_ratio`** — the standard repetition detector, for the
  repetition-loop failure ("نعم، نعم، بكثير من الوصف").
- **`avg_logprob`** — how confident the decode was. `SpeechData.confidence` is
  always 0.0 on this stack, so this is the first real confidence number
  available for a Groq turn.

**What the numbers turned out to say.** `eval/stt_compare.py --sweep` was run
against the committed fixtures, and the honest result is that the thresholds
do not separate the two populations. Non-speech decodes land at avg_logprob
-0.29 to -0.37 and compression_ratio 0.58 — comfortably *inside* the range
real speech occupies (-0.06 to -0.71, 0.33 to 1.28). No pair of cut-offs in
the swept grid rejects a single non-speech clip without also discarding real
speech, and `no_speech_prob` reads 0.0 on every clip of either kind, exactly
as docs/KNOWN_ISSUES.md #1 recorded. Whisper is not unsure when it hallucinates
onto silence; it is confident and wrong, and no confidence signal can catch
that. The `avg_logprob` threshold is therefore **off by default**: at the reference
value of -1.0 it discarded a real one-word "نعم" (-1.004) while rejecting
neither non-speech clip. Only the compression-ratio backstop stays on, for the
repetition-loop mode it genuinely covers.

**What does work is knowing what the hallucination will say.** Two modes, both
measured: with a prompt set, silence comes back as the prompt itself ("I'm
Sarjy." under the English prompt, "أنا سرجي، مساعدك الصوتي." under the Arabic
one); with no prompt, it comes back as Whisper's stock caption for empty audio
("Thank you."). Both are exactly matchable, neither depends on a confidence
number, and neither can fire on plausible user speech. `_prompt_echo` and
`_SILENCE_ARTEFACTS` below handle them.

Thresholds are overridable by env var so the sweep can be re-run against new
fixtures rather than trusting the numbers above forever. Every drop is logged
with its reason — the point of this module is as much to produce the
measurements nobody had as it is to act on them.

A transcript whose segments are all rejected comes back empty. That is
deliberate and is the existing, already-wired failure path: `stt.StreamAdapter`
drops an empty transcript, the SDK's `transcription_timeout` stays armed, and
the user hears main.py's "I didn't catch that" instead of the agent silently
acting on a word nobody said.
"""

import logging
import os

import httpx
import openai
from livekit import rtc
from livekit.agents import APIConnectOptions, APIStatusError, APITimeoutError, LanguageCode
from livekit.agents import stt as stt_base
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given
from livekit.plugins import groq

logger = logging.getLogger("sarjy-agent.groq_verbose_stt")


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("groq_verbose_stt: %s=%r is not a number, using %s", name, raw, default)
        return default


# Above this, the segment is repeating itself — gzip compresses a loop far
# better than it compresses speech. Kept on: real speech measured 0.33-1.28
# here, so 2.4 has enormous headroom and has never fired on a real fixture,
# while the repetition-loop failure it guards ("نعم، نعم، بكثير من الوصف") is
# real and has no other detector.
_COMPRESSION_RATIO_MAX = _env_float("STT_COMPRESSION_RATIO_MAX", 2.4)

# Off by default, deliberately, and this is the measurement talking rather
# than caution. At the reference-decoder default of -1.0 this fired on
# `short_yes_ar` — a genuine one-word Arabic "نعم", decoded at -1.004 — and
# threw the turn away, while rejecting neither of the two non-speech clips
# (which decode at -0.29 and -0.37, comfortably *better* than the real
# confirmation). It is a filter that discards real short confirmations and
# catches no hallucinations, which is the worst possible trade in a booking
# flow. Set STT_AVG_LOGPROB_MIN to re-enable it, e.g. to re-run
# `eval/stt_compare.py --sweep` against new fixtures.
_AVG_LOGPROB_MIN = _env_float("STT_AVG_LOGPROB_MIN", None)

# Above this, Whisper itself thinks there was no speech. Kept for
# completeness; measured at 0.0 on every clip of either kind, so nothing here
# depends on it firing.
_NO_SPEECH_PROB_MAX = _env_float("STT_NO_SPEECH_PROB_MAX", 0.6)


# Whisper's stock output for audio containing no speech. It does not return
# nothing — it returns its training data's most common caption, which for
# YouTube-derived data is a sign-off. Measured directly: `eval/stt_compare.py`
# on `silence.wav` and `room_tone.wav` returns exactly "Thank you." from both,
# at avg_logprob -0.29/-0.37, i.e. a *confident* decode.
#
# Matched only against the whole transcript, never a fragment of a longer one:
# "thank you" inside a real sentence is a real "thank you". As a complete turn
# in a booking assistant, the cost of being wrong is the agent asking the user
# to repeat themselves, which is the same thing it already does whenever a
# transcript doesn't arrive.
_SILENCE_ARTEFACTS = {
    "thank you",
    "thanks",
    "thanks for watching",
    "thank you for watching",
    "thank you very much",
    "شكرا",
    "شكرا لكم",
    "شكرا جزيلا",
    "ترجمة نانسي قنقر",
    "subtitles by the amara.org community",
}

_PUNCTUATION = str.maketrans("", "", ".,!?;:\"'()[]{}—-،؛؟…")


def _normalize(text: str) -> str:
    return " ".join(text.translate(_PUNCTUATION).lower().split())


def _prompt_echo(text: str, prompt: str | None) -> bool:
    """Whether this segment is just the decoder prompt read back.

    Whisper treats the prompt as decoder context, so with nothing else to
    condition on it will happily emit the prompt itself. Measured: with the
    English prompt active, `silence.wav` transcribes as "I'm Sarjy."; with the
    Arabic one, `room_tone.wav` transcribes as "أنا سرجي، مساعدك الصوتي." Both
    are verbatim fragments of the prompt that was sent.

    This is the precise detector the thresholds turned out not to be. It cannot
    fire on real speech unless the user reads the agent's own self-introduction
    aloud, and unlike a logprob cut-off it does not care how confident the
    decode was — which matters, because these decodes are confident.

    Two words minimum: single words ("hi") appear in the prompt and are also
    perfectly ordinary things for a user to say on their own.
    """
    if not prompt:
        return False
    normalized = _normalize(text)
    if len(normalized.split()) < 2:
        return False
    return normalized in _normalize(prompt)


def _segment_rejection(segment, prompt: str | None = None) -> str | None:
    """Why this segment should be dropped, or None to keep it."""
    text = (getattr(segment, "text", "") or "").strip()
    compression_ratio = getattr(segment, "compression_ratio", None)
    avg_logprob = getattr(segment, "avg_logprob", None)
    no_speech_prob = getattr(segment, "no_speech_prob", None)

    if _prompt_echo(text, prompt):
        return "prompt echo"
    if (
        _COMPRESSION_RATIO_MAX is not None
        and compression_ratio is not None
        and compression_ratio > _COMPRESSION_RATIO_MAX
    ):
        return f"compression_ratio={compression_ratio:.2f} > {_COMPRESSION_RATIO_MAX}"
    if _AVG_LOGPROB_MIN is not None and avg_logprob is not None and avg_logprob < _AVG_LOGPROB_MIN:
        return f"avg_logprob={avg_logprob:.2f} < {_AVG_LOGPROB_MIN}"
    if (
        _NO_SPEECH_PROB_MAX is not None
        and no_speech_prob is not None
        and no_speech_prob > _NO_SPEECH_PROB_MAX
    ):
        return f"no_speech_prob={no_speech_prob:.2f} > {_NO_SPEECH_PROB_MAX}"
    return None


def filter_hallucinated_segments(segments, prompt: str | None = None) -> tuple[str, list[str]]:
    """Join the segments that survive; return them plus the reasons the rest were
    dropped.

    Split out from the STT class so `eval/stt_compare.py` can score the same
    decision against fixture audio without standing up a LiveKit session.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for segment in segments or []:
        reason = _segment_rejection(segment, prompt)
        text = (getattr(segment, "text", "") or "").strip()
        if reason is None:
            if text:
                kept.append(text)
        else:
            dropped.append(f"{text!r} ({reason})")

    joined = " ".join(kept).strip()
    if _normalize(joined) in _SILENCE_ARTEFACTS:
        dropped.append(f"{joined!r} (whole transcript is a known no-speech artefact)")
        joined = ""
    return joined, dropped


class VerboseGroqSTT(groq.STT):
    """Drop-in `groq.STT` that keeps Whisper's own diagnostics.

    Only `_recognize_impl` is overridden; construction, `update_options`
    (which stt_adapter.py's per-language prompt retuning drives) and the
    plugin's error mapping are all inherited unchanged.
    """

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str | list[str]] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt_base.SpeechEvent:
        if is_given(language):
            # Matches the parent's contract: a per-call language overrides the
            # configured one. A list is meaningless to Whisper (one language
            # per request), so only the first is used.
            codes = [language] if isinstance(language, str) else list(language)
            self._opts.languages = [code for code in codes if code]

        data = rtc.combine_audio_frames(buffer).to_wav_bytes()
        prompt = self._opts.prompt if is_given(self._opts.prompt) else None
        # detect_language=True leaves `languages` empty (the plugin clears it
        # in __init__), which is what lets Whisper answer the question at all.
        pinned = self._opts.languages[0] if self._opts.languages else None

        try:
            resp = await self._client.audio.transcriptions.create(
                file=("file.wav", data, "audio/wav"),
                model=self._opts.model,  # type: ignore[arg-type]
                language=pinned or openai.omit,
                prompt=prompt or openai.omit,
                response_format="verbose_json",
                # Whisper's own hallucination guidance: no sampling. The stock
                # plugin leaves this unset, so it rides on the API default.
                temperature=0.0,
                timeout=httpx.Timeout(30, connect=conn_options.timeout),
            )
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message, status_code=e.status_code, request_id=e.request_id, body=e.body
            ) from None

        raw_text = (getattr(resp, "text", "") or "").strip()
        segments = getattr(resp, "segments", None)
        detected = getattr(resp, "language", None) or ""

        if segments:
            text, dropped = filter_hallucinated_segments(segments, prompt)
            if dropped:
                logger.warning(
                    "stt: dropped %d hallucinated segment(s): %s (kept %r)",
                    len(dropped),
                    "; ".join(dropped),
                    text,
                )
        else:
            # Some responses carry no segment breakdown at all. Nothing to
            # judge, so nothing is dropped — a filter with no signal must not
            # invent one and start eating real transcripts.
            text, dropped = raw_text, []

        return stt_base.SpeechEvent(
            type=stt_base.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt_base.SpeechData(
                    text=text,
                    # Whisper answers with a full language *name* here
                    # ("english", "arabic"), not a BCP-47 code — passed
                    # through as-is; language_detect.normalize_language is
                    # what folds either shape down to "ar"/"en".
                    language=LanguageCode(detected),
                    # Whisper gives no per-utterance confidence; avg_logprob is
                    # per segment. Left at the default rather than synthesising
                    # a number that would read as more trustworthy than it is —
                    # the ElevenLabs provider is the one that reports a real
                    # confidence (see stt_adapter.py).
                )
            ],
        )
