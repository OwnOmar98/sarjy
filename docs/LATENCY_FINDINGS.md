# Sarjy — Latency Findings

**Author:** Own Abu Hamour
**Date:** 13 Aug 2026
**Status:** Measured, against the deployed system
**Related:** `docs/PRD.md` §4 sets the pre-build targets this report measures against

---

## 1. Summary

`docs/PRD.md`'s latency section was explicit that its numbers were a _pre-build target, not a measurement_, and committed to reporting the real ones once the system existed, disagreements included. This is that report.

The headline: **real end-to-end latency (total, p50) is ~2.8s — roughly 3x the ≤900ms EN target set before building.** Every stage except TTS came in over its individual target. This is measured on the deployed system (LiveKit Cloud + Neon + Upstash), through the actual production code path, not a local or idealized benchmark — the gap is real, not an artifact of test conditions.

| Stage            | Measured p50 | Measured p95 | Pre-build target  | Result                    |
| ---------------- | -----------: | -----------: | ----------------- | ------------------------- |
| Endpointing      |      1117 ms |      1401 ms | 300–500 ms        | Over — see §3.1           |
| STT finalisation |      1113 ms |      1396 ms | 150–300 ms        | Over — see §3.2           |
| Memory retrieval |       450 ms |       546 ms | < 20 ms           | Over — expected, see §3.3 |
| LLM first token  |       800 ms |      1368 ms | 150–250 ms        | Over — see §3.4           |
| TTS first byte   |       198 ms |       257 ms | 150–400 ms        | **Met**                   |
| **Total**        |  **2804 ms** |  **3990 ms** | **≤ 900 ms (EN)** | **Over, ~3x**             |

n = 4 real user turns (one per eval fixture: clean English, clean Arabic, code-switched, tool-triggering), each driven through the actual `entrypoint()`/`SarjyAgent` in a real LiveKit room by the CI eval harness (`eval/run.py`). LLM first token and TTS first byte are n = 8 — those two stages also fire on each fixture's initial greeting, which has no endpointing/STT/memory stage of its own. Small sample either way — enough to establish direction and rough magnitude, not enough to treat any single percentile as precise.

## 2. Methodology

The pre-build targets in `docs/PRD.md` §4 were derived from providers' _published, isolated_ inference numbers — Groq's raw Whisper/LLM throughput, ElevenLabs' stated TTFB — none of which include real transport, a real turn-detector inference call, or a real memory round-trip. The numbers here are the opposite: measured on the deployed system, through the full real pipeline, with nothing mocked.

