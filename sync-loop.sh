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
# The container clock stays UTC (every DB stamp is a naive datetime.now(), so moving
# it would reinterpret every existing row). Only the quiet window is read in local
# time -- on UTC it silently ran 02:00-07:00 local, leaving the morning schedule five
# hours stale. Must match QUIET_HOURS_TZ in config.py, which does the same conversion.
QUIET_HOURS_TZ="${QUIET_HOURS_TZ:-Europe/Warsaw}"

# Consecutive failures are counted and logged loudly. We deliberately do NOT exit
# on failure: a restart loop would lose the in-container retry cadence and Vulcan
# outages are usually transient. The durable alert comes from /api/health going
# 503, which pve-healthcheck picks up out-of-band.
consecutive_failures=0

echo "[sync-loop] interval=${POLL_INTERVAL}s quiet=${QUIET_HOURS_START}:00-${QUIET_HOURS_END}:00 ${QUIET_HOURS_TZ}"

while true; do
    hour=$(TZ="$QUIET_HOURS_TZ" date '+%-H')
    if [ "$hour" -ge "$QUIET_HOURS_START" ] && [ "$hour" -lt "$QUIET_HOURS_END" ]; then
        target_epoch=$(TZ="$QUIET_HOURS_TZ" date -d "today ${QUIET_HOURS_END}:00" '+%s')
        now_epoch=$(date '+%s')
        sleep_for=$((target_epoch - now_epoch))
        if [ "$sleep_for" -lt 60 ]; then
            sleep_for=60
        fi
        # Nap in poll-interval chunks rather than one long sleep, publishing the
        # retained MQTT heartbeat between chunks. The HA sensor on school/status
        # carries expire_after: 2400, so a single five-hour sleep aged the entity out
        # to `unavailable`, wiped its `ts` attribute, and left the dashboard tile
        # reading "never synced" every night. Staying audible while deliberately idle
        # is the whole point of a heartbeat.
        if [ "$sleep_for" -gt "$POLL_INTERVAL" ]; then
            echo "[sync-loop] Quiet hours until ${QUIET_HOURS_END}:00 (${sleep_for}s), heartbeat every ${POLL_INTERVAL}s..."
            sleep "$POLL_INTERVAL"
            uv run vulcan-notify heartbeat \
                || echo "[sync-loop] WARN quiet-hours heartbeat failed" >&2
        else
            echo "[sync-loop] Quiet hours, sleeping ${sleep_for}s until ${QUIET_HOURS_END}:00..."
            sleep "$sleep_for"
        fi
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
