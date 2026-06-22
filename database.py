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
                -- Clip-Queue: eingereichte Twitch-Clips (FIFO), getrennt von der
                -- Song-Queue. Wird im Overlay nacheinander abgespielt.
                CREATE TABLE IF NOT EXISTS clip_queue (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug      TEXT NOT NULL,
                    url       TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    title     TEXT,
                    duration  REAL DEFAULT 0,
                    added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                -- Verlosung (Channel-Points-Raffle): jede Einloesung der
                -- Raffle-Belohnung ist EIN Los. Mehrfach-Lose erlaubt (mehrere
                -- Zeilen pro Nutzer). redemption_id ist eindeutig, damit dasselbe
                -- Event nie doppelt zaehlt (Reconnect/Reconcile).
                CREATE TABLE IF NOT EXISTS raffle (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    redemption_id TEXT UNIQUE NOT NULL,
                    reward_id     TEXT NOT NULL,
                    user_id       TEXT NOT NULL,
                    user_login    TEXT NOT NULL,
                    user_name     TEXT NOT NULL,
                    added_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO settings (key, value) VALUES ('sr_enabled', '1');
                INSERT OR IGNORE INTO settings (key, value) VALUES ('clip_enabled', '1');
                INSERT OR IGNORE INTO settings (key, value) VALUES ('raffle_open', '0');
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
        # clip_queue: title/duration nachrüsten (Tabelle evtl. älter als das Feature).
        clip_cols = {r["name"] for r in conn.execute("PRAGMA table_info(clip_queue)")}
        if clip_cols:  # Tabelle existiert bereits
            if "title" not in clip_cols:
                conn.execute("ALTER TABLE clip_queue ADD COLUMN title TEXT")
            if "duration" not in clip_cols:
                conn.execute("ALTER TABLE clip_queue ADD COLUMN duration REAL DEFAULT 0")

    # --- Queue ---

    def add_song(self, url: str, title: str, requester: str):
        """Song hinzufügen. Gibt (song_id, position) zurück.

        Der neue Song wird ans Ende der Warteschlange gehängt, sodass mehrere
        Requests in der Reihenfolge ihres Eingangs (FIFO) nacheinander spielen.
        Zusätzlich wird er dauerhaft in der Streamliste protokolliert.
        """
        with self._conn() as conn:
            keys = [
                r["sort_key"]
                for r in conn.execute(
                    "SELECT sort_key FROM queue ORDER BY sort_key ASC, id ASC"
                )
            ]
            # FIFO: ans Ende der Warteschlange (frueher wurde zwischen Key 0 und 1
            # eingefuegt, was mehrere Requests rueckwaerts sortierte).
            new_key = (max(keys) + 1.0) if keys else 0.0

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

    def get_by_yt_item_id(self, item_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM queue WHERE yt_item_id = ? LIMIT 1", (item_id,)
            ).fetchone()
            return dict(row) if row else None

    def remove_by_yt_item_id(self, item_id: str) -> bool:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM queue WHERE yt_item_id = ?", (item_id,))
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

    # --- Clip-Queue ---

    def clip_exists(self, slug: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM clip_queue WHERE slug = ? LIMIT 1", (slug,)
            ).fetchone()
            return row is not None

    def add_clip(self, slug: str, url: str, requester: str,
                 title: str = None, duration: float = 0):
        """Clip ans Ende der Queue haengen. Gibt (clip_id, position) zurueck."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO clip_queue (slug, url, requester, title, duration) "
                "VALUES (?, ?, ?, ?, ?)",
                (slug, url, requester, title, duration),
            )
            clip_id = cur.lastrowid
            position = conn.execute(
                "SELECT COUNT(*) FROM clip_queue"
            ).fetchone()[0]
            return clip_id, position

    def get_clips(self) -> List[Dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM clip_queue ORDER BY id ASC")
            ]

    def get_first_clip(self) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM clip_queue ORDER BY id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def remove_clip_by_id(self, clip_id: int) -> bool:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM clip_queue WHERE id = ?", (clip_id,))
            return c.rowcount > 0

    def remove_last_clip_by_requester(self, requester: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM clip_queue WHERE LOWER(requester) = LOWER(?) "
                "ORDER BY id DESC LIMIT 1",
                (requester,),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM clip_queue WHERE id = ?", (row["id"],))
            return dict(row)

    def clear_clips(self) -> int:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM clip_queue")
            return c.rowcount

    # --- Verlosung (Channel-Points-Raffle) ---

    def add_raffle_entry(self, redemption_id: str, reward_id: str,
                         user_id: str, user_login: str, user_name: str) -> bool:
        """Ein Los hinzufuegen. Doppelte redemption_id werden ignoriert.

        Gibt True zurueck, wenn ein neues Los eingetragen wurde.
        """
        with self._conn() as conn:
            c = conn.execute(
                "INSERT OR IGNORE INTO raffle "
                "(redemption_id, reward_id, user_id, user_login, user_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (redemption_id, reward_id, user_id, user_login, user_name),
            )
            return c.rowcount > 0

    def get_raffle_entries(self) -> List[Dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM raffle ORDER BY id ASC")
            ]

    def raffle_entry_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM raffle").fetchone()[0]

    def raffle_unique_count(self) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM raffle"
            ).fetchone()[0]

    def get_raffle_redemption_ids(self) -> List[str]:
        with self._conn() as conn:
            return [
                r["redemption_id"]
                for r in conn.execute("SELECT redemption_id FROM raffle")
            ]

    def clear_raffle(self) -> int:
        with self._conn() as conn:
            c = conn.execute("DELETE FROM raffle")
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
