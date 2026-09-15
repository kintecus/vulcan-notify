#!/bin/bash
# Sync loop. Runs as PID 1 of the vulcan-sync container.
#
# Previously this also forked the API into the background, which meant an API
# crash left bash alive and `restart: unless-stopped` never fired. The API is now
# its own compose service; this script does one job.
set -euo pipefail

POLL_INTERVAL="${POLL_INTERVAL:-1800}"
QUIET_HOURS_START="${QUIET_HOURS_START:-0}"
QUIET_HOURS_END="${QUIET_HOURS_END:-5}"

# Consecutive failures are counted and logged loudly. We deliberately do NOT exit
# on failure: a restart loop would lose the in-container retry cadence and Vulcan
# outages are usually transient. The durable alert comes from /api/health going
# 503, which pve-healthcheck picks up out-of-band.
consecutive_failures=0

echo "[sync-loop] interval=${POLL_INTERVAL}s quiet=${QUIET_HOURS_START}:00-${QUIET_HOURS_END}:00"

while true; do
    hour=$(date '+%-H')
    if [ "$hour" -ge "$QUIET_HOURS_START" ] && [ "$hour" -lt "$QUIET_HOURS_END" ]; then
        target_epoch=$(date -d "today ${QUIET_HOURS_END}:00" '+%s')
        now_epoch=$(date '+%s')
        sleep_for=$((target_epoch - now_epoch))
        if [ "$sleep_for" -lt 60 ]; then
            sleep_for=60
        fi
        echo "[sync-loop] Quiet hours, sleeping ${sleep_for}s until ${QUIET_HOURS_END}:00..."
        sleep "$sleep_for"
        continue
    fi

    echo "[sync-loop] $(date '+%Y-%m-%d %H:%M:%S') Running sync..."
    if uv run vulcan-notify sync; then
        if [ "$consecutive_failures" -gt 0 ]; then
            echo "[sync-loop] Recovered after ${consecutive_failures} consecutive failure(s)"
        fi
        consecutive_failures=0
    else
        code=$?
        consecutive_failures=$((consecutive_failures + 1))
        echo "[sync-loop] ERROR sync exited ${code} (${consecutive_failures} consecutive)" >&2
    fi

    echo "[sync-loop] Sleeping ${POLL_INTERVAL}s..."
    sleep "$POLL_INTERVAL"
done
