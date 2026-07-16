"""Outcome-ledger replay harness (Session 21).

Re-resolves stored signals against historical candles under configurable
rules, using the SAME ``walk_signal`` the live monitor runs — one
implementation, one semantics, so replay and production cannot drift.

What it answers:
- fidelity: does a legacy-rules replay (--entry-trigger off --resolution 5m)
  reproduce the stored ledger? (It must, before any variant is trusted.)
- variants: what does the ledger look like under entry-trigger fills and/or
  1m resolution? Per-cohort deltas, outcome flips, EV shift.

Usage (run on the VPS next to the prod DB, or anywhere with a candle cache):

    python -m tools.replay --db /path/india_db.sqlite3 \
        --candles ./candle_cache --days 30 \
        --entry-trigger on --resolution 1m

    # first run with a live Fyers token fills the cache:
    FYERS_CLIENT_ID=... FYERS_ACCESS_TOKEN=... \
    python -m tools.replay --db ... --candles ./candle_cache --fetch ...

Candle cache layout: one CSV per (symbol, date, resolution) —
``<cache>/<SYMBOL>_<YYYY-MM-DD>_<RES>.csv`` with header
``ts,open,high,low,close,volume`` (ts = ISO-8601, IST). Fetches are cached,
so replays are offline-repeatable. TP1/SL/TP2 geometry comes from the stored
rows — replay changes RESOLUTION semantics, never the printed trade plan.

This is a dev/ops tool: synchronous sqlite3 + httpx, never imported by the
engine runtime.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import httpx

import config
from src.market.candle import Candle
from src.signals.model import EntryType
from src.trade_monitor import (
    OUTCOME_EXPIRED,
    OUTCOME_NOT_TRIGGERED,
    OUTCOME_TP1_EXPIRED,
    TrackedSignal,
    _be_price,
    _points,
    _trigger_pending,
    walk_signal,
)

_HISTORY_URL = "https://api-t1.fyers.in/data/history"
_SESSION_CLOSE = time(15, 30)

# Stored rows predate the entry_type column — infer LEVEL for the three
# breakout evaluators whose printed entry is a resting level.
_LEVEL_SETUPS = {
    "OPENING_RANGE_BREAKOUT",
    "VOLUME_SURGE_BREAKOUT",
    "BREAKDOWN_SHORT",
}


@dataclass
class ReplayRow:
    signal_id: str
    symbol: str
    base: str
    setup_class: str
    direction: str
    tier: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    entry_type: str
    created_at: datetime
    stored_outcome: str
    stored_pct: float


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(str(raw))
    return ts if ts.tzinfo else config.IST.localize(ts)


def load_rows(db_path: str, days: int) -> list[ReplayRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT s.signal_id, s.symbol, s.base, s.setup_class, s.direction,
               s.tier, s.entry, s.sl, s.tp1, COALESCE(s.tp2, 0) AS tp2,
               s.entry_type, s.created_at,
               o.outcome AS stored_outcome, COALESCE(o.pct, 0) AS stored_pct
        FROM india_signal_outcomes o
        JOIN india_signals s ON s.signal_id = o.signal_id
        WHERE o.created_at >= DATE('now', 'localtime', ?)
        ORDER BY s.created_at
        """,
        (f"-{max(1, days)} day",),
    )
    rows: list[ReplayRow] = []
    for r in cur.fetchall():
        entry_type = r["entry_type"] or (
            EntryType.LEVEL
            if str(r["setup_class"]) in _LEVEL_SETUPS
            else EntryType.MARKET
        )
        rows.append(
            ReplayRow(
                signal_id=str(r["signal_id"]),
                symbol=str(r["symbol"]),
                base=str(r["base"]),
                setup_class=str(r["setup_class"]),
                direction=str(r["direction"]),
                tier=str(r["tier"]),
                entry=float(r["entry"]),
                sl=float(r["sl"]),
                tp1=float(r["tp1"]),
                tp2=float(r["tp2"]),
                entry_type=str(entry_type),
                created_at=_parse_ts(r["created_at"]),
                stored_outcome=str(r["stored_outcome"]),
                stored_pct=float(r["stored_pct"]),
            )
        )
    conn.close()
    return rows


