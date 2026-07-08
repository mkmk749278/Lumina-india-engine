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

_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS india_signal_outcomes (
    signal_id   TEXT PRIMARY KEY,
    outcome     TEXT NOT NULL,
    exit_price  REAL NOT NULL,
    points      REAL NOT NULL,
    pct         REAL NOT NULL DEFAULT 0,
    resolved_at TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

_SESSION_SUMMARY_DDL = """
CREATE TABLE IF NOT EXISTS india_session_summary (
    date             TEXT PRIMARY KEY,
    signal_count     INTEGER NOT NULL,
    a_plus_count     INTEGER NOT NULL,
    b_count          INTEGER NOT NULL,
    avg_confidence   REAL NOT NULL,
    total_suppressed INTEGER NOT NULL,
    gates_fired      TEXT NOT NULL,
    tp1_count        INTEGER NOT NULL,
    sl_count         INTEGER NOT NULL,
    expired_count    INTEGER NOT NULL,
    total_points     REAL NOT NULL,
    total_pct        REAL NOT NULL DEFAULT 0,
    avg_pct          REAL NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

# Columns added after the tables first shipped. SQLite has no
# ADD COLUMN IF NOT EXISTS, so we add each only when absent — an existing prod
# DB (with rows) is upgraded in place without dropping history.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "india_signal_outcomes": [("pct", "REAL NOT NULL DEFAULT 0")],
    "india_session_summary": [
        ("total_pct", "REAL NOT NULL DEFAULT 0"),
        ("avg_pct", "REAL NOT NULL DEFAULT 0"),
    ],
}


async def _migrate(db) -> None:  # type: ignore[no-untyped-def]
    for table, columns in _MIGRATIONS.items():
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {str(r[1]) for r in await cursor.fetchall()}
        for name, decl in columns:
            if name not in existing:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {decl}"
                )


async def init_tables() -> None:
    db = await get_db()
    await db.execute(_SIGNALS_DDL)
    await db.execute(_SUPPRESSIONS_DDL)
    await db.execute(_OUTCOMES_DDL)
    await db.execute(_SESSION_SUMMARY_DDL)
    await _migrate(db)
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
        clauses.append("DATE(s.created_at) = ?")
        params.append(date)
    if tier:
        clauses.append("s.tier = ?")
        params.append(tier)
    if setup_class:
        clauses.append("s.setup_class = ?")
        params.append(setup_class)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    cursor = await db.execute(
        f"SELECT {_SIGNAL_WITH_STATUS_SELECT}{where}"
        " ORDER BY s.created_at DESC LIMIT ?",
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# A signal joined to its outcome (if resolved). ``status`` is OPEN until the
# monitor resolves the signal to TP1_HIT / SL_HIT / EXPIRED, so every card can
# show where the trade stands. ``result_points`` / ``result_pct`` are the
# realised, signed result once resolved (NULL while OPEN).
_SIGNAL_WITH_STATUS_SELECT = """
    s.*,
    COALESCE(o.outcome, 'OPEN') AS status,
    o.points     AS result_points,
    o.pct        AS result_pct,
    o.exit_price AS exit_price,
    o.resolved_at AS resolved_at
FROM india_signals s
LEFT JOIN india_signal_outcomes o ON o.signal_id = s.signal_id
"""


async def get_signal_by_id(signal_id: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        f"SELECT {_SIGNAL_WITH_STATUS_SELECT} WHERE s.signal_id = ?",
        (signal_id,),
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
    return int(row[0]) if row else 0


async def insert_outcome(
    signal_id: str,
    outcome: str,
    exit_price: float,
    points: float,
    pct: float,
    resolved_at: datetime,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO india_signal_outcomes
            (signal_id, outcome, exit_price, points, pct, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (signal_id, outcome, exit_price, points, pct, str(resolved_at)),
    )
    await db.commit()


async def get_outcomes(date: str | None = None, limit: int = 100) -> list[dict]:
    """Outcomes joined onto their signals — the quality-window view."""
    db = await get_db()
    where = ""
    params: list[str | int] = []
    if date:
        where = "WHERE DATE(o.created_at) = ?"
        params.append(date)
    params.append(limit)
    cursor = await db.execute(
        f"""
        SELECT o.signal_id, o.outcome, o.exit_price, o.points, o.pct,
               o.resolved_at,
               s.symbol, s.base, s.direction, s.setup_class, s.tier,
               s.entry, s.sl, s.tp1, s.created_at AS emitted_at
        FROM india_signal_outcomes o
        LEFT JOIN india_signals s ON s.signal_id = o.signal_id
        {where}
        ORDER BY o.created_at DESC LIMIT ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def write_session_summary() -> dict:
    """Aggregate today's signals/suppressions/outcomes into one row.

    Called at the session-close transition (idempotent per date — a
    restart after close simply rewrites the same aggregates).
    """
    import json as _json

    db = await get_db()

    cursor = await db.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN tier = 'A+' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN tier = 'B' THEN 1 ELSE 0 END), 0),
               COALESCE(AVG(confidence), 0)
        FROM india_signals WHERE DATE(created_at) = DATE('now', 'localtime')
        """
    )
    sig_row = await cursor.fetchone()
    assert sig_row is not None  # aggregate query always yields one row
    signal_count, a_plus, b_count, avg_conf = (
        int(sig_row[0]),
        int(sig_row[1]),
        int(sig_row[2]),
        float(sig_row[3]),
    )

    cursor = await db.execute(
        """
        SELECT gate_name, COUNT(*) FROM india_suppressions
        WHERE DATE(created_at) = DATE('now', 'localtime')
        GROUP BY gate_name
        """
    )
    gates = {str(r[0]): int(r[1]) for r in await cursor.fetchall()}

    cursor = await db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN outcome = 'TP1_HIT' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'SL_HIT' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'EXPIRED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(points), 0),
            COALESCE(SUM(pct), 0),
            COALESCE(AVG(pct), 0)
        FROM india_signal_outcomes
        WHERE DATE(created_at) = DATE('now', 'localtime')
        """
    )
    oc_row = await cursor.fetchone()
    assert oc_row is not None  # aggregate query always yields one row

    summary = {
        "date": None,  # filled by SQL below
        "signal_count": signal_count,
        "a_plus_count": a_plus,
        "b_count": b_count,
        "avg_confidence": round(avg_conf, 1),
        "total_suppressed": sum(gates.values()),
        "gates_fired": _json.dumps(gates),
        "tp1_count": int(oc_row[0]),
        "sl_count": int(oc_row[1]),
        "expired_count": int(oc_row[2]),
        # total_points kept for continuity, but % is the honest cross-instrument
        # measure — summing points across a 46-base universe is meaningless.
        "total_points": round(float(oc_row[3]), 2),
        "total_pct": round(float(oc_row[4]), 3),
        "avg_pct": round(float(oc_row[5]), 3),
    }

    await db.execute(
        """
        INSERT INTO india_session_summary
            (date, signal_count, a_plus_count, b_count, avg_confidence,
             total_suppressed, gates_fired, tp1_count, sl_count,
             expired_count, total_points, total_pct, avg_pct)
        VALUES (DATE('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            signal_count = excluded.signal_count,
            a_plus_count = excluded.a_plus_count,
            b_count = excluded.b_count,
            avg_confidence = excluded.avg_confidence,
            total_suppressed = excluded.total_suppressed,
            gates_fired = excluded.gates_fired,
            tp1_count = excluded.tp1_count,
            sl_count = excluded.sl_count,
            expired_count = excluded.expired_count,
            total_points = excluded.total_points,
            total_pct = excluded.total_pct,
            avg_pct = excluded.avg_pct
        """,
        (
            summary["signal_count"],
            summary["a_plus_count"],
            summary["b_count"],
            summary["avg_confidence"],
            summary["total_suppressed"],
            summary["gates_fired"],
            summary["tp1_count"],
            summary["sl_count"],
            summary["expired_count"],
            summary["total_points"],
            summary["total_pct"],
            summary["avg_pct"],
        ),
    )
    await db.commit()
    return summary


async def get_session_summaries(limit: int = 30) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM india_session_summary ORDER BY date DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_unresolved_signals_today() -> list[dict]:
    """Today's emitted signals with no outcome yet (restart resume)."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT s.* FROM india_signals s
        LEFT JOIN india_signal_outcomes o ON o.signal_id = s.signal_id
        WHERE DATE(s.created_at) = DATE('now', 'localtime')
          AND o.signal_id IS NULL
        """
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]
