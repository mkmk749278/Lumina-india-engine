# ACTIVE_CONTEXT.md — Lumin India

**Last updated:** 2026-07-01 (Session 4)

---

## Current Phase

**Phase 1 — Engine build in progress. All 14 evaluators coded. No live deployment yet.**

Phase 1 goal: Signal delivery to lumin-india-app via FCM + REST. No Telegram. No auto-execution.
Phase 2 (auto-execution) is locked until SEBI RA registration + NSE empanelment + 30-day signal quality review.

---

## Repos

| Repo | Purpose | Branch convention | Status |
|---|---|---|---|
| `mkmk749278/Lumina-india-engine` | NSE F&O scanner, evaluators, API server, execution (Phase 2) | `feat/`, `fix/`, `docs/`, `chore/` | ACTIVE — 9 PRs merged, PR #10 in draft |
| `mkmk749278/lumin-india-app` | Flutter Android app (standalone Play Store listing) | same | Bootstrapped (PR #1 merged, operating docs + CI) |
| `mkmk749278/lumin-india-ops` | Ops dashboard (extends ops.luminapp.org pattern) | same | Needs CLAUDE.md |

---

## What's Built (engine, on `main` + PR #10)

### Merged to `main` (PRs #1–#9)

| PR | What |
|---|---|
| #1 | Operating docs + self-scaling CI |
| #2 | Engine skeleton: `config/__init__.py`, SessionManager, HolidayManager, ExpiryManager |
| #3 | Market substrate: `indicators.py`, `regime.py`, `structure_state.py` |
| #4 | Level Book + order blocks / FVG |
| #5 | Signal contract (`IndiaSignal`, `IndiaContext`) + confidence scoring model |
| #6 | Evaluator framework + LIQUIDITY_SWEEP_REVERSAL |
| #7 | ORB, VSB, BDS evaluators |
| #8 | INDIA_VIX_EXTREME, PCR_EXTREME + candlestick patterns |
| #9 | TREND_PULLBACK_EMA, OI_SPIKE_REVERSAL (owner sign-off) |

### In draft PR #10 (awaiting owner sign-off)

6 remaining evaluators completing the full 14-evaluator set:
- **SrFlipRetest** — broken S/R flip + rejection. Short-only default.
- **FailedAuctionReclaim** — false breakout above/below OR, volume-confirmed reclaim.
- **DivergenceContinuation** — RSI divergence on 5m, HTF-filtered.
- **QuietCompressionBreak** — BB squeeze breakout, time-gated 10:00–14:00 IST.
- **MaCrossTrendShift** — EMA21/55 crossover on 15m, HTF-filtered.
- **ExpiryGammaSqueeze** — expiry-day only, 13:00–15:00 IST, price→max-pain.

PR #10: https://github.com/mkmk749278/Lumina-india-engine/pull/10

### Source file inventory (20 files)

| Concern | File | Status |
|---|---|---|
| Config (env-overridable) | `config/__init__.py` | Complete — all 14 evaluator configs |
| Session gate | `src/session/session_manager.py` | Complete |
| Holiday calendar | `src/session/holiday_manager.py` | Complete |
| Expiry resolution | `src/session/expiry_manager.py` | Complete |
| Indicators (EMA, ATR, RSI, BB) | `src/indicators.py` | Complete |
| Regime classification | `src/regime.py` | Complete |
| Structure state (BOS/CHoCH) | `src/structure_state.py` | Complete |
| Level Book (S/R aggregation) | `src/level_book.py` | Complete |
| Order blocks + FVG | `src/order_blocks.py` | Complete |
| Candlestick patterns | `src/patterns.py` | Complete |
| Candle model | `src/market/candle.py` | Complete |
| Signal model (IndiaSignal, IndiaContext) | `src/signals/model.py` | Complete |
| Confidence scoring (8-component) | `src/signal_quality.py` | Complete |
| Evaluator base class | `src/channels/base.py` | Complete |
| 14 evaluators | `src/channels/india_scalp.py` | Complete (PR #10 pending merge) |
| Logger | `src/utils.py` | Complete |

### Test suite

**104 tests**, all passing. `ruff` clean. Covers all evaluators (emission + rejection paths), scoring, regime, indicators, session management, patterns, structure state.

---

## Infrastructure Status

| Item | Status | Notes |
|---|---|---|
| VPS provisioned | DONE | IP: `95.111.241.97`, domain: `lumintrade.app` |
| Fyers API app created | DONE | App ID: `PKMQRMWUZG-100`. **Secret key compromised** (shared in chat) — owner must regenerate at myapi.fyers.in |
| Store Fyers creds in GitHub Secrets | PENDING | After secret key regeneration: `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY` |
| Firebase project | PENDING | |
| Razorpay account | PENDING | |
| NSE algo provider empanelment | PENDING | Phase 2 blocker only |
| Play Store listing | PENDING | |
| SSH deploy key | PENDING | After VPS provisioned with Docker |

---

## Key Decisions (locked)

| Decision | Detail |
|---|---|
| No Telegram | Signal delivery is app-only. FCM push + REST API. |
| Standalone Android app | Separate Play Store listing, not an extension of crypto app. |
| Dedicated VPS | `95.111.241.97` / `lumintrade.app`. Static IP for Fyers whitelist. |
| Fyers API v3 | Free for account holders. WebSocket + REST. |
| Index futures only (Phase 1) | NIFTY and BANKNIFTY weekly near-expiry. |
| AUTO_EXECUTION_ENABLED | Locked false until SEBI RA + NSE empanelment + 30-day quality + owner sign-off. |
| Lot sizes | NIFTY: 75 units/lot. BANKNIFTY: 35 units/lot. |
| Razorpay billing | Google Play Billing disallowed for trading services in India. |

---

## Known Issues / Pending Decisions

### SL-floor tension (owner decision needed)

Multiple evaluators (SR_FLIP, FAR, TPE, VSB, PCR) produce SL distances under their configured % floors at normal NIFTY 5m ATR (~15–30 pts). This means they'll fire rarely or never in calm market conditions. Test fixtures use elevated ATR (60–100 pts) to exercise full paths.

**Options for owner:**
1. Lower SL-% floors (accept tighter stops)
2. Change SL basis to wider structural stops (prev-bar swing, multi-bar lookback)
3. Accept selective firing (evaluators only emit in volatile conditions)

Decision affects ~5 evaluators' real-market emission rates. Must be resolved before the 30-day quality window starts.

### Fyers secret key compromised

Owner shared Fyers API secret key (`VV4BBFGNM2`) in chat. Must regenerate at myapi.fyers.in before storing in GitHub Secrets. The old key should be considered compromised.

### Spec inconsistencies (flagged, not blocking)

- Lot sizes: spec says 65/30 in one place, 75/35 in another. Using 75/35 (current NSE-mandated).
- BANKNIFTY min-scalp: spec says 40 in one place, 25 in another. Using 40 (STT-aware).
- Futures expiry cadence: weekly vs monthly varies by spec section. Using weekly (current NSE schedule).

---

## Open Queue (in priority order)

1. **BLOCKED — PR #10 owner sign-off** — remaining 6 evaluators. Take out of draft to approve.
2. **Owner action: Regenerate Fyers secret key** — compromised by chat exposure. Store new key in GitHub Secrets.
3. ~~CTE: Create GitHub repos~~ — DONE
4. ~~CTE: Engine skeleton~~ — DONE (PR #2)
5. ~~CTE: Market substrate~~ — DONE (PRs #3–#4)
6. ~~CTE: Signal model + scoring~~ — DONE (PR #5)
7. ~~CTE: 14 evaluators~~ — DONE (PRs #6–#10, pending merge of #10)
8. **CTE: Fyers WebSocket integration** — tick store, historical data fetch at session open, OI store
9. **CTE: Scanner + gate chain** — wire evaluators to the 30s scan loop, 9-gate suppression chain
10. **CTE: Signal router + FCM dispatch** — SQLite write + Firebase Admin SDK push
11. **CTE: API server** — `/api/india/signals`, `/api/india/session`, `/api/india/pulse`, FCM token reg
12. **CTE: Docker compose + deploy** — `docker-compose.india.yml`, Dockerfile, `deploy.sh`
13. **CTE: lumin-india-app build** — Flutter project, auth, signal list, subscription
14. **CTE: Razorpay integration** — in-app billing, server-side verification
15. **Owner: SL-floor decision** — affects ~5 evaluators
16. **Owner: Phase 1 go-live** — review scanner on real NSE data, approve signal delivery

---

## Crypto Engine (360-v2) Open Items

(Carry forward until addressed in a 360-v2 session)
- Run `btc_state_backfill.py` on VPS after PR #676 deployment confirmed stable
- If BTC-State thesis confirms on backfill data: bring graded soft-confirmation wiring design for owner sign-off
- Layer-2 ops for Profit page: BTC-State-conditioned signal filters
- Scorer calibration: investigate 75–80 band inversion

---

## Session Log

| Session | Date | Key outcomes |
|---|---|---|
| 1 | 2026-07-01 | Market research complete. Full AI handover spec (27 parts). Architecture locked. CLAUDE.md + ACTIVE_CONTEXT.md. No Telegram. Standalone app. Fyers API v3. |
| 2 | 2026-07-01 | Bootstrapped repos (PR #1 each). Engine skeleton: config, SessionManager, HolidayManager, ExpiryManager. 22 tests. |
| 3 | 2026-07-01 | Market substrate (indicators, regime, structure, level book, order blocks). Signal model + scoring. All 14 evaluators built across PRs #3–#10. 104 tests. PR #9 merged (owner sign-off). PR #10 opened (draft, awaiting sign-off). Fyers API setup guidance. Security incident: owner shared API secret in chat — instructed to regenerate. SL-floor tension identified across ~5 evaluators. |
| 4 | 2026-07-01 | Updated ACTIVE_CONTEXT.md to reflect actual state. PR #10 still in draft. |
