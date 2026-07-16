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
    market_direction   TEXT,
    session_phase      TEXT,
    vix_regime         TEXT,
    expiry_date        TEXT,
    days_to_expiry     INTEGER,
    tp2                REAL,
    dispatch_timestamp TEXT NOT NULL,
    suppression_reason TEXT,
    tp1_touched_at     TEXT,
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
    tp1_be_count     INTEGER NOT NULL DEFAULT 0,
    tp2_count        INTEGER NOT NULL DEFAULT 0,
    tp1_expired_count INTEGER NOT NULL DEFAULT 0,
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
    "india_signal_outcomes": [
        ("pct", "REAL NOT NULL DEFAULT 0"),
        # Outcome-ledger truth (Session 21): max favourable / adverse
        # excursion (% of entry, post-trigger), bars walked to resolution,
        # which timeframe resolved it, and whether the resolving candle was
        # an ambiguous both-levels tie (the 5m-walk artifact being measured
        # away). NULL on pre-migration rows.
        ("mfe_pct", "REAL"),
        ("mae_pct", "REAL"),
        ("bars_to_resolve", "INTEGER"),
        ("resolution_tf", "TEXT"),
        ("ambiguous_tie", "INTEGER"),
    ],
    "india_session_summary": [
        ("total_pct", "REAL NOT NULL DEFAULT 0"),
        ("avg_pct", "REAL NOT NULL DEFAULT 0"),
        # A tier added Session 15 (IB14 — A+/A/B were the contract all along).
        ("a_count", "INTEGER NOT NULL DEFAULT 0"),
        # Two-target trade plan (Session 18): TP1-banked outcome breakdown.
        ("tp1_be_count", "INTEGER NOT NULL DEFAULT 0"),
        ("tp2_count", "INTEGER NOT NULL DEFAULT 0"),
        ("tp1_expired_count", "INTEGER NOT NULL DEFAULT 0"),
        # Entry-trigger plan (Session 21): LEVEL entries that never filled.
        ("not_triggered_count", "INTEGER NOT NULL DEFAULT 0"),
    ],
    # Two-target plan: when the runner is armed (TP1 banked) — lets a banked
    # TP1 survive an engine restart instead of silently re-racing SL vs TP1.
    # Market-context stamp (Phase 1): tape regime at emit, for the edge matrix.
    # Session 21: entry-trigger state (entry_type/triggered_at), truth
    # telemetry stamped at emission (extension/bias-age/dup), and the shadow
    # scoring + direction columns (v2 measured alongside v1 before any flip).
    "india_signals": [
        ("tp1_touched_at", "TEXT"),
        ("market_direction", "TEXT"),
        ("session_phase", "TEXT"),
        ("vix_regime", "TEXT"),
        ("entry_type", "TEXT"),
        ("triggered_at", "TEXT"),
        ("extension_vwap_atr", "REAL"),
        ("extension_ema21_atr", "REAL"),
        ("bias_age_min", "REAL"),
        ("dup_index", "INTEGER"),
        ("confidence_v2", "REAL"),
        ("score_components_v2", "TEXT"),
        ("market_direction_v2", "TEXT"),
        ("index_bias_v2", "TEXT"),
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


# Every hot query filters or sorts on created_at; without these, each one is
# a full table scan that degrades linearly as history accumulates. (The date
# predicates are range-form — `col >= day AND col < day+1` — because a
# function-wrapped column like DATE(created_at) can never use an index.)
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_signals_created"
    " ON india_signals(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_suppressions_created"
    " ON india_suppressions(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_created"
    " ON india_signal_outcomes(created_at)",
)


async def init_tables() -> None:
    db = await get_db()
    await db.execute(_SIGNALS_DDL)
    await db.execute(_SUPPRESSIONS_DDL)
    await db.execute(_OUTCOMES_DDL)
    await db.execute(_SESSION_SUMMARY_DDL)
    await _migrate(db)
    for ddl in _INDEX_DDL:
        await db.execute(ddl)
    await db.commit()

# Sargable "rows from today" predicate: created_at is stored as
# 'YYYY-MM-DD HH:MM:SS' (localtime = IST via the container TZ), so plain
# string comparison against the date bounds is correct and index-friendly.
_TODAY = (
    "{col} >= DATE('now', 'localtime')"
    " AND {col} < DATE('now', 'localtime', '+1 day')"
)


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
            market_direction, session_phase, vix_regime,
            expiry_date, days_to_expiry, tp2,
            dispatch_timestamp, suppression_reason,
            entry_type, extension_vwap_atr, extension_ema21_atr,
            bias_age_min, dup_index,
            confidence_v2, score_components_v2,
            market_direction_v2, index_bias_v2
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
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
            sig.market_direction,
            sig.session_phase,
            sig.vix_regime,
            str(sig.expiry_date) if sig.expiry_date else None,
            sig.days_to_expiry,
            sig.tp2,
            str(sig.dispatch_timestamp),
            sig.suppression_reason,
            sig.entry_type,
            sig.extension_vwap_atr,
            sig.extension_ema21_atr,
            sig.bias_age_min,
            sig.dup_index,
            sig.confidence_v2,
            sig.score_components_v2,
            sig.market_direction_v2,
            sig.index_bias_v2,
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
        clauses.append("s.created_at >= ? AND s.created_at < DATE(?, '+1 day')")
        params.extend([date, date])
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
# monitor resolves the signal (TP1_HIT / SL_HIT / EXPIRED, or the two-target
# outcomes TP1_BE / TP2_HIT / TP1_EXPIRED), so every card can show where the
# trade stands. ``result_points`` / ``result_pct`` are the realised, signed
# result once resolved (NULL while OPEN; position-weighted for two-leg
# outcomes).
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
        f"SELECT COUNT(*) FROM india_signals WHERE {_TODAY.format(col='created_at')}"
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def mark_tp1_touched(signal_id: str, touched_at: datetime) -> None:
    """Persist the runner arming (two-target plan) so it survives restarts."""
    db = await get_db()
    await db.execute(
        "UPDATE india_signals SET tp1_touched_at = ?"
        " WHERE signal_id = ? AND tp1_touched_at IS NULL",
        (str(touched_at), signal_id),
    )
    await db.commit()


