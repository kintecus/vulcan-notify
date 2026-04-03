#!/bin/bash
set -e

POLL_INTERVAL="${POLL_INTERVAL:-300}"

echo "[entrypoint] vulcan-notify sync loop (interval: ${POLL_INTERVAL}s)"

while true; do
    echo "[entrypoint] $(date '+%Y-%m-%d %H:%M:%S') Running sync..."
    uv run vulcan-notify sync || echo "[entrypoint] sync exited with code $?"
    echo "[entrypoint] Sleeping ${POLL_INTERVAL}s..."
    sleep "$POLL_INTERVAL"
done
