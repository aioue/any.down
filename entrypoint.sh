#!/bin/sh
# Runs anydown watch mode (jittered sync interval) as the container main process.
set -e

# Ensure session file exists (avoids Docker mounting it as a directory)
touch /app/session/session.json

INTERVAL="${ANYDOWN_WATCH_INTERVAL:-90}"
JITTER="${ANYDOWN_WATCH_JITTER:-10}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting any.down watch mode (${INTERVAL} ± ${JITTER} min)..."

exec anydown --watch \
  --watch-interval "${INTERVAL}" \
  --watch-jitter "${JITTER}"
