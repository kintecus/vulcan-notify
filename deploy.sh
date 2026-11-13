#!/bin/bash
set -euo pipefail

# Deploy now instead of waiting for the 5-minute timer, by starting the same
# vulcan-deploy.service the timer starts. One deploy path means one set of
# behaviour (build before recreate, rollback on a failed build, ntfy report), and
# systemd never runs a oneshot twice at once, so this cannot race the timer.
#
# LXC 103 is not a tailnet node, so it is reached through the PVE host rather
# than addressed directly. The old tools.dwelf-forel.ts.net default did not
# resolve; only pve and homepage are tailnet nodes.
PVE_HOST="${PVE_HOST:-pve.dwelf-forel.ts.net}"
CTID="${CTID:-103}"

echo "Deploying to LXC $CTID via $PVE_HOST..."
ssh "root@$PVE_HOST" "pct exec $CTID -- sh -s" <<'EOF'
since=$(date '+%Y-%m-%d %H:%M:%S')
rc=0
systemctl start vulcan-deploy.service || rc=$?
journalctl -u vulcan-deploy.service --since "$since" -o cat --no-pager | grep -E '^\[deploy\]' \
    || echo "[deploy] Nothing new: already at $(git -C /opt/vulcan-notify rev-parse --short HEAD)"
exit "$rc"
EOF
