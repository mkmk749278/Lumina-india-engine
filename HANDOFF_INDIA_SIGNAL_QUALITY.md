# Handoff — India Signal-Quality Program (new-session runbook)

*Everything a fresh CTE session needs to continue the signal-quality work without
re-deriving it. Companion to `PLAN_INDIA_SIGNAL_QUALITY.md` (the plan) and
`INDIA_MARKET_DOCTRINE.md` (the market-structure "why"). As of 2026-07-13.*

---

## Read first (in order)
1. `OWNER_BRIEF.md` (IB1–IB18) · `ACTIVE_CONTEXT.md` (Session 20 is this work)
2. `INDIA_MARKET_DOCTRINE.md` · `PLAN_INDIA_SIGNAL_QUALITY.md`
3. This file.

## Branch & phase
- Work branch (all 3 repos): **`claude/indian-stock-signals-quality-b3fz40`**.
- Everything below is **merged to `main`** across `Lumina-india-engine` and
  `lumin-india-ops`. Phase 1 — no live users, `AUTO_EXECUTION_ENABLED=false`.
- Cut a fresh branch off `main` HEAD for new work (do not reuse a merged branch).

## What is LIVE on `main` (engine)
Signal path today: scanner builds contexts → **MarketContext folded per scan** →
gate chain (incl. **direction_bias_gate**) → scorer (incl. **edge-aware adjustment**)
→ emit → `india_signals` (with market-context columns) → outcomes → **edge matrix** →
**allocator (observe-only)**.

### New modules
| File | Role |
|---|---|
| `src/market_context.py` | Per-scan `MarketContext` (session_phase, vix_regime, pcr, market_direction, leader, fii_dii_net_cr, open_gap_pct, is_expiry_day). `classify_market_direction` votes: NIFTY/BANKNIFTY intraday bias + NIFTY daily regime + **FII/DII** + **opening gap**; decisive = ≥2 aligned, 0 opposing. |
| `src/strategy_edge.py` | Edge matrix (`build_edge_matrix`, `get_edge_matrix`) + `build_edge_index`/`get_edge_index` (the `(setup,direction)→{n,ev_net_pct}` lookup the scorer reads). Win = any TP1-banked outcome; EV = mean realised % − round-trip cost. |
| `src/strategy_allocator.py` | `get_allocation` → per-cohort EMIT/SUPPRESS/HOLD/INSUFFICIENT_DATA verdicts. **Recommendation mode only** (`mode:"recommendation"`). |
| `src/data/india_macro_store.py` | `IndiaMacroStore` — prev-day FII/DII, once-daily fetch, freshness-gated, graceful-unavailable → NEUTRAL. |

### Changed hot spots
- `src/scanner/__init__.py`: builds `MarketContext` per scan (after index-bias), stamps
  `market_direction` on each ctx; `_direction_bias_gate` in the pre-score chain;
  `set_edge_index()` passthrough to the scorer.
- `src/signal_quality.py`: `_score_measured_edge` (bounded, sample-gated) added to the
  score sum; `set_edge_index()`.
- `src/data/india_context_builder.py`: injects `macro_store`, stamps `fii_dii_net_cr`.
- `src/main.py`: instantiates `IndiaMacroStore`; at each session-OPEN entry calls
  `macro.refresh()` and `scanner.set_edge_index(await strategy_edge.get_edge_index())`
  (once per open — not per scan).
- `src/signal_store.py`: `india_signals` gained `market_direction/session_phase/vix_regime`
  (migration-added); `get_resolved_signals(days)`.
- `src/api/server.py`: `GET /api/edge-matrix` and `GET /api/allocator` (both bearer-auth).

### New API endpoints (engine, bearer token)
- `GET /api/edge-matrix?days=N` — the edge matrix.
- `GET /api/allocator?days=N` — allocator recommendations (observe-only).

### Ops (lumin-india-ops) — new views
- **Edge** (`/edge`) renders `/api/edge-matrix`; **Allocator** (`/allocator`) renders
  `/api/allocator` with a prominent observe-only banner. Client: `engine_api.edge_matrix()`,
  `engine_api.allocator()`.

