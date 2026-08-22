"""Per-token Arabic/English code-switch detection for the LLM's response
language policy (docs/PRD.md §5; reviewer feedback points 3 and 8).

Two signals feed this module, in priority order:

1. **The language the STT provider reports for the audio**, when it reports
   one at all. This is the only signal derived from what the user actually
   *said* rather than from what came back as text, so it is the one that
   survives a mis-transcription. Groq/Whisper reports nothing through the
   stock plugin; `groq_verbose_stt.py` (verbose_json) and the ElevenLabs
   Scribe provider both do.
2. **Script counting over the transcript**, as the fallback and as the only
   way to see code-switching *within* one utterance (no provider reports a
   second language for a segment).

Signal 1 exists because signal 2 has a failure mode that is exactly the bug
this module is supposed to prevent: when English audio comes back in Arabic
script, counting the script yields "Arabic", the caller instructs the model
to reply in Arabic, and the user — who spoke English — gets an Arabic reply.
Every strengthening of that instruction makes the mis-transcription case
worse, so the instruction is only as good as the language estimate behind it.

Deliberately separate from main.py's `_detect_language()`: that one answers
"ar" or "en" for a whole utterance (used for turn_traces tagging),
Arabic-checked-first so a single Arabic character forces "ar" even in an
otherwise-English sentence — fine for a coarse presence flag, not for judging
whether a message is genuinely code-switched.
"""

import re
from collections import deque
from dataclasses import dataclass, field

_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN_LETTERS = re.compile(r"[A-Za-z]")

_LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}

# A turn counts as genuinely code-switched only when the minority language
# clears BOTH bars. Presence alone used to be enough (`len(languages) > 1`),
# which meant one Arabic token in an English sentence — a real filler
# ("يعني", "خلاص"), an Arabic proper noun read back out of memory, or a
# single hallucinated word — flipped the turn to "mixed" and produced an
# instruction to *reply* code-switched. That directly contradicts the agent's
# own system prompt ("meaningfully mixed, real code-switching rather than a
# single borrowed word"), and the injected tag wins because it sits closer to
# generation.
#
# Both bars, not either: the ratio alone still fires on a 3-token turn with
# one stray token, and a short turn carries the least evidence. The two error
# directions are not symmetric — a false "mixed" produces the wrong-language
# reply this module exists to prevent, while a false "not mixed" just answers
# in the dominant language, which is a fine answer to a two-word utterance.
# So the thresholds are deliberately biased toward not-mixed.
_MIXED_MIN_MINORITY_TOKENS = 2
_MIXED_MIN_MINORITY_RATIO = 0.25

# How many recent turns the rolling estimate looks at, and how many of them
# have to agree before it will name a language. See LanguageTracker.
_TRACKER_WINDOW = 3
_TRACKER_MIN_AGREEMENT = 2


def normalize_language(code: str | None) -> str | None:
    """Fold a provider's language code down to "ar", "en", or None.

    Providers disagree on shape — "ar", "ar-SA", "arabic", "en-US", and the
    empty string all show up across the three STTs configured here. Anything
    that isn't one of the two languages this project supports returns None
    rather than being passed through: a third language is not a signal this
    system knows how to act on, and treating it as one would silently pin the
    reply language to something the agent can't speak.
    """
    if not code:
        return None
    base = code.strip().lower().replace("_", "-").split("-")[0]
    if base in ("ar", "ara", "arabic"):
        return "ar"
    if base in ("en", "eng", "english"):
        return "en"
    return None


@dataclass
class SpeechMetadata:
    text: str
    languages: list[str]
    """Languages whose script is present at all, in no particular order — a subset of
    ["ar", "en"]. Presence only; `mixed` is what decides whether the turn is treated
    as code-switched."""
    primary_language: str | None
    """The language the reply should be in. The STT-reported language when the provider
    gave one, otherwise whichever script has more tokens. None if there's no signal at
    all (numbers-only, silence-timeout filler, etc.)."""
    mixed: bool
    """True only for genuine code-switching — both scripts present *and* the minority
    one clearing _MIXED_MIN_MINORITY_TOKENS/_MIXED_MIN_MINORITY_RATIO."""
    reported_language: str | None = None
    """What the STT provider said the audio was, normalized; None when it reported
    nothing (the stock Groq plugin never does — see docs/KNOWN_ISSUES.md #7)."""
    script_language: str | None = None
    """What script counting alone would have concluded. Kept separately so a caller can
    see the two signals disagree — which is itself the fingerprint of a
    mis-transcription, not of code-switching."""

    @property
    def transcript_disagrees(self) -> bool:
        """The provider heard one language and the transcript came back in the other.

        Worth logging: it means the transcript is probably wrong, which is the
        upstream cause of a wrong-language reply rather than a language-policy
        problem.
        """
        return (
            self.reported_language is not None
            and self.script_language is not None
            and self.reported_language != self.script_language
        )


