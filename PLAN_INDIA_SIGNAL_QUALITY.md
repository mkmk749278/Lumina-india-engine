# Plan — India F&O Signal Quality: Doctrine + Autonomous Regime-Adaptive Portfolio

*The approved, executed plan for lifting NSE F&O signal quality. Companion to
`INDIA_MARKET_DOCTRINE.md` (the "why", market structure) and
`HANDOFF_INDIA_SIGNAL_QUALITY.md` (execution status + how to continue). Mirrors the
crypto engine's `PLAN_AUTONOMOUS_PORTFOLIO` shape, adapted to the NSE.*

Branch (all three repos): `claude/indian-stock-signals-quality-b3fz40`
Session date: 2026-07-13 · Phase 1 (signal delivery, no live users, no execution)

---

## Context — why this exists

The 2026-07-13 live window (`india_signals_20260713`, 119 signals across 45 bases,
95 resolved) showed the engine trading like a **retail taker**: **36% win** vs the
~39% cost breakeven → net ≈ flat/negative after STT. Quantified:

| Slice | Result | Read |
|---|---|---|
| Overall | **36% win (34/95)** | below the ~39% cost breakeven |
| Direction | **LONG 56% (+11.6%) vs SHORT 13% (−5.6%)** | *biggest bleed* — no market-direction filter; shorts fired all day into a rising tape |
| Tier | **A+ 0/3, A 27%, B 44%** | the a-priori confidence score is **inverted** |
| Setup | VSB 65% / DIV 67%; **SR_FLIP 0/12 (−3.8%)**, TPE 18%, BDS 17% | edge varies wildly per setup |
| PCR | `0.0` on all 119 rows | banned dead-scaffold — never wired |
| Time-of-day | 11:00 (chop onset) 25% / −2.2% | session clock matters, wasn't gated |

**Intended outcome:** mirror the crypto approach — a *doctrine* that grounds every
decision in real NSE structure, plus a *self-driving regime-adaptive portfolio* that
measures each setup's real edge per market context and routes to what works *now* —
so good setups fire, counter-trend/chop garbage doesn't, and it stays observable
inside the Phase-1 safety envelope (which signals reach the app, not capital).

---

## The rail that never moves

Everything here is **Phase-1, off the money path** (`AUTO_EXECUTION_ENABLED=false`).
Every change ships via PR → CI green → merge. **No scaffolds** (store *and* consume in
the same change). **Cost discipline** (context/edge/macro all cached, session-open or
per-scan-local — no new per-tick/per-scan network reads, IB18). **Reality first** — no
scoring change is fit to one day; new data degrades to NEUTRAL rather than fabricating.

---

## Two CTE honesty calls baked into the design

- **No one-day scoring hand-fit.** With the edge matrix at n≈95/one day, a numeric
  rebudget of the scoring weights would be overfitting. So the "tier recalibration"
  is a **bounded, sample-gated, reversible** adjustment — inert until a cohort crosses
  the sample floor, auto-adapting as real data accrues.
- **Gift-Nifty is unreachable and unnecessary.** The Fyers feed carries no GIFT/SGX
  symbol. And for a post-09:30 engine the Gift-Nifty *value* (predicting the open) is
  already realised in `day_open` vs `prev_day_close` — the **opening gap**. So we fold
  the opening gap in as the overnight-sentiment vote and skip a fragile external feed.

---

## Architecture — the six layers (crypto plan → India)

**A. Market-Context Engine** (`src/market_context.py`) — the per-scan "what regime is
it now" vector: `session_phase` (power-hour / midday-chop / closing), `vix_regime`,
`pcr`, `market_direction` (composite vote), `leader` (NIFTY vs BANKNIFTY),
`fii_dii_net_cr`, `open_gap_pct`, `is_expiry_day`. Folded once from the index contexts;
stamped on every signal.

**B. Strategy portfolio + affinity** — the 14 evaluators, each with structural affinity
(`REGIME_AFFINITY` in `src/signal_quality.py`); affinity treated as *measured vs
expected*, not a hard one-day rule.

