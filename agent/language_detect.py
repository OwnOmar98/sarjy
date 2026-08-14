"""Per-token Arabic/English code-switch detection for the LLM's response
language policy (docs/PRD.md §5; reviewer feedback points 3 and 8).

Text-based script classification, not audio-based — Groq's STT doesn't
reliably report even a single per-utterance language field (confirmed
live: `ev.language` is empty on every real turn), let alone per-word, so
this works off the plain transcript instead of anything STT provides.

Deliberately separate from main.py's `_detect_language()`: that one
answers "ar" or "en" for a whole utterance (used for turn_traces
tagging), Arabic-checked-first so a single Arabic character forces "ar"
even in an otherwise-English sentence — fine for a coarse presence flag,
not for judging whether a message is genuinely code-switched or is
mostly one language with an incidental accent mark. This module counts
per-token, in whichever language reads best, so "primary" reflects which
language actually dominates.
"""

import re
from dataclasses import dataclass

_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN_LETTERS = re.compile(r"[A-Za-z]")

_LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}


@dataclass
class SpeechMetadata:
    text: str
    languages: list[str]
    """Languages actually present, in no particular order — a subset of ["ar", "en"]."""
    primary_language: str | None
    """Whichever of `languages` has more tokens; None if neither script is present at all
    (numbers-only, silence-timeout filler, etc.)."""
    mixed: bool
    """True iff both "ar" and "en" tokens are present — genuine code-switching, not just
    one language with occasional numerals/punctuation."""


def detect_code_switch(text: str) -> SpeechMetadata:
    ar_count = 0
    en_count = 0
    for token in text.split():
        if _ARABIC_SCRIPT.search(token):
            ar_count += 1
        if _LATIN_LETTERS.search(token):
            en_count += 1

    languages = [lang for lang, count in (("ar", ar_count), ("en", en_count)) if count]
    if not languages:
        primary = None
    else:
        primary = "ar" if ar_count >= en_count else "en"

    return SpeechMetadata(
        text=text, languages=languages, primary_language=primary, mixed=len(languages) > 1
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
    5/5 in testing, so every turn gets one now."""
    if meta.primary_language is None:
        return None
    if not meta.mixed:
        return f"Detected speech language for this turn: {_LANGUAGE_NAMES[meta.primary_language]}."
    secondary = [lang for lang in meta.languages if lang != meta.primary_language]
    return (
        "Detected speech language for this turn — primary: "
        f"{_LANGUAGE_NAMES[meta.primary_language]}, code-switching: "
        f"{', '.join(_LANGUAGE_NAMES[lang] for lang in secondary)}."
    )
