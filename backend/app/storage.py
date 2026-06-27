"""Постійне сховище маршрутів (SQLite).

Замість зберігання маршрутів у пам'яті (втрачались при рестарті) тримаємо їх
у SQLite. Кожен маршрут прив'язаний до chat_id користувача.
"""
import json
import sqlite3
import threading
from typing import Optional

from . import config

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def init() -> None:
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS routes (
            key         TEXT PRIMARY KEY,
            chat_id     INTEGER NOT NULL,
            from_id     TEXT NOT NULL,
            from_name   TEXT NOT NULL,
            to_id       TEXT NOT NULL,
            to_name     TEXT NOT NULL,
            date        TEXT NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1,
            wagon_filter TEXT DEFAULT '',
            snapshot    TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT DEFAULT (datetime('now'))
        )
        """
    )
    _conn.commit()


def route_key(chat_id: int, from_id: str, to_id: str, date: str) -> str:
    return f"{chat_id}_{from_id}_{to_id}_{date}"


def list_routes(chat_id: Optional[int] = None) -> list[dict]:
    with _lock:
        if chat_id is None:
            rows = _conn.execute("SELECT * FROM routes ORDER BY created_at").fetchall()
        else:
            rows = _conn.execute(
                "SELECT * FROM routes WHERE chat_id = ? ORDER BY created_at", (chat_id,)
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_routes(chat_id: int) -> int:
    with _lock:
        row = _conn.execute(
            "SELECT COUNT(*) AS c FROM routes WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row["c"]


def get_route(key: str) -> Optional[dict]:
    with _lock:
        row = _conn.execute("SELECT * FROM routes WHERE key = ?", (key,)).fetchone()
    return _row_to_dict(row) if row else None


def add_route(
    chat_id: int, from_id: str, from_name: str,
    to_id: str, to_name: str, date: str, wagon_filter: str = "",
) -> tuple[bool, str]:
    key = route_key(chat_id, from_id, to_id, date)
    with _lock:
        exists = _conn.execute("SELECT 1 FROM routes WHERE key = ?", (key,)).fetchone()
        if exists:
            return False, "Такий маршрут вже додано."
        cnt = _conn.execute(
            "SELECT COUNT(*) AS c FROM routes WHERE chat_id = ?", (chat_id,)
        ).fetchone()["c"]
        if cnt >= config.MAX_ROUTES:
            return False, f"Максимум {config.MAX_ROUTES} маршрутів."
        _conn.execute(
            """INSERT INTO routes
               (key, chat_id, from_id, from_name, to_id, to_name, date, wagon_filter)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, chat_id, from_id, from_name, to_id, to_name, date, wagon_filter),
        )
        _conn.commit()
    return True, key


def delete_route(key: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM routes WHERE key = ?", (key,))
        _conn.commit()


def set_active(key: str, active: bool) -> None:
    with _lock:
        _conn.execute(
            "UPDATE routes SET active = ? WHERE key = ?", (1 if active else 0, key)
        )
        _conn.commit()


def save_snapshot(key: str, snapshot: dict) -> None:
    with _lock:
        _conn.execute(
            "UPDATE routes SET snapshot = ? WHERE key = ?",
            (json.dumps(snapshot, ensure_ascii=False), key),
        )
        _conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["active"] = bool(d["active"])
    try:
        d["snapshot"] = json.loads(d.get("snapshot") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["snapshot"] = {}
    return d
