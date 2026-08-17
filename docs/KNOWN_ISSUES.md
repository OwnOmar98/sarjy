# Sarjy — Known Issues

Open problems only. Fixed items and the full history live in `docs/HANDOFF.md`;
root-cause analysis for the STT-side items is in `docs/ISSUE_ANALYSIS.md`.

Each entry separates **what was observed** from **what is believed** — several
issues here have been mis-diagnosed at least once, and a diagnosis recorded as a
fact is how that happens.

---

## Safety-relevant

### 1. Speech-to-text can hallucinate an affirmative onto silence or background noise

**Observed:** faint noise reproducibly transcribed as the literal word "نعم" (3/3 runs on
one clip). Dangerous because "نعم" is what the booking/cancellation flow listens for.

**Believed:** caused by "نعم" sitting in Groq's `_STT_PROMPT`, where Whisper treats it as
decoder context and can emit it for non-speech audio.

**Not fixable upstream, and this is now well-established rather than assumed** — four
separate mitigations were built or tested and all failed:

| Attempt                              | Outcome                                             |
| ------------------------------------ | --------------------------------------------------- |
| Remove "نعم" from `_STT_PROMPT`      | Regresses real "نعم" → "نام", 100% reproducibly     |
| Filter on Whisper's `no_speech_prob` | Reads 0.0 on the exact clips that hallucinated      |
| Raise VAD `min_speech_duration`      | Silences genuine short words too                    |
| Duration-based caution heuristic     | Built, then removed — no validated signal behind it |

**Mitigated, not fixed (this session):** the _consequence_ is now recoverable rather than
permanent. `confirm_pending_action` instructs the agent to speak back exactly what was
written, and a new `undo_last_action` tool reverses the last booking or cancellation for 15
minutes, by row id. A wrong write is now one utterance away from being undone instead of
silent and permanent.