**C. Continuous edge measurement** (`src/strategy_edge.py`) — the Strategy×Context
edge matrix: realised win% / net% / **cost-adjusted expectancy** per
`(setup, direction, session_phase, vix_regime, market_direction)` cohort, over the
already-resolved `india_signal_outcomes`. Exposed at `/api/edge-matrix`.

**D. Autonomous allocator** (`src/strategy_allocator.py`) — reads context × edge →
per-cohort **EMIT / SUPPRESS / HOLD / INSUFFICIENT_DATA** verdicts. **Recommendation
mode** (observe-only) at `/api/allocator`; not yet armed to gate emission.

**E. Safety envelope** — existing gate chain + the new `direction_bias_gate`; every new
behaviour env-flagged and reversible; scoring adjustment bounded ±cap and sample-gated.

**F. Ops observability** (`lumin-india-ops`) — **Edge** and **Allocator** views render
C and D on real data.

---

## Execution status (all merged to `main` this session)

| Item | What | Ships | PR |
|---|---|---|---|
| Phase 0 | Unblock + merge **PR #54** (chop/TP-feasibility gates, PCR wiring, LSR key-level, two-target TP2/BE plan). Was red on one mypy error. | sign-off | #54 |
| Doctrine | `INDIA_MARKET_DOCTRINE.md` | docs | #55 |
| A | Market-context backbone; `market_direction/session_phase/vix_regime` stamped on every signal (→ API + ops + CSV) | normal | #55 |
| E | **`direction_bias_gate`** — suppress counter-trend in a *decisive* tape (the SHORT-13% bleed). Replay: keeps 50 longs (56%/+11.6%), cuts 45 shorts (13%/−5.6%) | sign-off | #55 |
| C | Strategy×Context **edge matrix** + `/api/edge-matrix` | normal | #56 |
| D | **Allocator** (recommendation mode) + `/api/allocator` | normal | #57 |
| F | ops **Edge** view (#7) + **Allocator** view (#8) | normal | ops #7,#8 |
| B/tier | **Edge-aware confidence adjustment** — bounded ±8, sample-gated (≥20 resolved); the non-overfit "tier recalibration" | sign-off | #58 |
| A v2 | **FII/DII** once-daily feed + **opening-gap** direction votes (no Gift-Nifty feed) | sign-off | #58 |

**Deliberately NOT shipped:** a hard `session_phase_gate` (midday-chop suppression).
The 07-13 replay showed midday *breakouts* were the best cohort (48%/+3.74%), so a
blanket rule would cut winners. `session_phase` stays a measured dimension for the edge
matrix / allocator to act on with data — measured, not assumed.

---

## What remains (owner-gated / data-gated)

1. **Let the 30-day window fill.** The edge matrix, the edge-adjust, and the allocator
   all get more reliable as real sessions accrue (IB10 is the arbiter). Today only the
   largest cohort (VSB/LONG, n=26) crosses the sample floor.
2. **Arm the allocator** — flip it from recommendation-mode to actually gating emission
   (weight / emit-floor / setup-enable by measured edge). **Owner sign-off**, only once
   its recommendations visibly track live outcomes.
3. **Activate FII/DII** — point `INDIA_FII_DII_URL` at a live NSE FII/DII source (or a
   thin adapter returning `{"fii_net_cr":…, "dii_net_cr":…}`). Until then the vote stays
   NEUTRAL; the opening-gap vote already works.
4. **Optional ops** — panels for the macro votes / live context vector.

---

## Owner sign-off gates (never auto-merge)
- `AUTO_EXECUTION_ENABLED` — never, ever.
- Arming the allocator to gate emission.
- Any further scoring-model change (weights, new evaluator).
- Business-rule (IB1–IB18) edits.

## Verification standard (every change met this)
- `python -m pytest tests/ -q` green; `ruff check src config` + `mypy src config` clean;
  `ast.parse` pre-commit syntax check.
- **Replay gates/adjustments against the uploaded CSV** before trusting them (validate,
  don't tune) — then the 30-day ledger judges.

## The one-line reframe (from the doctrine)
**We were building a faster retail taker. The NSE pays the house, and the house is
selective, direction-aligned, session-aware, positioning-aware, and willing to stand
down. Every change moved us one step from the taker toward the house.**
