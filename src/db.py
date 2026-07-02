"""Async SQLite connection manager.

Provides a single shared connection in WAL mode for concurrent reads from
the API while the engine writes.  The database file lives on the shared
``india-data`` Docker volume so both engine and (future) API containers
can access it.
"""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

_DB_DIR = Path(os.environ.get("INDIA_DATA_DIR", "/app/data"))
_DB_PATH = _DB_DIR / "india_db.sqlite3"

_conn: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the singleton async SQLite connection, creating it on first call."""
    global _conn
    if _conn is None:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _conn = await aiosqlite.connect(str(_DB_PATH))
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
