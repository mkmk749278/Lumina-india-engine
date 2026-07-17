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

Ledger-truth conventions (Session 21):

- **NOT_TRIGGERED rows are excluded from every cell** (reported per cell as
  ``not_triggered``): a cancelled LEVEL entry that never filled is not a
  trade, and letting its zero-pct rows dilute cohorts would hide real edge.
- **Legacy single-target rows are segregated**: TP1_HIT exists only for
  ``tp2 <= 0`` signals and credits 100% of the position at TP1 — a different
  measurement than the two-target blends. Cells count them (``legacy_n``)
  but the headline stats still include them (they are real trades) — the
  segregated count keeps the mix visible instead of silently comparable.
- **``win_rate_net``**: % of trades whose realised pct beat the round-trip
  cost — the honest "would a subscriber have made money" rate, alongside
  the TP1-banked ``win_rate``.
- **Context cells exclude NULL-context rows** (pre-MarketContext history)
  instead of lumping them into a mixed "?" cohort that poisoned allocator
  verdicts; the exclusion is reported per dimension, never silent.

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

_NOT_TRIGGERED = "NOT_TRIGGERED"

# Dimensions whose grouping field arrived with the MarketContext /
# truth-telemetry migrations — rows predating them carry NULL and are
# excluded from these cells (counted per dimension) rather than bucketed
# into a mixed "?" cohort.
_CONTEXT_FIELDS = {
    "by_session_phase": "session_phase",
    "by_vix_regime": "vix_regime",
    "by_market_vs_signal": "market_direction",
    "by_extension_bucket": "extension_vwap_atr",
    "by_dup_index": "dup_index",
}


def _is_win(outcome: str) -> bool:
    """Every TP1-banked outcome counts as a win (TP1_HIT/TP1_BE/TP2_HIT/
    TP1_EXPIRED all start with 'TP')."""
    return outcome.upper().startswith("TP")


@dataclass(frozen=True)
class EdgeCell:
    key: str
    n: int  # filled trades only (NOT_TRIGGERED excluded)
    wins: int
    losses: int  # SL_HIT
    expired: int
    not_triggered: int  # cancelled LEVEL entries mapped to this cell
    legacy_n: int  # legacy single-target (TP1_HIT / tp2<=0) rows in n
    win_rate: float  # % of n that are TP1-banked wins
    win_rate_net: float  # % of n whose realised pct beat the round-trip cost
    net_pct: float  # sum of realised pct (gross)
    avg_pct: float  # mean realised pct per trade (gross)
    ev_net_pct: float  # avg_pct minus the round-trip cost — expectancy


