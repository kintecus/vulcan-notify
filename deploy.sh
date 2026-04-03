#!/bin/bash
set -euo pipefail

TOOLS_HOST="${TOOLS_HOST:-tools.dwelf-forel.ts.net}"

echo "Deploying to $TOOLS_HOST..."
ssh "root@$TOOLS_HOST" "cd /opt/vulcan-notify && git pull origin main && docker compose up -d --build"
echo "Done."
