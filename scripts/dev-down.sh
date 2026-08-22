#!/usr/bin/env bash
# Stops everything scripts/dev-up.sh started. Pattern-matched, not
# PID-file-tracked — `npm run dev` spawns a Nitro worker child
# (@nuxt/cli/dist/dev/index.mjs) that doesn't share its parent's PID, so
# killing only the top-level "nuxt dev" process reliably leaves it
# running (confirmed live) and blocks the next start with a stale lock.
set -uo pipefail
cd "$(dirname "$0")/.."

pkill -f "nuxt dev" 2>/dev/null && echo "web: stopped" || echo "web: not running"
pkill -f "@nuxt/cli" 2>/dev/null
rm -f web/.nuxt/nuxt.lock

pkill -f "main\.py dev" 2>/dev/null && echo "agent: stopped" || echo "agent: not running"

npm run docker:down
