"""
SQLite storage for Postline (data/postline.db).

items  - one row per video (album or manual upload) and its whole lifecycle
events - human-readable activity feed ("Reel posted", "Approved", ...)

Item statuses (match the UI's vocabulary):
  baseline   - was in the album before Postline started; never shown or posted
  detected   - found, downloading / caption being written
  review     - waiting for the creator's OK
  scheduled  - approved for a time in the future
  posting    - being published right now
  posted / partial / failed / rejected / notvideo
Per-destination status (d_reel / d_story): pending | posting | ok | fail | off
"""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "postline.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

ITEM_COLS = {
    "id": "TEXT PRIMARY KEY",
    "source": "TEXT",              # album | upload
    "title": "TEXT",
    "file": "TEXT",
    "base_url": "TEXT",            # Google Photos base URL (album items)
    "dur": "REAL DEFAULT 0",
    "w": "INTEGER DEFAULT 0",
    "h": "INTEGER DEFAULT 0",
    "size_bytes": "INTEGER DEFAULT 0",
    "recorded_at": "TEXT",
    "status": "TEXT",
    "cap": "TEXT DEFAULT 'generating'",   # generating | ok | failed
    "caption": "TEXT DEFAULT ''",
    "tags": "TEXT DEFAULT '[]'",
    "loc": "TEXT",
    "home": "INTEGER DEFAULT 0",
    "summary": "TEXT DEFAULT ''",
    "model": "TEXT",
    "gen_time": "TEXT",
    "cap_err": "TEXT",
    "reel": "INTEGER DEFAULT 1",
    "story": "INTEGER DEFAULT 1",
    "sched_at": "TEXT",
    "step": "INTEGER DEFAULT 0",
    "d_reel": "TEXT DEFAULT 'pending'",
    "d_story": "TEXT DEFAULT 'pending'",
    "reel_id": "TEXT",
    "reel_url": "TEXT",
    "story_id": "TEXT",
    "story_at": "TEXT",
    "err_t": "TEXT",
    "err_x": "TEXT",
    "attempts": "INTEGER DEFAULT 0",
    "posted_at": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        cols = ", ".join(f"{k} {v}" for k, v in ITEM_COLS.items())
        _conn.execute(f"CREATE TABLE IF NOT EXISTS items ({cols})")
        _conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, item_id TEXT,
            text TEXT, kind TEXT)""")
        # add columns introduced after the table was first created
        have = {r["name"] for r in _conn.execute("PRAGMA table_info(items)")}
        for k, v in ITEM_COLS.items():
            if k not in have:
                _conn.execute(f"ALTER TABLE items ADD COLUMN {k} {v.replace('PRIMARY KEY', '')}")
        _conn.commit()
    return _conn


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["reel"], d["story"], d["home"] = bool(d["reel"]), bool(d["story"]), bool(d["home"])
    return d


def get(item_id: str) -> dict | None:
    with _lock:
        return _row(conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def all_items() -> list[dict]:
    with _lock:
        return [_row(r) for r in conn().execute(
            "SELECT * FROM items WHERE status != 'baseline' ORDER BY COALESCE(recorded_at, created_at) DESC")]


def ids() -> set[str]:
    with _lock:
        return {r["id"] for r in conn().execute("SELECT id FROM items")}


def count() -> int:
    with _lock:
        return conn().execute("SELECT COUNT(*) FROM items").fetchone()[0]


def insert(item: dict) -> None:
    item = {**item}
    item.setdefault("created_at", now_iso())
    item["updated_at"] = now_iso()
    if "tags" in item:
        item["tags"] = json.dumps(item["tags"])
    keys = [k for k in item if k in ITEM_COLS]
    with _lock:
        conn().execute(
            f"INSERT OR IGNORE INTO items ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
            [item[k] for k in keys])
        conn().commit()


def update(item_id: str, **fields) -> None:
    fields["updated_at"] = now_iso()
    if "tags" in fields:
        fields["tags"] = json.dumps(fields["tags"])
    for b in ("reel", "story", "home"):
        if b in fields and fields[b] is not None:
            fields[b] = int(bool(fields[b]))
    keys = [k for k in fields if k in ITEM_COLS and k != "id"]
    with _lock:
        conn().execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in keys)} WHERE id=?",
                       [fields[k] for k in keys] + [item_id])
        conn().commit()


def due_scheduled(now: str) -> list[dict]:
    with _lock:
        return [_row(r) for r in conn().execute(
            "SELECT * FROM items WHERE status='scheduled' AND sched_at <= ?", (now,))]


def add_event(item_id: str | None, text: str, kind: str = "info") -> None:
    with _lock:
        conn().execute("INSERT INTO events (ts, item_id, text, kind) VALUES (?,?,?,?)",
                       (now_iso(), item_id, text, kind))
        conn().commit()


def events(limit: int = 50, item_id: str | None = None) -> list[dict]:
    with _lock:
        if item_id:
            rows = conn().execute("SELECT * FROM events WHERE item_id=? ORDER BY id", (item_id,))
        else:
            rows = conn().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
