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

from affirmatives import confirmation_policy, looks_affirmative, looks_negative
from arabic_normalize import normalize_for_speech
from groq_verbose_stt import filter_hallucinated_segments
from language_detect import (
    LanguageTracker,
    describe_for_llm,
    detect_code_switch,
    language_directive,
    normalize_language,
)
from main import _detect_language, _keyterms_from_facts
from memory import _parse_facts_response
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


class TestParseFactsResponse:
    """extract_facts()'s raw model output -> (add, remove), including the
    add/remove object shape a correction needs (memory.py's own docstring:
    a restated fact must not leave the old one behind for retrieve() to
    keep surfacing alongside the new one)."""

    def test_add_only(self):
        add, remove = _parse_facts_response('{"add": ["favorite color is blue"], "remove": []}')
        assert add == ["favorite color is blue"]
        assert remove == []

    def test_add_and_remove_a_correction(self):
        raw = '{"add": ["favorite color is green"], "remove": ["favorite color is blue"]}'
        add, remove = _parse_facts_response(raw)
        assert add == ["favorite color is green"]
        assert remove == ["favorite color is blue"]

    def test_both_empty_for_nothing_durable(self):
        assert _parse_facts_response('{"add": [], "remove": []}') == ([], [])

    def test_none_response_defaults_to_both_empty(self):
        assert _parse_facts_response(None) == ([], [])

    def test_malformed_json_discards_rather_than_raising(self):
        assert _parse_facts_response("not json at all") == ([], [])

    def test_prose_wrapped_around_the_object_is_stripped(self):
        raw = 'Sure, here it is:\n{"add": ["name is Omar"], "remove": []}\nHope that helps!'
        assert _parse_facts_response(raw) == (["name is Omar"], [])

    def test_non_string_entries_are_dropped(self):
        add, remove = _parse_facts_response('{"add": ["ok", 5, null], "remove": [true]}')
        assert add == ["ok"]
        assert remove == []

    def test_blank_entries_are_dropped(self):
        add, _ = _parse_facts_response('{"add": ["  ", "real fact"], "remove": []}')
        assert add == ["real fact"]


class TestCodeSwitchThreshold:
    """One stray token used to flip a turn to "mixed", which produced an explicit
    instruction to reply code-switched — the most common cause of an Arabic word
    landing in an otherwise-English reply."""

    def test_single_arabic_filler_in_an_english_sentence_is_not_mixed(self):
        meta = detect_code_switch("I will be there يعني around five")
        assert meta.primary_language == "en"
        assert meta.mixed is False

    def test_single_english_loanword_in_an_arabic_sentence_is_not_mixed(self):
        meta = detect_code_switch("احجز لي meeting بكرا الساعة خمسة")
        assert meta.primary_language == "ar"
        assert meta.mixed is False

    def test_genuine_code_switching_still_counts_as_mixed(self):
        meta = detect_code_switch("I need حجز اجتماع tomorrow at خمسة")
        assert meta.mixed is True
        assert set(meta.languages) == {"ar", "en"}

    def test_presence_is_still_reported_even_when_not_mixed(self):
        # `languages` stays honest about what's in the text; only `mixed` is
        # thresholded, and only `mixed` drives the reply-language instruction.
        meta = detect_code_switch("I will be there يعني around five")
        assert set(meta.languages) == {"ar", "en"}


class TestReportedLanguageWins:
    """The reproduced failure: Groq returns tool_trigger.wav — a pure English
    sentence — as Arabic script, 2/2 runs, while reporting language "en".
    Script counting alone concludes "Arabic" and instructs the model to reply in
    Arabic, which is exactly the user-visible bug."""

    GROQ_OUTPUT = (
        "ما هو مغرب في رياد اليوم؟"  # spoken: "What time is Maghrib prayer in Riyadh today?"
    )

    def test_script_counting_alone_gets_it_backwards(self):
        assert detect_code_switch(self.GROQ_OUTPUT).primary_language == "ar"

    def test_reported_language_overrides_the_script(self):
        meta = detect_code_switch(self.GROQ_OUTPUT, reported_language="en")
        assert meta.primary_language == "en"
        assert meta.script_language == "ar"
        assert meta.transcript_disagrees is True

    def test_disagreement_is_not_treated_as_code_switching(self):
        meta = detect_code_switch("hello مرحبا كيف حالك", reported_language="en")
        assert meta.mixed is False

    def test_directive_commands_the_reported_language(self):
        meta = detect_code_switch(self.GROQ_OUTPUT, reported_language="en")
        assert "in English only" in language_directive(meta)

    def test_tag_explains_the_disagreement_rather_than_just_asserting_it(self):
        meta = detect_code_switch(self.GROQ_OUTPUT, reported_language="en")
        assert "mis-transcription" in describe_for_llm(meta)

    def test_an_unsupported_reported_language_falls_back_to_the_script(self):
        # Scribe reports "vi" for the one-word Arabic yes. A third language is
        # not a signal this system can act on.
        meta = detect_code_switch("نعم", reported_language="vi")
        assert meta.primary_language == "ar"
        assert meta.transcript_disagrees is False

    def test_language_code_shapes_all_fold_to_the_same_answer(self):
        for code in ("ar", "ar-SA", "AR_sa", "arabic", "Arabic"):
            assert normalize_language(code) == "ar"
        for code in ("en", "en-US", "english", "English"):
            assert normalize_language(code) == "en"
        for code in ("", None, "vi", "fr-FR"):
            assert normalize_language(code) is None


