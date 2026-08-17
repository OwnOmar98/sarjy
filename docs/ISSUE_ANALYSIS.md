# Sarjy — Known Issues: analysis and recommendation

Companion to `docs/KNOWN_ISSUES.md`.

**Evidence rule for this document:** code comments in `agent/` are treated as claims, not
findings. Several of them report experiments ("confirmed live", "tested all 10 words")
that cannot be re-checked from the repo. Where a comment's claim turned out to be
independently verifiable from library source, it is marked _verified_ and the source is
cited. Where it is not, it is marked _unverified_ and a test that would settle it is given.

Each finding below is tagged:

- **[V]** verified by reading installed library source or this repo's code
- **[E]** external/vendor-published, not independently confirmed here
- **[?]** hypothesis — not established, with the experiment that would settle it

---

## 1. Issue #4 — some short utterances get no response

**Not root-caused. My first pass claimed it was; that was wrong, and here is why.**

The hypothesis was that an empty final transcript satisfies the transcription watchdog and
then returns without committing a turn, producing silence. Three checks against installed
`livekit-agents` source refute it for this configuration:

1. **[V]** `agents/stt/stream_adapter.py`, `StreamAdapterWrapper._run`: an empty result is
   dropped inside the adapter (`elif not t_event.alternatives[0].text: continue`) and never
   reaches `audio_recognition.py` at all. Since `build_stt` passes a VAD and both providers
   are one-shot, every transcript in this app goes through this adapter.
2. **[V]** `audio_recognition.py:1162-1167`: the watchdog is disarmed
   (`_mark_turn_transcribed()`) **only** on a FINAL_TRANSCRIPT with non-empty text. An
   empty result therefore leaves it armed.
3. **[V]** `audio_recognition.py:1424-1435`: the watchdog is armed on **VAD**
   END_OF_SPEECH, independent of STT.

So an empty Whisper result should produce `user_transcription_timeout` → the
`_MISSED_SPEECH_MESSAGE` at `main.py:423-426`. The existing net covers this case.

It also covers the greeting-overlap case, contrary to what the code's own comment implies:
**[V]** `agent_activity.py:1367-1399` substitutes silence **only on the STT path** during
AEC warmup — "VAD, AMD and the interruption detector keep receiving the real frame". So VAD
still fires, the watchdog still arms, and the timeout still fires.

### What is actually left as a cause

For a turn to produce _literal silence_, the watchdog must never arm — which means **VAD
never reported speech**. Candidates, none established:

- **[?]** Krisp BVC noise cancellation (`main.py:464-467`) runs upstream of VAD and
  attenuates a short or quiet utterance below Silero's `activation_threshold` (default 0.5).
- **[?]** The utterance falls under Silero's `min_speech_duration` (default 0.05s — low, so
  this is unlikely on its own).
- **[?]** A stale `_turn_transcript_received`: it is cleared in `_reset_transcription_timeout`,
  called at end-of-turn cleanup (`audio_recognition.py:1743`). If a second utterance arrives
  before that cleanup runs, `_arm_transcription_timeout` returns early
  (`if timeout is None or self._turn_transcript_received: return`) and that turn has no net.

### The experiment that settles it — one session, three signals

Run with `LIVEKIT_LOG_LEVEL=debug` and reproduce a dropped utterance, then check in order:

| Signal present?                                               | Conclusion                                                                                                       |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| No VAD `START_OF_SPEECH`                                      | VAD/BVC suppressed it — nothing downstream could ever fire. Tune `activation_threshold`, test with BVC disabled. |
| VAD fired, no `recognize()` call                              | Segmentation issue inside StreamAdapter.                                                                         |
| `recognize()` returned empty, no `user_transcription_timeout` | The stale-flag race above.                                                                                       |
| `user_transcription_timeout` fired but nothing was heard      | Bug in the `session.say()` handler path, not in recognition.                                                     |
| Non-empty transcript, no reply                                | LLM/turn-detector side, not STT.                                                                                 |

This is a 30-minute test and it converts #4 from "not understood" to a located fault. It
should happen before any code changes to this area.

---

## 2. Issues #1 and #2 — a hallucinated or misheard "yes" writes a real booking

### What is established

**[V]** `confirm_pending_action` (`tools.py:358-390`) takes no arguments and inspects
nothing about the turn that confirmed. The propose→confirm machine gates _ordering_
(a proposal must exist, within a 300s TTL) but not _evidence_: if the LLM decides it heard
a yes, the row is written. A hallucinated "نعم" and a real one are indistinguishable to it.

