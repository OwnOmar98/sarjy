# Sarjy

A browser-based voice assistant that speaks Arabic and English, including mid-sentence code-switching. It remembers users across sessions and chains tool calls to work with prayer times and a calendar.

Built for the Sarj take-home. Full plan, architecture, and scope: [`docs/PRD.md`](docs/PRD.md) ([PDF](docs/PRD.pdf)).

**Status:** complete. It ran live end to end (web on Cloudflare Workers, agent worker on Fly.io, LiveKit Cloud for transport, Postgres on Neon, Redis on Upstash). The hosted demo has since been taken down, so run it locally with the steps below.

## What it does

- **Voice loop** on LiveKit Agents: VAD, turn detection, STT, LLM, then TTS, with a latency HUD in the browser showing per-stage timings and session p50/p95.
- **Tool calling.** The LLM gets prayer times from the Aladhan API (Umm al-Qura method, resolved in `Asia/Riyadh`), then lists, checks, books, edits and cancels calendar events. Every write is proposed first and only runs after a spoken confirmation, and the last action can be undone.
- **Long-term memory with RAG.** Durable facts are extracted from conversations, embedded into Postgres + pgvector (768-dim, HNSW index) and retrieved per user in later sessions. Embeddings fall back from `gemini-embedding-2` to `gemini-embedding-001` to OpenAI `text-embedding-3-small`, and each fact stores which model embedded it, so a provider outage never mixes vector spaces.
- **Provider failover** for STT, LLM (Groq `gpt-oss-120b`, falling back to OpenAI) and TTS (ElevenLabs Flash v2.5), so one provider failing doesn't silence the assistant.
- **Arabic handling:** language detection per turn and Arabic text normalization before TTS.
- **CI eval harness** (`eval/run.py`) that drives synthesized audio fixtures through the real agent in a real LiveKit room and reports p50/p95 latency per stage on every push to `main`.
- Optional email/password sign-up; anonymous per-browser identity by default.

Engineering notes: [`docs/LATENCY_FINDINGS.md`](docs/LATENCY_FINDINGS.md), [`docs/COST_MODEL.md`](docs/COST_MODEL.md), [`docs/DEBUGGING_STORIES.md`](docs/DEBUGGING_STORIES.md), [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

## Layout

```
web/     Nuxt 4 — UI, latency HUD, Nitro /api/token
agent/   Python — LiveKit Agents worker
eval/    CI eval harness (audio fixtures + latency scorecard)
db/      Postgres schema (pgvector)
docs/    PRD/TDD
```

## Running locally

**web/**

```
cd web
cp .env.example .env   # fill in LiveKit Cloud creds
npm install
npm run dev
```

**agent/**

```
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in LiveKit + Groq + Google keys
python main.py dev
```

**db/**

```
psql "$DATABASE_URL" -f db/schema.sql
```
