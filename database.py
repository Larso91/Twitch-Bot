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
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    url        TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    requester  TEXT NOT NULL,
                    sort_key   REAL,
                    yt_item_id TEXT,
                    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                -- Dauerhafte Streamliste: jeder jemals requestete Song,
                -- wächst stätig und wird nie automatisch geleert.
                CREATE TABLE IF NOT EXISTS streamlist (
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
            self._migrate(conn)

    def _migrate(self, conn):
        """Bestehende Datenbanken auf das neue Schema heben."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(queue)")}
        if "sort_key" not in cols:
            conn.execute("ALTER TABLE queue ADD COLUMN sort_key REAL")
        if "yt_item_id" not in cols:
            conn.execute("ALTER TABLE queue ADD COLUMN yt_item_id TEXT")
        # Alte Einträge ohne sort_key in Einfügereihenfolge auffüllen.
        conn.execute(
            "UPDATE queue SET sort_key = id WHERE sort_key IS NULL"
        )

    # --- Queue ---

    def add_song(self, url: str, title: str, requester: str):
        """Song hinzufügen. Gibt (song_id, position) zurück.

        Der neue Song wird direkt hinter dem aktuell laufenden (ersten) Song
        eingereiht, spielt also als Nächstes. Zusätzlich wird er dauerhaft in
        der Streamliste protokolliert.
        """
        with self._conn() as conn:
            keys = [
                r["sort_key"]
                for r in conn.execute(
                    "SELECT sort_key FROM queue ORDER BY sort_key ASC, id ASC"
                )
            ]
            if not keys:
                new_key = 0.0
            elif len(keys) == 1:
                new_key = keys[0] + 1.0
            else:
                # Zwischen aktuellem Song (keys[0]) und dem danach einsortieren.
                new_key = (keys[0] + keys[1]) / 2.0

            cur = conn.execute(
                "INSERT INTO queue (url, title, requester, sort_key) VALUES (?, ?, ?, ?)",
                (url, title, requester, new_key),
            )
            song_id = cur.lastrowid
            conn.execute(
                "INSERT INTO streamlist (url, title, requester) VALUES (?, ?, ?)",
                (url, title, requester),
            )

            # Position des neuen Songs in der aktuellen Queue ermitteln.
            ordered = conn.execute(
                "SELECT sort_key FROM queue ORDER BY sort_key ASC, id ASC"
            ).fetchall()
            position = len(ordered)
            for i, r in enumerate(ordered):
                if r["sort_key"] == new_key:
                    position = i + 1
                    break
            return song_id, position

    def set_yt_item_id(self, song_id: int, item_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE queue SET yt_item_id = ? WHERE id = ?", (item_id, song_id)
            )

    def get_queue(self) -> List[Dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM queue ORDER BY sort_key ASC, id ASC"
                )
            ]

    def get_first(self) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM queue ORDER BY sort_key ASC, id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def remove_first(self) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM queue ORDER BY sort_key ASC, id ASC LIMIT 1"
            ).fetchone()
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

    # --- Streamliste (dauerhaft) ---

    def get_streamlist(self) -> List[Dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM streamlist ORDER BY id ASC")
            ]

    def streamlist_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM streamlist").fetchone()[0]

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
