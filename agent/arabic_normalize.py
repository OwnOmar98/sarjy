"""Arabic TTS text normalization (docs/PRD.md §2) — a defense-in-depth
safety net right before synthesis, not the primary mechanism.
SarjyAgent's own instructions (main.py) already tell the LLM to restate
ISO dates/times as natural spoken Arabic instead of reading raw tool
output verbatim; this exists for the rare case that doesn't hold.

Confirmed live via TTS-then-STT round-trip testing that a raw ISO
date/time reaching the TTS provider isn't just awkward, it's outright
unintelligible: "2026-08-20" came back as "2000 وسعي دياتوند", "14:00"
as "بيت عويس" — complete gibberish, not a mispronunciation. Bare digits
in natural context ("30 دقيقة") and naturally-phrased dates/times both
came back perfectly — the colon/dash delimiter structure is what
breaks it, not the presence of numbers. Removing the delimiters and
using natural word order fixed it in the same round-trip test (minor
residual STT-side quirks, but no more gibberish).
"""

import re

_ARABIC_MONTHS = {
    1: "يناير",
    2: "فبراير",
    3: "مارس",
    4: "أبريل",
    5: "مايو",
    6: "يونيو",
    7: "يوليو",
    8: "أغسطس",
    9: "سبتمبر",
    10: "أكتوبر",
    11: "نوفمبر",
    12: "ديسمبر",
}

# (start_hour, end_hour, marker) — matches the period-of-day style
# SarjyAgent's own instructions already use elsewhere.
_PERIOD_MARKERS = [
    (0, 4, "فجرًا"),
    (4, 12, "صباحًا"),
    (12, 15, "ظهرًا"),
    (15, 18, "عصرًا"),
    (18, 21, "مساءً"),
    (21, 24, "ليلاً"),
]

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_ISO_DATETIME = re.compile(
    r"(?P<date>[0-9٠-٩]{4}-[0-9٠-٩]{2}-[0-9٠-٩]{2})"
    r"(?:[T ](?P<time>[0-9٠-٩]{1,2}:[0-9٠-٩]{2})(?::[0-9٠-٩]{2})?"
    r"(?:[+-][0-9٠-٩]{2}:[0-9٠-٩]{2}|Z)?)?"
)
_ISO_TIME_ONLY = re.compile(r"(?<!\d)(?P<time>[0-9٠-٩]{1,2}:[0-9٠-٩]{2})(?::[0-9٠-٩]{2})?(?!\d)")

# Currency (docs/PRD.md §2 named it as a risk category). No feature in this
# app produces currency text today, so unlike the ISO patterns above this
# was never confirmed broken by a live round-trip — it is written to the
# same shape as the fix that *was* proven (strip the delimiter structure,
# use natural word order), on the reasoning that a symbol-prefixed amount
# is the same kind of non-word token as a colon-delimited time.
#
# Handles both writing orders — "SAR 250" / "250 SAR" / "$250" — because
# an LLM asked for a price in Arabic text may produce either.
_CURRENCY_WORDS = {
    "SAR": "ريال",
    "﷼": "ريال",
    "USD": "دولار",
    "$": "دولار",
    "AED": "درهم",
    "EUR": "يورو",
    "€": "يورو",
}
_CURRENCY_ALTERNATION = "|".join(
    re.escape(code) for code in sorted(_CURRENCY_WORDS, key=len, reverse=True)
)
_CURRENCY = re.compile(
    rf"(?<![\w])(?:(?P<pre>{_CURRENCY_ALTERNATION})\s*(?P<amount_after>[0-9٠-٩]+(?:[.,][0-9٠-٩]+)?)"
    rf"|(?P<amount_before>[0-9٠-٩]+(?:[.,][0-9٠-٩]+)?)\s*(?P<post>{_CURRENCY_ALTERNATION}))(?![\w])"
)

# Longest realistic match ("2026-08-20T14:30:00+03:00") plus margin —
# how much text a streaming caller should hold back so a pattern split
# across chunk boundaries doesn't get missed (see main.py's tts_node).
MAX_PATTERN_LEN = 32


def _period_marker(hour: int) -> str:
    for start, end, marker in _PERIOD_MARKERS:
        if start <= hour < end:
            return marker
    return "مساءً"  # unreachable given the ranges above cover 0-23, kept as a safe default


def _speak_date(date_str: str) -> str:
    year, month, day = (int(p) for p in date_str.translate(_ARABIC_DIGITS).split("-"))
    return f"{day} {_ARABIC_MONTHS.get(month, str(month))} {year}"


def _speak_time(time_str: str) -> str:
    # No leading "الساعة" here deliberately — text feeding this (e.g.
    # "الموعد الساعة 14:00") almost always already has that word right
    # before the raw time; adding it here doubled it ("الساعة الساعة").
    # _replace_datetime below adds it explicitly for the one case (a
    # combined date+time) where nothing already says it.
    hour, minute = (int(p) for p in time_str.translate(_ARABIC_DIGITS).split(":")[:2])
    marker = _period_marker(hour)
    hour_12 = hour % 12 or 12
    if minute:
        # Not grammatically perfect Arabic (دقيقة doesn't inflect for
        # count here) — acceptable for a safety net that should rarely
        # fire; SarjyAgent's own instructions remain the primary,
        # grammatically-correct path.
        return f"{hour_12} و{minute} دقيقة {marker}"
    return f"{hour_12} {marker}"


def _speak_currency(amount: str, code: str) -> str:
    # Decimal separator spelled out rather than left as "." or "," — the
    # same delimiter-structure problem the ISO patterns above exist for.
    digits = amount.translate(_ARABIC_DIGITS)
    whole, _, frac = re.match(r"(\d+)(?:([.,])(\d+))?$", digits).groups("")
    spoken = f"{whole} فاصلة {frac}" if frac else whole
    return f"{spoken} {_CURRENCY_WORDS[code]}"


def normalize_for_speech(text: str) -> str:
    def _replace_datetime(m: re.Match) -> str:
        spoken = _speak_date(m.group("date"))
        if m.group("time"):
            spoken += " الساعة " + _speak_time(m.group("time"))
        return spoken

    def _replace_currency(m: re.Match) -> str:
        if m.group("pre"):
            return _speak_currency(m.group("amount_after"), m.group("pre"))
        return _speak_currency(m.group("amount_before"), m.group("post"))

    text = _ISO_DATETIME.sub(_replace_datetime, text)
    text = _ISO_TIME_ONLY.sub(lambda m: _speak_time(m.group("time")), text)
    return _CURRENCY.sub(_replace_currency, text)
