#!/usr/bin/env bash
# Starts the full local dev stack: Postgres/Redis/LiveKit (db/docker-compose.yml)
# plus the agent worker and web dev server. Logs go to logs/*.log. Stop
# everything with scripts/dev-down.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

npm run docker:up

if pgrep -f "main\.py dev" >/dev/null; then
  echo "agent: already running"
else
  (cd agent && nohup .venv/bin/python main.py dev > ../logs/agent.log 2>&1 &)
  echo "agent: started (log: logs/agent.log)"
fi

if pgrep -f "nuxt dev" >/dev/null; then
  echo "web: already running"
else
  # A lock left behind by an unclean previous stop blocks the next start.
  rm -f web/.nuxt/nuxt.lock
  (cd web && nohup npm run dev > ../logs/web.log 2>&1 &)
  echo "web: started (log: logs/web.log, http://localhost:3000)"
fi
