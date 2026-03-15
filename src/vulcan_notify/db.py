"""SQLite storage for tracking seen items and deduplication."""

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_type, item_id)
);

CREATE TABLE IF NOT EXISTS poll_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    async def is_new_or_changed(self, item_type: str, item_id: str, item_hash: str) -> bool:
        """Check if an item is new or has changed since last seen.

        Returns True if the item should trigger a notification.
        """
        cursor = await self.db.execute(
            "SELECT item_hash FROM seen_items WHERE item_type = ? AND item_id = ?",
            (item_type, item_id),
        )
        row = await cursor.fetchone()

        if row is None:
            # New item
            await self.db.execute(
                "INSERT INTO seen_items (item_type, item_id, item_hash) VALUES (?, ?, ?)",
                (item_type, item_id, item_hash),
            )
            await self.db.commit()
            return True

        if row[0] != item_hash:
            # Changed item
            await self.db.execute(
                "UPDATE seen_items SET item_hash = ?, last_seen = CURRENT_TIMESTAMP "
                "WHERE item_type = ? AND item_id = ?",
                (item_hash, item_type, item_id),
            )
            await self.db.commit()
            return True

        # Already seen, no change
        return False

    async def mark_seen(self, item_type: str, item_id: str, item_hash: str) -> None:
        """Mark an item as seen (upsert)."""
        await self.db.execute(
            "INSERT INTO seen_items (item_type, item_id, item_hash) VALUES (?, ?, ?) "
            "ON CONFLICT(item_type, item_id) DO UPDATE SET "
            "item_hash = excluded.item_hash, last_seen = CURRENT_TIMESTAMP",
            (item_type, item_id, item_hash),
        )
        await self.db.commit()

    async def get_state(self, key: str) -> str | None:
        """Get a poll state value."""
        cursor = await self.db.execute("SELECT value FROM poll_state WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_state(self, key: str, value: str) -> None:
        """Set a poll state value."""
        await self.db.execute(
            "INSERT INTO poll_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )
        await self.db.commit()