**[V]** Confidence-based gating is **not available** with the current providers.
`stt.SpeechData.confidence` defaults to `0.0` (`agents/stt/stt.py:63`) and the OpenAI/Groq
path constructs `SpeechData(text=..., language=...)` without ever setting it
(`plugins/openai/stt.py:644`; `groq.STT` subclasses it, `plugins/groq/services.py:76`).
So every transcript arrives with confidence 0.0. Any recommendation resting on an ASR
confidence threshold — including the standard IVR practice of a higher threshold for yes/no
contexts — is inapplicable here without changing provider.

**[V]** Per-turn language is also always empty on this stack:
`plugins/openai/stt.py:620-622` requests `verbose_json` (the format that returns a detected
language) only when the model is literally `"whisper-1"`, and `_transcript_language`
(line 129-131) returns the _configured_ language only when exactly one is set — Groq is
configured with `detect_language=True` and no single language, OpenAI with `["ar","en"]`.
Both yield `LanguageCode("")`. This independently confirms the claim in `main.py:163-177`
and explains the "STT doesn't report language" accepted limitation.

### What is _not_ established

**[?]** That `نعم` in `_STT_PROMPT` (`stt_adapter.py:34`) materially causes the observed
false-yes rate. The _mechanism_ is real and externally documented — a Whisper prompt is
decoder context, and non-speech hallucination is a known Whisper failure mode where
`no_speech_prob` is an unreliable filter ([arXiv 2501.11378](https://arxiv.org/pdf/2501.11378),
[whisper#1606](https://github.com/openai/whisper/discussions/1606)) — but the specific
claims in the file's comments (that `نعم` alone fixed a real mishearing, that the full
10-word list caused a worse hallucination) are unreproducible experiment reports. Treat
them as unverified.

**The test:** feed N minutes of room tone and low-level background noise through
`recognize()` with and without `نعم` in the prompt, and count emissions of any affirmative
token. Then feed a set of genuine spoken `نعم` recordings through both and count
`نعم`→`نام` errors. Two numbers decide it, and they also give you the false-accept
baseline you currently lack.

Note the asymmetry when reading those numbers: `نعم`→`نام` costs a repeated turn;
noise→`نعم` costs an unrequested booking. They should not be weighted equally.

### Recommendation

**SHIPPED — make every write reversible and audible.** `confirm_pending_action` now requires
the agent to speak back exactly what was written, and `undo_last_action` reverses the last
booking or cancellation for 15 minutes by row id. This does not lower the false-accept rate;
it converts an unrecoverable outcome into a one-utterance recovery. It is the only one of
these three that needs no calibration data to justify, which is why it went first.

**DEFERRED, deliberately — gate `confirm_pending_action` on turn evidence.** The idea is to
record the confirming turn's VAD speech duration and refuse the write when it is near zero.
An earlier session already built almost exactly this (a short-and-fast caution heuristic),
tested it, and **removed it** — because the signal was never validated: the "correct"
industry signal for the same job, Whisper's `no_speech_prob`, read 0.0 on the very clips
that hallucinated, which left a duration threshold calibrated against a single real
measurement and carrying real false-positive risk on genuine short confirmations.

Re-attempting it on the same evidence base would repeat that mistake. The order has to be:
run `eval/run.py --safety` to get an actual false-accept rate and an actual distribution of
speech durations for genuine confirmations, **then** decide whether a threshold exists that
separates them.

The measurement side of that is now built, because it was the actual blocker: **[V]**
`livekit-agents` exposes no per-turn VAD speech duration on the success path —
`speech_duration` rides only on `UserTranscriptionTimeoutEvent`, and the full public event
list (`voice/events.py:298-314`) carries nothing else — which is precisely why the earlier
attempt had to threshold a proxy it had measured exactly once. `main.py` now records a
`speech_duration_proxy` stage per turn and `--safety` prints the per-scenario range. It is
still a proxy (wall-clock between `user_state_changed` transitions, so it includes Silero's
`min_silence_duration` hangover), but the offset is constant across both populations being
compared, so the _separation_ is measurable even though the absolute values are not. If the safety scenarios come back at a zero write rate, this may never be
worth building at all. Note also that confidence-based gating is closed off entirely on this
stack (confidence is always 0.0), so duration is the only candidate signal — which is
exactly why it deserves data rather than another attempt.

**NOT SHIPPED — ask for a content-bearing confirmation for writes**, not a bare yes/no. A
yes/no grammar is the worst case in a high-risk slot: both answers are single short tokens,
and short tokens are what non-speech hallucination produces. This is a prompt/UX change with
no calibration problem, so it is a reasonable next step — but it trades conversational
naturalness for safety, which is a product call rather than a purely technical one, and it
should be measured on the same fixtures rather than assumed to help.

**Do after the prompt experiment:** remove `نعم` from `_STT_PROMPT` if the numbers support
it. Keep the name hint — that is script-biasing and name disambiguation, a different
purpose from the confirmation token.

---

## 3. Issue #3 — accuracy drops on mid-sentence Arabic/English switching

**[V]** The current stack cannot do better than it does. Whisper predicts a single language
per segment and decodes the whole segment in it; and here the segment boundary is set by
VAD before STT sees anything (`StreamAdapter`), so a mid-sentence switch is inside one
segment by construction. Nothing in `prompt`, `keywords`, or `detect_language` changes that,
and the empty-language finding above means nothing downstream can even detect when it
happens.

**[E]** [Speechmatics' Arabic–English bilingual model](https://www.speechmatics.com/company/articles-and-news/arabic-english-bilingual-speech-to-text)
is built for exactly this case: vendor-published 6.3% WER on mixed AR/EN vs 9.7% (Google),
4.5% Arabic-only, Gulf/Egyptian/Levantine plus MSA, real-time streaming on the same model.
A first-party LiveKit plugin exists ([`livekit-plugins-speechmatics`](https://docs.livekit.io/agents/integrations/speechmatics/)).
[Soniox](https://soniox.com/speech-translation/arabic/english) is the other single-model
code-switching option; its published Arabic WER (16.2%) is much weaker.

These are vendor numbers. They are a reason to run the trial, not a reason to skip
measuring — which is what §5 is for.

### Recommendation

Trial Speechmatics as primary STT behind the existing `STT_PROVIDER` switch
(`stt_adapter.py:80`), keeping Groq in the `FallbackAdapter`. Beyond issue #3 it would also
address, as a structural consequence rather than a hoped-for side effect:

- the always-empty language field (native streaming STT reports per-result language);
- the always-zero confidence field, which would make confidence gating in §2 possible;
- the whole `StreamAdapter` + VAD-segmentation layer, which is implicated in #4's remaining
  candidates and forces segment boundaries before recognition.

`docs/LATENCY_FINDINGS.md` also attributes its STT-stage number to one-shot `recognize()`
over a full utterance. I have not verified that measurement (n=4, and I have not re-run it),
but the mechanism is **[V]**: `StreamAdapterWrapper` awaits `recognize()` only after VAD
END_OF_SPEECH, so utterance duration is necessarily inside that stage's timing.

---

## 4. Lower-priority items

- **Coarse language tag, and stray Arabic filler opening an English reply.** **[V]** Two
  different implementations exist: `main.py:_detect_language` (line 162) is Arabic-first, so
  a single Arabic character forces `"ar"`, while `language_detect.detect_code_switch`
  computes a token-count majority. Using the latter in both places fixes the coarse tag by
  construction. Separately, `describe_for_llm` (`language_detect.py:65`) returns `None` for
  single-language turns, so nothing but the system prompt drives language choice on the vast
  majority of turns — injecting the detected primary language every turn is a cheap
  candidate fix for the stray-filler case. **[?]** Whether that actually fixes it is untested.
- **"I want all my meetings after 5pm" isn't remembered.** Phrasing-dependence points at the
  fact-extraction prompt in `memory.py` handling declarative facts but not standing
  preferences phrased as imperatives. **[?]** Read the prompt and add preference-shaped
  few-shots in both languages before assuming anything structural.

---

## 5. The real blocker: nothing here is measured

**[V], as originally written:** `eval/run.py` scored latency only; `FIXTURES` was four clean
recordings. **No safety-relevant issue had a test.** That is precisely why #1 and #2 are
recorded as "occasionally" and #4 as "not reproduced" — and why the code comments claiming
experimental results cannot be checked by anyone, including me.

**Now partly addressed.** `eval/run.py --safety` (or `EVAL_SAFETY=1`) runs six multi-turn
scenarios and asserts on real database state — a fresh identity per scenario, and a failing
scenario's rows deliberately left behind for inspection:

| Scenario                        | Asserts                                                                        | Status                        |
| ------------------------------- | ------------------------------------------------------------------------------ | ----------------------------- |
| `false_confirm_silence`         | booking request → digital silence → **no row** in `calendar_events`            | needs `book_request` clip     |
| `false_confirm_room_tone`       | same, over low-level noise — the condition "نعم" was seen to hallucinate under | `room_tone.wav` **committed** |
| `spoken_no_ar` / `spoken_no_en` | booking request → real spoken refusal → **no row**                             | needs `decline_*` clips       |
| `short_utterance_ar` / `_en`    | a bare single word produces at least one user-turn trace (targets #4)          | needs `short_yes_*` clips     |

`silence.wav` and `room_tone.wav` are generated locally by `eval/generate_fixtures.py` (pure
numpy, no API key) and are committed. The five speech clips need a Gemini key, so **the
suite cannot run yet** — which means #1/#2/#4 still have no measured rate, and the deferred
evidence gate in §2 still has no data to be calibrated against.

**Still not built:** WER scoring on `clean_ar` / `clean_en` / `code_switched`. That is what
turns the Speechmatics decision from a vendor claim into a measured one, and it remains the
main gap in this section.

---

## 6. `KNOWN_ISSUES.md` itself

- ~~**Line 3 points to `docs/HANDOFF.md`, which is no longer in the repo.**~~ **Withdrawn —
  this was wrong.** `docs/HANDOFF.md` had been temporarily moved aside while this analysis
  was being written, and was restored before any of it was acted on; the reference was never
  dangling in the state that matters. Left visible rather than deleted because it is an
  instance of the exact failure this document sets out to avoid: a transient observation
  written down as a settled fact.
- **Separate observed from inferred.** Several entries mix a symptom with a diagnosis
  ("a separate mechanism from #1"). Splitting each row into _what was observed_ / _what is
  believed_ / _what would confirm it_ is what keeps the next reader — human or model — from
  inheriting an assumption as a fact. That is the same failure mode this document was
  rewritten to avoid.
- **No entry has a repro, a rate, or a pass condition**, which is why nothing can graduate
  out of the list. Tie each to a fixture from §5.

---

## Recommended order

Status as of the Day 4 triage session (see `docs/HANDOFF.md` §4a):

| #   | Step                                                                      | Status                                                                                              |
| --- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1   | **Undo + read-back** (§2) — turns a bad write into a recoverable one      | **Shipped**, unit-tested, not yet live-verified                                                     |
| 2   | **Safety fixtures + assertions** (§5) — `eval/run.py --safety`            | **Built**; the two non-speech fixtures are committed, the five speech clips need a Gemini key       |
| 3   | **Diagnose #4** with the three-signal test (§1)                           | Not done — needs one live debug session, no code change                                             |
| 4   | **Run `--safety` for a real false-accept rate and duration distribution** | Blocked on step 2's clips                                                                           |
| 5   | **Decide on the evidence gate** (§2) using step 4's numbers               | Deliberately deferred until then — see §2 for why re-attempting it now would repeat a known mistake |
| 6   | **Run the `نعم` prompt A/B** (§2) and act on the numbers                  | Not done                                                                                            |
| 7   | **Trial Speechmatics** (§3), scored on the WER fixtures                   | Blocked on an account/key                                                                           |

Steps 3-6 need no vendor decision and no new spend. Nothing shipped in step 1 or 2 has run
against a live LiveKit room yet, which is the single most useful thing to do next.

---

## Sources

- [Investigation of Whisper ASR hallucinations induced by non-speech audio](https://arxiv.org/pdf/2501.11378)
- [openai/whisper — hallucination on audio with no speech](https://github.com/openai/whisper/discussions/1606)
- [Speechmatics — Arabic–English bilingual speech-to-text](https://www.speechmatics.com/company/articles-and-news/arabic-english-bilingual-speech-to-text) (vendor-published figures)
- [LiveKit — Speechmatics STT plugin](https://docs.livekit.io/agents/integrations/speechmatics/)
- [LiveKit — Turns, endpointing and interruptions](https://docs.livekit.io/agents/logic/turns/)
- [Soniox — Arabic/English code-switching](https://soniox.com/speech-translation/arabic/english)
- [Adaptive confidence thresholds for speech recognition (US20090259466A1)](https://patents.google.com/patent/US20090259466) — context for why yes/no slots need a higher bar
- [Stop letting the LLM drive your voice agent's state machine](https://voxam.hashnode.dev/stop-letting-llm-drive-voice-agent-state-machine)

Local source read for the **[V]** findings, all under
`agent/.venv/lib/python3.11/site-packages/livekit/`:
`agents/stt/stream_adapter.py`, `agents/stt/stt.py`, `agents/voice/audio_recognition.py`,
`agents/voice/agent_activity.py`, `agents/voice/agent_session.py`, `plugins/openai/stt.py`,
`plugins/groq/services.py`, `plugins/silero/vad.py`.
