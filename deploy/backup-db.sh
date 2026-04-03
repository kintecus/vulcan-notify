#!/bin/bash
set -euo pipefail

DATA_DIR="/opt/vulcan-notify/data"
BACKUP_DIR="$DATA_DIR/backups"
DB_PATH="$DATA_DIR/vulcan_notify.db"
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/vulcan_notify_$(date +%Y%m%d_%H%M%S).db"

# Use SQLite .backup for a consistent copy (safe even during writes)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

echo "[backup] Created $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Remove backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "vulcan_notify_*.db" -mtime +$KEEP_DAYS -delete

REMAINING=$(find "$BACKUP_DIR" -name "vulcan_notify_*.db" | wc -l)
echo "[backup] $REMAINING backups retained"
