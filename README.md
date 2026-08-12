# Sarjy

A deployed, browser-accessible voice assistant — bilingual (Arabic/English, including mid-sentence code-switching), with memory across sessions and a multi-step tool chain (prayer times + calendar).

Built for the Sarj take-home. Full plan, architecture, and scope: [`docs/PRD.md`](docs/PRD.md) ([PDF](docs/PRD.pdf)).

**Status:** repo scaffolded — voice loop not wired up yet (day 1 target).

## Layout

```
web/     Nuxt 4 — UI, latency HUD, Nitro /api/token
agent/   Python — LiveKit Agents worker
eval/    CI eval harness (audio fixtures + scorecard) — day 3
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