def detect_code_switch(text: str, *, reported_language: str | None = None) -> SpeechMetadata:
    """Judge the language of one user turn.

    `reported_language` is the STT provider's own answer for this utterance,
    when it gave one. It wins over script counting for `primary_language`,
    because it is the only one of the two signals that describes the audio
    rather than the transcription of it.

    A disagreement between the two also *clears* `mixed`: when the provider
    heard one language and the text came back in the other, the honest reading
    is "one of these transcriptions is wrong", not "the user code-switched".
    Treating it as code-switching would ask the model to reply in a mix of two
    languages on the strength of a mistranscription.
    """
    ar_count = 0
    en_count = 0
    for token in text.split():
        if _ARABIC_SCRIPT.search(token):
            ar_count += 1
        if _LATIN_LETTERS.search(token):
            en_count += 1

    languages = [lang for lang, count in (("ar", ar_count), ("en", en_count)) if count]
    script_primary = None if not languages else ("ar" if ar_count >= en_count else "en")

    minority = min(ar_count, en_count)
    total = ar_count + en_count
    mixed = (
        len(languages) > 1
        and minority >= _MIXED_MIN_MINORITY_TOKENS
        and minority / total >= _MIXED_MIN_MINORITY_RATIO
    )

    reported = normalize_language(reported_language)
    primary = reported or script_primary
    if reported is not None and script_primary is not None and reported != script_primary:
        mixed = False

    return SpeechMetadata(
        text=text,
        languages=languages,
        primary_language=primary,
        mixed=mixed,
        reported_language=reported,
        script_language=script_primary,
    )


@dataclass
class LanguageTracker:
    """A rolling estimate of which language this conversation is actually in.

    Exists because the STT prompt is retuned per turn from this estimate, and
    retuning off a *single* turn is a feedback loop rather than an adaptation:
    a turn wrongly read as Arabic biases the decoder toward Arabic, which
    makes the next turn more likely to come back Arabic too. Run in reverse it
    is just as bad — a run of English turns biases the decoder to English
    right up until the user switches, which is the one moment the bias is
    wrong. Neither direction self-corrects, because the thing being measured
    is downstream of the thing being tuned.

    Requiring _TRACKER_MIN_AGREEMENT of the last _TRACKER_WINDOW turns to
    agree breaks the loop: one bad turn can no longer move the decoder, and a
    genuine language switch still takes hold after two turns. When there's no
    agreement the estimate is None, which callers should read as "stay
    bilingual", not as "no signal to act on".
    """

    _turns: deque[str] = field(default_factory=lambda: deque(maxlen=_TRACKER_WINDOW))
    _pending_reported: str | None = None

    def note_reported(self, code: str | None) -> None:
        """Record the language the STT provider reported for the utterance that just
        transcribed. Held until the turn it belongs to is processed."""
        normalized = normalize_language(code)
        if normalized is not None:
            self._pending_reported = normalized

    def take_reported(self) -> str | None:
        """Consume the pending provider-reported language for this turn."""
        reported, self._pending_reported = self._pending_reported, None
        return reported

    def observe(self, meta: SpeechMetadata) -> None:
        """Fold one completed turn into the rolling estimate.

        A mixed turn is not evidence for either language, and a turn with no
        script at all is not evidence of anything — both are skipped rather
        than voting, so a bilingual stretch of conversation leaves the
        estimate where it was instead of dragging it around.
        """
        if meta.mixed or meta.primary_language is None:
            return
        self._turns.append(meta.primary_language)

    def estimate(self) -> str | None:
        """The settled language, or None when the recent turns don't agree."""
        if not self._turns:
            return None
        for language in ("ar", "en"):
            if list(self._turns).count(language) >= _TRACKER_MIN_AGREEMENT:
                return language
        return None

    def seed(self, language: str | None) -> None:
        """Prime the estimate before the first user turn — the greeting's own language
        is real evidence for which language turn 1 is likely to arrive in, and turn 1
        is otherwise the one turn nothing has tuned for."""
        normalized = normalize_language(language)
        self._turns.clear()
        if normalized is not None:
            self._turns.extend([normalized] * _TRACKER_MIN_AGREEMENT)


