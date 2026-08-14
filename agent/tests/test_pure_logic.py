"""Unit tests for the pure (no-network, no-DB) logic touched this session.

Scoped deliberately: every function here is deterministic string/dict work,
so these are real assertions rather than smoke tests. The parts that need a
live LiveKit room, a real STT provider, or Postgres are covered by
eval/run.py instead — see docs/KNOWN_ISSUES.md for what is still unverified.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from arabic_normalize import normalize_for_speech
from language_detect import detect_code_switch
from main import _detect_language, _keyterms_from_facts
from tools import _expire_stale


class TestGreetingLanguageSelection:
    """The bug fixed this session: the greeting picked its language with
    _detect_language(), which is Arabic-first, so a single Arabic token in an
    otherwise-English fact set greeted an English speaker in Arabic."""

    def test_arabic_first_detector_still_flags_presence(self):
        # Unchanged behaviour, kept for the turn_traces presence flag.
        assert _detect_language("my colleague is سارة") == "ar"

    def test_token_majority_picks_the_dominant_language(self):
        mostly_english = "name is Omar, works at Acme, colleague is سارة"
        assert detect_code_switch(mostly_english).primary_language == "en"

    def test_token_majority_still_picks_arabic_when_arabic_dominates(self):
        mostly_arabic = "الاسم عمر ويعمل في شركة Acme"
        assert detect_code_switch(mostly_arabic).primary_language == "ar"

    def test_the_two_detectors_disagree_on_exactly_the_bug_case(self):
        # If this ever stops disagreeing, the fix has become a no-op and the
        # greeting could quietly go back to using either one.
        facts = "favorite color is blue, dog is named Rocky, one meeting with سارة"
        assert _detect_language(facts) == "ar"
        assert detect_code_switch(facts).primary_language == "en"


class TestKeytermsFromFacts:
    def test_extracts_proper_nouns_not_common_words(self):
        terms = _keyterms_from_facts(["name is Omar", "has a meeting with Acme every Sunday"])
        assert "Omar" in terms
        assert "Acme" in terms
        assert "meeting" not in terms
        assert "has" not in terms

    def test_extracts_arabic_tokens(self):
        assert "سارة" in _keyterms_from_facts(["زميلته سارة"])

    def test_strips_punctuation_and_dedupes(self):
        terms = _keyterms_from_facts(["works with Omar.", "Omar, again"])
        assert terms.count("Omar") == 1

    def test_skips_short_tokens(self):
        assert _keyterms_from_facts(["is at HQ"]) == []

    def test_caps_the_list(self):
        # Alphabetic on purpose — the extractor requires isalpha(), so a
        # name with a digit in it is deliberately not a keyterm.
        facts = [f"Colleague{chr(ord('A') + i % 26)}{'y' * (i // 26)}" for i in range(50)]
        assert len(_keyterms_from_facts(facts)) == 20

    def test_alphanumeric_tokens_are_not_keyterms(self):
        assert _keyterms_from_facts(["room is B12"]) == []

    def test_empty_input_is_empty_output(self):
        assert _keyterms_from_facts([]) == []


class TestStaleEntryExpiry:
    """_pending / _last_action are module-level and keyed by user_id, in a
    long-lived worker process. Both were already TTL-checked on read, so this
    only guards against unbounded growth — the behaviour must not change."""

    def test_expires_only_entries_past_the_ttl(self):
        now = time.monotonic()
        store = {
            "fresh": SimpleNamespace(created_at=now),
            "stale": SimpleNamespace(created_at=now - 600),
        }
        _expire_stale(store, 300, "created_at")
        assert set(store) == {"fresh"}

    def test_keeps_an_entry_exactly_at_the_boundary(self):
        # Strictly-greater-than, matching the TTL checks in the tools
        # themselves — an entry at exactly the TTL is still live there, so it
        # must not be swept out from under them here.
        store = {"edge": SimpleNamespace(created_at=time.monotonic() - 300)}
        _expire_stale(store, 300.5, "created_at")
        assert "edge" in store

    def test_reads_the_named_stamp_attribute(self):
        now = time.monotonic()
        store = {"a": SimpleNamespace(completed_at=now - 1000)}
        _expire_stale(store, 900, "completed_at")
        assert store == {}

    def test_empty_store_is_a_noop(self):
        store = {}
        _expire_stale(store, 300, "created_at")
        assert store == {}

    def test_does_not_mutate_while_iterating(self):
        # The implementation must materialise the key list first; a plain
        # `for k in store` with a del inside raises RuntimeError.
        now = time.monotonic()
        store = {f"u{i}": SimpleNamespace(created_at=now - 600) for i in range(10)}
        _expire_stale(store, 300, "created_at")
        assert store == {}


class TestCurrencyNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SAR 250", "250 ريال"),
            ("250 SAR", "250 ريال"),
            ("$99", "99 دولار"),
            ("99 USD", "99 دولار"),
            ("AED 40", "40 درهم"),
            ("EUR 12", "12 يورو"),
        ],
    )
    def test_amounts_become_spoken_words(self, raw, expected):
        assert normalize_for_speech(raw) == expected

    def test_decimal_separator_is_spelled_out(self):
        assert normalize_for_speech("SAR 12.50") == "12 فاصلة 50 ريال"

    def test_arabic_indic_digits(self):
        assert normalize_for_speech("٢٥٠ SAR") == "250 ريال"

    def test_inside_a_sentence(self):
        assert normalize_for_speech("السعر SAR 250 فقط") == "السعر 250 ريال فقط"

    def test_does_not_touch_a_bare_number(self):
        assert normalize_for_speech("30 دقيقة") == "30 دقيقة"

    def test_does_not_touch_a_word_ending_in_a_currency_code(self):
        assert normalize_for_speech("BAZAAR 250") == "BAZAAR 250"


class TestExistingNormalizationStillWorks:
    """Regression guard: the currency pass runs after the ISO passes, so it
    must not re-match digits the date/time rules just produced."""

    def test_iso_date_unchanged_by_the_currency_pass(self):
        assert normalize_for_speech("2026-08-20") == "20 أغسطس 2026"

    def test_iso_time_unchanged_by_the_currency_pass(self):
        assert normalize_for_speech("14:00") == "2 ظهرًا"

    def test_combined_datetime(self):
        out = normalize_for_speech("2026-08-20T14:00")
        assert out == "20 أغسطس 2026 الساعة 2 ظهرًا"

    def test_plain_text_passes_through(self):
        assert normalize_for_speech("مرحبا") == "مرحبا"
