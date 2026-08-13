# Sarjy — Cost Model

**Author:** Own Abu Hamour
**Date:** 13 Aug 2026
**Status:** Measured against real, current provider pricing and this codebase's actual token/character usage
**Related:** `docs/PRD.md` §6 defines the model this report fills in with real numbers; `docs/LATENCY_FINDINGS.md` covers the same system's real latency

---

## 1. Summary

`docs/PRD.md` §6 committed to a real per-minute unit cost, decomposed by stage, extrapolated to 100/1k/10k users — and specifically wanted to know **which line dominates, and what caching removes from it.** This is that model, built from real current provider pricing (checked live, not from memory — pricing pages change) and real measured token/character counts from this codebase's actual system prompt, tool schemas, and provider configuration.

Three findings worth leading with:

1. **TTS provider choice is the single biggest cost lever available — bigger than any caching strategy.** The deployed default is ElevenLabs Flash v2.5, not Gemini TTS as `docs/PRD.md` §2's prose states — `TTS_PROVIDER` was never explicitly set, so `tts_adapter.py`'s own fallback logic picked ElevenLabs because a key happens to be configured. Switching the one already-supported env var to Gemini cuts **total** cost by ~43%.
2. **Once TTS is optimized, LiveKit's own agent-compute minutes — not any AI model call — become the largest single cost line**, at real scale. This runs against the usual assumption that model API calls dominate a voice agent's bill.
3. **Unit economics are essentially flat per user across 100 → 10,000 users** (~$2.30/user/month on the current default config) — this system has no built-in economies of scale from provider pricing tiers; the only real lever is reducing marginal per-call cost (caching, provider choice), not growing bigger.

## 2. Conversation profile (stated explicitly, not hidden)

