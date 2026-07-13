"""The autonomous allocator — recommendation mode (Layer D, observe-only).

`PLAN_AUTONOMOUS_PORTFOLIO`: the allocator reads each strategy's *measured*
edge per market context and decides which cohorts should be active. This first
cut runs in **recommendation mode** — it turns the Strategy×Context edge matrix
into per-cohort EMIT / SUPPRESS / HOLD verdicts and surfaces them at
`/api/allocator`, but changes **nothing** about what the scanner emits. It is
the "what it would do" the owner watches before the allocator is ever armed to
act; auto-tuning the emit floor / setup enables by measured edge comes only once
these recommendations visibly track the live outcomes.

A cohort is judged only once it has `ALLOCATOR_MIN_SAMPLE` resolved trades
(thin cells read INSUFFICIENT_DATA, never a verdict). Expectancy is the edge
matrix's cost-adjusted `ev_net_pct`: at/above the EV floor → EMIT, at/below the
suppress threshold → SUPPRESS, in between → HOLD. Pure function over stored
rows; no new I/O, no emission change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import config
from src import strategy_edge


class Verdict:
    EMIT = "EMIT"
    HOLD = "HOLD"
    SUPPRESS = "SUPPRESS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Recommendation:
    key: str
    verdict: str
    n: int
    win_rate: float
    ev_net_pct: float
    reason: str


def _verdict(
    cell: dict,
    min_sample: int,
    ev_floor: float,
    suppress_ev: float,
) -> Recommendation:
    n = int(cell.get("n", 0))
    ev = float(cell.get("ev_net_pct", 0.0))
    win = float(cell.get("win_rate", 0.0))
    key = str(cell.get("key", "?"))
    if n < min_sample:
        return Recommendation(
            key, Verdict.INSUFFICIENT_DATA, n, win, ev,
            f"only {n} resolved (< {min_sample} needed to judge)",
        )
    if ev >= ev_floor:
        return Recommendation(
            key, Verdict.EMIT, n, win, ev,
            f"expectancy {ev:+.3f}% ≥ floor {ev_floor:+.3f}% over {n} trades",
        )
    if ev <= suppress_ev:
        return Recommendation(
            key, Verdict.SUPPRESS, n, win, ev,
            f"expectancy {ev:+.3f}% ≤ {suppress_ev:+.3f}% over {n} trades",
        )
    return Recommendation(
        key, Verdict.HOLD, n, win, ev,
        f"expectancy {ev:+.3f}% is marginal over {n} trades",
    )


def recommend(
    cells: list[dict],
    *,
    min_sample: int | None = None,
    ev_floor: float | None = None,
    suppress_ev: float | None = None,
) -> list[dict]:
    """Per-cohort recommendations for one edge-matrix dimension, best-first."""
    ms = config.ALLOCATOR_MIN_SAMPLE if min_sample is None else min_sample
    fl = config.ALLOCATOR_EV_FLOOR if ev_floor is None else ev_floor
    su = config.ALLOCATOR_SUPPRESS_EV if suppress_ev is None else suppress_ev
    recs = [_verdict(c, ms, fl, su) for c in cells]
    recs.sort(key=lambda r: r.ev_net_pct, reverse=True)
    return [asdict(r) for r in recs]


def build_allocation(matrix: dict[str, list[dict]]) -> dict:
    """Recommendations over the decision-relevant dimensions of the matrix
    plus a verdict tally. The scanner does not read this — it is the
    observe-only 'what it would do' surface."""
    dims = {
        "by_setup_direction": recommend(matrix.get("by_setup_direction", [])),
        "by_market_vs_signal": recommend(matrix.get("by_market_vs_signal", [])),
        "by_setup": recommend(matrix.get("by_setup", [])),
    }
    tally: dict[str, int] = {}
    for recs in dims.values():
        for r in recs:
            tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    return {"recommendations": dims, "tally": tally}


async def get_allocation(days: int = 30) -> dict:
    """Load the edge matrix over the window and produce the allocation."""
    edge = await strategy_edge.get_edge_matrix(days=days)
    return {
        "mode": "recommendation",  # observe-only; changes no emission
        "days": days,
        "sample": edge.get("sample", 0),
        "thresholds": {
            "min_sample": config.ALLOCATOR_MIN_SAMPLE,
            "ev_floor": config.ALLOCATOR_EV_FLOOR,
            "suppress_ev": config.ALLOCATOR_SUPPRESS_EV,
        },
        "allocation": build_allocation(edge.get("matrix", {})),
    }