class TestLanguageTracker:
    """Retuning the decoder off a single turn is a feedback loop: the signal
    being measured is downstream of the thing being tuned."""

    def test_one_off_turn_cannot_flip_a_settled_conversation(self):
        tracker = LanguageTracker()
        tracker.seed("en")
        tracker.observe(detect_code_switch("نعم"))
        assert tracker.estimate() == "en"

    def test_two_agreeing_turns_do_move_it(self):
        tracker = LanguageTracker()
        tracker.seed("en")
        tracker.observe(detect_code_switch("مرحبا كيف حالك"))
        tracker.observe(detect_code_switch("احجز اجتماع بكرا"))
        assert tracker.estimate() == "ar"

    def test_mixed_turns_do_not_vote(self):
        tracker = LanguageTracker()
        tracker.seed("en")
        for _ in range(3):
            tracker.observe(detect_code_switch("I need حجز اجتماع tomorrow at خمسة"))
        assert tracker.estimate() == "en"

    def test_unseeded_tracker_has_no_opinion(self):
        assert LanguageTracker().estimate() is None

    def test_reported_language_is_consumed_once(self):
        tracker = LanguageTracker()
        tracker.note_reported("ar-SA")
        assert tracker.take_reported() == "ar"
        assert tracker.take_reported() is None


class TestAffirmatives:
    """The STT prompt no longer carries "نعم", so the mishearings it was there to
    prevent have to be recognised at the intent layer instead."""

    def test_the_measured_mishearings_count_as_yes(self):
        # "نام" from Groq with the word removed from the prompt; "NOM"/"Nam"
        # are what Groq and Scribe actually return for short_yes_ar.wav.
        for heard in ("نام", "NOM", "Nam", "نعام"):
            assert looks_affirmative(heard), heard

    def test_plain_affirmatives_in_both_languages(self):
        for heard in ("yes", "Yes.", "go ahead", "نعم", "تمام", "أيوة"):
            assert looks_affirmative(heard), heard

    def test_arabic_spelling_variants_fold_together(self):
        assert looks_affirmative("أيوة") == looks_affirmative("ايوه")

    def test_a_refusal_is_never_an_affirmative(self):
        for heard in ("no", "No, don't book it.", "لا، لا تحجزه", "cancel that"):
            assert not looks_affirmative(heard), heard

    def test_a_flipped_refusal_containing_yes_is_not_a_confirmation(self):
        # docs/KNOWN_ISSUES.md #2: "no cancel it please" came back as
        # "نعم، نعم، بكثير من الوصف". A transcript holding both readings is
        # not evidence of consent.
        assert not looks_affirmative("نعم، نعم، لا تحجزه")

    def test_empty_is_neither(self):
        assert not looks_affirmative("")
        assert not looks_negative("")

    def test_the_policy_names_the_mishearings_it_relies_on(self):
        policy = confirmation_policy()
        assert "نام" in policy
        assert "نعم" in policy


class TestHallucinationFilter:
    """Measured via eval/stt_compare.py: with a prompt set, non-speech audio
    comes back as the prompt itself; with none, as Whisper's stock caption."""

    @staticmethod
    def _segment(text, *, avg_logprob=-0.3, compression_ratio=0.6, no_speech_prob=0.0):
        return SimpleNamespace(
            text=text,
            avg_logprob=avg_logprob,
            compression_ratio=compression_ratio,
            no_speech_prob=no_speech_prob,
        )

    def test_prompt_echo_is_dropped(self):
        prompt = "Hi, I'm Sarjy, your voice assistant."
        text, dropped = filter_hallucinated_segments([self._segment("I'm Sarjy.")], prompt)
        assert text == ""
        assert "prompt echo" in dropped[0]

    def test_arabic_prompt_echo_is_dropped(self):
        prompt = "أنا سرجي، مساعدك الصوتي."
        text, _ = filter_hallucinated_segments([self._segment("أنا سرجي، مساعدك الصوتي.")], prompt)
        assert text == ""

    def test_a_single_word_from_the_prompt_is_kept(self):
        # "Hi" is in the prompt and is also an ordinary thing to say.
        text, _ = filter_hallucinated_segments(
            [self._segment("Hi")], "Hi, I'm Sarjy, your voice assistant."
        )
        assert text == "Hi"

    def test_real_speech_is_untouched(self):
        prompt = "Hi, I'm Sarjy, your voice assistant."
        sentence = "Book a meeting called Team Sync tomorrow at ten."
        text, dropped = filter_hallucinated_segments([self._segment(sentence)], prompt)
        assert text == sentence
        assert dropped == []

    def test_stock_no_speech_caption_is_dropped(self):
        text, dropped = filter_hallucinated_segments([self._segment("Thank you.")], None)
        assert text == ""
        assert "no-speech artefact" in dropped[0]

    def test_thank_you_inside_a_real_sentence_survives(self):
        sentence = "Thank you, book it for tomorrow."
        text, _ = filter_hallucinated_segments([self._segment(sentence)], None)
        assert text == sentence

    def test_a_repetition_loop_is_dropped(self):
        loop = "نعم، نعم، نعم، نعم، نعم، نعم، نعم، نعم"
        text, dropped = filter_hallucinated_segments(
            [self._segment(loop, compression_ratio=3.1)], None
        )
        assert text == ""
        assert "compression_ratio" in dropped[0]

    def test_a_low_confidence_real_confirmation_is_not_dropped(self):
        # The measured regression: "نعم" decodes at avg_logprob -1.004, which
        # the reference-decoder default of -1.0 would have thrown away while
        # rejecting neither non-speech clip. The threshold is off by default.
        text, dropped = filter_hallucinated_segments(
            [self._segment("NOM", avg_logprob=-1.004)], None
        )
        assert text == "NOM"
        assert dropped == []
