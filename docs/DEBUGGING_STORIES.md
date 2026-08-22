# Two bugs, and how I actually found them

Not everything worth showing is a feature. These are two real bugs from building Sarjy, picked
because the fix wasn't the interesting part; the path to it was.

---

## 1. "Conversations don't save" — three plausible-and-wrong answers before the right one

**The report:** after deploying, starting a new conversation never showed up anywhere. Not an error,
not a crash — just silently gone.

The obvious suspects, in order, each ruled out with actual evidence rather than assumed fixed and
moved past:

**Guess 1 — a missing Vercel env var.** The web app reads Postgres directly now (for the
conversation-history sidebar), so a fresh `NUXT_DATABASE_URL` seemed likely. Added it, redeployed.
Still broken.

**Guess 2 — schema drift.** Pulled the live Fly.io agent logs instead of guessing again, and there
it was, a real stack trace:

```
asyncpg.exceptions.UndefinedColumnError: column "updated_at" of relation "sessions" does not exist
```

A migration existed in the repo but nothing in CI ever actually _ran_ it against the production
database — `db/schema.sql` was a source file, not a step anyone had wired up. Applied it by hand
against Neon, confirmed the column existed, redeployed. Still broken. Genuinely surprising —
the stack trace was real, the fix was correct, and it _still_ didn't work.

**Guess 3 — stale deployment.** Checked Vercel's actual build history against GitHub's deployment
API to see whether the redeploy had really picked up the new env var. It had. Still broken.

At this point every plausible _web-side_ explanation was exhausted and confirmed exhausted, not
just assumed. So the question had to move: is the call even reaching the database layer at all?

**The tell:** checking the deployed worker's own logs directly, instead of trusting that "deployed"
meant "handling traffic," showed something odd — zero job requests recorded there in nearly two
hours, despite a real call having just happened seconds earlier. The call was completing
successfully somewhere. Just not on the service that was supposed to be handling it.

The real-time layer dispatches each new call to whichever registered worker picks it up — and a
second, stray worker process was still registered against that same job queue alongside the
deployed one. Two workers, one queue, no way for either side to know only one of them was supposed
to be authoritative. Some calls were landing on the extra worker and getting written to a completely
different database than the one the production app actually reads from. No error anywhere, because
from each individual system's point of view, nothing was wrong — the call really did complete
successfully. It just completed in the wrong place.

**The actual fix** wasn't "stop the stray process" — that's a workaround, and the same collision is
one accidental process start away from happening again. It was making the production job queue
exclusive: giving the deployed environment its own dedicated real-time infrastructure that nothing
else can ever register against — folded into the project's existing Docker Compose setup rather than
a one-off script, with `dev-up`/`dev-down` scripts so the whole local stack starts and stops as one
unit going forward, cleanly separated from anything production-facing.

The lesson I'd actually generalize from this: when three independently-plausible, independently-
verified fixes all fail to change the outcome, the bug almost certainly isn't in any of the systems
you've been staring at — it's in an assumption about how those systems relate to each other that
nobody stated out loud. Shared infrastructure between two environments was never a decision anyone
made; it was just never _not_ true yet.

---

## 2. "English STT is really sensitive and usually picks up Arabic" — from a respectful patch to the actual root cause

This one came up directly during testing, and it took two real passes to actually fix — the first
made it rarer, the second found why it was still happening at all.

**First pass: respecting a fix before changing it.** Pulling a real conversation transcript straight
from the database made the bug concrete immediately:

```
14:51:48 assistant: Got it — once you say yes, I'll cancel TeamSync at 3:00 PM tomorrow.
14:51:52 user: نعم.
```

That user was speaking English the entire conversation. "Yes" came back transcribed as Arabic.

The lazy fix is "strip Arabic bias from the STT prompt." I didn't do that, because the prompt's own
comment in the codebase turned out to already explain _why_ it was there:

> "نعم" is the only [word] that ever fixed a real mishearing (نعم → نام without it) ... Doesn't fully
> fix it though — نعم itself still occasionally hallucinates onto noise; that's inherent to keeping
> the one word that's actually needed.

Someone had already hit the opposite bug — نعم itself getting misheard — and traded one failure mode
for a smaller one, on purpose, with the tradeoff written down. Deleting that line would silently
reintroduce a bug that had already been found and fixed once. The prompt wasn't wrong; it was a
correct answer to a narrower question than the one now being asked.

So the real question was: bias toward Arabic _only when the conversation is actually in Arabic_.
That meant retuning STT settings mid-call, per turn — not something obviously supported. Reading the
actual installed SDK (not documentation, the installed package source) turned up a real
`update_options()` method on the provider STT classes, unused anywhere in this codebase. Rather than
trust that from the docstring alone, I verified it directly:

```
Before retune:  Groq  languages=[]         (auto-detect, نعم-biased prompt)
After retune(en): Groq languages=['en']    prompt="Hi, I'm Sarjy..." (نعم dropped)
After retune(None): Groq languages=[]      prompt="Hi, I'm Sarjy... نعم." (restored)
```

