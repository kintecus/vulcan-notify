#!/bin/bash
set -euo pipefail

# LXC 103 is not a tailnet node, so it is reached through the PVE host rather
# than addressed directly. The old tools.dwelf-forel.ts.net default did not
# resolve; only pve and homepage are tailnet nodes.
PVE_HOST="${PVE_HOST:-pve.dwelf-forel.ts.net}"
CTID="${CTID:-103}"

echo "Deploying to LXC $CTID via $PVE_HOST..."
ssh "root@$PVE_HOST" \
    "pct exec $CTID -- sh -lc 'cd /opt/vulcan-notify && git pull origin main && docker compose up -d --build --remove-orphans'"
echo "Done."
