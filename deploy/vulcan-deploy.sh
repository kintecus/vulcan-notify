#!/bin/bash
set -euo pipefail

DEPLOY_DIR="/opt/vulcan-notify"
cd "$DEPLOY_DIR"

# Read ntfy config from .env
NTFY_TOPIC=$(grep -E '^NTFY_TOPIC=' .env 2>/dev/null | cut -d= -f2- || echo "vulcan-notify")
NTFY_SERVER=$(grep -E '^NTFY_SERVER=' .env 2>/dev/null | cut -d= -f2- || echo "https://ntfy.sh")

notify() {
    local title="$1" msg="$2" tags="$3" priority="${4:-default}"
    curl -sf -d "$msg" "$NTFY_SERVER/$NTFY_TOPIC" \
        -H "Title: $title" -H "Tags: $tags" -H "Priority: $priority" >/dev/null 2>&1 || true
}

git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

SHORT_SHA=$(echo "$REMOTE" | cut -c1-7)
echo "[deploy] New changes detected: ${LOCAL:0:7} -> $SHORT_SHA"

# Advance the working tree so the build context carries the new code, but be ready
# to undo it. If the build fails, roll HEAD back to $LOCAL: the timer's gate is
# "HEAD == origin/main", so leaving HEAD advanced after a failed build would make
# every later run see them equal and silently no-op forever — old container, new
# HEAD, no retry. Rolling back keeps HEAD != REMOTE so the next run retries.
if ! git pull origin main --quiet; then
    notify "Deploy failed" "vulcan-notify git pull FAILED ${LOCAL:0:7} -> $SHORT_SHA" "x,warning" "high"
    echo "[deploy] FAILED: git pull error" >&2
    exit 1
fi

# Build the image WITHOUT touching the running container yet. Only recreate the
# container if the build succeeds, so a broken build never takes the service down.
# (`docker compose build` has no --quiet-pull; that flag is up/pull-only.)
if docker compose build 2>&1; then
    # --remove-orphans matters: the single `vulcan-notify` service was split into
    # `vulcan-api` and `vulcan-sync`, and without it the old container keeps port
    # 8585 bound so the new API never starts.
    docker compose up -d --quiet-pull --remove-orphans 2>&1
    notify "Deploy success" "vulcan-notify deployed: $SHORT_SHA" "white_check_mark"
    echo "[deploy] Success: $SHORT_SHA"
else
    # Build failed: revert the working tree so the deploy gate stays open and the
    # next timer run retries instead of going dormant on un-deployed code.
    git reset --hard "$LOCAL" --quiet
    notify "Deploy failed" "vulcan-notify build FAILED at $SHORT_SHA (rolled back to ${LOCAL:0:7}, will retry)" "x,warning" "high"
    echo "[deploy] FAILED: build error at $SHORT_SHA, rolled back to ${LOCAL:0:7}" >&2
    exit 1
fi
