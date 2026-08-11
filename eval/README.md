# Eval harness

CI-run scorecard against ~12 recorded audio fixtures (docs/PRD.md §2, §7):
clean AR, clean EN, code-switched, noisy. Reports WER per language, turn
latency p50/p95, and tool-call accuracy on every push.

Not built yet — day 3 per the plan in `docs/PRD.md`. `fixtures/` will hold
the recorded audio + expected-transcript/expected-tool-call pairs.