def _cell(key: str, rows: list[dict]) -> EdgeCell:
    filled = [
        r for r in rows if str(r["outcome"]).upper() != _NOT_TRIGGERED
    ]
    not_triggered = len(rows) - len(filled)
    n = len(filled)
    wins = sum(1 for r in filled if _is_win(str(r["outcome"])))
    losses = sum(1 for r in filled if str(r["outcome"]).upper() == "SL_HIT")
    expired = sum(1 for r in filled if str(r["outcome"]).upper() == "EXPIRED")
    legacy = sum(
        1
        for r in filled
        if str(r["outcome"]).upper() == "TP1_HIT"
        and float(r.get("tp2") or 0.0) <= 0.0
    )
    net_wins = sum(
        1 for r in filled if float(r.get("pct") or 0.0) > _COST_PCT
    )
    net = sum(float(r.get("pct") or 0.0) for r in filled)
    avg = net / n if n else 0.0
    return EdgeCell(
        key=key,
        n=n,
        wins=wins,
        losses=losses,
        expired=expired,
        not_triggered=not_triggered,
        legacy_n=legacy,
        win_rate=round(100.0 * wins / n, 1) if n else 0.0,
        win_rate_net=round(100.0 * net_wins / n, 1) if n else 0.0,
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


def _has_context(row: dict, field: str) -> bool:
    """True when *field* was actually stamped on this row (post-migration)."""
    return row.get(field) is not None and str(row.get(field)) != ""


def _extension_bucket(row: dict) -> str:
    """Coarse exhaustion bucket from the signed VWAP extension (in ATRs)."""
    ext = float(row.get("extension_vwap_atr") or 0.0)
    if ext < 0.0:
        return "BEHIND_VWAP"
    if ext < 0.5:
        return "NEAR_VWAP(0-0.5)"
    if ext < 1.5:
        return "EXTENDED(0.5-1.5)"
    return "EXHAUSTED(>1.5)"


def build_edge_matrix(rows: list[dict]) -> dict[str, list[dict] | dict]:
    """The full edge matrix from resolved rows, sliced along the dimensions the
    direction gate, the tier recalibration, and the allocator each care about.

    Context dimensions run over the subset of rows that actually carry the
    field; the excluded (pre-migration) count is reported per dimension in
    ``context_excluded`` so the shrink is visible, never silent.
    """
    excluded: dict[str, int] = {}

    def _ctx_rows(dim: str) -> list[dict]:
        field = _CONTEXT_FIELDS[dim]
        kept = [r for r in rows if _has_context(r, field)]
        excluded[dim] = len(rows) - len(kept)
        return kept

    return {
        "overall": _group(rows, lambda r: "ALL"),
        "by_setup": _group(rows, lambda r: _s(r, "setup_class")),
        "by_setup_direction": _group(
            rows, lambda r: f"{_s(r, 'setup_class')}/{_s(r, 'direction')}"
        ),
        "by_tier": _group(rows, lambda r: _s(r, "tier")),
        "by_session_phase": _group(
            _ctx_rows("by_session_phase"), lambda r: _s(r, "session_phase")
        ),
        "by_vix_regime": _group(
            _ctx_rows("by_vix_regime"), lambda r: _s(r, "vix_regime")
        ),
        # The direction gate's evidence surface: signal direction under each
        # whole-market direction (counter-trend cohorts show here).
        "by_market_vs_signal": _group(
            _ctx_rows("by_market_vs_signal"),
            lambda r: f"{_s(r, 'market_direction')}/{_s(r, 'direction')}",
        ),
        # Truth-telemetry dimensions (Session 21): exhaustion at entry and
        # duplicate ordinal — the evidence surfaces for the (future,
        # sign-off-gated) exhaustion gate and duplicate policy.
        "by_extension_bucket": _group(
            _ctx_rows("by_extension_bucket"), _extension_bucket
        ),
        "by_dup_index": _group(
            _ctx_rows("by_dup_index"),
            lambda r: f"dup#{int(row_dup(r))}",
        ),
        "context_excluded": excluded,
    }


def row_dup(r: dict) -> int:
    try:
        return int(r.get("dup_index") or 0)
    except (TypeError, ValueError):
        return 0


async def get_edge_matrix(days: int = 30) -> dict:
    """Load resolved signals over the window and build the edge matrix."""
    rows = await signal_store.get_resolved_signals(days=days)
    return {
        "days": days,
        "sample": len(rows),
        "cost_pct": _COST_PCT,
        "matrix": build_edge_matrix(rows),
    }


def build_edge_index(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """A (setup_class, direction) -> {n, ev_net_pct} lookup for the scorer's
    edge-aware confidence adjustment. Keyed off the same measured cohorts as
    the matrix's ``by_setup_direction`` dimension."""
    index: dict[tuple[str, str], dict] = {}
    matrix = build_edge_matrix(rows)
    cells = matrix.get("by_setup_direction", [])
    assert isinstance(cells, list)  # context_excluded is the only dict value
    for cell in cells:
        setup, _, direction = str(cell["key"]).partition("/")
        index[(setup, direction)] = {
            "n": cell["n"],
            "ev_net_pct": cell["ev_net_pct"],
        }
    return index


async def get_edge_index(days: int = 30) -> dict[tuple[str, str], dict]:
    """Session-open load of the edge index (cached by the caller; not a
    per-scan read)."""
    rows = await signal_store.get_resolved_signals(days=days)
    return build_edge_index(rows)