# ── candle cache ─────────────────────────────────────────────────────


def _cache_path(cache_dir: Path, symbol: str, day: str, res: str) -> Path:
    safe = symbol.replace(":", "_").replace("/", "_")
    return cache_dir / f"{safe}_{day}_{res}.csv"


def load_candles(
    cache_dir: Path, symbol: str, day: str, res: str
) -> list[Candle] | None:
    path = _cache_path(cache_dir, symbol, day, res)
    if not path.exists():
        return None
    out: list[Candle] = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            out.append(
                Candle(
                    ts=_parse_ts(row["ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return out


def save_candles(
    cache_dir: Path, symbol: str, day: str, res: str, candles: list[Candle]
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, symbol, day, res)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])


def fetch_candles(
    symbol: str, day: str, res: str
) -> list[Candle]:
    """One-day Fyers REST history fetch (requires FYERS_CLIENT_ID +
    FYERS_ACCESS_TOKEN in the environment — run on the VPS)."""
    client_id = os.environ.get("FYERS_CLIENT_ID", "")
    token = os.environ.get("FYERS_ACCESS_TOKEN", "")
    if not client_id or not token:
        raise SystemExit(
            "--fetch needs FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in the env"
        )
    resp = httpx.get(
        _HISTORY_URL,
        params={
            "symbol": symbol,
            "resolution": res,
            "date_format": "1",
            "range_from": day,
            "range_to": day,
            "cont_flag": "1",
        },
        headers={"Authorization": f"{client_id}:{token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("s") != "ok":
        raise RuntimeError(f"history fetch failed for {symbol} {day}: {data}")
    out: list[Candle] = []
    for row in data.get("candles", []):
        ts = datetime.fromtimestamp(int(row[0]), tz=config.IST)
        out.append(
            Candle(
                ts=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    return out


# ── re-resolution (same walk as production) ─────────────────────────


def resolve(
    row: ReplayRow,
    candles: list[Candle],
    *,
    entry_trigger: bool,
    session_close: time = _SESSION_CLOSE,
) -> tuple[str, float]:
    """Re-resolve one stored signal against *candles* (its session, ascending)
    with the production ``walk_signal`` + the monitor's force-close rules.

    Returns (outcome, pct)."""
    prev_flag = config.ENTRY_TRIGGER_ENABLED
    config.ENTRY_TRIGGER_ENABLED = entry_trigger
    try:
        tracked = TrackedSignal(
            signal_id=row.signal_id,
            symbol=row.symbol,
            direction=row.direction,
            entry=row.entry,
            sl=row.sl,
            tp1=row.tp1,
            tp2=row.tp2,
            be_price=_be_price(row.direction, row.entry),
            registered_at=row.created_at,
            entry_type=row.entry_type if entry_trigger else EntryType.MARKET,
            triggered_at=None
            if (entry_trigger and row.entry_type == EntryType.LEVEL)
            else row.created_at,
        )
        window = [
            c
            for c in candles
            if c.ts >= row.created_at and c.ts.time() < session_close
        ]
        decision = walk_signal(tracked, window)
        if decision is not None:
            outcome, exit_price = decision
        elif _trigger_pending(tracked):
            outcome, exit_price = OUTCOME_NOT_TRIGGERED, 0.0
        else:
            exit_price = window[-1].close if window else row.entry
            outcome = (
                OUTCOME_TP1_EXPIRED
                if tracked.tp1_touched_at is not None
                else OUTCOME_EXPIRED
            )
        # Same blend as IndiaTradeMonitor._close.
        if outcome == OUTCOME_NOT_TRIGGERED:
            points = 0.0
        elif outcome in ("TP1_BE", "TP2_HIT", "TP1_EXPIRED"):
            frac = min(1.0, max(0.0, config.TP1_EXIT_FRACTION))
            points = frac * _points(row.direction, row.entry, row.tp1) + (
                1.0 - frac
            ) * _points(row.direction, row.entry, exit_price)
        else:
            points = _points(row.direction, row.entry, exit_price)
        pct = (points / row.entry * 100.0) if row.entry > 0 else 0.0
        return outcome, pct
    finally:
        config.ENTRY_TRIGGER_ENABLED = prev_flag


def _is_win(outcome: str) -> bool:
    return outcome.upper().startswith("TP")


def summarize(results: list[dict]) -> str:
    lines: list[str] = []

    def _block(title: str, keyfn) -> None:  # type: ignore[no-untyped-def]
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            groups[keyfn(r)].append(r)
        lines.append(f"\n== {title} ==")
        lines.append(
            f"{'cohort':42s} {'n':>4s} {'nt':>4s} {'win%':>6s} {'net%':>8s}"
            f" {'ev%':>8s} {'flips':>5s}"
        )
        for key in sorted(groups):
            rs = groups[key]
            filled = [r for r in rs if r["new_outcome"] != OUTCOME_NOT_TRIGGERED]
            nt = len(rs) - len(filled)
            n = len(filled)
            wins = sum(1 for r in filled if _is_win(r["new_outcome"]))
            net = sum(r["new_pct"] for r in filled)
            ev = (net / n - config.ROUNDTRIP_COST_PCT) if n else 0.0
            flips = sum(1 for r in rs if r["new_outcome"] != r["stored_outcome"])
            lines.append(
                f"{key:42s} {n:4d} {nt:4d} {100 * wins / n if n else 0:6.1f}"
                f" {net:+8.2f} {ev:+8.3f} {flips:5d}"
            )

    _block("overall", lambda r: "ALL")
    _block("by setup", lambda r: r["setup_class"])
    _block("by setup/direction", lambda r: f"{r['setup_class']}/{r['direction']}")
    _block("by tier", lambda r: r["tier"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, help="path to india_db.sqlite3")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--candles", required=True, help="candle cache directory")
    p.add_argument("--resolution", choices=("1", "5"), default="5",
                   help="candle resolution to walk (minutes)")
    p.add_argument("--entry-trigger", choices=("on", "off"), default="off")
    p.add_argument("--fetch", action="store_true",
                   help="fetch missing candles from Fyers REST (needs token)")
    p.add_argument("--out", default="", help="write per-signal CSV here")
    args = p.parse_args(argv)

    rows = load_rows(args.db, args.days)
    if not rows:
        print("no resolved signals in window", file=sys.stderr)
        return 1
    cache = Path(args.candles)

    results: list[dict] = []
    missing: set[tuple[str, str]] = set()
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        candles = load_candles(cache, row.symbol, day, args.resolution)
        if candles is None:
            if args.fetch:
                candles = fetch_candles(row.symbol, day, args.resolution)
                save_candles(cache, row.symbol, day, args.resolution, candles)
            else:
                missing.add((row.symbol, day))
                continue
        outcome, pct = resolve(
            row, candles, entry_trigger=args.entry_trigger == "on"
        )
        results.append(
            {
                "signal_id": row.signal_id,
                "base": row.base,
                "setup_class": row.setup_class,
                "direction": row.direction,
                "tier": row.tier,
                "stored_outcome": row.stored_outcome,
                "stored_pct": round(row.stored_pct, 4),
                "new_outcome": outcome,
                "new_pct": round(pct, 4),
            }
        )

    if missing:
        print(
            f"WARNING: {len(missing)} (symbol, day) candle files missing —"
            f" run with --fetch on the VPS to fill the cache. First few:"
            f" {sorted(missing)[:5]}",
            file=sys.stderr,
        )
    if not results:
        return 1

    exact = sum(
        1 for r in results if r["new_outcome"] == r["stored_outcome"]
    )
    print(
        f"replayed {len(results)} signals"
        f" (resolution={args.resolution}m, entry_trigger={args.entry_trigger})"
        f" — {exact}/{len(results)} outcomes match stored ledger"
    )
    print(summarize(results))

    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"per-signal comparison written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
