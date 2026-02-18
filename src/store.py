"""
Store — SQLite-backed deduplication store and message update tracking.
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("hntldr.store")


class Store:
    def __init__(self, db_path: str = "hntldr.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posted_items (
                    hn_id TEXT PRIMARY KEY,
                    title TEXT,
                    posted_at TEXT NOT NULL,
                    score INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_posted_at
                ON posted_items(posted_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_updates (
                    hn_id TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    title TEXT,
                    hook TEXT,
                    url TEXT,
                    score INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    posted_at TEXT NOT NULL,
                    last_updated_at TEXT,
                    update_count INTEGER DEFAULT 0,
                    phase TEXT DEFAULT '10min',
                    next_update_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_next_update_at
                ON message_updates(next_update_at)
            """)
            conn.commit()

        logger.debug(f"Store initialized at {self.db_path}")

    def has_been_posted(self, hn_id: str) -> bool:
        """Check if an HN item has already been posted."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posted_items WHERE hn_id = ?", (str(hn_id),)
            ).fetchone()
            return row is not None

    def mark_posted(self, hn_id: str, title: str = "", score: int = 0):
        """Mark an HN item as posted."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO posted_items (hn_id, title, posted_at, score)
                   VALUES (?, ?, ?, ?)""",
                (str(hn_id), title, datetime.now(timezone.utc).isoformat(), score)
            )
            conn.commit()
        logger.debug(f"Marked {hn_id} as posted: {title[:50]}")

    def prune_old(self, days: int = 30):
        """Remove entries older than N days to keep DB small."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._get_conn() as conn:
            deleted = conn.execute(
                "DELETE FROM posted_items WHERE posted_at < ?", (cutoff,)
            ).rowcount
            conn.commit()
        if deleted:
            logger.info(f"Pruned {deleted} old entries from store")

    # --- Message update tracking ---

    def add_update_task(self, hn_id: str, message_id: int, chat_id: str,
                        title: str, hook: str, url: str,
                        score: int, comments: int):
        """Register a posted message for live score/comment updates."""
        now = datetime.now(timezone.utc)
        next_update = (now + timedelta(minutes=10)).isoformat()
        expires = (now + timedelta(hours=3)).isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO message_updates
                (hn_id, message_id, chat_id, title, hook, url,
                 score, comments, posted_at, last_updated_at, update_count,
                 phase, next_update_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '10min', ?, ?)
            """, (hn_id, message_id, chat_id, title, hook, url,
                  score, comments, now.isoformat(), now.isoformat(),
                  next_update, expires))
            conn.commit()
        logger.debug(f"Added update task for {hn_id}")

    def get_next_update_task(self) -> dict | None:
        """Get the next message that needs updating."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM message_updates
                WHERE next_update_at <= ? AND expires_at > ?
                ORDER BY next_update_at
                LIMIT 1
            """, (now, now)).fetchone()
            return dict(row) if row else None

    def advance_update_task(self, hn_id: str, new_score: int, new_comments: int):
        """Advance the update schedule or expire the task."""
        now = datetime.now(timezone.utc)
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT update_count, phase, expires_at FROM message_updates WHERE hn_id = ?",
                (hn_id,)
            ).fetchone()
            if not row:
                return

            update_count = row["update_count"] + 1
            phase = row["phase"]
            expires_at = row["expires_at"]

            # After 3 updates in 10min phase, switch to 30min
            if phase == "10min" and update_count >= 3:
                phase = "30min"

            interval = timedelta(minutes=10) if phase == "10min" else timedelta(minutes=30)
            next_update = (now + interval).isoformat()

            # If next update would be past expiry, delete
            if next_update >= expires_at:
                conn.execute("DELETE FROM message_updates WHERE hn_id = ?", (hn_id,))
            else:
                conn.execute("""
                    UPDATE message_updates
                    SET score = ?, comments = ?, update_count = ?,
                        phase = ?, next_update_at = ?, last_updated_at = ?
                    WHERE hn_id = ?
                """, (new_score, new_comments, update_count,
                      phase, next_update, now.isoformat(), hn_id))
            conn.commit()

    def remove_expired_updates(self):
        """Clean up expired update tasks."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            deleted = conn.execute(
                "DELETE FROM message_updates WHERE expires_at <= ?", (now,)
            ).rowcount
            conn.commit()
        if deleted:
            logger.debug(f"Removed {deleted} expired update tasks")
