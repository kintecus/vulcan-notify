#!/bin/bash
set -e

POLL_INTERVAL="${POLL_INTERVAL:-300}"
API_PORT="${API_PORT:-8585}"

# Start API server in background
echo "[entrypoint] Starting API server on port ${API_PORT}"
uv run python -m vulcan_notify.api &

echo "[entrypoint] vulcan-notify sync loop (interval: ${POLL_INTERVAL}s)"

while true; do
    echo "[entrypoint] $(date '+%Y-%m-%d %H:%M:%S') Running sync..."
    uv run vulcan-notify sync || echo "[entrypoint] sync exited with code $?"
    echo "[entrypoint] Sleeping ${POLL_INTERVAL}s..."
    sleep "$POLL_INTERVAL"
done
