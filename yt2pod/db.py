"""SQLite storage for subscriptions and downloaded episodes."""

import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    url              TEXT NOT NULL,
    mode             TEXT NOT NULL DEFAULT 'audio',
    audio_quality    INTEGER NOT NULL DEFAULT 192,
    video_max_height TEXT NOT NULL DEFAULT '1080',
    numbering        TEXT NOT NULL DEFAULT 'oldest_first',
    name_prefix      TEXT NOT NULL DEFAULT '',
    separator        TEXT NOT NULL DEFAULT ' - ',
    folder           TEXT NOT NULL,
    date_after       TEXT,
    max_items        INTEGER,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_checked     TEXT,
    last_status      TEXT,
    last_error       TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    video_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    upload_date     TEXT,
    duration        INTEGER,
    thumbnail       TEXT,
    webpage_url     TEXT,
    filepath        TEXT NOT NULL,
    filesize        INTEGER,
    mime            TEXT,
    downloaded_at   TEXT NOT NULL,
    UNIQUE (subscription_id, video_id)
);
"""

SUBSCRIPTION_FIELDS = (
    "name", "url", "mode", "audio_quality", "video_max_height", "numbering",
    "name_prefix", "separator", "folder", "date_after", "max_items", "enabled",
)

_path = None
_local = threading.local()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(path):
    global _path
    _path = str(path)
    with connect() as conn:
        conn.executescript(SCHEMA)


def connect():
    """Return a per-thread connection (the worker thread and web threads each get their own)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


# --- subscriptions -----------------------------------------------------------

def list_subscriptions():
    return connect().execute(
        """SELECT s.*, (SELECT COUNT(*) FROM episodes e WHERE e.subscription_id = s.id) AS episode_count
           FROM subscriptions s ORDER BY s.name COLLATE NOCASE"""
    ).fetchall()


def get_subscription(sub_id):
    return connect().execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()


def create_subscription(data):
    cols = ", ".join(SUBSCRIPTION_FIELDS) + ", created_at"
    marks = ", ".join("?" for _ in SUBSCRIPTION_FIELDS) + ", ?"
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO subscriptions ({cols}) VALUES ({marks})",
            [data[f] for f in SUBSCRIPTION_FIELDS] + [now_iso()],
        )
        return cur.lastrowid


def update_subscription(sub_id, data):
    fields = [f for f in SUBSCRIPTION_FIELDS if f in data]
    assignments = ", ".join(f"{f} = ?" for f in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE subscriptions SET {assignments} WHERE id = ?",
            [data[f] for f in fields] + [sub_id],
        )


def set_status(sub_id, status, error=None):
    with connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET last_checked = ?, last_status = ?, last_error = ? WHERE id = ?",
            (now_iso(), status, error, sub_id),
        )


def delete_subscription(sub_id):
    with connect() as conn:
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))


def folder_in_use(folder, exclude_id=None):
    return connect().execute(
        "SELECT 1 FROM subscriptions WHERE folder = ? AND id IS NOT ?", (folder, exclude_id)
    ).fetchone() is not None


def enabled_subscription_ids():
    return [r["id"] for r in connect().execute("SELECT id FROM subscriptions WHERE enabled = 1")]


# --- episodes ----------------------------------------------------------------

def list_episodes(sub_id):
    return connect().execute(
        "SELECT * FROM episodes WHERE subscription_id = ? ORDER BY upload_date DESC, id DESC",
        (sub_id,),
    ).fetchall()


def has_episode(sub_id, video_id):
    return connect().execute(
        "SELECT 1 FROM episodes WHERE subscription_id = ? AND video_id = ?", (sub_id, video_id)
    ).fetchone() is not None


def add_episode(sub_id, ep):
    with connect() as conn:
        conn.execute(
            """INSERT INTO episodes (subscription_id, video_id, title, description, upload_date,
                   duration, thumbnail, webpage_url, filepath, filesize, mime, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (subscription_id, video_id) DO UPDATE SET
                   title = excluded.title, filepath = excluded.filepath,
                   filesize = excluded.filesize, mime = excluded.mime""",
            (sub_id, ep["video_id"], ep["title"], ep.get("description"), ep.get("upload_date"),
             ep.get("duration"), ep.get("thumbnail"), ep.get("webpage_url"), ep["filepath"],
             ep.get("filesize"), ep.get("mime"), now_iso()),
        )
