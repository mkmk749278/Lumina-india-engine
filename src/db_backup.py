"""Nightly SQLite backup — the database is the 30-day quality evidence.

The signal/outcome history is the Phase-2 sign-off artifact (and, once SEBI
RA registration exists, part of the compliance record). Until now it lived
as one file on one Docker volume with no copy anywhere. ``backup_database``
writes a compacted snapshot (``VACUUM INTO``) to ``<data>/backups/`` — same
volume, so it survives container rebuilds and protects against DB
corruption and accidental wipes; copying the newest file off-box is a
one-line owner cron if desired. Retention prunes to the newest
``INDIA_DB_BACKUP_KEEP`` copies.

Called once per day at the session-close transition — never on a hot path.
Best-effort: a failed backup is logged loudly and never disturbs the close
sequence.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import config
from src.db import get_db
from src.utils import get_logger

logger = get_logger("db_backup")


def _backup_dir() -> Path:
    return Path(os.environ.get("INDIA_DATA_DIR", "/app/data")) / "backups"


async def backup_database() -> Path | None:
    """Write today's compacted DB snapshot and prune old ones. Never raises."""
    try:
        backup_dir = _backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(config.IST).date().isoformat()
        target = backup_dir / f"india_db_{stamp}.sqlite3"
        if target.exists():
            target.unlink()  # VACUUM INTO refuses to overwrite

        db = await get_db()
        await db.execute("VACUUM INTO ?", (str(target),))

        backups = sorted(backup_dir.glob("india_db_*.sqlite3"))
        for old in backups[: -config.DB_BACKUP_KEEP]:
            old.unlink()
            logger.info("pruned old DB backup {}", old.name)

        size_kb = target.stat().st_size / 1024
        logger.info("DB backup written: {} ({:.0f} KiB)", target.name, size_kb)
        return target
    except Exception:
        logger.opt(exception=True).error(
            "DB BACKUP FAILED — the quality-window history has no fresh copy"
        )
        return None
