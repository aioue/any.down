#!/bin/sh
set -e

# Ensure session file exists (avoids Docker mounting it as a directory)
touch /app/session/session.json

echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting any.down sync..."
anydown --quiet
echo "$(date '+%Y-%m-%d %H:%M:%S') - Sync complete."
