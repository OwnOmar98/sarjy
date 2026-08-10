# Sarjy

A deployed, browser-accessible voice assistant — bilingual (Arabic/English, including mid-sentence code-switching), with memory across sessions and a multi-step tool chain (prayer times + calendar).

Built for the Sarj take-home. Full plan, architecture, and scope: [`docs/PRD.md`](docs/PRD.md) ([PDF](docs/PRD.pdf)).

**Status:** planning complete, implementation starting.

## Layout

```
web/     Nuxt 4 — UI, latency HUD, Nitro /api/token
agent/   Python — LiveKit Agents worker
eval/    CI eval harness (audio fixtures + scorecard)
db/      Postgres schema / migrations (pgvector)
docs/    PRD/TDD
```
