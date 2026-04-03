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

if git pull origin main --quiet && docker compose up -d --build --quiet-pull 2>&1; then
    notify "Deploy success" "vulcan-notify deployed: $SHORT_SHA" "white_check_mark"
    echo "[deploy] Success: $SHORT_SHA"
else
    notify "Deploy failed" "vulcan-notify deploy FAILED at $SHORT_SHA" "x,warning" "high"
    echo "[deploy] FAILED at $SHORT_SHA" >&2
    exit 1
fi
