import sqlite3
from typing import Optional, List, Dict


class Database:
    def __init__(self, db_path: str = "queue.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS queue (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    url       TEXT NOT NULL,
                    title     TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO settings (key, value) VALUES ('sr_enabled', '1');
            """)

    # --- Queue ---

    def add_song(self, url: str, title: str, requester: str) -> int:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO queue (url, title, requester) VALUES (?, ?, ?)",
                (url, title, requester),
            )
            return conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    def get_queue(self) -> List[Dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM queue ORDER BY id ASC")]

    def get_first(self) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM queue ORDER BY id ASC LIMIT 1").fetchone()
            return dict(row) if row else None

    def remove_first(self) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM queue ORDER BY id ASC LIMIT 1").fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
            return dict(row)

    def remove_by_id(self, song_id: int) -> bool:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM queue WHERE id = ?", (song_id,))
            return c.rowcount > 0

    def get_last_by_requester(self, requester: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM queue WHERE LOWER(requester) = LOWER(?) ORDER BY id DESC LIMIT 1",
                (requester,),
            ).fetchone()
            return dict(row) if row else None

    def clear_queue(self) -> int:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM queue")
            return c.rowcount

    # --- Settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