def language_directive(meta: SpeechMetadata) -> str | None:
    """A stronger, imperative version of describe_for_llm's tag — for
    re-injecting right before a reply generated *after* a tool call
    (main.py's llm_node), where the passive "Detected speech language"
    phrasing wasn't enough to override an earlier stretch of
    same-conversation history in the other language: confirmed live and
    by direct reproduction against the real model/prompt/tools, the
    declarative form still left a booking confirmation in Arabic despite
    sitting immediately before generation, right after a run of English
    turns and a purely-English tool result — an explicit command did
    override it in the same reproduction. Not a replacement for
    describe_for_llm's own tag, which every turn already gets — this is
    specifically for the harder case of pulling generation back on-language
    after a tool round-trip added distance and its own content to the context.

    Note this is the most load-bearing instruction in the system and the one
    that most amplifies a bad language estimate: it tells the model to ignore
    everything else it can see. That is exactly right when the estimate is
    right, and exactly wrong when it isn't — which is why `primary_language`
    prefers the provider's audio-derived answer over the transcript's script.
    """
    if meta.primary_language is None:
        return None
    if not meta.mixed:
        language = _LANGUAGE_NAMES[meta.primary_language]
        other = _LANGUAGE_NAMES["en" if meta.primary_language == "ar" else "ar"]
        return (
            f"You must reply to this specific message in {language} only. "
            f"Do not use {other} anywhere in this reply, regardless of the "
            "language used earlier in this conversation, and regardless of "
            "the script the transcript itself is written in."
        )
    secondary = [lang for lang in meta.languages if lang != meta.primary_language]
    return (
        f"This message code-switches — primary language "
        f"{_LANGUAGE_NAMES[meta.primary_language]}, also using "
        f"{', '.join(_LANGUAGE_NAMES[lang] for lang in secondary)}. Reply naturally mixing "
        "both the same way, regardless of the language used earlier in this conversation."
    )


def describe_for_llm(meta: SpeechMetadata) -> str | None:
    """A short system-context line for on_user_turn_completed, stating the
    detected language for every turn — not just mixed ones. Used to be
    mixed-only, on the assumption that a single-language turn was already
    covered by SarjyAgent's own three-mode language policy; confirmed live
    that assumption is wrong. A plain "Yes." following an earlier
    Arabic-language confirmation got answered in Arabic 3/3 reproductions
    — the model anchored on the language of its own most recent reply
    instead of the current turn's, and adding more instructions about
    judging language "fresh" didn't fix it. An explicit per-turn tag did,
    5/5 in testing, so every turn gets one now.

    When the provider's reported language and the transcript's script
    disagree, the tag says so outright: the model is about to read a message
    in one script and be told to answer in the other language, and left
    unexplained that reads as a contradiction to argue with rather than an
    instruction to follow.
    """
    if meta.primary_language is None:
        return None
    if meta.transcript_disagrees:
        return (
            "Detected speech language for this turn: "
            f"{_LANGUAGE_NAMES[meta.primary_language]} (from the audio itself). "
            "The transcript below came back in the other script, which means it is "
            "probably a mis-transcription — trust the detected speech language for "
            "which language to reply in, and if the transcript doesn't make sense, "
            "ask the user to repeat rather than guessing at what it says."
        )
    if not meta.mixed:
        return f"Detected speech language for this turn: {_LANGUAGE_NAMES[meta.primary_language]}."
    secondary = [lang for lang in meta.languages if lang != meta.primary_language]
    return (
        "Detected speech language for this turn — primary: "
        f"{_LANGUAGE_NAMES[meta.primary_language]}, code-switching: "
        f"{', '.join(_LANGUAGE_NAMES[lang] for lang in secondary)}."
    )