## Env knobs (all default to safe/inert; `_safe_*` in `config/__init__.py`)
| Env | Default | Effect |
|---|---|---|
| `INDIA_DIRECTION_BIAS_GATE_ENABLED` | true | false = no counter-trend suppression |
| `INDIA_DIRECTION_GATE_EXEMPT_SETUPS` | "" | CSV of setups exempt from the gate |
| `INDIA_POWER_HOUR_END` / `INDIA_MIDDAY_END` | 10:30 / 13:30 | session-phase boundaries |
| `INDIA_VIX_LOW_THRESHOLD` | 14.0 | LOW-vix boundary |
| `INDIA_ALLOCATOR_MIN_SAMPLE` | 20 | resolved-trade floor to judge a cohort |
| `INDIA_ALLOCATOR_EV_FLOOR` / `_SUPPRESS_EV` | 0.0 / −0.05 | EMIT / SUPPRESS thresholds |
| `INDIA_EDGE_ADJUST_ENABLED` | true | false = exact prior scoring |
| `INDIA_EDGE_ADJUST_CAP` / `_K` | 8.0 / 20.0 | ± cap; delta = clamp(K·ev, ±cap) |
| `INDIA_FII_DII_URL` | "" | **set to activate FII/DII** (else NEUTRAL) |
| `INDIA_FII_DII_MIN_CR` | 500.0 | ₹cr net magnitude to cast a vote |
| `INDIA_OPEN_GAP_MIN_PCT` | 0.3 | gap % to cast the opening-gap vote |
| `INDIA_MACRO_TTL_SEC` | 86400 | FII/DII freshness window |

## Data status (important context for next steps)
- Evidence base is **n≈95, one day (07-13)**. The edge-adjust and allocator are
  **designed to be inert on thin samples** — today only **VSB/LONG (n=26)** crosses the
  n≥20 floor. Trust grows as the **30-day window** (IB10) fills. Do **not** hand-tune
  scoring on one day.

## How to continue (next steps, in order)

### 1. Activate FII/DII (low-risk, no sign-off)
Point `INDIA_FII_DII_URL` at a source returning JSON with `fii_net_cr` / `dii_net_cr`
(NSE FII/DII report or a thin adapter). Verify: `curl -H "Authorization: Bearer $TOKEN"
$BASE/api/pulse` shows the feed alive; `IndiaMacroStore.snapshot()` becomes `available`.
Parser tolerates key spellings in `_FII_KEYS`/`_DII_KEYS`.

### 2. Watch, don't arm (owner-gated)
Let real sessions accrue; watch ops **Edge** and **Allocator**. Confirm the allocator's
EMIT/SUPPRESS verdicts track realised outcomes before arming anything.

### 3. Arm the allocator (OWNER SIGN-OFF — the big remaining step)
Turn recommendation-mode into action. Design already scoped:
- Load the allocation at session open (same hook as `set_edge_index`, `src/main.py`
  OPEN-entry block).
- In the emission stage (`src/scanner/__init__.py`, `check_emission` / the emit loop),
  suppress cohorts the allocator marks `SUPPRESS`, keeping the existing safety envelope.
- Gate behind a new flag (default OFF); **replay against the accrued CSV first**; ship
  dark, measure, then owner flips it on. Never act on `INSUFFICIENT_DATA`.

### 4. Optional
Ops panels for the macro votes / live context vector (needs a small engine
`/api/market-context` endpoint exposing the latest `MarketContext`).

## Verification (run before any PR)
```bash
cd Lumina-india-engine
python -m pytest tests/ -q          # 495 green as of this handoff
ruff check src config && mypy src config
# ops:
cd ../lumin-india-ops && python -m pytest -q && ruff check app
```
Replay a gate/adjustment against a signals CSV the way this session did (validate, don't
tune) before trusting it; the 30-day ledger is the final arbiter.

## Guardrails to respect
- No scaffolds — store *and* consume in the same change.
- No new per-tick/per-scan network reads (macro + edge index are **session-open**).
- New data unavailable → NEUTRAL, never fabricated.
- Scoring/gate/new-data changes are **owner-sign-off** — do not auto-merge.
- Every new behaviour stays env-flagged and reversible.
