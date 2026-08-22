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

**Measured and largely fixed (offline harness session).** `eval/stt_compare.py` — a new
offline harness that runs fixture audio straight through each provider with no LiveKit room
— settles several of the guesses above:

- The hallucination is **not** low-confidence. Non-speech decodes at `avg_logprob` -0.29 to
  -0.37 and `compression_ratio` 0.58, both comfortably _inside_ the range real speech
  occupies (-0.06 to -0.71, 0.33 to 1.28). `--sweep` finds **no** threshold pair that
  rejects a non-speech clip without also discarding real speech, and `no_speech_prob` reads
  0.0 on every clip of either kind. A confidence gate cannot catch this, and the reference
  default of -1.0 actively _discarded a real one-word "نعم"_ (-1.004). That threshold is now
  off by default; only the compression-ratio backstop (2.4, never fired on real speech)
  stays on.
- What the hallucination **says** is exactly predictable, and that is the usable signal.
  With a prompt set, silence returns the prompt verbatim ("I'm Sarjy." / "أنا سرجي، مساعدك
  الصوتي."); with none, Whisper's stock caption for empty audio ("Thank you.", both clips).
  `agent/groq_verbose_stt.py` rejects both by exact match — precise, confidence-independent,
  and impossible to trigger with plausible user speech.
- With "نعم" removed from the decoder prompt and those two rejections in place, **silence
  and room tone both transcribe as empty on all three providers**, 2/2 runs each — measured,
  not asserted. The affirmative it used to hallucinate is no longer in the prompt for it to
  echo, and the recognition that word was protecting moved to `agent/affirmatives.py`, which
  accepts the measured mishearings ("نام", "NOM", "Nam") as a yes.

The evidence gate remains deliberately unbuilt, and the sweep is now the reason rather than
the absence of data: the two populations do not separate on any available signal.

**Mitigated, not fixed (earlier session):** the _consequence_ is also recoverable rather than
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

**Measured, and no longer blocked on a new vendor.** Two providers already installed and
already keyed were never compared against this. On `code_switched.wav` (mean of 2 runs):

| Provider                      | code-switch WER | mean WER, all fixtures | p50 latency |
| ----------------------------- | --------------- | ---------------------- | ----------- |
| Groq `whisper-large-v3-turbo` | 0.231           | 0.285                  | 503 ms      |
| ElevenLabs `scribe_v1`        | 0.154           | 0.147                  | 739 ms      |
| OpenAI `gpt-transcribe`       | **0.077**       | **0.009**              | 1291 ms     |

`gpt-transcribe` — currently configured as the _fallback_ — is a third of Groq's WER on
code-switched speech and near-perfect across the set, including the two clips Groq fails
outright (see #8). ElevenLabs Scribe additionally reports a real per-word-logprob confidence
(0.83–1.00 measured) and its own detected language, which is what makes the deferred
confirmation gate and the language policy buildable at all.

The remaining cost is latency: `STT_PROVIDER=openai` roughly doubles p50 transcription time
(503 → 1291 ms), against a pipeline whose latency is already the weakest pillar. That is a
product tradeoff, not a technical blocker, and it is one env var either way. Speechmatics
may still beat all three; it is no longer the only path.

**Reproduce:** `python eval/stt_compare.py --runs 2`.

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

**Was:** `livekit-plugins-openai` requests the `verbose_json` format that carries a detected
language only for the literal model name `"whisper-1"`, which Groq never uses — so both
configured providers always reported an empty language and `SpeechData.confidence` was always
0.0.

**Now fixed, and it did not need a fork or a new provider — only asking for the right response
format.** `agent/groq_verbose_stt.py` is a ~60-line `groq.STT` subclass whose `_recognize_impl`
requests `verbose_json`. Whisper's detected language comes back on every turn (measured: `ar`
on the Arabic fixtures, `en` on the English ones), along with the per-segment `avg_logprob`,
`compression_ratio` and `no_speech_prob` that #1's analysis needed. ElevenLabs Scribe reports
both a language and a real confidence natively.

This matters well beyond a trace column: it is the only language signal derived from _audio_
rather than from the transcript's script, so it is the only one that survives a
mis-transcription — see #8.

---

## Language routing (fixed this session, unverified live)

### 8. Whisper can answer an English utterance in Arabic script — and that is what made the agent reply in the wrong language

**Observed, reproducible on a committed fixture:** `tool_trigger.wav` says _"What time is
Maghrib prayer in Riyadh today?"_ — pure English. Groq returns
`ما هو مغرب في رياد اليوم؟`, an Arabic _translation_, 2/2 runs, while correctly reporting the
detected language as `en`. `gpt-transcribe` and Scribe both transcribe it correctly. The
trigger appears to be the Arabic-associated proper nouns ("Maghrib", "Riyadh") pulling the
whole segment's decode into Arabic.

**Why it mattered far beyond one bad transcript.** The reply-language policy was built on
script-counting the transcript (`language_detect.detect_code_switch`). An Arabic-script
transcript therefore concluded "the user spoke Arabic" and injected
_"You must reply in Arabic only, do not use English anywhere"_ — so a user who spoke English
got an Arabic answer, and got it **because** the instruction worked. Every strengthening of
that instruction made this case worse. The wrong-language complaint was never an LLM
obedience problem; it was a transcript problem with an obedience layer amplifying it.

**Fixed.** `SpeechData.language` (now available on Groq via #7, and natively on Scribe) is
the only language signal derived from audio rather than from text, so it now wins:
`detect_code_switch(text, reported_language=...)` takes the provider's answer for
`primary_language`, treats a script/audio disagreement as evidence of mis-transcription
rather than of code-switching, and `describe_for_llm` tells the model outright that the
transcript is suspect and to ask for a repeat rather than guess at it. Verified against this
exact transcript in `agent/tests/test_pure_logic.py::TestReportedLanguageWins`.

**Second, independent cause, also fixed:** `mixed` was `len(languages) > 1` — presence, with
no threshold. A single Arabic token in an English sentence (a filler like "يعني", an Arabic
proper noun read back out of memory, or one hallucinated word) made the turn "mixed" and
produced an instruction to _reply_ code-switched, contradicting the agent's own "not a single
borrowed word" rule from a position closer to generation. The minority language now has to
clear both 2 tokens and 25% of the turn.

**Third contributor, also fixed:** the per-turn context was assembled as
`[language tag][remembered facts][user message]`, and `memory.py` normalizes facts into
"whichever language reads best" — so an Arabic fact sat _between_ the instruction to reply in
English and the message it applied to. The tag is now injected last.

---

## Fixed but not verified live

Everything in this section is implemented, lint-clean, and unit-tested where the logic is
pure — but **no part of this session's work has been through a real browser session or a
live LiveKit room**, because no credentials were available. Treat as unproven.

The STT/language work above is measured against fixture audio through real provider APIs
(`eval/stt_compare.py`), which is stronger evidence than the previous round had — but it is
still not a live room. Specifically unproven end-to-end:

- **Provider-reported language reaching the language policy.** `SpeechData.language` is
  confirmed populated by direct API calls; that it survives `StreamAdapter` →
  `FallbackAdapter` → `user_input_transcribed` and lands in `LanguageTracker` has not been
  seen in a live session.
- **Per-language prompt retuning under `FallbackAdapter`.** `retune_for_language` reaches
  into `_stt_instances`; exercised via the harness, never during a live turn.
- **The rolling language estimate over a real conversation.** Unit-tested against synthetic
  turn sequences only.
- **ElevenLabs Scribe as a live STT.** Only ever called through `recognize()` on fixture
  audio — never as the session STT, and never with `scribe_v2_realtime`.
- **The model actually honouring the "known mishearings count as yes" policy.** The wording
  is composed and unit-tested; whether the LLM acts on it needs a live confirmation turn.

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