`eval/run.py` drives 4 fixtures — synthesized via the same GeminiTTS plugin the agent uses, so they sound like real speech, not a recording — through a real LiveKit room, using `fake_job_context` to run the actual production `entrypoint()` rather than a re-implementation of it. Every stage timing comes from `agent/latency.py`, which reads `ChatMessage.metrics` (the SDK's own per-turn measurements) rather than a hand-rolled stopwatch — the same values the live on-screen HUD shows during a real session.

Two real bugs had to be found and fixed before this produced any data at all, both the SDK behaving correctly but not obviously: LiveKit Cloud auto-dispatches the deployed production agent into any new room by default, which meant every test room briefly had two agent sessions competing for the same audio; and the harness's fixture track needed an explicit microphone-source tag, or the SDK's own input pipeline silently dropped it before it ever reached the agent's speech recognition.

## 3. Per-stage analysis

### 3.1 Endpointing — 1117/1401ms vs. 300–500ms target

The multilingual turn detector (`inference.TurnDetector()`) is confirmed live and correctly calibrated, not just configured: agent logs show it resolving to the cloud `turn-detector-v1` model with real, server-set per-language confidence thresholds (Arabic 0.355, English 0.56, plus 12 more languages) rather than the local fallback model. That confirmation is real; the latency number is still over target. The pre-build target assumed the turn detector's own published latency in isolation. The measured number includes the model actually deciding a turn is complete against real, natural speech pauses in the fixture audio — not just silence-detection, a real inference call per turn. `docs/PRD.md` §4 already flagged Arabic as the likely miss ("endpointing models are trained overwhelmingly on English... code-switching can look like an endpoint"); the data doesn't yet isolate Arabic from English specifically (§5), but the miss is real and broader than just Arabic — English endpointing missed by a similar margin.

### 3.2 STT finalisation — 1113/1396ms vs. 150–300ms target

Groq's published number is raw model inference time on an already-received audio buffer. The measured number includes the actual network round-trip to Groq's API from the deployed `us-east` agent, which §4's region findings below put at ~117–139ms TTFB alone — real, but not enough on its own to explain a gap this size. The larger factor is almost certainly that STT here is a one-shot `recognize()` call per completed utterance (both Groq's and OpenAI's Whisper-style APIs, wrapped via `stt.StreamAdapter`), not genuinely streaming, so the full utterance duration is part of what's being measured, not just processing time after it ends.

### 3.3 Memory retrieval — 450/546ms vs. <20ms target

The <20ms target assumed a warm Redis cache in front of pgvector (`agent/memory.py` does have this cache — keyed by user, fact-kind, and query hash). Every eval-harness turn is a fresh room with a fresh query, so this number reflects the **cold-cache path**: a real Gemini embedding API call plus a real pgvector similarity search, not the cached path a returning user asking a repeated question would actually hit. This is the correct worst case to report, and the one that matters most for a first-time query in a session — but it means the number here shouldn't be read as "the cache doesn't work," only that this particular measurement never exercises it.

### 3.4 LLM first token — 800/1368ms vs. 150–250ms target

The target assumed prompt caching on the system prompt and memory block. `docs/PRD.md` §4's caching plan lists this as caching layer 3; it isn't implemented — every turn currently resends the full system prompt, tool schemas (4 registered tools), and injected memory facts from scratch. This is the most directly actionable gap of the four: implementing prompt caching is the single highest-leverage next step for this pillar.

### 3.5 TTS first byte — 198/257ms, met the 150–400ms target

The one stage that landed inside its target. Two things are working as designed here: Gemini TTS (the default provider) performing close to its published figures, and the phrase cache (`agent/tts_cache.py`) — verified via isolated tests (passthrough, cache-miss-then-store, cache-hit, and a multi-chunk false-alarm case) to skip real synthesis entirely on a hit for the codebase's genuinely fixed strings. The eval fixtures mostly produce novel LLM-generated replies rather than a cached fixed phrase, so this number is the **uncached** case — the actual cache-hit path is faster still (near-0ms), just not what these 4 runs happen to exercise.

## 4. Region findings

`docs/PRD.md` §4 flagged an open question: KSA users, but no public confirmation of whether a standard developer API key for Groq or Gemini routes through their Saudi infrastructure (Groq's Dammam facility, Google's `me-central2`) or generic US infrastructure. Measured directly from the deployed `us-east` agent, 3 independent runs, TTFB against each provider plus a same-region AWS reference:

| Endpoint                             |        TTFB |
| ------------------------------------ | ----------: |
| Gemini                               |   ~54–57 ms |
| Groq                                 | ~117–139 ms |
| US-only reference (AWS, same region) |   ~32–39 ms |

Gemini tracks close to the US baseline (+~20ms) — consistent with the consumer API key not routing through Google's KSA reseller product. Groq is measurably further out, ~3–4x the US baseline — real evidence the network path differs from a same-region call, though this alone can't fully separate geographic routing from Groq's own gateway overhead. Net: no region move is justified by this data: the agent is correctly placed close to the transport layer (where RTT compounds on every packet), and neither provider showed evidence of a materially closer serving location worth chasing.

## 5. Honest limitations

- **n = 4.** Enough to establish direction and rough magnitude, not enough to treat any single percentile as a precise, stable number — especially p95 off 4 samples.
- **No per-language breakdown yet.** `turn_traces.language` was landing as `"unknown"` on every row for most of this data-collection period — a real bug (the STT provider never reported a detected language back through the plugin, fixed same day with a text-based fallback) rather than a design gap, but it means this report can't yet honestly split Arabic vs. English latency, which was the original ask.
- **Endpointing and STT numbers reflect the eval fixtures' specific speech patterns** (synthesized TTS audio with natural pauses), not necessarily every real conversational pattern a live microphone would produce.

## 6. Recommendation

The largest, most actionable gap is LLM first token — prompt caching (system prompt + memory block, already scoped in `docs/PRD.md` §4 as caching layer 3, not yet built) is the highest-leverage next step, and directly addressable without new architecture. Endpointing and STT gaps are more structural (real network + real turn-detector inference vs. isolated published numbers) and worth tracking via the CI eval harness over time rather than chasing in one pass. TTS and region placement are both already performing as designed — no action needed there.