async def mark_triggered(signal_id: str, triggered_at: datetime) -> None:
    """Persist a LEVEL entry's fill (entry-trigger plan) across restarts."""
    db = await get_db()
    await db.execute(
        "UPDATE india_signals SET triggered_at = ?"
        " WHERE signal_id = ? AND triggered_at IS NULL",
        (str(triggered_at), signal_id),
    )
    await db.commit()


async def insert_outcome(
    signal_id: str,
    outcome: str,
    exit_price: float,
    points: float,
    pct: float,
    resolved_at: datetime,
    mfe_pct: float = 0.0,
    mae_pct: float = 0.0,
    bars_to_resolve: int = 0,
    resolution_tf: str = "",
    ambiguous_tie: bool = False,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO india_signal_outcomes
            (signal_id, outcome, exit_price, points, pct, resolved_at,
             mfe_pct, mae_pct, bars_to_resolve, resolution_tf, ambiguous_tie)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            outcome,
            exit_price,
            points,
            pct,
            str(resolved_at),
            mfe_pct,
            mae_pct,
            bars_to_resolve,
            resolution_tf,
            1 if ambiguous_tie else 0,
        ),
    )
    await db.commit()


async def get_outcomes(date: str | None = None, limit: int = 100) -> list[dict]:
    """Outcomes joined onto their signals — the quality-window view."""
    db = await get_db()
    where = ""
    params: list[str | int] = []
    if date:
        where = "WHERE o.created_at >= ? AND o.created_at < DATE(?, '+1 day')"
        params.extend([date, date])
    params.append(limit)
    cursor = await db.execute(
        f"""
        SELECT o.signal_id, o.outcome, o.exit_price, o.points, o.pct,
               o.resolved_at,
               s.symbol, s.base, s.direction, s.setup_class, s.tier,
               s.entry, s.sl, s.tp1, s.tp2, s.created_at AS emitted_at
        FROM india_signal_outcomes o
        LEFT JOIN india_signals s ON s.signal_id = o.signal_id
        {where}
        ORDER BY o.created_at DESC LIMIT ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_resolved_signals(days: int = 30) -> list[dict]:
    """Resolved signals (with their market-context stamp) over the last
    ``days`` — the raw material for the Strategy×Context edge matrix. Filters
    on the outcome's localtime ``created_at`` (indexed, IST) so the window is
    index-friendly."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT s.setup_class, s.direction, s.tier,
               s.session_phase, s.market_direction, s.vix_regime,
               s.regime_60m, s.confidence,
               s.entry_type, s.tp2, s.dup_index,
               s.extension_vwap_atr, s.extension_ema21_atr,
               s.confidence_v2, s.market_direction_v2,
               o.outcome, o.pct, o.mfe_pct, o.mae_pct,
               o.resolution_tf, o.ambiguous_tie
        FROM india_signal_outcomes o
        JOIN india_signals s ON s.signal_id = o.signal_id
        WHERE o.created_at >= DATE('now', 'localtime', ?)
        """,
        (f"-{max(1, days)} day",),
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
        f"""
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN tier = 'A+' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN tier = 'A' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN tier = 'B' THEN 1 ELSE 0 END), 0),
               COALESCE(AVG(confidence), 0)
        FROM india_signals WHERE {_TODAY.format(col='created_at')}
        """
    )
    sig_row = await cursor.fetchone()
    assert sig_row is not None  # aggregate query always yields one row
    signal_count, a_plus, a_count, b_count, avg_conf = (
        int(sig_row[0]),
        int(sig_row[1]),
        int(sig_row[2]),
        int(sig_row[3]),
        float(sig_row[4]),
    )

    cursor = await db.execute(
        f"""
        SELECT gate_name, COUNT(*) FROM india_suppressions
        WHERE {_TODAY.format(col='created_at')}
        GROUP BY gate_name
        """
    )
    gates = {str(r[0]): int(r[1]) for r in await cursor.fetchall()}

    # NOT_TRIGGERED rows are cancelled LEVEL entries that never filled — no
    # trade happened, so points/pct aggregates run over FILLED outcomes only
    # (their pct is 0 by construction, but they must not dilute avg_pct).
    cursor = await db.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN outcome = 'TP1_HIT' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'SL_HIT' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'EXPIRED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(points), 0),
            COALESCE(SUM(pct), 0),
            COALESCE(AVG(CASE WHEN outcome != 'NOT_TRIGGERED' THEN pct END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'TP1_BE' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'TP2_HIT' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'TP1_EXPIRED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN outcome = 'NOT_TRIGGERED' THEN 1 ELSE 0 END), 0)
        FROM india_signal_outcomes
        WHERE {_TODAY.format(col='created_at')}
        """
    )
    oc_row = await cursor.fetchone()
    assert oc_row is not None  # aggregate query always yields one row

    summary = {
        "date": None,  # filled by SQL below
        "signal_count": signal_count,
        "a_plus_count": a_plus,
        "a_count": a_count,
        "b_count": b_count,
        "avg_confidence": round(avg_conf, 1),
        "total_suppressed": sum(gates.values()),
        "gates_fired": _json.dumps(gates),
        "tp1_count": int(oc_row[0]),
        "sl_count": int(oc_row[1]),
        "expired_count": int(oc_row[2]),
        "tp1_be_count": int(oc_row[6]),
        "tp2_count": int(oc_row[7]),
        "tp1_expired_count": int(oc_row[8]),
        "not_triggered_count": int(oc_row[9]),
        # total_points kept for continuity, but % is the honest cross-instrument
        # measure — summing points across a 46-base universe is meaningless.
        "total_points": round(float(oc_row[3]), 2),
        "total_pct": round(float(oc_row[4]), 3),
        "avg_pct": round(float(oc_row[5]), 3),
    }

    await db.execute(
        """
        INSERT INTO india_session_summary
            (date, signal_count, a_plus_count, a_count, b_count, avg_confidence,
             total_suppressed, gates_fired, tp1_count, sl_count,
             expired_count, tp1_be_count, tp2_count, tp1_expired_count,
             not_triggered_count, total_points, total_pct, avg_pct)
        VALUES (DATE('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            signal_count = excluded.signal_count,
            a_plus_count = excluded.a_plus_count,
            a_count = excluded.a_count,
            b_count = excluded.b_count,
            avg_confidence = excluded.avg_confidence,
            total_suppressed = excluded.total_suppressed,
            gates_fired = excluded.gates_fired,
            tp1_count = excluded.tp1_count,
            sl_count = excluded.sl_count,
            expired_count = excluded.expired_count,
            tp1_be_count = excluded.tp1_be_count,
            tp2_count = excluded.tp2_count,
            tp1_expired_count = excluded.tp1_expired_count,
            not_triggered_count = excluded.not_triggered_count,
            total_points = excluded.total_points,
            total_pct = excluded.total_pct,
            avg_pct = excluded.avg_pct
        """,
        (
            summary["signal_count"],
            summary["a_plus_count"],
            summary["a_count"],
            summary["b_count"],
            summary["avg_confidence"],
            summary["total_suppressed"],
            summary["gates_fired"],
            summary["tp1_count"],
            summary["sl_count"],
            summary["expired_count"],
            summary["tp1_be_count"],
            summary["tp2_count"],
            summary["tp1_expired_count"],
            summary["not_triggered_count"],
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


async def clear_history(scope: str = "all") -> dict[str, int]:
    """Owner maintenance: wipe signal history (ops Control panel).

    ``scope`` is ``"all"`` (every signal, outcome, suppression and session
    summary — a clean restart of the quality window) or ``"today"`` (just the
    current session's rows). Returns rows deleted per table. The caller is
    responsible for resetting the in-memory engine state (gate chain, trade
    monitor) so the live process matches the emptied tables.
    """
    if scope not in ("all", "today"):
        raise ValueError(f"clear_history: unknown scope {scope!r}")
    db = await get_db()
    deleted: dict[str, int] = {}
    tables = (
        "india_signals",
        "india_signal_outcomes",
        "india_suppressions",
        "india_session_summary",
    )
    for table in tables:
        where = (
            ""
            if scope == "all"
            else (
                " WHERE date = DATE('now', 'localtime')"
                if table == "india_session_summary"
                else " WHERE DATE(created_at) = DATE('now', 'localtime')"
            )
        )
        cursor = await db.execute(f"DELETE FROM {table}{where}")
        deleted[table] = cursor.rowcount if cursor.rowcount is not None else 0
    await db.commit()
    return deleted


async def get_signals_today_for_gates() -> list[dict]:
    """Today's emissions, for gate-chain rehydration after a restart.

    ``age_sec`` (seconds since emission) is computed by SQLite in its own
    clock frame, so a container-timezone mismatch between the DB's
    ``localtime`` strings and the engine's IST clock cannot skew cooldown
    windows.
    """
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT setup_class, base, direction,
               (strftime('%s', 'now', 'localtime') - strftime('%s', created_at))
                   AS age_sec
        FROM india_signals
        WHERE DATE(created_at) = DATE('now', 'localtime')
        ORDER BY created_at
        """
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
