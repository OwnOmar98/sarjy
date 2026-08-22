"""
Offline STT comparison — one wav in, every configured provider's answer out.

`eval/run.py` drives whole LiveKit rooms and reports latency percentiles. It
cannot answer any of the questions the STT work actually turns on, because a
live room mixes the transcription in with VAD, turn detection, the LLM and the
DB: which provider hears Arabic better, whether a non-speech clip comes back as
an affirmative, what Whisper's own confidence numbers look like on the clips
that hallucinate, or where the rejection thresholds should sit. This runs the
providers directly against fixture audio, with nothing else in the loop.

Three things it measures that nothing measured before:

1. **Provider comparison on identical audio.** Exact match, WER, and the
   detected language, per provider, per fixture. Vendor WER claims and one-off
   live impressions are what STT decisions have been made on so far.
2. **The false-affirmative rate on non-speech.** `silence.wav` and
   `room_tone.wav` played into each provider, scored with the same
   `affirmatives.looks_affirmative()` the agent's confirmation policy is built
   around. This is the number docs/KNOWN_ISSUES.md #1 asks for and calls a
   precondition for deciding whether an evidence gate is even possible.
3. **The hallucination-threshold distribution.** `--sweep` replays the recorded
   Whisper diagnostics (avg_logprob, compression_ratio, no_speech_prob — now
   available via agent/groq_verbose_stt.py) across candidate threshold pairs
   and prints what each would cost: hallucinations rejected vs. real speech
   thrown away. A threshold picked off that table is a measurement; the same
   threshold picked without it is the third guess the docs warn against.

Usage:
    python eval/stt_compare.py                      # every provider, every fixture
    python eval/stt_compare.py --providers groq,elevenlabs
    python eval/stt_compare.py --fixtures clean_ar,code_switched --runs 3
    python eval/stt_compare.py --sweep              # + threshold table (Groq only)
    python eval/stt_compare.py --json results.json  # raw records for later analysis

Costs real API calls against every provider it runs, so it is opt-in and not
part of the CI eval run.
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).parent.parent / "agent"
sys.path.insert(0, str(AGENT_DIR))
load_dotenv(AGENT_DIR / ".env")  # local dev only; CI sets real env vars directly

from affirmatives import looks_affirmative, normalize_arabic
from generate_fixtures import FIXTURES, NOISE_FIXTURES
from groq_verbose_stt import VerboseGroqSTT
from language_detect import detect_code_switch, normalize_language
from livekit import rtc
from livekit.agents.utils import http_context
from livekit.agents.utils.audio import audio_frames_from_file
from stt_adapter import PROVIDER_NAMES, build_provider, retune_for_language

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Candidate thresholds for --sweep. The first pair in each list is the
# reference-decoder default that agent/groq_verbose_stt.py ships with, so the
# table always shows what the current configuration costs alongside the
# alternatives.
_SWEEP_LOGPROB = [-1.0, -0.8, -0.6, -0.4]
_SWEEP_COMPRESSION = [2.4, 2.2, 2.0, 1.8]


@dataclass
class Result:
    provider: str
    fixture: str
    run: int
    text: str = ""
    language: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None
    # Whisper's per-segment diagnostics, Groq only. Empty elsewhere — no other
    # provider exposes them, which is itself worth seeing in the output.
    segments: list[dict] = field(default_factory=list)

    @property
    def is_speech_fixture(self) -> bool:
        return self.fixture in FIXTURES

    @property
    def reference(self) -> str:
        return FIXTURES.get(self.fixture, "")


def _normalize_for_match(text: str) -> list[str]:
    """Words, lowercased, Arabic spelling variants folded, punctuation dropped.

    Comparing raw strings would score every provider on its punctuation habits
    rather than on what it heard.
    """
    folded = normalize_arabic(text.lower())
    cleaned = "".join(char if char.isalnum() or char.isspace() else " " for char in folded)
    return cleaned.split()


def _wer(reference: str, hypothesis: str) -> float:
    """Word error rate — plain Levenshtein over word lists."""
    ref = _normalize_for_match(reference)
    hyp = _normalize_for_match(hypothesis)
    if not ref:
        # No reference to score against: any output at all is pure insertion,
        # which is the right reading for the non-speech fixtures.
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ref_word != hyp_word),  # substitution
                )
            )
        previous = current
    return previous[-1] / len(ref)


def _expected_language(fixture: str) -> str | None:
    """The language the fixture's own reference text is in, or None when it is
    genuinely bilingual (or has no reference at all)."""
    reference = FIXTURES.get(fixture)
    if not reference:
        return None
    meta = detect_code_switch(reference)
    return None if meta.mixed else meta.primary_language


async def _load_audio(path: Path) -> rtc.AudioFrame:
    frames = [frame async for frame in audio_frames_from_file(str(path))]
    return rtc.combine_audio_frames(frames)


async def _run_one(provider_name: str, stt, fixture: str, run: int) -> Result:
    result = Result(provider=provider_name, fixture=fixture, run=run)
    path = FIXTURES_DIR / f"{fixture}.wav"
    if not path.exists():
        result.error = "fixture missing (run eval/generate_fixtures.py)"
        return result

    buffer = await _load_audio(path)
    started = time.monotonic()
    try:
        event = await stt.recognize(buffer)
    except Exception as exc:  # a provider failing is a result, not a crash
        result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = (time.monotonic() - started) * 1000
        return result
    result.latency_ms = (time.monotonic() - started) * 1000

    if event.alternatives:
        best = event.alternatives[0]
        result.text = (best.text or "").strip()
        result.language = normalize_language(best.language) or (best.language or "")
        result.confidence = best.confidence
    return result


async def _groq_diagnostics(fixture: str) -> list[dict]:
    """Whisper's own per-segment numbers, before any filtering.

    Fetched with a second, unfiltered call rather than read off the filtered
    one: --sweep needs the segments the production filter would have *dropped*
    just as much as the ones it kept, and by design those never come back.
    """
    path = FIXTURES_DIR / f"{fixture}.wav"
    if not path.exists():
        return []
    stt = build_provider("groq")
    if not isinstance(stt, VerboseGroqSTT):
        return []
    buffer = await _load_audio(path)
    data = buffer.to_wav_bytes()
    try:
        resp = await stt._client.audio.transcriptions.create(
            file=("file.wav", data, "audio/wav"),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            temperature=0.0,
        )
    except Exception as exc:
        print(f"  (diagnostics unavailable for {fixture}: {exc})", file=sys.stderr)
        return []
    return [
        {
            "text": (getattr(seg, "text", "") or "").strip(),
            "avg_logprob": getattr(seg, "avg_logprob", None),
            "compression_ratio": getattr(seg, "compression_ratio", None),
            "no_speech_prob": getattr(seg, "no_speech_prob", None),
        }
        for seg in (getattr(resp, "segments", None) or [])
    ]


def _print_provider_table(provider: str, results: list[Result]) -> None:
    print(f"\n=== {provider} ===")
    header = f"{'fixture':<18} {'lang':<6} {'conf':>5} {'wer':>6} {'ms':>6}  transcript"
    print(header)
    print("-" * len(header))
    for result in sorted(results, key=lambda r: (r.fixture, r.run)):
        if result.error:
            print(f"{result.fixture:<18} {'ERROR':<6} {'':>5} {'':>6} {result.latency_ms:>6.0f}"
                  f"  {result.error}")
            continue
        expected = _expected_language(result.fixture)
        lang = result.language or "-"
        if expected and normalize_language(result.language) != expected:
            lang = f"{lang}!"  # disagrees with the fixture's own language
        wer = _wer(result.reference, result.text) if result.is_speech_fixture else float("nan")
        wer_cell = "  n/a" if math.isnan(wer) else f"{wer:>6.3f}"
        text = result.text if result.text else "(empty)"
        print(
            f"{result.fixture:<18} {lang:<6} {result.confidence:>5.2f} {wer_cell} "
            f"{result.latency_ms:>6.0f}  {text[:70]}"
        )


def _print_safety_summary(results: list[Result]) -> None:
    """The load-bearing number: what non-speech audio turns into.

    A non-empty transcript on silence or room tone is a hallucination. One that
    also reads as a yes is the docs' #1 failure with a booking behind it.
    """
    print("\n=== non-speech (docs/KNOWN_ISSUES.md #1) ===")
    print(f"{'provider':<14} {'clip':<12} {'runs':>5} {'non-empty':>10} {'affirmative':>12}")
    by_key: dict[tuple[str, str], list[Result]] = {}
    for result in results:
        if result.fixture in NOISE_FIXTURES and not result.error:
            by_key.setdefault((result.provider, result.fixture), []).append(result)
    if not by_key:
        print("  (no non-speech fixtures were run)")
        return
    for (provider, fixture), group in sorted(by_key.items()):
        non_empty = sum(1 for r in group if r.text)
        affirmative = sum(1 for r in group if looks_affirmative(r.text))
        print(
            f"{provider:<14} {fixture:<12} {len(group):>5} {non_empty:>10} {affirmative:>12}"
            + ("   <-- FALSE CONFIRM RISK" if affirmative else "")
        )


def _print_sweep(diagnostics: dict[str, list[dict]]) -> None:
    """What each candidate threshold pair would actually cost.

    Rejecting hallucinations is easy on its own (reject everything) and keeping
    real speech is easy on its own (reject nothing); the only interesting number
    is the pair. A row that rejects every non-speech segment while keeping every
    speech segment is a separating threshold and can be adopted. If no row does,
    then no threshold separates the two populations on this data, and that is a
    result too — the same one the duration-based gate should have had before it
    was built the first time.
    """
    speech_segments = [
        seg for name, segs in diagnostics.items() if name in FIXTURES for seg in segs
    ]
    noise_segments = [
        seg for name, segs in diagnostics.items() if name in NOISE_FIXTURES for seg in segs
    ]
    print("\n=== hallucination threshold sweep (Groq/Whisper) ===")
    if not speech_segments and not noise_segments:
        print("  (no segment diagnostics collected)")
        return
    print(
        f"  {len(speech_segments)} segment(s) from speech fixtures, "
        f"{len(noise_segments)} from non-speech fixtures"
    )
    for population, segments in (("speech", speech_segments), ("non-speech", noise_segments)):
        for field_name in ("avg_logprob", "compression_ratio", "no_speech_prob"):
            values = [s[field_name] for s in segments if s.get(field_name) is not None]
            if not values:
                continue
            print(
                f"  {population:<10} {field_name:<18} "
                f"min={min(values):>7.3f} median={statistics.median(values):>7.3f} "
                f"max={max(values):>7.3f}"
            )

    separators: list[tuple[float, float]] = []
    header = (
        f"\n  {'avg_logprob >=':<16}{'compression <=':<16}"
        f"{'speech kept':>13}{'noise rejected':>16}"
    )
    print(header)
    print("  " + "-" * (len(header) - 3))
    for logprob_min in _SWEEP_LOGPROB:
        for compression_max in _SWEEP_COMPRESSION:

            def keeps(seg, logprob_min=logprob_min, compression_max=compression_max) -> bool:
                logprob = seg.get("avg_logprob")
                compression = seg.get("compression_ratio")
                if logprob is not None and logprob < logprob_min:
                    return False
                return not (compression is not None and compression > compression_max)

            kept_speech = sum(1 for s in speech_segments if keeps(s))
            rejected_noise = sum(1 for s in noise_segments if not keeps(s))
            speech_pct = f"{kept_speech}/{len(speech_segments)}" if speech_segments else "-"
            noise_pct = f"{rejected_noise}/{len(noise_segments)}" if noise_segments else "-"
            separating = (
                speech_segments
                and noise_segments
                and kept_speech == len(speech_segments)
                and rejected_noise == len(noise_segments)
            )
            if separating:
                separators.append((logprob_min, compression_max))
            print(
                f"  {logprob_min:<16}{compression_max:<16}{speech_pct:>13}{noise_pct:>16}"
                + ("   <-- separates" if separating else "")
            )

    if not separators:
        print(
            "\n  No threshold pair separates the two populations on this data: every pair "
            "that\n  rejects a non-speech segment also discards real speech. That is a "
            "result, not a\n  gap — a confidence cut-off cannot catch a hallucination the "
            "model is confident about.\n  agent/groq_verbose_stt.py handles these by "
            "matching what the hallucination says\n  (prompt echo, known no-speech "
            "artefacts) rather than how sure it sounded."
        )


async def _run(args: argparse.Namespace) -> int:
    fixtures = (
        [f.strip() for f in args.fixtures.split(",") if f.strip()]
        if args.fixtures
        else [*FIXTURES, *NOISE_FIXTURES]
    )
    providers = (
        [p.strip() for p in args.providers.split(",") if p.strip()]
        if args.providers
        else list(PROVIDER_NAMES)
    )

    results: list[Result] = []
    async with http_context.open():
        for provider_name in providers:
            try:
                stt = build_provider(provider_name)
            except Exception as exc:
                print(f"skipping {provider_name}: {exc}", file=sys.stderr)
                continue
            if stt is None:
                print(f"skipping {provider_name}: no API key configured", file=sys.stderr)
                continue
            if args.prompt_language:
                # Pin the decoder prompt the way a settled conversation would
                # have pinned it, instead of leaving every clip on the
                # bilingual default. This is how the per-language prompt claim
                # gets tested rather than asserted: run the same Arabic clip
                # under --prompt-language en and then ar and compare.
                retune_for_language(stt, args.prompt_language)
            for fixture in fixtures:
                for run in range(args.runs):
                    print(f"  {provider_name}/{fixture} run {run + 1}", file=sys.stderr)
                    results.append(await _run_one(provider_name, stt, fixture, run))

        diagnostics: dict[str, list[dict]] = {}
        if args.sweep:
            print("collecting Whisper segment diagnostics...", file=sys.stderr)
            for fixture in fixtures:
                diagnostics[fixture] = await _groq_diagnostics(fixture)

    for provider_name in providers:
        provider_results = [r for r in results if r.provider == provider_name]
        if provider_results:
            _print_provider_table(provider_name, provider_results)

    _print_safety_summary(results)
    if args.sweep:
        _print_sweep(diagnostics)

    if args.json:
        payload = {
            "results": [
                {
                    **{k: v for k, v in vars(result).items() if k != "segments"},
                    "wer": _wer(result.reference, result.text)
                    if result.is_speech_fixture and not result.error
                    else None,
                    "reference": result.reference,
                    "looks_affirmative": looks_affirmative(result.text),
                }
                for result in results
            ],
            "diagnostics": diagnostics,
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json}", file=sys.stderr)

    # A false affirmative on non-speech is the one outcome with no acceptable
    # non-zero value, so it decides the exit code — this can gate CI later
    # without changing anything here.
    false_confirms = [
        r for r in results if r.fixture in NOISE_FIXTURES and looks_affirmative(r.text)
    ]
    return 1 if false_confirms else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--providers", help=f"comma-separated; default all of {PROVIDER_NAMES}")
    parser.add_argument("--fixtures", help="comma-separated fixture names; default all")
    parser.add_argument("--runs", type=int, default=1, help="repeats per fixture (default 1)")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="collect Whisper segment diagnostics and print the threshold table",
    )
    parser.add_argument(
        "--prompt-language",
        choices=("ar", "en"),
        help="pin the STT prompt to one language before running, as a settled "
        "conversation would (default: the bilingual prompt)",
    )
    parser.add_argument("--json", help="write the raw records to this path")
    sys.exit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