The conversation already tracks its own dominant language per turn for a different reason (steering
the LLM's reply language). Reusing that same signal to retune STT for the _next_ turn means: once
you've clearly settled into English, the Arabic bias goes away for you specifically — and comes
right back the moment you code-switch or speak Arabic, so the original نعم/نام fix never regresses
for anyone who actually needs it.

The habit that mattered here isn't "read the SDK source" — it's _ask why the thing you're about to
delete exists_ before deleting it. A comment explaining a tradeoff is usually evidence of an earlier
bug, not decoration.

**Second pass: the fix above turned out to be treating a symptom.** It shipped, and the wrong-language
replies got rarer, not gone. That's the moment it's tempting to call a bug "mostly fixed" and move on.
Instead I went back to the actual mechanism the retuning was built on: `detect_code_switch()` decides
what language to tell the model to reply in by **counting the script of the transcript** — Arabic
characters vs. Latin letters. That's a guess about what the user said, built from a guess about what
the user said. If the transcription itself is ever wrong, the first pass's fix doesn't just fail to
help — it actively enforces the wrong answer, and harder each time, because every version of it up to
that point made the reply-language instruction _more_ forceful in order to make it stick.

**Why testing this live wasn't going to find it.** A live conversation mixes transcription in with
VAD, turn detection, the LLM, and the DB — by the time you hear a wrong-language reply, you can't tell
whether STT mis-transcribed, the language policy mis-read a correct transcript, or the model just
didn't obey. So instead of talking to it more, I built a small offline harness
(`eval/stt_compare.py`) that removes everything except the one component in question:

1. **Generate real audio, not recordings.** `eval/generate_fixtures.py` synthesizes a fixed set of
   test utterances — clean English, clean Arabic, a genuinely code-switched sentence, a sentence with
   Arabic-associated proper nouns in English, a booking request, spoken confirmations and refusals in
   both languages, and a couple of pure-noise clips (digital silence, low-level room tone) — through
   the same real TTS provider the agent itself uses, so the test audio has the same characteristics as
   what a real caller produces. The clips are committed as `.wav` files so every run after that is
   reproducible and comparable.
2. **Play each clip through every STT provider directly, no LiveKit room involved.** The script calls
   each provider's real API on the same audio and records the transcript, the reported language, and
   (once `agent/groq_verbose_stt.py` was added to stop the plugin from discarding it) Whisper's own
   confidence diagnostics.
3. **Score it against the truth, automatically.** Word-error rate per provider per clip, an
   affirmative/negative check run through the same `affirmatives.looks_affirmative()` the agent's
   confirmation logic actually uses, and — via `--sweep` — every candidate hallucination-rejection
   threshold scored against every clip at once, instead of picking one number and hoping.

**What it found.** On the fixture that says, in plain English, _"What time is Maghrib prayer in
Riyadh today?"_ — Groq's Whisper came back with `ما هو مغرب في رياد اليوم؟`, an Arabic
**translation** of the sentence, reproducibly, 2 out of 2 runs — while its own `language` field
correctly said `en`. The transcript was Arabic; the audio was English; the model had the right answer
sitting right next to the wrong one and nothing was reading it. That's the actual bug behind "it keeps
answering in Arabic": not a stubborn LLM, and not (only) the STT prompt bias the first pass already
fixed — a transcription that translates instead of transcribes, on sentences carrying Arabic-associated
proper nouns, that script-counting can't tell apart from a genuinely Arabic message.

Once the harness could isolate that, the fix followed directly: read Whisper's own `language` field
(available all along, just never requested — the plugin only asks for the response format that
includes it when the model name is literally `"whisper-1"`, which Groq never is) and let it override
script-counting whenever the two disagree, instead of trusting the transcript's script as if it were
the audio.

**The harness also killed an idea I was sure was right.** The obvious next move was a confidence
threshold — reject a transcript when Whisper wasn't sure. I built it (`avg_logprob`,
`compression_ratio`, `no_speech_prob`), then ran `--sweep` before trusting it. The numbers said no:
non-speech clips decoded at confidence levels _better_ than a genuine one-word "نعم" did. There is no
threshold that rejects the silence-hallucination without also throwing away real short confirmations —
so that gate ships **off** by default, with the measurement that killed it left in the code as the
reason. What actually works against the hallucination is duller and more reliable: Whisper's silence
hallucination turned out to be deterministic, not random — it reads back the decoder prompt verbatim,
or falls back to its training data's stock caption ("Thank you."). Matching that exactly catches it
without touching a single real utterance.

**And it turned a hunch into a number worth acting on.** The same harness, pointed at all three
configured STT providers instead of just Groq, measured OpenAI's `gpt-transcribe` at roughly a third
of Groq's word-error rate on the code-switched clip specifically (0.077 vs. 0.231) and near-perfect
overall (0.009 mean WER), at the cost of roughly double the latency (503ms → 1291ms). That's a
concrete, comparable number instead of "OpenAI seems better," and it's why the agent's default STT
provider changed on the strength of it.

**The generalizable point:** the first pass treated the symptom — an instruction the model was obeying
too well — and made real progress. But every prompt-side fix to "wrong language reply" was built on top
of a signal (the transcript's script) that can itself be wrong, and no amount of tightening the
instruction on top of a wrong signal was ever going to close the gap — it could only make the wrong
cases more confidently wrong. The fix wasn't a better instruction; it was refusing to trust the signal
the instruction was built on until there was a script that could check it against something else.
