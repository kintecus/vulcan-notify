#!/bin/bash
set -e

POLL_INTERVAL="${POLL_INTERVAL:-1800}"
QUIET_HOURS_START="${QUIET_HOURS_START:-0}"
QUIET_HOURS_END="${QUIET_HOURS_END:-5}"
API_PORT="${API_PORT:-8585}"

# Start API server in background
echo "[entrypoint] Starting API server on port ${API_PORT}"
uv run python -m vulcan_notify.api &

echo "[entrypoint] vulcan-notify sync loop (interval: ${POLL_INTERVAL}s, quiet: ${QUIET_HOURS_START}:00-${QUIET_HOURS_END}:00)"

while true; do
    hour=$(date '+%-H')
    if [ "$hour" -ge "$QUIET_HOURS_START" ] && [ "$hour" -lt "$QUIET_HOURS_END" ]; then
        target_epoch=$(date -d "today ${QUIET_HOURS_END}:00" '+%s')
        now_epoch=$(date '+%s')
        sleep_for=$((target_epoch - now_epoch))
        if [ "$sleep_for" -lt 60 ]; then
            sleep_for=60
        fi
        echo "[entrypoint] Quiet hours, sleeping ${sleep_for}s until ${QUIET_HOURS_END}:00..."
        sleep "$sleep_for"
        continue
    fi

    echo "[entrypoint] $(date '+%Y-%m-%d %H:%M:%S') Running sync..."
    uv run vulcan-notify sync || echo "[entrypoint] sync exited with code $?"
    echo "[entrypoint] Sleeping ${POLL_INTERVAL}s..."
    sleep "$POLL_INTERVAL"
done
