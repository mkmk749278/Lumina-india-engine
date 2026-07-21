"""Scoring-v2 vs v1 calibration check — run BEFORE flipping INDIA_SCORING_V2_ACTIVE.

v2 may only drive tiers/delivery once the ledger shows it is *monotonic* where
v1 was inverted (ACTIVE_CONTEXT Session-21 step 3, owner sign-off). This tool
reads the live outcome ledger, buckets resolved trades by both v1 `confidence`
and v2 `confidence_v2`, and reports realised win% / net% / cost-adjusted EV per
band, plus a monotonicity verdict and the v2 score distribution (so the A+/A
tier cutoffs can be re-set for the v2 distribution instead of inherited from
v1's).

    python -m tools.v2_calibration --db data/india_db.sqlite3 --days 30

Exit code 0 = v2 is monotonic across populated bands (safe to consider
activating, after tier recalibration); 1 = not yet — keep v2 in shadow.

Dev/ops tool: synchronous sqlite3, never imported by the engine.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import config

# Confidence band edges (upper-exclusive except the last). Below the emit floor
# nothing is delivered, so the lowest band starts there.
_DEFAULT_EDGES = (55.0, 60.0, 65.0, 70.0, 75.0, 100.1)


def load_rows(db_path: str, days: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT s.confidence, s.confidence_v2, o.outcome, o.pct
            FROM india_signal_outcomes o
            JOIN india_signals s ON s.signal_id = o.signal_id
            WHERE o.created_at >= DATE('now', 'localtime', ?)
              AND o.outcome != 'NOT_TRIGGERED'
            """,
            (f"-{max(1, days)} day",),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _band(value: float, edges: tuple[float, ...]) -> str | None:
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return f"{edges[i]:.0f}-{edges[i + 1]:.0f}"
    return None


def calibrate(
    rows: list[dict], field: str, edges: tuple[float, ...] = _DEFAULT_EDGES
) -> list[dict]:
    """Per-band n / win% / mean net% / cost-adjusted EV for one score field."""
    cost = config.ROUNDTRIP_COST_PCT
    buckets: dict[str, list[float]] = {}
    for r in rows:
        score = r.get(field)
        pct = r.get("pct")
        if score is None or pct is None:
            continue
        band = _band(float(score), edges)
        if band is None:
            continue
        buckets.setdefault(band, []).append(float(pct))
    out: list[dict] = []
    for i in range(len(edges) - 1):
        band = f"{edges[i]:.0f}-{edges[i + 1]:.0f}"
        pcts = buckets.get(band, [])
        if not pcts:
            continue
        n = len(pcts)
        wins = sum(1 for p in pcts if p > 0)
        mean = sum(pcts) / n
        out.append(
            {
                "band": band,
                "n": n,
                "win_pct": wins / n * 100.0,
                "net_pct_mean": mean,
                "ev_per_trade": mean - cost,
            }
        )
    return out


def is_monotonic(cal: list[dict]) -> bool:
    """Win% non-decreasing across populated bands (higher score => not worse)."""
    wins = [b["win_pct"] for b in cal]
    return all(a <= b + 1e-9 for a, b in zip(wins, wins[1:], strict=False))


def percentiles(rows: list[dict], field: str) -> dict[str, float]:
    vals = sorted(float(r[field]) for r in rows if r.get(field) is not None)
    if not vals:
        return {}
    def pct(p: float) -> float:
        idx = min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1))))
        return vals[idx]
    return {f"p{int(p)}": pct(p) for p in (10, 25, 50, 75, 90, 100)}


def _print_cal(title: str, cal: list[dict]) -> None:
    print(f"\n== {title} ==")
    print(f"  {'band':10} {'n':>4} {'win%':>6} {'net%':>9} {'EV/tr':>9}")
    for b in cal:
        print(
            f"  {b['band']:10} {b['n']:>4} {b['win_pct']:>6.1f}"
            f" {b['net_pct_mean']:>+9.4f} {b['ev_per_trade']:>+9.4f}"
        )
    print(f"  monotonic (win% non-decreasing): {is_monotonic(cal)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, help="path to india_db.sqlite3")
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args(argv)

    rows = load_rows(args.db, args.days)
    if not rows:
        print("no resolved signals in window — nothing to calibrate")
        return 1
    has_v2 = any(r.get("confidence_v2") for r in rows)
    print(f"resolved trades: {len(rows)} (last {args.days}d)")

    cal_v1 = calibrate(rows, "confidence")
    _print_cal("v1 confidence (drives tiers today)", cal_v1)

    if not has_v2:
        print(
            "\nconfidence_v2 not populated — run with the shadow scorer live"
            " first. v2 verdict: NOT READY."
        )
        return 1

    cal_v2 = calibrate(rows, "confidence_v2")
    _print_cal("v2 confidence (shadow)", cal_v2)

    dist = percentiles(rows, "confidence_v2")
    print("\nv2 score distribution (for A+/A tier recalibration):")
    print("  " + "  ".join(f"{k}={v:.1f}" for k, v in dist.items()))

    v1_mono = is_monotonic(cal_v1)
    v2_mono = is_monotonic(cal_v2)
    ready = v2_mono and not (v1_mono and not v2_mono)
    verdict = (
        "v2 READY to consider (recalibrate tiers first)"
        if v2_mono
        else "v2 NOT READY — keep in shadow"
    )
    print(
        f"\nVERDICT: v1 monotonic={v1_mono}  v2 monotonic={v2_mono}"
        f"  ->  {verdict}"
    )
    return 0 if ready else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
