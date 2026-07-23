#!/bin/sh
# Runs anydown watch mode (jittered sync interval) with optional HTTP API sidecar.
set -e

# Ensure session file exists (avoids Docker mounting it as a directory)
touch /app/session/session.json

INTERVAL="${ANYDOWN_WATCH_INTERVAL:-90}"
JITTER="${ANYDOWN_WATCH_JITTER:-10}"
API_PID=""

cleanup() {
  if [ -n "$API_PID" ]; then
    kill "$API_PID" 2>/dev/null || true
  fi
}

trap cleanup TERM INT

if [ "${ANYDOWN_API_ENABLED:-1}" = "1" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting anydown API on port ${ANYDOWN_API_PORT:-8080}..."
  anydown-api &
  API_PID=$!
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting any.down watch mode (${INTERVAL} ± ${JITTER} min)..."

anydown --watch \
  --watch-interval "${INTERVAL}" \
  --watch-jitter "${JITTER}"
