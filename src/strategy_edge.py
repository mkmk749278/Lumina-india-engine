"""The Strategy × Context edge matrix — measured edge, not assumed score.

`INDIA_MARKET_DOCTRINE` §7F: measure everything against structure, not opinion.
The 2026-07-13 window showed the a-priori confidence tier is *inverted*
(A+ 0/3, A 27%, B 44%) — so selection must be driven by each setup's *realised*
edge in each market context, not by the score it was given up front.

This module aggregates resolved outcomes (`india_signal_outcomes` joined to the
market-context stamp now on every signal) into a per-context edge matrix:
win-rate, gross net %, and cost-adjusted expectancy per cell, with a sample
count so thin cells are visibly thin. It reads only already-stored rows (no new
I/O), and is surfaced at `/api/edge-matrix` — its same-change consumer — for the
owner/ops. It is the substrate the tier recalibration and the allocator read
next; nothing here changes emission.

Win convention (ops doctrine): every **TP1-banked** outcome is a win
(`TP1_HIT`/`TP1_BE`/`TP2_HIT`/`TP1_EXPIRED`). `SL_HIT` is a loss; `EXPIRED`
(neither leg touched) is a non-win. `pct` is the position-weighted realised %
the monitor recorded; expectancy is netted against the round-trip cost model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

import config
from src import signal_store

# Per-trade round-trip cost (%), the same model the scorer nets R:R against.
_COST_PCT: float = config.ROUNDTRIP_COST_PCT


def _is_win(outcome: str) -> bool:
    """Every TP1-banked outcome counts as a win (TP1_HIT/TP1_BE/TP2_HIT/
    TP1_EXPIRED all start with 'TP')."""
    return outcome.upper().startswith("TP")


@dataclass(frozen=True)
class EdgeCell:
    key: str
    n: int
    wins: int
    losses: int  # SL_HIT
    expired: int
    win_rate: float  # % of n that are TP1-banked wins
    net_pct: float  # sum of realised pct (gross)
    avg_pct: float  # mean realised pct per trade (gross)
    ev_net_pct: float  # avg_pct minus the round-trip cost — expectancy


def _cell(key: str, rows: list[dict]) -> EdgeCell:
    n = len(rows)
    wins = sum(1 for r in rows if _is_win(str(r["outcome"])))
    losses = sum(1 for r in rows if str(r["outcome"]).upper() == "SL_HIT")
    expired = sum(1 for r in rows if str(r["outcome"]).upper() == "EXPIRED")
    net = sum(float(r.get("pct") or 0.0) for r in rows)
    avg = net / n if n else 0.0
    return EdgeCell(
        key=key,
        n=n,
        wins=wins,
        losses=losses,
        expired=expired,
        win_rate=round(100.0 * wins / n, 1) if n else 0.0,
        net_pct=round(net, 3),
        avg_pct=round(avg, 4),
        ev_net_pct=round(avg - _COST_PCT, 4),
    )


def _group(rows: Iterable[dict], keyfn: Callable[[dict], str]) -> list[dict]:
    """Bucket rows by ``keyfn`` and return one cell per bucket, best expectancy
    first (so the allocator/owner reads the strongest cohorts at the top)."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(keyfn(r), []).append(r)
    cells = [_cell(k, v) for k, v in buckets.items()]
    cells.sort(key=lambda c: c.ev_net_pct, reverse=True)
    return [asdict(c) for c in cells]


def _s(row: dict, field: str) -> str:
    return str(row.get(field) or "") or "?"


def build_edge_matrix(rows: list[dict]) -> dict[str, list[dict]]:
    """The full edge matrix from resolved rows, sliced along the dimensions the
    direction gate, the tier recalibration, and the allocator each care about."""
    return {
        "overall": _group(rows, lambda r: "ALL"),
        "by_setup": _group(rows, lambda r: _s(r, "setup_class")),
        "by_setup_direction": _group(
            rows, lambda r: f"{_s(r, 'setup_class')}/{_s(r, 'direction')}"
        ),
        "by_tier": _group(rows, lambda r: _s(r, "tier")),
        "by_session_phase": _group(rows, lambda r: _s(r, "session_phase")),
        "by_vix_regime": _group(rows, lambda r: _s(r, "vix_regime")),
        # The direction gate's evidence surface: signal direction under each
        # whole-market direction (counter-trend cohorts show here).
        "by_market_vs_signal": _group(
            rows, lambda r: f"{_s(r, 'market_direction')}/{_s(r, 'direction')}"
        ),
    }


async def get_edge_matrix(days: int = 30) -> dict:
    """Load resolved signals over the window and build the edge matrix."""
    rows = await signal_store.get_resolved_signals(days=days)
    return {
        "days": days,
        "sample": len(rows),
        "cost_pct": _COST_PCT,
        "matrix": build_edge_matrix(rows),
    }