- 1 call/day per active user, 3 minutes/call, 30 calls/user/month.
- 6 user turns per call (roughly one exchange every 30s), plus the greeting.
- User turn: ~20 tokens (~15 words) of transcribed speech, ~4s of audio.
- Assistant reply: ~25 tokens (~19 words, per the `SarjyAgent` instruction to keep replies short for voice), ~4s of audio.
- Memory facts injected from turn 2 onward (the greeting's own one-time lookup aside): ~3 facts, ~45 tokens.
- No prompt caching (confirmed absent in `docs/LATENCY_FINDINGS.md` §3.4) — every turn resends the full system prompt, all 4 tool schemas, and the entire prior conversation. This is the real, measured mechanism behind the numbers below, not an assumption.

## 3. What actually gets billed, measured directly from this codebase

`997 tokens` of fixed overhead (system instructions + all 4 tool schemas) gets resent on **every single LLM call** — measured directly by running the real `SarjyAgent` instructions and the real tool schemas (as `livekit-agents` itself serializes them for the wire, via `ToolContext.parse_function_tools("openai")`) through a tokenizer, not estimated:

| Piece                         |  Tokens |
| ----------------------------- | ------: |
| System instructions           |     362 |
| `get_prayer_time`             |     192 |
| `check_calendar_availability` |     155 |
| `list_calendar_events`        |     128 |
| `book_calendar_event`         |     159 |
| **Total per turn**            | **997** |

_(Tokenized with `tiktoken`'s `gpt-4o` encoding as a stand-in for Groq's Llama 3.3 tokenizer, which isn't publicly exposed — a reasonable approximation for English text, not an exact figure. See §7.)_

With no prompt caching, this 997-token block compounds across a call alongside the growing chat history — turn 6 of a call resends everything from turns 1–5 too. Summed across a 6-turn, 3-minute call: **8,019 input tokens, 180 output tokens** for the main conversational LLM alone (full turn-by-turn arithmetic available on request; the compounding, not a flat per-turn estimate, is what produces this number).

## 4. Per-call unit cost

Current live provider pricing (checked directly against each provider's pricing/docs pages, not assumed):

| Provider · model                                    | Rate                                                       |
| --------------------------------------------------- | ---------------------------------------------------------- |
| Groq · `whisper-large-v3-turbo` (STT)               | $0.04 / hour of audio                                      |
| Groq · `llama-3.3-70b-versatile` (LLM)              | $0.59 / $0.79 per 1M in/out tokens                         |
| Groq · `llama-3.1-8b-instant` (fact extraction)     | $0.05 / $0.08 per 1M in/out tokens                         |
| Google · `gemini-embedding-2`                       | $0.20 per 1M tokens                                        |
| ElevenLabs · Flash v2.5 (TTS, **deployed default**) | $0.05 per 1,000 characters                                 |
| Google · Gemini TTS (available, not default)        | $10.00 per 1M output audio tokens (25 tokens/sec of audio) |
| LiveKit Cloud · WebRTC transport                    | $0.0005/min (Ship-tier marginal rate)                      |
| LiveKit Cloud · Agent session compute               | $0.01/min (Ship-tier marginal rate)                        |

| Cost line                        | Per call (ElevenLabs, **as deployed**) | Per call (Gemini TTS instead) |
| -------------------------------- | -------------------------------------: | ----------------------------: |
| STT (~25s of user speech)        |                                $0.0003 |                       $0.0003 |
| LLM (8,019 in / 180 out tokens)  |                                $0.0049 |                       $0.0049 |
| Fact extraction + embeddings     |                               < $0.001 |                      < $0.001 |
| TTS                              |                                $0.0405 |                       $0.0073 |
| LiveKit transport (marginal)     |                                $0.0015 |                       $0.0015 |
| LiveKit agent compute (marginal) |                                  $0.03 |                         $0.03 |
| **Total per 3-minute call**      |                            **~$0.077** |                   **~$0.044** |
| **Per minute**                   |                            **~$0.026** |                   **~$0.015** |

## 5. Which line dominates

With the deployed default (ElevenLabs), **TTS is the single largest AI-provider line by a wide margin** — roughly half the total per-call cost, and ~8x the LLM line. This isn't a caching problem; the TTS phrase cache already built (`agent/tts_cache.py`) only covers the two genuinely fixed strings in this codebase (the fallback apology, the missed-speech apology) — real conversational replies are unique every time and were never cacheable. **Provider choice is the actual lever here**, and it's already built: `TTS_PROVIDER=gemini` is a one-line env var change, not new engineering, and cuts the TTS line by ~82% (and total cost by ~43%).

Once that's applied, **LiveKit's own agent-compute minutes become the largest single line** — $0.03 of the ~$0.044/call total, ~68%. This is infrastructure, not a model API call, and isn't something either caching or provider choice touches — it scales directly with session duration regardless of what's said. The only lever on this line is shorter sessions or a different transport provider, neither evaluated here.

The LLM line, while modest here (~11% of total on the Gemini-TTS config), is where the absent prompt caching flagged in `docs/LATENCY_FINDINGS.md` §3.4 shows up on the cost side too: caching the fixed 997-token system+tools block would remove most of it from every turn after the first, directly cutting the LLM line — connects the two reports' recommendations into the same fix.

**Arabic costs measurably more, but modestly.** A same-meaning Arabic sentence tokenizes to ~1.6x the English token count (measured directly: 21 vs. 13 tokens for a matched pair), consistent with `docs/PRD.md` §2's expectation that Arabic would cost more, not just cost differently. Applied to just the conversation-dependent portion of the LLM line (the fixed English system prompt and tool schemas don't inflate), a fully-Arabic call's LLM cost comes out ~16% higher than English — real, but diluted by the large fixed-overhead block that doesn't change with conversation language.

## 6. Scaled: 100 / 1k / 10k users

At 30 calls/user/month, LiveKit's tiered pricing (Build free / Ship $50 base + $0.01/min agent overage / Scale $500 base) actually matters at these volumes — this table uses real tier math, not a flat per-call multiply:

| Users  | Calls/mo | Total (ElevenLabs) | $/user/mo | Total (Gemini TTS) | $/user/mo |
| ------ | -------: | -----------------: | --------: | -----------------: | --------: |
| 100    |    3,000 |              ~$230 |    ~$2.30 |              ~$130 |    ~$1.30 |
| 1,000  |   30,000 |            ~$2,310 |    ~$2.31 |            ~$1,315 |    ~$1.32 |
| 10,000 |  300,000 |           ~$23,160 |    ~$2.32 |           ~$13,190 |    ~$1.32 |

Neon, Upstash, and Vercel are minor lines throughout this range (all three stay within or just past their free tiers even at 10k users, together under ~$200–400/month at the top end) — not broken out in detail here since they don't change which line dominates at any of these scales.

**Per-user cost is essentially flat, not falling with scale.** Nothing here compounds into an economy of scale — LiveKit's overage rate is constant regardless of tier, and every AI-provider line is metered per-unit with no volume discount modeled. The only way to actually lower the per-user number is reducing marginal per-call cost itself (the TTS provider switch above is the clearest example already available), not growing the user base.

## 7. Honest limitations

- **Tokenizer approximation.** Groq doesn't publicly expose Llama 3.3's actual tokenizer, so `tiktoken`'s `gpt-4o` encoding was used as a stand-in for all token counts in this report. Reasonable for relative comparisons and rough absolute figures, not an exact bill — real usage should be reconciled against actual Groq invoices once available.
- **Conversation profile is a stated assumption, not measured usage** — this system has no real users yet, only test/eval traffic. §2's numbers are grounded in this session's real test transcripts (turn length, reply length) but the call frequency (1/day) and duration (3 min) are reasonable placeholders, not observed behavior.
- **LiveKit's per-call cost figures use marginal/overage rates**, which slightly overstate cost at very low volume (where a plan's included minutes haven't been exhausted yet) — the scaled table in §6 corrects for this with real tier math; the per-call table in §4 does not.
- **Fact extraction and embedding costs are stated as "negligible" rather than modeled to the same precision as the other lines** — both are genuinely small enough (well under $0.001/call each) that further precision wouldn't change any conclusion here.

## 8. Recommendation

Switch `TTS_PROVIDER` to `gemini` — it's a one-line, already-supported config change (no new code), cuts total cost by ~43%, and the tradeoff (voice quality/character, ElevenLabs vs. Gemini) is a product call worth making deliberately rather than by accident of which API key happened to be set. Beyond that, LiveKit agent-compute minutes are the next real lever once TTS is fixed — worth watching as the dominant cost line at scale, though no alternative was evaluated here. Prompt caching (already flagged as the top latency lever in `docs/LATENCY_FINDINGS.md`) would also measurably cut the LLM line — a second win from the same fix.