**Deliberately not attempted:** a code-level evidence gate on `confirm_pending_action`
(refuse the write when the confirming turn had near-zero VAD speech duration). This is the
obvious remaining idea, and it is the same idea as the duration-based guard that was already
built and then removed — the threshold was calibrated against a single real measurement,
carried real false-positive risk on genuine short confirmations, and had no validated signal
behind it once `no_speech_prob` turned out to read 0.0 on hallucinated clips. Since
`SpeechData.confidence` is always 0.0 on this stack (see #7), duration is the _only_
candidate signal, which is why it needs a measured distribution before a third attempt, not
another guess. The order is: get a false-accept rate from `--safety`, get the duration
distribution for genuine confirmations, then decide whether a separating threshold exists.

**What changed to make that possible:** nothing recorded per-turn speech duration on the
success path, which is why the earlier attempt had to threshold a proxy it had measured
once. `main.py` now logs a `speech_duration_proxy` stage to `turn_traces` on every turn, and
`--safety` prints the per-scenario range, so the `false_confirm_*` and `short_utterance_*`
distributions can be compared directly. This is **recording only** — nothing reads it to
make a decision. If the two ranges overlap, no threshold separates them and the gate should
not be built at all; that is the decision this data exists to inform.

**Would confirm a real fix:** `eval/run.py --safety`, scenarios `false_confirm_silence` and
`false_confirm_room_tone`, at a zero write rate over repeated runs.

### 2. A real spoken "no" can be mis-transcribed as "yes"

**Observed:** "no cancel it please" transcribed as `نعم، نعم، بكثير من الوصف.` — a genuine
refusal flipped to the literal word for yes, twice, in the 62-utterance benchmark.

**Believed:** a distinct mechanism from #1 — a real mistranscription under code-switched or
accented speech, not a hallucination onto non-speech. Same dangerous outcome.

**Status:** same mitigation as #1 (read-back + undo), same lack of an upstream fix. This one
is more likely to improve as a side effect of #3 than through any confirmation-side change,
since its root is code-switch transcription quality.

**Would confirm a real fix:** `eval/run.py --safety`, scenarios `spoken_no_ar` and
`spoken_no_en`.

### 3. Accuracy drops sharply on mid-sentence Arabic/English code-switching

**Observed, measured:** 62-utterance benchmark — pure Arabic 100% exact match (WER 0.000),
pure English 60% (WER 0.229), clause-boundary switching 20% (WER 0.402), single-word
interleaved switching with no pause **0% exact match, WER 0.564**.

**Believed, and now supported by reading the library source rather than inferred:** this is
architectural, not tunable. Whisper predicts one language per segment and decodes the whole
segment in it, and `stt.StreamAdapter` fixes the segment boundary by VAD _before_ STT sees
the audio — so a mid-sentence switch is inside a single segment by construction. Prompt and
keyword changes cannot reach it; both have been tried.

**Path forward, not yet taken:** Speechmatics ships a purpose-built Arabic–English bilingual
model (vendor-published 6.3% WER on mixed speech, Gulf/Egyptian/Levantine, real-time
streaming) with a first-party `livekit-plugins-speechmatics`. Swapping it in behind the
existing `STT_PROVIDER` switch would also retire the empty-language limitation (#7), remove
the `StreamAdapter` layer implicated in #4, and make ASR confidence available for the first
time. Those are vendor figures — the WER fixtures in `eval/` are what would verify them.

**Blocked on:** a Speechmatics account/key. No code change is blocked.

### 4. Some short utterances get no response at all

**Observed:** bare single-word clips ("نعم", "لا") that transcribe correctly through a
direct Groq API call, and that trigger Silero VAD cleanly in isolation, produced **zero
events of any kind** through the live pipeline — no `user_state_changed`, no
`user_input_transcribed`, nothing. Longer clips in the same run worked.

**Believed, narrowed this session by reading `livekit-agents` source:** two hypotheses that
were previously suspected are now ruled out.

- An empty Whisper result cannot cause this: `stt.StreamAdapter` drops empty transcripts
  before they reach the recognition layer, and the SDK's `transcription_timeout` is armed on
  _VAD_ end-of-speech and disarmed only by a non-empty final — so an empty result leaves the
  net armed and the missed-speech reply does fire.
- Greeting overlap cannot cause this either: during AEC warmup the SDK substitutes silence
  **only on the STT path**; VAD keeps receiving the real frame.

That leaves causes where **VAD itself never reports speech**. Prime suspects, untested:
Krisp BVC noise cancellation (which sits upstream of VAD and is the main difference between
"VAD in isolation" and "VAD in the pipeline"), or the sub-second clips failing some
full-pipeline threshold.

**Partially mitigated (this session):** a `user_state_changed`-driven watchdog in `main.py`
covers one hole the SDK's own net genuinely has — `_arm_transcription_timeout` returns early
while `_turn_transcript_received` is still set from the previous turn, so a second utterance
arriving inside that window had no net at all. This does **not** help if VAD never fires,
which is the more likely cause of the observed anomaly.

**Would confirm a real fix:** `eval/run.py --safety`, scenarios `short_utterance_ar` /
`short_utterance_en`. Before that, one live session with debug logging answers which of the
three stages (VAD / recognize / transcript) is missing — see `docs/ISSUE_ANALYSIS.md` §1.

---

## Accepted limitations

### 5. An English reply can open with a stray Arabic filler word

Tested directly against the LLM with clean text: 4/5 cases improved under the current
three-mode language policy, but a purely English input still occasionally opens with "أكيد"
before switching to English. Believed to be the model's own filler habit rather than a
wording flaw; tightening further risks reintroducing the rigid word-by-word mirroring the
policy exists to prevent. Left as-is deliberately.

### 6. If a user starts speaking as the greeting begins, only part may be captured

A partial transcript counts as "caught", so the missed-speech net doesn't fire and the LLM
just asks for clarification. Not silent, not smooth. Inherent to `aec_warmup_duration`
echo protection.

### 7. The STT provider doesn't report which language it detected

**Verified from source this session**, not assumed: `livekit-plugins-openai` requests the
`verbose_json` format that carries a detected language only for the literal model name
`"whisper-1"`, and `_transcript_language` returns a code only when exactly one language is
configured. Both configured providers therefore always report an empty language, and
`SpeechData.confidence` is likewise always 0.0. A script-based fallback covers the
`turn_traces` tagging; nothing else depends on it. Fixable only by forking the plugin or
changing provider (#3).

---

## Fixed but not verified live

Everything in this section is implemented, lint-clean, and unit-tested where the logic is
pure — but **no part of this session's work has been through a real browser session or a
live LiveKit room**, because no credentials were available. Treat as unproven.

- **`undo_last_action` and the write read-back** (#1/#2 mitigation). Unit-tested logic is
  pure-Python only; the DB paths and the LLM actually calling the tool are unverified.
- **Memory → STT vocabulary hints** — now wired through the supported
  `stt_context_options=STTContextOptions(keyterms=...)` API, closing PRD §5's second memory
  promise. **Honest scope:** this only reaches an STT that advertises the keyterms
  capability. Groq (the primary) does not, so today it applies to the OpenAI fallback only,
  and would start applying to the primary under #3.
- **Memory extraction of "I want all my meetings after 5pm"** — `_EXTRACT_SYSTEM_PROMPT` now
  states that a universal word ("all", "every", "كل") makes even a bare "want" a standing
  rule. Written as a shape rather than a worked example on purpose: a concrete example
  sentence in this prompt previously got echoed verbatim onto unrelated input. Needs a real
  extraction run against Groq to confirm.
- **Arabic currency normalization** — built and unit-tested (12 cases, both writing orders,
  Arabic-Indic digits, decimal separator spelled out). Never exercised end-to-end because no
  feature in the app produces currency text yet.
- **Whitespace-insensitive event title matching** — STT dropping a space ("TeamSync" for a
  booked "Team Sync") no longer defeats `propose_cancellation`'s title match.
- **`speech_duration_proxy` instrumentation** — recorded per turn to `turn_traces`, reported
  per scenario by `--safety`. Recording only; nothing reads it to decide anything. Exists so
  the deferred evidence gate above can be settled on two measured distributions rather than
  a third guess. The value itself is unverified until a live run produces one.
- **Stale-entry sweep for `_pending` / `_last_action`** — both are module-level dicts keyed
  by `user_id` in a long-lived worker, and both only ever shrank on `pop`, so a user who
  proposed and never confirmed (or confirmed and never undid) left an entry for the life of
  the process. Now swept lazily on propose/confirm/undo. Behaviour is unchanged — both were
  already TTL-checked on read — so this is a leak fix, not a semantics change. The sweep
  logic is unit-tested (5 cases including the TTL boundary); it has never run in a real
  session.

## Verified once, not re-confirmed

- The fix for the agent speaking a broken tool call aloud is in place and unit-tested, but
  the original bug was nondeterministic, so absence in testing proves little.
- Cross-session recall (state a fact, return in a new session, hear it recalled) has been
  verified at the script level against real Postgres/pgvector/Redis, never through an actual
  browser round-trip.

## Not yet built

- The presentation deliverable for the Sarj team — all the substance exists
  (`docs/PRD.md`, `docs/LATENCY_FINDINGS.md`, `docs/COST_MODEL.md`,
  `docs/ISSUE_ANALYSIS.md`), it just isn't packaged.
- Speech clips for the new safety scenarios: `book_request`, `decline_ar`, `decline_en`,
  `short_yes_ar`, `short_yes_en`. `eval/generate_fixtures.py` knows how to make them but
  needs a Gemini key. The two non-speech fixtures (`silence.wav`, `room_tone.wav`) are
  generated locally and already committed.
