"""Affirmative/refusal recognition, moved out of the STT decoder prompt.

`نعم` used to sit in Groq's Whisper prompt because without it a genuine "نعم"
came back as "نام", 100% reproducibly. That is a real result, and it was read
as a dilemma with no exit: keep the word and it hallucinates onto silence
(docs/KNOWN_ISSUES.md #1), drop it and confirmations stop being recognised.

It is only a dilemma if the transcript has to contain the literal token "نعم",
and nothing requires that. Confirmation is a judgement the model makes when it
calls `confirm_pending_action` — so "نام" can simply *count* as a yes. The
decoder stops being asked to guarantee one specific spelling, the
hallucination-shaped word leaves the prompt, and the recognition it was
protecting happens one layer down where a wrong guess costs nothing.

This module is the single place that list lives. It is **advisory**: nothing
here gates a write. The model still decides, using the wording composed into
its instructions by `confirmation_policy()`; these functions exist so the same
list can be asserted in unit tests and scored by `eval/stt_compare.py` against
real fixture audio, instead of the vocabulary living only inside a prompt
string where nothing can check it.

It is deliberately *not* wired as a code-level gate on `confirm_pending_action`.
A hallucinated "نعم" would sail straight through such a gate anyway — it is,
by construction, a perfectly-formed affirmative — so the gate would add
false-negative risk on genuine confirmations while catching none of the
failures it was built for. The mitigation for a wrong write stays where it
works: speaking back exactly what was written, and `undo_last_action`.
"""

import re

_DIACRITICS = re.compile(r"[ً-ْٰـ]")


def normalize_arabic(text: str) -> str:
    """Fold the spelling variants that make a literal word-list useless in Arabic.

    Alef forms (أ إ آ ٱ) are written interchangeably and STT picks between them
    more or less at random; ة/ه and ى/ي are the same story. Without folding
    these, a list matching "أيوة" misses "ايوه" for no reason a speaker would
    recognise.
    """
    text = _DIACRITICS.sub("", text)
    for source, target in (("أإآٱ", "ا"), ("ة", "ه"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي")):
        for char in source:
            text = text.replace(char, target)
    return text


# Arabic affirmatives, already normalized by normalize_arabic. "نام" and "نعام"
# are in here as *known mishearings* of "نعم", not as words a user would mean
# — that is the whole point of the module. Their literal senses ("he slept",
# "ostrich") are not plausible answers to "shall I book this?", so accepting
# them costs nothing.
_AFFIRMATIVE_AR = {
    "نعم",
    "نام",  # the reproducible mishearing of نعم once it left the STT prompt
    "نعام",
    "ايوه",
    "ايوا",
    "ايه",
    "تمام",
    "اكيد",
    "ماشي",
    "طيب",
    "زين",
    "اوكي",
    "اوك",
    "احجزها",
    "اكد",
    "وافق",
    "صح",
}

# Romanized mishearings of "نعم" belong with the English set because that is
# the script they arrive in: measured via eval/stt_compare.py, `short_yes_ar`
# ("نعم.") transcribes as the Latin string "NOM" and is reported as English.
# The Arabic-script mishearings are in _AFFIRMATIVE_AR above.
_AFFIRMATIVE_EN = {
    "nom",
    "nam",
    "naam",
    "yes",
    "yeah",
    "yep",
    "yup",
    "yah",
    "sure",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "correct",
    "absolutely",
    "definitely",
}

_AFFIRMATIVE_EN_PHRASES = (
    "go ahead",
    "do it",
    "book it",
    "go for it",
    "sounds good",
    "that works",
    "please do",
)

_NEGATIVE_AR = {
    "لا",
    "له",
    "مش",
    "ابدا",
    "الغي",
    "الغه",
    "توقف",
    "انتظر",
    "غلط",
    "خطا",
}

_NEGATIVE_EN = {
    "no",
    "nope",
    "nah",
    "cancel",
    "stop",
    "wait",
    "wrong",
    "incorrect",
    "dont",
    "don't",
}

_NEGATIVE_EN_PHRASES = ("do not", "hold on", "never mind", "not that", "i didn't")

_WORD = re.compile(r"[^\w؀-ۿ]+")


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD.split(normalize_arabic(text.lower())) if token}


def looks_affirmative(text: str) -> bool:
    """Whether this transcript reads as a yes in either language.

    A refusal anywhere in the utterance wins over an affirmative token: "no,
    cancel it please" was observed being flipped to a yes by STT
    (docs/KNOWN_ISSUES.md #2), and the safe reading of a transcript containing
    both is that it is not a confirmation.
    """
    if not text:
        return False
    if looks_negative(text):
        return False
    lowered = normalize_arabic(text.lower())
    if any(phrase in lowered for phrase in _AFFIRMATIVE_EN_PHRASES):
        return True
    tokens = _tokens(text)
    return bool(tokens & _AFFIRMATIVE_AR or tokens & _AFFIRMATIVE_EN)


def looks_negative(text: str) -> bool:
    """Whether this transcript reads as a refusal in either language."""
    if not text:
        return False
    lowered = normalize_arabic(text.lower())
    if any(phrase in lowered for phrase in _NEGATIVE_EN_PHRASES):
        return True
    tokens = _tokens(text)
    return bool(tokens & _NEGATIVE_AR or tokens & _NEGATIVE_EN)


# The words worth naming explicitly in the model's instructions. Not the whole
# list — the point is to establish the *shape* ("mishearings count too"), and a
# long recital of vocabulary in a system prompt is both wasted tokens and, per
# memory.py's own experience with worked examples, something the model starts
# echoing back at unrelated input.
_PROMPT_AFFIRMATIVES = ('"yes"', '"go ahead"', '"confirm"', '"نعم"', '"تمام"', '"احجزها"')
_PROMPT_MISHEARINGS = ('"نام"', '"نعام"')


def confirmation_policy() -> str:
    """The sentence(s) about confirmation wording to compose into the agent's
    instructions. Generated here so the vocabulary has exactly one home."""
    return (
        "This is voice with no keyboard, so any clear spoken yes — "
        + ", ".join(_PROMPT_AFFIRMATIVES)
        + " — counts as confirmation; never require an exact word or ask the user to "
        "type anything. Speech recognition also mangles short words, and "
        + " and ".join(_PROMPT_MISHEARINGS)
        + ' are known mishearings of "نعم" — when one of those is the whole '
        "reply to a proposal you just described, treat it as the yes it plainly "
        "is rather than as the word it literally spells. A clear no, a refusal, "
        "or changed details means propose again instead of confirming — and if a "
        "single reply contains both a refusal and an affirmative, it is not a "
        "confirmation, so ask again rather than guessing which half was meant."
    )
