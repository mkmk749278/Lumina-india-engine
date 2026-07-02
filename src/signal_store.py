"""SQLite persistence for emitted signals and gate suppressions.

Signals that pass all gates and score above the confidence floor are
written here by the ``IndiaSignalRouter``.  The API server reads from
these tables to serve ``/api/signals`` and ``/api/suppressed``.
"""

from __future__ import annotations

from datetime import datetime

from src.db import get_db
from src.signals.model import IndiaSignal

_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS india_signals (
    signal_id          TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    base               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    setup_class        TEXT NOT NULL,
    entry              REAL NOT NULL,
    sl                 REAL NOT NULL,
    tp1                REAL NOT NULL,
    sl_pct             REAL NOT NULL,
    tp1_pct            REAL NOT NULL,
    rr_ratio           REAL NOT NULL,
    lot_size           INTEGER NOT NULL,
    confidence         REAL NOT NULL,
    tier               TEXT NOT NULL,
    regime_60m         TEXT,
    regime_daily       TEXT,
    htf_trend_aligned  INTEGER NOT NULL DEFAULT 0,
    breakout_volume_ratio REAL,
    setup_reason       TEXT,
    atr_at_entry       REAL,
    vix_at_entry       REAL,
    pcr_at_entry       REAL,
    expiry_date        TEXT,
    days_to_expiry     INTEGER,
    tp2                REAL,
    dispatch_timestamp TEXT NOT NULL,
    suppression_reason TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

_SUPPRESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS india_suppressions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL DEFAULT '',
    base       TEXT NOT NULL,
    gate_name  TEXT NOT NULL,
    reason     TEXT NOT NULL,
    setup_class TEXT NOT NULL DEFAULT '',
    direction  TEXT NOT NULL DEFAULT '',
    scan_time  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


async def init_tables() -> None:
    db = await get_db()
    await db.execute(_SIGNALS_DDL)
    await db.execute(_SUPPRESSIONS_DDL)
    await db.commit()


async def insert_signal(sig: IndiaSignal) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO india_signals (
            signal_id, symbol, base, direction, setup_class,
            entry, sl, tp1, sl_pct, tp1_pct, rr_ratio, lot_size,
            confidence, tier, regime_60m, regime_daily,
            htf_trend_aligned, breakout_volume_ratio, setup_reason,
            atr_at_entry, vix_at_entry, pcr_at_entry,
            expiry_date, days_to_expiry, tp2,
            dispatch_timestamp, suppression_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sig.signal_id,
            sig.symbol,
            sig.base,
            sig.direction,
            sig.setup_class,
            sig.entry,
            sig.sl,
            sig.tp1,
            sig.sl_pct,
            sig.tp1_pct,
            sig.rr_ratio,
            sig.lot_size,
            sig.confidence,
            sig.tier,
            sig.regime_60m.value if hasattr(sig.regime_60m, "value") else str(sig.regime_60m),
            sig.regime_daily.value if hasattr(sig.regime_daily, "value") else str(sig.regime_daily),
            1 if sig.htf_trend_aligned else 0,
            sig.breakout_volume_ratio,
            sig.setup_reason,
            sig.atr_at_entry,
            sig.vix_at_entry,
            sig.pcr_at_entry,
            str(sig.expiry_date) if sig.expiry_date else None,
            sig.days_to_expiry,
            sig.tp2,
            str(sig.dispatch_timestamp),
            sig.suppression_reason,
        ),
    )
    await db.commit()


async def insert_suppression(
    base: str,
    gate_name: str,
    reason: str,
    setup_class: str,
    direction: str,
    scan_time: datetime,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO india_suppressions
            (base, gate_name, reason, setup_class, direction, scan_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (base, gate_name, reason, setup_class, direction, str(scan_time)),
    )
    await db.commit()


async def get_signals(
    date: str | None = None,
    tier: str | None = None,
    setup_class: str | None = None,
    limit: int = 50,
) -> list[dict]:
    db = await get_db()
    clauses: list[str] = []
    params: list[str | int] = []

    if date:
        clauses.append("DATE(created_at) = ?")
        params.append(date)
    if tier:
        clauses.append("tier = ?")
        params.append(tier)
    if setup_class:
        clauses.append("setup_class = ?")
        params.append(setup_class)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    cursor = await db.execute(
        f"SELECT * FROM india_signals{where} ORDER BY created_at DESC LIMIT ?",
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_signal_by_id(signal_id: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM india_signals WHERE signal_id = ?", (signal_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_suppressions(limit: int = 100) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM india_suppressions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_signal_count_today() -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM india_signals WHERE DATE(created_at) = DATE('now', 'localtime')"
    )
    row = await cursor.fetchone()
    return row[0] if row else 0
