"""Gate what-if replay (Session 21).

Given the stored ledger, report what a candidate emission gate WOULD have
suppressed and how the kept vs suppressed cohorts actually performed —
the evidence a gate proposal must attach before any flag flips (doctrine:
prove we cut losers, not just volume).

Candidate gates (combine freely):
  --phase-block  FAMILY:PHASE[,FAMILY:PHASE...]   e.g. TREND:POWER_HOUR
  --max-extension X    suppress signals with extension_vwap_atr > X
  --max-dup N          suppress dup_index > N
  --suppress-cohorts SETUP/DIR[,...]   the allocator SUPPRESS list

Rows missing the relevant telemetry (pre-migration) are never counted as
suppressed — a gate can only be judged on rows that carry its input.

    python -m tools.replay_gates --db /path/india_db.sqlite3 --days 30 \
        --phase-block TREND:POWER_HOUR --max-dup 1
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import config
from src.signals.model import SETUP_FAMILY

_NOT_TRIGGERED = "NOT_TRIGGERED"


def load(db_path: str, days: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT s.setup_class, s.direction, s.session_phase,
               s.extension_vwap_atr, s.dup_index,
               o.outcome, COALESCE(o.pct, 0) AS pct
        FROM india_signal_outcomes o
        JOIN india_signals s ON s.signal_id = o.signal_id
        WHERE o.created_at >= DATE('now', 'localtime', ?)
        """,
        (f"-{max(1, days)} day",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def would_suppress(
    row: dict,
    phase_block: set[tuple[str, str]],
    max_extension: float | None,
    max_dup: int | None,
    suppress_cohorts: set[str],
) -> str | None:
    """Name of the first gate that would have suppressed *row*, else None."""
    setup = str(row.get("setup_class") or "")
    family = SETUP_FAMILY.get(setup, "")
    phase = row.get("session_phase")
    if phase_block and phase and (family, str(phase)) in phase_block:
        return "phase_block"
    ext = row.get("extension_vwap_atr")
    if max_extension is not None and ext is not None and float(ext) > max_extension:
        return "max_extension"
    dup = row.get("dup_index")
    if max_dup is not None and dup is not None and int(dup) > max_dup:
        return "max_dup"
    cohort = f"{setup}/{row.get('direction')}"
    if cohort in suppress_cohorts:
        return "allocator_suppress"
    return None


def _stats(rows: list[dict]) -> tuple[int, float, float, float]:
    filled = [r for r in rows if str(r["outcome"]).upper() != _NOT_TRIGGERED]
    n = len(filled)
    wins = sum(1 for r in filled if str(r["outcome"]).upper().startswith("TP"))
    net = sum(float(r["pct"]) for r in filled)
    ev = (net / n - config.ROUNDTRIP_COST_PCT) if n else 0.0
    return n, (100.0 * wins / n if n else 0.0), net, ev


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--phase-block", default="")
    p.add_argument("--max-extension", type=float, default=None)
    p.add_argument("--max-dup", type=int, default=None)
    p.add_argument("--suppress-cohorts", default="")
    args = p.parse_args(argv)

    phase_block = {
        (pair.split(":", 1)[0].strip().upper(), pair.split(":", 1)[1].strip().upper())
        for pair in args.phase_block.split(",")
        if ":" in pair
    }
    suppress_cohorts = {
        c.strip().upper() for c in args.suppress_cohorts.split(",") if c.strip()
    }

    rows = load(args.db, args.days)
    if not rows:
        print("no resolved signals in window", file=sys.stderr)
        return 1

    kept: list[dict] = []
    cut: dict[str, list[dict]] = {}
    for r in rows:
        gate = would_suppress(
            r, phase_block, args.max_extension, args.max_dup, suppress_cohorts
        )
        if gate is None:
            kept.append(r)
        else:
            cut.setdefault(gate, []).append(r)

    def _line(label: str, rs: list[dict]) -> str:
        n, win, net, ev = _stats(rs)
        return f"{label:24s} n={n:4d} win={win:5.1f}% net={net:+8.2f}% ev={ev:+7.3f}%"

    print(_line("BASELINE (all)", rows))
    print(_line("KEPT", kept))
    for gate, rs in sorted(cut.items()):
        print(_line(f"CUT by {gate}", rs))
    if not cut:
        print("no rows suppressed by the given spec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
