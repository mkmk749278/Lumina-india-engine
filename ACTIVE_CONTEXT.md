# ACTIVE_CONTEXT.md — Lumin India

**Last updated:** 2026-07-21 (Session 23 — strategy-gap verification + net-new
gaps. Branch `claude/new-session-jo0eon`, engine.)

---

## Session 23 (2026-07-21) — Strategy-gap handoff verification + net-new gaps

**Trigger:** owner uploaded the ops-session `ENGINE_STRATEGY_GAP_HANDOFF2`
(inferred from signal output, *no engine code in that session*) plus the live
07-15→07-21 ledger CSV + Edge/Allocator PDFs, and authorised implementing every
gap "like production."

**Verification (each G-item checked against code + the live ledger):** most of
the handoff's §5 rebuild spec was **already built** (Sessions 9/19/20/21). G1
(ATR stops), G5 (4-quadrant OI), G6 (direction gate), G7 (phase), G8
(macro/expiry) refuted or already-present; G3/G7 corrective levers are
**built-but-dark**; G4 fixed in shadow. The real bleed = a LONG book in a
choppy week the **allocator already flags SUPPRESS** but is unarmed. Full table
+ arming runbook in `docs/STRATEGY_GAP_REBUILD.md`.

**Shipped (engine, this branch — all reversible, env-flagged, tested):**
- **G8 earnings blackout gate** — `src/session/earnings_calendar.py` +
  `_earnings_blackout_gate`; `config/earnings_events.json` (env-pointable,
  inert until populated — never fabricated). Index bases exempt.
- **G2 structural TP1** — `_structural_tp1` replaces pure fixed-2R for ORB/VSB;
  snaps to nearest real level in the `[MIN_RR, R]` band, else exact 2R fallback.
  `INDIA_STRUCTURAL_TP1_ENABLED` (default on).
- **G5 OI walls as S/R** — call/put OI walls derived in the chain poll
  (`IndiaMarketData.compute_and_set_oi_walls`), stamped on context
  (`call_oi_wall`/`put_oi_wall`), fed to confluence scoring + structural
  targets (`_oi_levels`). Index bases, Fyers chain feed, graceful-absent.
- **v2 calibration tool** — `tools/v2_calibration.py`: the monotonicity check
  to run before flipping `INDIA_SCORING_V2_ACTIVE`.

**NOT changed (owner-gated, need the VPS replay I can't run here):**
`INDIA_ALLOCATOR_ARMED` and `INDIA_SCORING_V2_ACTIVE` stay dark. CTE
recommendation: **arm the allocator first** (highest leverage, already built,
covers the `NEUTRAL/LONG` cohort the direction gate misses); hold v2 until the
calibration tool reports READY. Exact VPS commands in the runbook.

**Tests:** 580 green (was 553), ruff + mypy clean.

---

## Session 22 (2026-07-17) — CLAUDE.md Reality Reconciliation (docs only)

**Trigger:** scheduled documentation pass — CLAUDE.md in all three repos had
drifted from the code.

**Shipped (no code changes anywhere):**
- **Engine CLAUDE.md rewritten to as-built state:** Module Map now lists only
  files that exist (trade_monitor, signal_store, strategy_edge/allocator,
  broker/ package incl. AngelOne alternate feed, macro store, event calendar,
  replay harness, db/db_backup, owner_alerts) with the Phase-2 layer
  (`src/execution/`, `src/security/`, `src/billing/`, snapshot writer)
  explicitly marked planned-not-on-disk. Architecture corrected to the real
  3-container stack (india-redis / india-engine / india-autoheal, API served
  in-process). API routes corrected (`/api/...`, no `/india/` segment; added
  pulse, outcomes, edge-matrix, allocator, admin, fyers/callback). New
  Feature Flags section documents the Session-21 ACTIVE/SHADOW/DARK flags.
  Stale "NIFTY+BANKNIFTY only" Phase-1 wording replaced with the IB1
  owner-expanded universe (the old hard-limit line contradicted IB1 and this
  file's own Conventions). Dark-flag arming added to owner-sign-off list.
- **App CLAUDE.md rewritten:** real API contract (`/api/` prefix; pulse,
  outcomes, session-summary, fcm-token), actual 3-tab navigation (no named
  routes), generated-`android/` CI flow documented, Razorpay/paywall moved
  to a clearly-labeled planned-not-built section, Session-tab analytics /
  two-target plan / live P&L documented.
- **Ops CLAUDE.md updated:** edge-matrix + allocator endpoints added,
  NOT_TRIGGERED/ambiguous-tie outcome handling, runtime env vars, ruff in
  Commands, analytics/exports module notes.

**No open items added.** Session 21 forward plan unchanged and still owns
next steps (NT-rate watch, direction-v2 judgement window, scoring-v2
calibration, allocator arming decision, geometry floor re-set).

**Trigger:** owner uploaded the 5-session live artifacts (334 resolved,
28.4% win, +4.93% gross, **−0.045%/trade after the 0.06% cost model**) and
asked for a full audit — with the constraint that *disabling paths or
cutting volume ≠ good signals*.

**Audit verdict (data + code):**
1. **The ledger itself was distorted** — every conclusion downstream of it
   (edge matrix, allocator verdicts, edge-nudge) inherited the distortion:
   - ORB/VSB/BDS print resting-LEVEL entries better than market at emit;
     the monitor assumed instant fills → VSB's 50% "best setup" win rate
     partly fictitious (B1).
   - Outcomes resolved on 5m candles with a conservative same-candle
     SL+TP tie → SL_HIT; median SL (~0.20%) fits inside one 5m bar →
     191 SL_HITs partly resolution artifact (B2).
   - `_derive_tp2` pinned mapped TP2s to the band bottom; TP1_HIT is a
     legacy single-target status crediting 100% at TP1 → outcome cohorts
     not comparable (B3).
2. **Confidence is anti-predictive by construction** (55-60 conf → 33.8%
   win; 75+ → 19.4%; tier A −5.29% net vs tier B +8.93%): regime 15 + HTF
   12 + BOS 7 + index 5 = up to 39/100 all restating "trend fully
   established" — the late/exhausted condition. TPE collected 68/161 A
   tiers with negative EV.
3. **Direction is lagging beta**: rising week LONG 38%/SHORT 12%; falling
   week the exact mirror. The v1 bias latches after the move is mature and
   the direction gate then forces entries into exhaustion
   (LONG_BIASED/LONG cohort: 6.2% win). FII/DII vote was unwired.
4. **Geometry is sub-cost**: median TP1 0.45% / SL 0.20% vs 0.06% cost;
   gross avg +0.015%/trade. Churn, not edge. (Geometry floors deliberately
   NOT changed this session — they get re-set on the corrected ledger.)
5. "numpy errors" — red herring: zero numpy/pandas in engine+ops; broker
   SDKs log those. Real bug found instead: unguarded `next()` in
   DivergenceContinuation, swallowed by the scanner's blanket except (B5).

**Shipped (engine, 9 commits on the branch):**
- **Truth track (ACTIVE by default — measurement, not strategy):**
  entry-trigger state machine (LEVEL entries fill only when price trades
  through entry; `NOT_TRIGGERED` outcome excluded from all win/EV
  denominators; `INDIA_ENTRY_TRIGGER_ENABLED=true`), 1m outcome
  resolution with per-signal 5m fallback
  (`INDIA_OUTCOME_RESOLUTION_TF=1m`; new 1m ring in the tick store),
  TP2 target-anchored selection (`INDIA_TP2_SELECT_MODE=target_anchored`),
  MFE/MAE + bars-to-resolve + resolving-TF + ambiguous-tie persisted per
  outcome, extension-at-entry (VWAP/EMA21, ATRs) + bias-age + dup-ordinal
  stamped per signal, edge matrix rebuilt (NT excluded, net-win%, legacy
  segregation, extension/dup dimensions, pre-migration rows excluded from
  context cohorts instead of the "?" pollution).
- **Shadow (measured, nothing gates on them):** scoring v2
  (`confidence_v2` + component JSON; one trend read ≤15, extension
  penalty 0..−10, phase affinity ≤8, freshness ≤7; v1 still drives
  tiers/delivery) and direction v2 (`market_direction_v2`/`index_bias_v2`;
  VWAP/EMA/day-change majority vote, live from the first bar).
- **Dark / default-OFF (owner sign-off to arm):** phase-affinity blocklist
  gate, duplicate entry-move gate, **allocator SUPPRESS arming**
  (`INDIA_ALLOCATOR_ARMED=false` — would-suppress decisions are dark-
  logged every session; verdicts re-derived at each open, self-reversing).
- **Hygiene:** DivergenceContinuation StopIteration guards; per-evaluator
  error counters + FII/DII macro snapshot in `/api/pulse`; spread-gate
  stub deleted (no bid/ask in the lite tick — no stubs); NSE
  `fiidiiTradeReact` list-shape parser (point `INDIA_FII_DII_URL` at the
  NSE report or an adapter); optional intraday daily-regime refresh
  (`INDIA_DAILY_REGIME_REFRESH_MIN`, default 0 = frozen legacy).
- **Replay harness:** `tools/replay.py` (re-resolves the stored ledger
  under any rule set using the SAME `walk_signal` as production; candle
  CSV cache + `--fetch` via Fyers REST) and `tools/replay_gates.py`
  (kept-vs-cut cohort EV for any candidate gate spec). Parity pinned by
  tests. **Run on the VPS** (needs the prod DB + a Fyers token for the
  first candle fetch):
  `python -m tools.replay --db data/india_db.sqlite3 --candles ./candle_cache --fetch --resolution 1 --entry-trigger on --out replay_report.csv`
- **Ops:** Outcomes (NT/ambiguous counts, MFE/MAE, resolving TF), Edge
  (net-win%, NT/legacy, extension + dup dimensions, exclusion counts),
  Quality (NT column), Pulse (evaluator errors, macro snapshot).

**Tests:** engine 553 (was 504) + ops 24, ruff + mypy clean.

**What the next sessions do (in order):**
1. First live session on the corrected ledger: watch NT rate per setup,
   ambiguous-tie rate (should collapse vs the 5m era), VSB's honest win
   rate. Run the replay harness on the VPS for the historical comparison.
2. Let ≥5 forward sessions accrue → judge direction v2 vs v1 (the
   LONG_BIASED/LONG death cohort must shrink) → owner sign-off to feed
   the direction gate from v2.
3. ≥10 forward sessions → scoring-v2 calibration check (v2 buckets must
   be monotonic where v1's inverted) → owner sign-off
   `INDIA_SCORING_V2_ACTIVE` + tier recalibration.
4. Watch the dark allocator would-suppress log track outcomes → owner
   sign-off `INDIA_ALLOCATOR_ARMED=true`.
5. Re-set geometry floors (MIN_SCALP_COST_MULT etc.) on the corrected
   ledger's MFE/MAE distributions — NOT before.

---

## Current Phase

**Phase 1 — LIVE ON REAL NSE DATA since 2026-07-03 12:59 IST.** The 30-day
signal-quality window (Phase 2 prerequisite) starts counting from today.

Phase 1 goal: Signal delivery to lumin-india-app via FCM + REST. No Telegram. No auto-execution.
Phase 2 (auto-execution) is locked until SEBI RA registration + NSE empanelment + 30-day signal quality review.

**Live right now (`https://lumintrade.app` → `95.111.241.97`):**
- Fyers data feed connected — historical seed (45×5m candles per base),
  WebSocket ticks (`NSE:NIFTY26JULFUT`, `NSE:BANKNIFTY26JULFUT`,
  `NSE:INDIAVIX-INDEX`), 60s OI polling
- Scanner running every 30s during market hours; verified via `/api/pulse`
  (`session_state: OPEN`, scan_count climbing)
- nginx :80 behind Cloudflare (SSL mode **Flexible** — origin-CA cert +
  Full (strict) still pending, see Open Queue)
- **Daily token (SEBI mandates daily 2FA on all broker APIs, fully
  enforced since 2026-04-01):** one tap on the bookmarked Fyers login
  URL → `/fyers/callback` exchanges the code server-side and hot-swaps
  the feed, no Termux. OR switch to `DATA_FEED=angel` (PR #26) for
  zero-touch once the owner's Angel One account exists.

---

## Repos

| Repo | Purpose | Branch convention | Status |
|---|---|---|---|
| `mkmk749278/Lumina-india-engine` | NSE F&O scanner, evaluators, API server, execution (Phase 2) | `feat/`, `fix/`, `docs/`, `chore/` | ACTIVE — 27 PRs merged, live on real NSE data |
| `mkmk749278/lumin-india-app` | Flutter Android app (standalone Play Store listing) | same | ACTIVE — PR #7: Firebase + FCM wired (open). Foundation PRs #2–#3 merged. Owner device test pending. |
| `mkmk749278/lumin-india-ops` | Ops dashboard (owner-only diagnostic) | same | ACTIVE — all 5 views implemented (Pulse, Signals, Suppressed, Outcomes, Quality). Auth working. |

---

## What's Built (engine, all merged to `main`)

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
| #10 | Remaining 6 evaluators — SRF, FAR, DIV, QCB, MAC, EGS (owner sign-off) |
| #11 | Data stores: IndiaTickStore, IndiaOIStore, IndiaMarketData, IndiaContextBuilder |
| #12 | Scanner + 9-gate chain: scan loop, suppression telemetry, scoring wire-up |
| #13 | VPS bootstrap script (Docker, nginx, Cloudflare Origin CA) |
| #14 | Fyers data feed: WebSocket ticks + REST historical/OI/VIX |
| #15 | GitHub Actions deploy pipeline + engine entry point (`src/main.py`) |
| #16 | API server + signal persistence + nginx reverse proxy |
| #17 | Dockerfile fix: /app/data writable by appuser |
| #18 | Verified NSE 2026 holiday calendar (circular NSE/CMTR/71775) |
| #19 | Daily Fyers token helper (`scripts/fyers_token.py`) + deploy no longer wipes VPS-set `.env` values |
| #21 | Token helper: Cloudflare UA block fix; engine tests no longer boot real uvicorn (CI hang) |
| #22 | Fyers data endpoints `/data/*` (were 404 under `/api/v3/`), futures symbol `-FF` suffix removed, `fyers-apiv3` dependency added |
| #24 | Refresh-token automation (built, then Fyers disabled the API citing SEBI — script retained) |
| #25 | One-tap daily token refresh: `/fyers/callback` exchanges the auth code server-side + hot-swaps the feed |
| #26 | Angel One SmartAPI feed (`DATA_FEED=angel`) — zero-touch daily TOTP auth, OI on every tick |
| #27 | Prev-day levels bug: `set_prev_day` was never called — evaluators keyed on PDH/PDL were blind. 96h fetch + date bucketing |
| #33 | FCM push dispatcher: Firebase Admin SDK init, `india_fcm_tokens` table, `POST /api/fcm-token` endpoint, signal_router fan-out, deploy secret injection. 20 new tests. |

### Signal delivery path (wired end-to-end as of PR #16)

```
FyersDataFeed → stores → IndiaScanner (30s) → 14 evaluators → 9 gates → scoring
    → IndiaSignalRouter → SQLite (india_signals, india_suppressions)
    → FastAPI (/api/signals, /api/pulse, /api/suppressed) → nginx :80
```

FCM push: **BUILT** (PR #33). Firebase Admin SDK dispatches push notification on every
signal emit. Notification body: symbol + direction + tier — never price targets.

### API surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/health` | none | liveness |
| `GET /api/pulse` | Bearer | uptime, session state, scan count, signals today |
| `GET /api/signals` | Bearer | signal list; filters: date, tier, setup_class, limit |
| `GET /api/signals/{id}` | Bearer | single signal |
| `GET /api/suppressed` | Bearer | recent gate suppressions |
| `GET /api/outcomes` | Bearer | TP1/SL/EXPIRED outcomes |
| `GET /api/session-summary` | Bearer | 30-day quality ledger |
| `POST /api/fcm-token` | Bearer | register FCM device token |

Auth: static Bearer token (`API_STATIC_TOKEN` GitHub secret → env). Firebase auth for app users comes with the app build.

### Test suite

**253 tests**, all passing. `ruff` + `mypy` clean.

---

## Infrastructure Status

| Item | Status | Notes |
|---|---|---|
| VPS provisioned | DONE | IP: `95.111.241.97` (fresh Ubuntu reinstall 2026-07-02), domain: `lumintrade.app` (live) |
| GitHub Actions deploy | DONE | Push to `main` → SSH deploy → rebuild containers. ssh-action v1.2.2. |
| GitHub Secrets | DONE | `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `API_STATIC_TOKEN`, `GH_PAT`, `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_DEPLOY_PUBKEY` |
| nginx reverse proxy | DONE | Installed by deploy workflow; `tools/setup-nginx.sh`; rate-limit 60 r/m |
| NSE holiday calendar | DONE | Verified against NSE circular NSE/CMTR/71775 (all 15 weekday holidays for 2026) |
| Fyers API app created | DONE | App ID: `QHX93US4FU-100` (recreated 2026-07-03; redirect URI `https://lumintrade.app/fyers/callback`). |
| Fyers access token | DONE (one-tap daily) | Bookmarked login URL → `/fyers/callback` auto-exchanges + hot-swaps the feed. First token live 2026-07-03. |
| Domain → VPS | DONE | `lumintrade.app` + `api.lumintrade.app` proxied via Cloudflare, HTTPS live (Flexible mode) |
| Firebase project | DONE | `lumin-india-d887d`, package `com.luminapp.india`. Secrets set: `FIREBASE_SERVICE_ACCOUNT_JSON` (engine), `GOOGLE_SERVICES_JSON` (app). |
| Razorpay account | PENDING | |
| NSE algo provider empanelment | PENDING | Phase 2 blocker only |
| Play Store listing | PENDING | |

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
| Single-process API | API server runs in the engine process (uvicorn task). Isolated api container is a Phase 2 scale decision, not needed at zero subscribers. |
| SSL via Cloudflare | nginx listens on 80; Cloudflare terminates TLS once the domain is pointed. |

---

## Known Issues / Pending Decisions

### SL-floor tension — LOOSENED (Session 8b), tighten-by-quality next

Root-caused: every evaluator's `MIN_SL_PCT` floor was 0.15–0.30% (a 42–84 pt
NIFTY stop at ~27,900), but IB11's STT-viable minimum is 15 NIFTY / 40
BANKNIFTY pts ≈ 0.054%/0.065%. The floors sat 3–5× above the real compliance
floor, so at normal 5m ATR (15–30 pts) most setups were suppressed — the
dominant emission bottleneck after the PR #38 lifecycle fixes.

**Decision taken (owner: "loosen first, then do it right one by one"):** option 1
— floors dropped to ~0.06% (≈ IB11) plus emit-floor 65→55 and VSB volume/OI
loosened. All env-overridable and independently tunable.

**Tighten-by-quality plan (per-evaluator, driven by 30-day outcome data):**
- Watch B-tier win rate vs the 55 emit floor → raise back toward 65–70 if noisy.
- Watch SL-hit rate per evaluator → raise that evaluator's `MIN_SL_PCT` or move
  it to a wider structural stop (option 2) if it's getting noise-stopped.
- `EXPIRY_GAMMA_SQUEEZE`, `SR_FLIP` (short-only) are the thinnest — review first.

### Fyers WebSocket never delivered ticks — FIXED (Session 8c, the real cause)

Live `/api/pulse` on 2026-07-07 showed `feed_connected: true`, `scan_count: 223`,
`signals_today: 0`, `suppressed_today: 0`, **`data_age_seconds: 6961`** — the
newest candle was ~2h old (frozen at the session-open seed). The engine was
scanning static seeded bars every 30s; no live tick ever moved the buffer, so
no fresh setup could form. This is the true chronic cause of near-zero signals
(the day-1 "0 signals + 0 suppressions" was the same symptom, misattributed).

Root cause: `FyersDataFeed._start_websocket` created the `FyersDataSocket` and
called `subscribe()` + `keep_running()` but **never called `connect()`**. In
fyers-apiv3, `connect()` is what starts `run_forever` (in a background thread)
and fires `on_connect`; `keep_running()` only spins a keep-alive loop. So the
socket never opened, `on_connect` never fired (its log line was absent), and no
ticks arrived. Fix: call `connect()` and issue `subscribe()` from the
`on_connect` callback (correct SDK lifecycle). Verified against the installed
SDK source + a regression test.

### `min_scalp_points` is a dead scaffold — RESOLVED (Session 9)

Wired as the central `min_scalp_gate` in the pre-score chain: TP1 distance
must clear `min_scalp_points_for(base, entry)` (index floors 15/40/15/30 pts;
stock bases price-relative via `INDIA_MIN_SCALP_PCT`, default 0.10%). Full
suppression telemetry. The per-evaluator `MIN_SL_PCT` floors are now pure
quality knobs.

### Signal-firing root causes — FIXED (Session 8, PR pending)

Owner reported near-zero signals ("one at 09:15, then nothing"). Deep trace of
the path/scoring/gates found a cluster of lifecycle + market-reality bugs, all
fixed this session:

1. **Stale intraday state** — `IndiaTickStore` never reset day-to-day, so
   `day_open` froze at the first session's open; once price drifted >5% the
   `circuit_check_gate` silenced every signal all day. Now auto-resets on the
   first tick of a new IST date.
2. **60m regime could never form** — EMA55 regime needs ≥56 60m bars; the seed
   supplied ~6, so `regime_60m` sat permanently RANGING and trend evaluators
   (TREND_PULLBACK_EMA, MA_CROSS) never fired. Seed window widened to ~11
   trading days and 15m/60m buffers now seeded directly from the full history.
3. **Stale prev-day levels** — a long-running Fyers container never re-derived
   PDH/PDL/PDC (source of the nonsensical 24325 far-target / RR 17.4). Added
   `feed.refresh_daily()`, called at the session-open transition.
4. **Expiry treated futures as weekly** — index futures are MONTHLY (last
   Tuesday). ExpiryManager now separates the monthly contract expiry (symbol,
   roll, days-to-expiry, card display) from the weekly-Tuesday flag (gamma /
   IB16). Fixes the card's wrong "Expiry 2026-07-07 (1d)".
5. **Lot sizes stale** — NSE Jan-2026 rebaseline NIFTY 75→65, BANKNIFTY 35→30.
6. **Throughput** — `duplicate_direction_gate` capped at 1/direction/day (≤4
   signals/day, below the 3+/day target). Now configurable
   (`INDIA_MAX_SIGNALS_PER_DIRECTION`, default 2). Redundant double gate-chain
   pass per candidate removed.

Owner tuning still open: SL-floor tension (below) and the per-direction cap.

### Fyers access token expires daily

Fyers OAuth access tokens are valid for one trading day. Until the signing-service daily-refresh automation is built (Phase 2 design), the owner runs `scripts/fyers_token.py` on the VPS each trading morning (PR #19 — exchanges the pasted auth code, verifies against the profile endpoint, writes `.env`, prints the restart command; deploys no longer wipe a VPS-set token). This is the **current blocker for live scanning**.

### Spec inconsistencies (flagged, not blocking)

- Lot sizes: spec says 65/30 in one place, 75/35 in another. Using 75/35 (current NSE-mandated).
- BANKNIFTY min-scalp: spec says 40 in one place, 25 in another. Using 40 (STT-aware).
- Futures expiry cadence: weekly vs monthly varies by spec section. Using weekly (current NSE schedule) — reconciliation with NSE contract reality still open (see config note).

---

## Open Queue (in priority order)

1. **CTE: merge PR #33 (engine FCM)** — CI fix pushed, awaiting green. Auto-merge once green (off-money-path).
2. **CTE: merge PR #7 (app Firebase + FCM)** — CI green. Auto-merge (off-money-path).
3. **Owner/CTE: Cloudflare Full (strict)** — generate Origin CA cert (SSL/TLS → Origin Server), install on nginx :443, flip mode from Flexible. Before real subscribers.
4. **Owner: app device test** — run "Build testing APK" workflow (now includes Firebase), install, verify FCM permission prompt + signal feed. Mandatory before further signal-screen work.
5. **CTE: app auth screens** — Firebase Phone OTP login. Owner sign-off item (auth flow change).
6. **Owner: SL-floor decision** — affects ~5 evaluators (see Known Issues). Needs ~5 trading days of data.
7. **CTE: Razorpay integration** — in-app billing, server-side verification. Blocked on owner Razorpay account.
8. **Owner: Phase 1 go-live review** — watch scanner on real NSE data, approve signal delivery quality window start.

---

## Crypto Engine (360-v2) Open Items

(Carry forward until addressed in a 360-v2 session)
- Run `btc_state_backfill.py` on VPS after PR #676 deployment confirmed stable
- If BTC-State thesis confirms on backfill data: bring graded soft-confirmation wiring design for owner sign-off
- Layer-2 ops for Profit page: BTC-State-conditioned signal filters
- Scorer calibration: investigate 75–80 band inversion

---

## Session 9 — Full-system audit (2026-07-07, owner signed off all changes)

Owner instruction: deep audit of paths / scoring / entry-exit / data
sufficiency / caching / seeding / universe, fix everything, owner sign-off
granted up front. Branch `claude/indian-stock-market-audit-e8lk5m`. 301 tests
green, ruff + mypy clean. Findings and fixes:

**Critical (signal quality was actively corrupted):**
1. **Cumulative-volume tick bug** — Fyers `vol_traded_today` / Angel
   `volume_trade_for_the_day` are cumulative day totals but were summed into
   the building candle once per tick. Live 5m bar volume was inflated by
   orders of magnitude → every volume gate (LSR/ORB/VSB/BDS/FAR/QCB/MAC)
   passed trivially and volume scoring maxed at 15/15 on every live signal
   since the WS fix. Now per-symbol deltas via `CumulativeVolume`
   (day-rollover + reset-safe) in both feeds.
2. **PCR / max-pain never updated** — `fetch_option_chain` was never called,
   so PCR_EXTREME + EXPIRY_GAMMA_SQUEEZE were permanently inert and the
   PCR scoring bonus never fired. Also: the underlier symbol was wrong
   (`NSE:NIFTY-INDEX` → must be `NSE:NIFTY50-INDEX`/`NSE:NIFTYBANK-INDEX`),
   the parser expected a non-Fyers response shape (now parses the real v3
   flat `optionsChain` + `callOi`/`putOi` totals), the max-pain formula had
   the call/put legs swapped, and PCR was last-chain-wins (now aggregated
   per-base in IndiaOIStore). Chain polling wired into the OI loop every
   `FYERS_CHAIN_POLL_SEC` (300s) for index bases.
3. **Mid-session restart blinded the day** — day_open came from the first
   live tick (wrong price → circuit gate + VIX-extreme drop% wrong) and the
   opening range was lost for the rest of the day (ORB + FAR dead). Now
   `seed_intraday_state()` rebuilds day_open / intraday extremes / OR (with
   09:45 lock) from today's fetched candles at every seed.
4. **`regime_daily` hardcoded RANGING** — daily-timeframe scoring component
   could never see a trend. The feed now fetches ~300 calendar days of daily
   candles per base at seed and classifies a real daily regime
   (`on_daily_regime` → context builder).

**Universe-expansion correctness (46 bases shipped with index-scaled numbers):**
5. `min_atr_gate` 3.0 abs points suppressed every cheap stock forever (2.5%
   of SAIL) — stocks now use `INDIA_MIN_ATR_PCT` (0.05% of price).
6. `TPE_MIN_SL_POINTS` 8.0 forced 5%+ stops on cheap stocks (TPE could never
   fire there) — stocks now use a price-relative floor.
7. Round-number levels: OIS/SRF/confluence-scoring used 50/100-point steps
   for stocks — now `config.round_step_for(base, price)` (price-banded ₹1/5/
   10/50/100; instrument step for indices).
8. Structure score baseline (10/25 abs pts) — now ATR%-of-price with per-class
   baselines (index 0.035%, stock 0.12%).
9. **Emission budget** — 46 bases × 2/direction allowed ~184 signals/day and
   scan-order decided who got through. Scan now scores all survivors first,
   ranks by confidence, then emits under `INDIA_MAX_SIGNALS_PER_SCAN` (3) and
   `INDIA_MAX_SIGNALS_PER_DAY` (10) with `scan_cap_gate`/`daily_cap_gate`
   telemetry. Duplicate-direction moved to the emission stage.

**Business rules that were unimplemented:**
10. **IB11** min-scalp gate — see Known Issues (resolved).
11. **IB16** expiry-day +5 confidence floor — implemented
    (`INDIA_EXPIRY_CONFIDENCE_BUMP`). Stock bases key expiry-day off their
    monthly contract expiry (new `is_contract_expiry_day`), not the weekly
    Tuesday. EGS window corrected 13:00→13:30 per IB16.
12. **IB13** macro-event gate — `config/macro_events.json` (verified RBI MPC
    announcement dates 2026-08-05 / 10-07 / 12-04 + Budget 2027-02-01) via
    new `EventCalendar`, enforced in `event_risk_gate`.

**Data / measurement hygiene:**
13. `aggregate_candles` grouped by fixed count from the oldest bar — 60m
    seeds drifted across the 75-bar NSE day and could merge two sessions
    into one candle. Now clock-time bucketed per day (matches live bars).
14. Seed no longer double-writes the currently-forming 5m bucket.
15. Outcome tracking ran only while OPEN — TP/SL touches during CLOSING
    (15:20–15:30) were misrecorded EXPIRED. Monitor now runs through CLOSING
    plus a final check before force-close.
16. OI quotes polling batched (46 sequential calls/min → 1 call of 50
    symbols) with VIX riding along as a WS-independent fallback; VIX=0 no
    longer awards the low-VIX scoring bonus.
17. MA_CROSS detects the 15m cross on completed bars only (no building-bar
    flicker entries).

**Watch next session:** live volume ratios will drop to honest levels — if
emission rate falls too far, the loosened floors (Session 8b) are the knob,
not the volume gates.

---

## Session 20 — India Market Doctrine + autonomous regime-adaptive portfolio (2026-07-13, owner-directed)

Owner uploaded the full 2026-07-13 live window (119 signals across 45 bases, 95
resolved) and directed: research the Indian market properly, then mirror the
crypto engine's autonomous regime-adaptive strategy portfolio to lift signal
quality. The 07-13 data re-proved PR #54's diagnosis on a fresh day and added
two findings #54 hadn't isolated:

- **Overall 36% win (34/95)** — below the ~39% cost breakeven; net ≈ flat/neg.
- **LONG 56% (+11.6%) vs SHORT 13% (−5.6%)** — the biggest bleed. No
  market-direction filter; shorts fired all day into a rising tape.
- **Tier inverted: A+ 0/3, A 27%, B 44%** — the a-priori score is not
  predictive; measured edge (not assumed score) must drive selection.
- **PCR 0.0 on all rows** (fixed by #54, now merged), midday-chop dead zone
  (11:00 hour 25%/−2.2%), sub-noise stops in the VIX-13 quiet tape.

**Phase 0 (done):** unblocked and merged **PR #54** — its lone CI failure was a
mypy `no-any-return` in `trade_monitor._parse_ts`; fixed by binding the pytz
`.localize()` result to a `datetime` local. #54's chop + TP-feasibility gates,
LSR key-level rule, PCR wiring and two-target TP2/BE plan are now on `main`.

**Deliverable 1 (done):** `INDIA_MARKET_DOCTRINE.md` — NSE F&O market-structure
doctrine (analog to the crypto doctrine): session clock as our Wyckoff, market
direction (FII/DII + Gift Nifty + NIFTY/BANKNIFTY leadership) as our
dominance/rotation, VIX/PCR/max-pain/expiry as positioning, STT as the fee tax,
"stand down" as a product feature.

**Phase 1 (done, this branch):** `src/market_context.py` — a per-scan
`MarketContext` (session_phase, vix_regime, market_direction, pcr, leader,
expiry) folded once from the index contexts and stamped onto every emitted
signal. New `india_signals` columns `market_direction / session_phase /
vix_regime` (migration-added, flow through `s.*` into `/api/signals` + ops +
CSV) — so the by-direction / by-phase slices that had to be computed by hand on
07-13 are now first-class. Measurement only; no scoring/gate change. 11 new
tests (`test_market_context*.py`), 465 green, ruff + mypy clean.

**Phase 4a (done, this branch — owner sign-off):** `direction_bias_gate` —
suppresses a signal fighting a *decisive* whole-market direction
(`ctx.market_direction`, stamped from MarketContext; NEUTRAL is inert, needs two
aligned index votes and zero opposing). Pre-score gate, after
`_index_conflict_gate`, before chop/tp (so those keep last-in-chain telemetry).
Config `INDIA_DIRECTION_BIAS_GATE_ENABLED` (default true; false = exact prior
behaviour) + `INDIA_DIRECTION_GATE_EXEMPT_SETUPS`. **07-13 replay:** on the
LONG-biased tape it cuts the 45 SHORTs (13%, −5.6%) and keeps the 50 LONGs
(**56%, +11.6%** vs 36%/+6.0% baseline). **Session-phase gate deliberately NOT
shipped** — 07-13 midday breakouts were the *best* cohort (48%/+3.74%), so
blanket midday suppression would cut winners; `session_phase` stays a measured
dimension for the edge matrix. 471 tests green (+6), ruff + mypy clean.

**Phase 3 (done, this branch — ships normally):** `src/strategy_edge.py` — the
Strategy×Context edge matrix. Aggregates resolved outcomes (joined to the Phase-1
market-context stamp) into per-cohort win-rate / net% / cost-adjusted expectancy
(EV = avg realised % − round-trip cost), sliced by setup, setup×direction, tier,
session_phase, vix_regime, and **market-direction-vs-signal** (the direction gate's
evidence surface). Win convention = every TP1-banked outcome (ops doctrine).
Read-only over stored rows (no new I/O); surfaced at **`/api/edge-matrix?days=N`**
(its same-change consumer). This is measured edge for the tier recalibration and
the allocator, replacing trust in the inverted a-priori tier. `get_resolved_signals`
added to `signal_store`. 5 new tests, 476 green, ruff + mypy clean.

**Phase 7 (done — lumin-india-ops PR #7 merged):** ops **Edge** view renders the
engine's edge matrix (win% / net% / cost-adjusted expectancy per cohort, incl.
market-direction × signal side). `engine_api.edge_matrix()` + `/edge` route.

**Phase 5 (done, this branch — ships normally, observe-only):**
`src/strategy_allocator.py` — turns the edge matrix into per-cohort **EMIT /
SUPPRESS / HOLD / INSUFFICIENT_DATA** verdicts (a cohort needs
`ALLOCATOR_MIN_SAMPLE` resolved trades; judged on cost-adjusted expectancy vs
`ALLOCATOR_EV_FLOOR` / `ALLOCATOR_SUPPRESS_EV`). **Recommendation mode only** —
surfaced at **`/api/allocator`**, changes no emission; the "what it would do"
the owner watches before it is ever armed. 4 new tests, 480 green, ruff + mypy
clean.

**Item 2 (done, this branch — owner-approved scoring input):** edge-aware
confidence adjustment. `IndiaSignalScoringEngine._score_measured_edge` nudges a
candidate toward its `(setup, direction)` cohort's **measured** cost-adjusted
expectancy (`strategy_edge.build_edge_index`), **only when the cohort has ≥
`ALLOCATOR_MIN_SAMPLE` resolved trades**, capped ±`EDGE_ADJUST_CAP` (8). The
honest, non-overfit "tier recalibration": on 07-13 exactly one cohort (VSB/LONG,
n=26, the best) crosses the floor and gets a small +nudge; everything else inert;
it strengthens as the 30-day window fills. Edge index loaded once at session open
(`scanner.set_edge_index`, main loop — no per-scan DB read).
`INDIA_EDGE_ADJUST_ENABLED=false` = exact prior scoring.

**Item 3 (done, this branch — owner-approved macro data):** prev-day **FII/DII**
via `src/data/india_macro_store.py` (once-daily fetch at session open, IB18;
`INDIA_FII_DII_URL` unset/unreachable → NEUTRAL, never fabricated; tolerant JSON
parser). Plus the **opening gap** (`day_open` vs `prev_day_close`) as the
Gift-Nifty-equivalent overnight vote — **no Gift-Nifty feed** (Fyers carries no
GIFT/SGX symbol; the realised gap is the equivalent signal for a post-09:30
engine). Both fold into `classify_market_direction` as extra votes; a decisive
FII-buy + gap-up strengthens the LONG_BIASED label the `direction_bias_gate` acts
on. `fii_dii_net_cr` stamped on context; `fii_dii_net_cr`/`open_gap_pct` on
MarketContext. 495 tests green (+15), ruff + mypy clean.

**Next (plan `claude/indian-stock-signals-quality-b3fz40`):** let the 30-day
window fill → watch Edge/Allocator on real data → **arm the allocator** to act
once its recommendations visibly track live outcomes (owner sign-off). Optional:
ops panels for the macro votes; point `INDIA_FII_DII_URL` at a live source.

---

## Session 19 — Signal-quality analysis of the first post-#52 window + two-target plan (2026-07-11, owner-directed)

Owner uploaded the 13:49–15:19 IST window from 2026-07-10 (the 1h30 after PR
#52 deployed): 40 signals, 12 TP1 / 22 SL / 6 EXPIRED — **35.3% win vs the
~39% gross breakeven, −0.20% gross, ≈ −2.6% net of the 0.06% cost model.**
Directive: analyse and get expectancy positive; setup quality first, then
telemetry, then TP/SL structure. One PR, three commits, in that order.

**Diagnosis (each verified in both the data and the code):**
1. **No regime gate existed** — candidates with BOTH 60m and daily regime
   RANGING/QUIET went 0/8 resolved (−1.01% gross); the neutral HTF score
   tiers still cleared the 55 floor.
2. **No target-feasibility check** — every rr>2.5 signal lost (0/7);
   tp1_pct>0.25% ran ~11% win. Far level-mapped targets emitted late expire
   at 15:30 instead of resolving. 15:19 emissions had 11 minutes to close.
3. **LSR 0/6 (−0.79%)** sweeping arbitrary 15m swings; its inflated A-tier
   scores drove the tier inversion (A 26.7% win vs B 42.1%).
4. **pcr_at_entry never wired** — a scaffold: model+DDL+INSERT existed, no
   assignment anywhere; all 40 rows stored 0.0.

**Shipped (commit order = owner's priority order):**
1. *Setup quality:* `chop_gate` (both-TF RANGING/QUIET suppression,
   `INDIA_CHOP_GATE_ENABLED` + exempt-setup CSV), `tp_feasibility_gate`
   (TP1 ≤ ATR × 5m-bars-to-close × `INDIA_TP_FEASIBILITY_EFFICIENCY` 0.30,
   dev-mode bypass), `LAST_SIGNAL_TIME` 15:20→15:00, LSR key-level
   requirement (`INDIA_LSR_REQUIRE_KEY_LEVEL`: swept swing must sit within
   0.25×ATR of PDH/PDL/PDC/locked-OR/VWAP; round numbers deliberately
   excluded). Both gates registered LAST in the pre-score chain so their
   telemetry counts only would-have-emitted candidates. Replay: cuts 14/40
   signals, zero winners lost except one 15:19 TP1 nobody could act on.
2. *Telemetry:* raw `ctx.pcr` from `IndiaOIStore.get_pcr()` stamped onto
   `pcr_at_entry` at all three build sites + scanner enrichment. 0.0 =
   unavailable (same TTL doctrine as VIX).
3. *TP/SL structure (revises IB12, owner-directed):* two-target plan — book
   `INDIA_TP1_EXIT_FRACTION` (50%) at TP1, runner behind a cost-covering BE
   (`INDIA_BE_COST_BUFFER`), targeting TP2 (next structural level in the
   1.5–3× TP1-distance band, else 2× TP1 distance;
   `INDIA_TP2_ENABLED=false` restores single-target). Monitor walks candles
   in order with two-leg state; new outcomes TP1_BE / TP2_HIT / TP1_EXPIRED,
   position-weighted results; same-candle ties stay conservative and the
   runner race starts the candle AFTER the TP1 touch. Banked TP1 survives
   restarts via `india_signals.tp1_touched_at` (migration) + resume().
   Session summary gains tp1_be/tp2/tp1_expired counts.

**Deliberately NOT done:** no confidence-scoring rewrite (the tier inversion
is explained by LSR + infeasible targets; recalibrating 9 components on n=40
is overfit), no daily budget reinstated (owner decision, PR #50 stands).

**Cross-repo:** ops Strategy/Outcomes views + app signal detail updated for
the new outcomes and TP2 (same branch in both repos).

**Validation:** judge the gates against the 30-day ledger — chop/feasibility
suppressions are visible per-gate in `/api/suppressed` and `gates_fired`.

---

## Session 18 — Stability-audit implementation (2026-07-10 PM, owner-directed)

Owner cleared signal history (clean quality window collecting) and directed:
implement the engine-stability audit while waiting for data. Everything from
the audit's "Now" tier plus most of "Next", one PR:

**Escalation & self-healing (the audit's core theme — make silence impossible):**
1. **Owner alerts** (`src/owner_alerts.py` + `dispatch_owner_alert`): FCM to
   the owner's device on a dedicated "engine-alerts" Android channel for:
   feed stalled (watchdog firing), feed restart failed, session OPEN with no
   feed (the forgot-the-token morning), engine self-restart. Per-kind
   cooldown `INDIA_OWNER_ALERT_COOLDOWN_SEC` (1800). **Set INDIA_OWNER_UIDS
   before subscriber onboarding** — until then alerts go to all registered
   tokens (Phase-1 single-user posture, documented in .env.example).
2. **Process-suicide escalation**: > `INDIA_FEED_SUICIDE_AFTER_RESTARTS` (3)
   consecutive watchdog restarts without a revived tick → alert + exit(1);
   `restart: always` boots a clean process — the only guaranteed cure for a
   wedged fyers-SDK singleton.
3. **Autoheal sidecar** (`india-autoheal`, compose): restarts any container
   whose healthcheck reports unhealthy — Docker's `restart: always` never
   acted on health status, so a hung-but-alive engine was detected and then
   ignored forever.

**Scale bomb defused:**
4. **FCM batched + off-loop**: `messaging.send_each` (500/batch) via
   `asyncio.to_thread`, stale tokens pruned from batch responses. The old
   per-token synchronous `send()` on the event loop would have frozen the
   engine ~15–40s per signal at ~100 subscribers.

**Data honesty (freshness doctrine completed):**
5. **VIX TTL** (`INDIA_VIX_TTL_SEC` 600): stale VIX reads 0.0/unavailable —
   consumers already fail safe (no low-VIX bonus, no event-risk trip, VIX
   evaluator can't arm). **OI TTL** (`INDIA_OI_TTL_SEC` 600) on current OI +
   15m change; **PCR TTL** (`INDIA_PCR_TTL_SEC` 1800) reads neutral when the
   chain poll dies.
6. **Single-writer tick handoff**: both feeds now parse on the WS thread and
   mutate the stores on the event loop via `call_soon_threadsafe` (inline
   fallback when no loop — tests/pre-start). Deletes the whole torn-read
   class (building-candle updates, day-rollover reset raced the scanner/API
   with zero synchronisation).

**Database layer:**
7. **Nightly backup** (`src/db_backup.py`): `VACUUM INTO` dated snapshot to
   `<data>/backups/` at the session-close transition, retention
   `INDIA_DB_BACKUP_KEEP` (14). The DB is the 30-day quality evidence and
   had no copy anywhere.
8. **Indexes** on `created_at` (signals/suppressions/outcomes) + all date
   predicates rewritten sargable (range form; `DATE(col)=` can never use an
   index). Ops date-range fan-outs stop degrading with table growth.

**Ops polish:** seeding now runs 5-way concurrent (`FYERS_SEED_CONCURRENCY`)
— the 46-base sequential reseed was a 1–2 min feed-down window on every
boot/hot-swap/watchdog restart; SDK thread-joins moved off the event loop
(`asyncio.to_thread`); static-token compares use `hmac.compare_digest`;
optional rotating file log sink (`INDIA_LOG_DIR`, compose sets
`/app/data/logs`, 10 MB × 14 days) since docker json logs cap at ~30 MB.

419 tests green (10 new in test_stability_s18.py), ruff + mypy clean.

**Still open from the audit (deliberately deferred):** CLAUDE.md
architecture/module-map rewrite + Redis remove-or-reserve decision; feed
process isolation (only if the SDK misbehaves through the watchdog+suicide
net); ops rate-limit check for 92-day fan-outs.

---

## Session 17 — Signal-quality pass (2026-07-10 PM, owner-directed)

Owner supplied the first **clean** (post-watchdog) outcome data — the
2026-07-10 half-day CSV + ops Strategy/Suppressed exports — and directed:
proceed with the signal-quality audit's improvements. The clean window
(11:00–13:20 after the Session-16 deploy revived the feed): **88 signals in
2h20m** (a flood; goal is 3–6/day), 62 resolved, 40.3% win, +0.42% net.
Slices that drove every change below:

- **LSR 1/9 (11% win, −1.66%)** — every loss a forming-bar "reclaim" that
  evaporated before the 5m bar closed.
- **SRF: 26 emissions, 26/26 with rr == exactly 1.5** — the LevelBook target
  never once qualified (the adjacent level is nearly always a round number
  one step away); every signal shot the synthetic fallback. 37.5% win,
  net negative at 30% of total volume. One candidate re-fired every 30s
  scan for 5 minutes (floor-blocked, but spamming telemetry).
- **conf 50–54: 27.8% win, −0.99%** vs **55–59: 46.2%, +1.02%** — the emit
  floor was admitting a cleanly-negative band (27% of volume).
- **12 duplicate emissions** of the same base+setup+direction within 15 min
  (cooldown was 300s).
- **VSB 75% win +1.61%** (the day's best path), **DIV post-tightening 40%
  win +0.45%** (probation continuing, trending right), A-tier 47.4%/+1.08%
  vs B 37.2%/−0.66% — the score discriminates.

**Changes (branch `claude/signals-frozen-zero-ed55js`, PR #52):**
1. **Pattern-bar discipline** — `IndiaContext.bar_elapsed_fraction` (builder-
   stamped); LSR/SRF/DIV/OIS/PCR/VIX only judge a 5m bar ≥
   `INDIA_PATTERN_BAR_MIN_ELAPSED` (0.8) formed. Completed bars always
   qualify. Kills forming-bar flicker triggers and the every-30s candidate
   spam at the source.
2. **ATR trigger floor** — `is_bullish/bearish_rejection(min_range=...)`:
   rejection/sweep trigger bars must span ≥ `INDIA_MIN_TRIGGER_RANGE_ATR`
   (0.5) × ATR. A lunch doji no longer counts as a rejection for the whole
   level-rejection family. LSR's sweep bar gets the same floor.
3. **SRF mapped destination** — target is now the nearest book level at
   least `SRF_MIN_RR` away (skipping the useless adjacent round number);
   no qualifying level → no signal (`SRF_REQUIRE_BOOK_TARGET`, default
   true; false restores the legacy fallback).
4. **Emit floor 50 → 55** — removes the measured-negative 50–54 band.
5. **Cooldown 300s → 900s** (`INDIA_COOLDOWN_SEC`) — no same-setup echo
   pairs 5–9 minutes apart.
6. **Weekly expiry is NIFTY-only** (`INDIA_WEEKLY_OPTION_BASES`) — SEBI's
   one-weekly-per-exchange rule left BANKNIFTY/FINNIFTY/NIFTYNXT50 monthly-
   only; they no longer get the IB16 bump (or arm EGS) on ordinary Tuesdays.
7. **VWAP wired** (audit dead-scaffold): builder computes session VWAP and
   feeds it through `key_levels_extra` into level-confluence scoring — both
   previously-dead wires now live.
8. **Converged 60m seed** — dedicated ~38-trading-day 15m fetch (aggregated
   to clock-aligned 60m; Fyers native 60m is 09:15-aligned and would
   misalign with live bars). Regime EMA55 now runs on ~230 bars instead of
   ~70 (it needs ~150 to converge). Fallback to 5m-window aggregation.

408 tests green (17 new in test_signal_quality_s17.py), ruff + mypy clean.

**Watch next sessions:** emission rate (expect ~5–15/day; if it collapses
below ~3, the first knobs back are PATTERN_BAR_MIN_ELAPSED 0.8→0.6 and the
floor 55→52); LSR win rate with final-bar triggers; whether SRF now emits
at all and at what quality; VSB stays the benchmark path; DIV probation
verdict on a full clean week; **the 30-day quality window officially starts
2026-07-10** — everything before is feed-contaminated (Session 16).

---

## Session 16 — Frozen-feed incident (2026-07-10, owner-reported)

Owner screenshots at 10:29 IST: 8 signals, all emitted 09:30–09:36, every card
pinned at **+0.00%** for an hour, outcomes never resolving — and the signals
were *duplicates with identical entries 5 minutes apart* (INFY 1049.7 at 09:31
and 09:36, TCS 2045.1 ×2, AXISBANK 1301.6, NIFTY 23999.3). Identical entries
across four instruments means the tick data itself froze: the WebSocket died
silently (likely at/after the morning token hot-swap), the scanner ran all
session on the static seed, the warm-up gate opened at 09:30 and emitted
frozen-data signals, the 5-min cooldown re-emitted them once, then the
per-direction caps went quiet. Branch `claude/signals-frozen-zero-ed55js`.

**Verified SDK failure modes (fyers-apiv3 3.1.14 source, all silent):**
1. `FyersDataSocket` is a **singleton** — the daily token hot-swap re-inits
   the same object (every morning tap exercises this untested path; the one
   live "ticks flowing" verification, Session 8d, was a *first* instance
   after a container restart).
2. Default `reconnect_retry=5` — five failed reconnects then
   "Connection abandoned" via `print()` the engine never sees. Overnight
   token expiry guarantees those failures.
3. `connect()` fires `on_connect` even when token validation failed and no
   socket exists — the engine logged "WebSocket connected — subscribing"
   while `subscribe()` silently no-opped on `__valid_token=False`.
4. Queue-wipe race: `connect()` waits a fixed 2s then fires `on_connect`; if
   the socket opens later, the SDK's `__on_open` wipes its outbound queue —
   destroying a subscription issued too early (connected, authenticated,
   subscribed to nothing).

**Fixes (defence in depth — detect at the symptom, "no ticks", not per-mode):**
1. **Tick store tracks the newest live tick per symbol** (seed never counts);
   context builder stamps `last_tick_age_sec` on every context.
2. **`stale_data_gate`** (pre-score, after warm-up): suppresses any candidate
   whose symbol never ticked or whose newest tick is older than
   `INDIA_MAX_TICK_AGE_SEC` (120) — frozen data cannot emit unfillable
   entries. Full suppression telemetry ("first stop when signals look
   frozen"). Dev-mode bypass.
3. **Feed watchdog in the main loop**: session OPEN/CLOSING + feed nominally
   up + no tick anywhere for `INDIA_FEED_STALL_RESTART_SEC` (180, clocked
   from session-open so a pre-open boot isn't misjudged) → full
   `feed.restart()` (fresh WebSocket + reseed heals the candle gap),
   `INDIA_FEED_RESTART_COOLDOWN_SEC` (300) between attempts. Restart prefers
   the freshest `/fyers/callback` token. On restart failure the feed is
   marked down (pulse shows it honestly).
4. **WS lifecycle**: `reconnect_retry=50` (SDK max); subscription now waits
   (bounded 30s) for a *genuinely open* socket before subscribing — fixes
   both the auth-failure false "connected" log and the queue-wipe race.
5. **Honest live overlay**: `_live_prices` omits symbols whose last tick is
   stale — the app shows no running % rather than a frozen +0.00% (no app
   change needed; cards already handle absent `current_price`).
6. **Pulse**: new `last_tick_age_seconds`; ops Pulse gains a Data-feed
   stat card (LIVE / STALLED / NO TICKS / DOWN) + tick/candle age rows —
   this incident was previously invisible on the dashboard.

392 engine tests green (17 new), ruff + mypy clean; ops 20 tests green.
Angel feed got the same `seconds_since_last_tick()`/`restart()` interface.

**Retrospective note:** the 07-08 duplicate MAC emissions and the 07-09
restart-burst pattern (signals only at restart times) are both consistent
with the feed dying earlier than assumed and every restart/reseed briefly
"reviving" the data — worth re-reading the first clean sessions after this
fix before tuning any more gates on that window's outcomes.

**Watch next session:** `/api/pulse` `last_tick_age_seconds` through a full
session; whether the watchdog ever fires (each firing is a real broker-side
event worth logging in this file); stale_data_gate suppressions should be
zero on a healthy day.

---

## Session 15 — Live-outcome-driven signal fixes (2026-07-09, owner-directed)

Owner supplied the first real performance exports (ops Strategy/Outcomes/
Suppressed PDFs + the 2026-07-09 signals CSV) and asked: what's actually
wrong, fix everything, and how much can we honestly scalp intraday.
Branch `claude/indian-stock-signals-analysis-s5ny6v`. 365 engine tests green
(24 new), ruff + mypy clean; ops 14 tests green.

**What the live data showed (window 07-03..07-09, 82 signals, 72 resolved):**
win rate 27.8%, PF 0.73, expectancy −0.054%/signal. A+ tier 0/4. LONGs 15.4%
win. DIVERGENCE_CONTINUATION was 48% of all volume at 15.6% win; ORB 11%;
LSR 0/5. 2026-07-09 alone emitted **40 signals (4× the daily cap)** in four
bursts — 09:15, 11:00, 11:27, 12:42 — each an engine restart re-opening the
in-memory daily budget; Jul-08 had literal duplicate emissions (same MAC
signal twice at 57969.8). The 09:15 burst held all four A+ of the day (all
SL) and six ORBs fired against ~30 seconds of "opening range".

**Root causes fixed (engine):**
1. **Gate state now survives restarts.** `GateChain.rehydrate()` rebuilds
   daily caps / per-direction counts / cooldowns / direction-conflict windows
   from `india_signals` at boot (`get_signals_today_for_gates`, ages computed
   by SQLite so container-TZ mismatches can't skew windows). `main` no longer
   calls `reset_day()` when booting straight into OPEN — only on the genuine
   PRE_OPEN→OPEN transition. This alone removes ~3/4 of yesterday's volume.
2. **Session warm-up gate** (`warmup_gate`, `INDIA_WARMUP_END` 09:30): no
   emissions in the opening auction chaos; also stops the day's budget being
   spent by 09:16.
3. **ORB requires the locked 09:45 range** (`ctx.opening_range_locked`) and
   respects a **chase guard** (`INDIA_MAX_CHASE_ATR` 0.5): entry a subscriber
   cannot fill is not a signal. FAR's OR legs also wait for the lock (PDH/PDL
   legs unaffected). VSB/BDS get the same chase guard.
4. **DIVERGENCE_CONTINUATION tightened**: prior extreme must print RSI ≥ 60
   (mirror ≤ 40), RSI fade ≥ 5 pts (`DIV_RSI_EXTREME`/`DIV_MIN_RSI_MARGIN`),
   and the trigger bar must be a real rejection (pin/engulfing), not any
   red/green close. Kills the "fires on every base for hours" behaviour.
5. **`setup_flood_gate`** (`INDIA_MAX_PER_SETUP_PER_SCAN` 1): one market-wide
   move → one best expression of a setup per scan, across sector groups (the
   correlation-group gate only capped within a group).
6. **`index_conflict_gate`**: a stock signal fighting a non-NEUTRAL proxy-index
   intraday bias is suppressed (scoring alone didn't stop them; they cleared
   the floor and lost). Indices exempt; `INDIA_INDEX_CONFLICT_GATE` to disable.
7. **`sl_noise_gate`** (`INDIA_MIN_SL_ATR_MULT` 0.45): stops narrower than
   0.45× ATR are inside one bar's noise — suppressed with telemetry. The live
   SL_HIT cluster sat at 0.08–0.20% stops. Paired with SL ATR pads raised
   0.3 → 0.5 (LSR/VSB/TPE/SRF/DIV/MAC) so geometry clears the gate naturally.
   Watch QCB: its 0.1-pad squeeze stop may now suppress — telemetry will show.
8. **A tier exists** (`INDIA_CONFIDENCE_A` 65): A+ ≥ 80, A ≥ 65, B ≥ floor.
   IB14 and the app always assumed A+/A/B; the code only ever emitted A+/B.
   Session summary gains `a_count` (in-place migration); ops Quality/Signals/
   Strategy updated (A dropdown option, A column, badge style). App already
   handled 'A' (blue) — no app change needed.

**Honest intraday scalp math (recorded for owner):** at NIFTY ~24,000 the
all-in round trip is ~14.4 pts (0.06%), so the engine's floors already imply:
minimum viable TP1 ~22 pts (NIFTY) / ~48 pts (BANKNIFTY ~54k) / 0.10% stocks.
A realistic winning scalp captures 0.10–0.25% net of costs; with 2R geometry
the system breaks even near 35% win rate — the fixes target the failure modes
that produced 27.8%, they do not change the cost reality. 3–6 signals/day of
that quality is the honest goal, not 40.

**Owner follow-up (same session, approved):**
- **No fixed daily signal budget.** `INDIA_MAX_SIGNALS_PER_DAY` default 10 → 0
  (= unlimited; set a positive value to restore a ceiling). The old cap was
  the reason signals only appeared at open/restart: it was fully spent in the
  opening burst and everything after was `daily_cap_gate`. Volume is now
  bounded by quality gates only (confidence floor, cooldowns, per-direction/
  base, per-setup + per-group flood caps, per-scan cap of 3).
- **Ops Control panel** (new view, `/control`): clear signal history
  (all/today — wipes signals, outcomes, suppressions, session summaries AND
  resets the live engine's gate chain + trade monitor) and reset today's
  gates. Backed by new engine admin endpoints `POST /api/admin/clear-history`
  (requires `confirm: "CLEAR"`) and `POST /api/admin/reset-gates` — both
  accept ONLY the static ops token (a subscriber Firebase token can never
  authorise maintenance). Engine registers `set_admin_state_reset` from main.

**Watch next sessions:** emission rate under warm-up + flood caps + DIV
tightening with the daily cap now removed — the honest volume with no
count ceiling is the real signal; whether index-conflict suppressions line
up with saved losses (check `/api/suppressed` vs what the market did); QCB
starvation via sl_noise_gate telemetry; A-tier population (65–79 band) and
whether A outperforms B as designed.

---

## Session 14 — Signal-quality overhaul (2026-07-09, owner-directed)

Owner instruction: "improve the signals quality drastically — scoring system,
gates, market structure, regime, dependency pairs — everything need to fix."
Branch `claude/indian-stock-signals-quality-bpbrmn`. 341 tests green (29 new),
ruff + mypy clean. **Scoring-model change — owner-sign-off item, no auto-merge.**

**Dead modules wired in (scaffold violations found):**
1. **`structure_state.py` (BOS/CHoCH) was never consumed anywhere.** The
   "structure" score was pure ATR normality. New `last_structure_event()`
   (persistent: latest break within a 12-bar 15m window, judged per-bar with
   no lookahead) now drives a 10-pt structure component: aligned BOS 7 /
   aligned CHoCH 5 / none 3 / opposing break 0, plus ATR normality 0–3.
2. **`order_blocks.py` (OB/FVG) was never consumed anywhere.** An unmitigated
   15m order block / FVG backing the signal's direction, containing entry, now
   counts as one confluence in the level score (zones judged on bars *before*
   the entry bar so the entry tap doesn't self-mitigate).
3. **Equal highs broke swing detection** — fully strict fractals never saw a
   double-top/bottom (routine at NSE round numbers). Now left-strict /
   right-gte: the plateau registers once at its first bar.

**Dependency pairs (new `src/dependency.py`):**
4. Static sector groups over the 46-base universe (BANKS, NBFC, IT, METALS,
   AUTO, PHARMA, FMCG, ENERGY, ADANI, INFRA, CONSUMER, TELECOM, INDEX) +
   proxy-index chain (banks→BANKNIFTY→NIFTY, NBFC→FINNIFTY→NIFTY, stocks→
   NIFTY, NIFTY↔BANKNIFTY). Scanner is now two-pass: build all contexts,
   compute each index's intraday bias (day-change ≥ 0.10% AND price on the
   matching side of 5m EMA21, else NEUTRAL), stamp `ctx.index_bias`.
5. New 5-pt scoring component: aligned with proxy bias 5 / neutral 3 /
   fighting the anchor index 0.
6. **`correlation_group_gate`** — max 1 same-direction emission per sector
   group per scan (`INDIA_MAX_PER_GROUP_PER_SCAN`): one index move no longer
   emits three near-identical bank breakouts; best confidence wins.
7. **`direction_conflict_gate`** — an opposite-direction signal on the same
   base within 30 min of an emission is suppressed
   (`INDIA_CONFLICT_WINDOW_MIN`): no whipsawing subscribers.

**Volume honesty (Session-11 deferred item):**
8. New `src/market_profile.py`: NSE U-shape time-of-day factors (9 buckets,
   open 2.2× → lunch 0.6× → close 1.8×) — every volume ratio is now "vs
   normal for this session phase", so an opening 1.5× is no longer a "surge"
   and a midday 1.5× finally is. `INDIA_VOL_TOD_ENABLE` to disable.
9. **Building-bar pro-rating** — the scanner reads the forming 5m bar 30s in;
   its partial volume was compared against full-bar averages, suppressing
   every early breakout then re-detecting it stale at bar close. Volume is
   now scaled by elapsed bucket fraction (floored at 0.3 → max 3.3× scale-up).
   Evaluators + scorer consume one `ctx.current_volume_ratio()`.

**Regime + gates:**
10. `classify()` now requires EMA21/EMA55 separation ≥ 0.25×ATR
    (`REGIME_MIN_EMA_SEP_ATR`) before awarding a trend label — an ordered but
    flat stack is chop; it was feeding the largest score component and the
    trend evaluators on noise.
11. `min_atr_gate` index floor is now max(3 pts, 0.02% of price)
    (`INDIA_MIN_ATR_PCT_INDEX`) — the absolute floor alone was 0.01% of NIFTY
    and never fired.

**Scoring rebudget (still 0–100, 9 components):** regime 15 (was 20 — rarer,
more reliable trend labels shouldn't dominate), HTF 12 (was 15), volume 15
(TOD-normalised), net-of-cost R:R 15, level confluence 10 (+OB/FVG), OI 10
(proper 4-quadrant buildup matrix — an OI surge *against* the signal scored
7/10 before, now 0), VIX/PCR 8 (was 10, wrong-side PCR now penalises both
directions), structure 10 (was 5), index alignment 5 (new). REGIME_AFFINITY
rescaled to the 15-pt budget. Emit floor stays 50; A+ stays 80 and now
requires near-total confluence (max realistic ≈ 89 for a non-breakout setup).

**Watch next sessions:** emission rate under the honest volume ratios + new
gates; if B-tier flow drops too far the emit floor (50) and per-evaluator
volume mults are the knobs. Per-sector-group cap (1/scan) is deliberately
tight — revisit with outcome data if two same-sector signals genuinely differ.

---

## Session Log

| Session | Date | Key outcomes |
|---|---|---|
| 19 | 2026-07-15 | **Signal-quality tuning from the 2026-07-14 review (owner-directed, no dark flags).** 07-14 lost -0.60% gross / ~-3.8% cost-adj at 25.9% win. Root cause: trend-continuation setups fired into a **ranging daily regime** (TREND family, daily RANGING = 3/23, 13%, -3.76% — the whole day's loss), while the same tape's reversion/breakout setups won 50%; the `_chop_gate` only caught *double* chop, so a "trending" 60m inside a ranging day sailed through. Shipped: (A) first-class `SETUP_FAMILY` taxonomy (TREND/REVERSION/BREAKOUT/NEUTRAL) in `signals/model.py`; (B) `_regime_setup_gate` — suppress TREND family when `regime_daily` is RANGING/QUIET (env `INDIA_REGIME_SETUP_GATE_ENABLED` + exempt set). **Dropped two planned changes:** the scoring tweak (C) — the pre-score gate runs before scoring, so a daily-ranging trend setup never reaches the scorer (dead code), and penalising trend setups on a ranging *60m* would down-score healthy pullbacks in a real trend; and the per-setup-per-day diversity cap (D) — owner decided against capping on volume. Gate B is the sufficient fix. Replay of the 07-14 tape through gate B: 31 kept, **35% win, +3.16% gross / +1.30% cost-adj** (from -3.84%), all winning setups retained. Allocator left observe-only (gathering its own 30-day data). Owner-sign-off item (scoring/emission). |
| 18 | 2026-07-10 | **Stability-audit implementation** (see section above). Owner alerts via FCM (feed stall/down/no-feed-at-open/self-restart), batched off-loop FCM (send_each), process-suicide escalation + autoheal sidecar, nightly DB backup + indexes + sargable queries, VIX/OI/PCR staleness TTLs, single-writer tick handoff, 5-way concurrent reseed, off-loop SDK joins, compare_digest, file log sink. 419 tests green. |
| 17 | 2026-07-10 | **Signal-quality pass on first clean data** (see section above). Half-day flood (88 signals/2h20m) dissected: LSR 1/9 on forming-bar flicker, SRF 26/26 synthetic fallback targets, 50–54 conf band cleanly negative, 12 duplicate pairs. Shipped pattern-bar discipline, ATR trigger floor, SRF mapped-destination requirement, floor 55, cooldown 900s, NIFTY-only weekly expiry, VWAP confluence (dead scaffold wired), converged 60m seed. 408 tests green. Quality window restarts 2026-07-10. |
| 16 | 2026-07-10 | **Frozen-feed incident** (see section above). WebSocket died silently; scanner emitted duplicate frozen-data signals, live P&L pinned +0.00%, outcomes never resolved. Added stale_data_gate, feed watchdog (auto-restart), WS lifecycle fixes (reconnect_retry=50, subscribe-when-open), fresh-only live overlay, feed health on ops Pulse. 392 tests green. |
| 15 | 2026-07-09 | **Live-outcome-driven fixes** (see section above). First real performance data analysed (27.8% win, PF 0.73): restart bursts quadrupled the daily cap (gate-state rehydration added), ORB fired on 30s of range (09:45 lock), DIV mass-fire tightened, warm-up/flood/index-conflict/SL-noise gates added, chase guards, A tier created. 365 tests green. Owner-sign-off item (evaluator + gate changes). |
| 14 | 2026-07-09 | **Signal-quality overhaul** (see section above). BOS/CHoCH + OB/FVG wired into scoring (were dead modules), dependency pairs (sector groups, proxy-index bias, alignment score, correlation-group + direction-conflict gates), TOD volume normalisation + building-bar pro-rating, regime EMA-separation floor, OI 4-quadrant matrix, 9-component score rebudget. 341 tests green. Owner-sign-off item (scoring model). |
| 1 | 2026-07-01 | Market research complete. Full AI handover spec (27 parts). Architecture locked. CLAUDE.md + ACTIVE_CONTEXT.md. No Telegram. Standalone app. Fyers API v3. |
| 2 | 2026-07-01 | Bootstrapped repos (PR #1 each). Engine skeleton: config, SessionManager, HolidayManager, ExpiryManager. 22 tests. |
| 3 | 2026-07-01 | Market substrate, signal model + scoring, all 14 evaluators (PRs #3–#10). 104 tests. PR #9 merged (owner sign-off). Security incident: Fyers secret exposed in chat — regenerated. SL-floor tension identified. |
| 4 | 2026-07-01 | ACTIVE_CONTEXT sync. PR #10 approved + merged. Data stores (#11), scanner + gates (#12), VPS bootstrap (#13), Fyers feed (#14). |
| 5 | 2026-07-02 | VPS reinstalled fresh; deploy pipeline fixed (SSH key) and green. Deploy workflow (#15). API server + SQLite persistence + nginx (#16), Dockerfile volume fix (#17). **Engine LIVE on VPS** — `/api/health` + `/api/pulse` responding through nginx. NSE 2026 holiday calendar verified from official circular (#18). Fyers daily-token helper + deploy wipe fix (#19). **App foundation built** (app PRs #2–#3): signal feed + detail vs live API, testing-APK workflow — first APK build green. Engine 207 tests. |
| 6 | 2026-07-03 | Domain live (Cloudflare Flexible after 521 diagnosis). Fyers token flow debugged end-to-end: redirect-URI mismatch → Cloudflare UA block (#21) → data endpoints under /data/, futures symbol -FF removed, fyers-apiv3 dep (#22). **ENGINE LIVE ON REAL NSE DATA 12:59 IST** — 45 candles seeded/base, WebSocket ticks, scanner OPEN, verified via /api/pulse over HTTPS. Fyers app recreated: QHX93US4FU-100. |
| 6b | 2026-07-03 (eve) | Broker research: SEBI Feb-2025 circular forces daily token expiry on ALL brokers. One-tap /fyers/callback (#25) replaces Termux ritual after Fyers disabled refresh API (#24). Angel One zero-touch feed shipped default-off (#26). Day-1 review: engine stable 163 scans, 0 signals + 0 suppressions exposed unwired prev-day levels — fixed (#27). Monday is first full-context session. |
| 7 | 2026-07-05 | Firebase project created (`lumin-india-d887d`). **Engine FCM dispatcher** (PR #33): Firebase Admin SDK push on signal emit, `POST /api/fcm-token`, token storage + auto-cleanup, deploy secret injection. 253 tests. **App Firebase + FCM** (PR #7): `firebase_core` + `firebase_messaging`, `FcmService`, `registerFcmToken()`, `build-apk.yml` patched for google-services. **Ops dashboard** explored — all 5 views already built (Pulse, Signals, Suppressed, Outcomes, Quality), 5 tests, auth working. Signal delivery pipeline end-to-end complete. |
| 8e | 2026-07-07 | **Universe expansion (owner-approved).** Widened from 2 index futures to **46 bases**: 4 index futures (added FINNIFTY 60, NIFTYNXT50 25) + 42 curated liquid intraday F&O stocks (`STOCK_BASES`, env-overridable). Index-only evaluators (PCR_EXTREME, EGS) auto-skip stocks. Per-stock lot size is display-only in Phase 1 (0 until broker resolution — the pre-Phase-2 follow-up). Cost: 46 WS subs + 46 OI quote-polls/min + 46 seed fetches at open — manageable; instant revert via `ALLOWED_BASES=NIFTY,BANKNIFTY`. Held for post-close merge. **Recommend watching the first 46-symbol session closely.** (Note: `fetch_option_chain` is defined but never called — PCR/max-pain are never updated, so the 2 index-only evaluators are currently inert regardless; separate pre-existing gap.) |
| 8d | 2026-07-07 | **Feed alive + first quality fix.** After the WS fix (#41), live `/api/pulse` showed `data_age_seconds` 6961→36, `signals_today` 0→2 — real signals flowing (BANKNIFTY LSR RR 2.0, regime "trending up", lot 30, expiry 2026-07-28: all Session-8 fixes visibly correct). First quality bug caught from a live card: `TREND_PULLBACK_EMA` emitted RR 0.2 (used a 15m swing barely beyond entry). It uniquely lacked the min-R:R guard the other swing-target evaluators have — surfaced only now because the regime fix first let TPE fire. Fixed: `TPE_MIN_RR` (1.5) guard + fallback to 2R + final reject. Held from mid-session merge to preserve the live session; merges after 15:30 close. |
| 8b | 2026-07-07 | **Loosen pass** — SL-floor tension root-caused (floors 3–5× above IB11) and loosened to ~0.06%; emit-floor 65→55; VSB volume 2.0→1.5, OI gate 0.5→0.0. All config, env-overridable, 273 tests green. Demonstrated LSR emitting at normal ATR (sl% 0.07–0.11, old floor would've suppressed). `min_scalp_points` scaffold flagged. Tighten-by-quality plan recorded. |
| 13 | 2026-07-08 | **Stock lot sizes resolved from broker + ORB stale-range gate.** Follow-ups to the P&L work. (1) **Lot sizes** — stock cards showed "lot 0" (INSTRUMENTS covers only the 4 indices), blocking rupee P&L. The feed now resolves NSE lot sizes from the **Fyers public symbol master** (`FYERS_SYMBOL_MASTER_URL`, NSE_FO.csv) once/day at seed and populates a `config` registry; `config.lot_size_for(base)` returns broker value → static INSTRUMENTS fallback → 0. Verified against the live master: NIFTY 65, BANKNIFTY 30, RELIANCE 500, SBIN 750, TATASTEEL 2750 (215 underlyings parsed). Best-effort — a fetch failure leaves the static fallback intact. Off hot path (1 fetch/day). (2) **ORB stale-range gate** — the 12:22 BHARTIARTL "opening range breakout" was a stale-level trade; ORB now only fires while the 09:15-09:30 range is still relevant (`INDIA_ORB_WINDOW_END`, default 11:00 IST). 312 tests green (4 new), ruff clean. Remaining: ops dashboard %-parity (same points-summing flaw in its Outcomes/Quality views). |
| 12 | 2026-07-08 | **Real % P&L + per-signal outcome status (owner reported the session summary was unreadable).** From live app screenshots: "Net points +3657.4" sums raw points across a 46-base universe of wildly different price scales (a +67 NIFTY-point win and a +0.4 TATASTEEL-point win are both ~+0.2% moves) — meaningless as performance; and individual signal cards showed no outcome/status. Root cause: `trade_monitor` recorded `points` but never `pct`. Fixes (all engine, off money-path): (1) `SignalOutcome.pct` = signed points/entry×100, persisted (`india_signal_outcomes.pct`, in-place migration for the prod DB); (2) session summary now aggregates `total_pct` + `avg_pct` (the cross-instrument-comparable figures) alongside legacy points; (3) `/api/signals` + `/api/signals/{id}` carry per-signal `status` (OPEN/TP1_HIT/SL_HIT/EXPIRED) + realised `result_pct`/`result_points` via a LEFT JOIN to outcomes, so every card can badge where the trade stands; (4) `_with_live` adds running `live_pct`. `/api/pulse` already returns `allowed_bases`, so the app can fix its stale "Scanning NIFTY and BANKNIFTY" label from real data. 308 tests green (5 new), ruff clean. **Deferred: stock lot sizes still 0** — will resolve properly from the Fyers symbol master (dynamic, accurate), NOT hardcode 42 guessed values; blocks ₹ P&L only (% is lot-independent). Also flagged: ORB fired at 12:22 (stale opening range) — own PR. App display PR consumes all of the above. |
| 11 | 2026-07-08 | **Cost-aware signal quality (grounded in NSE research).** Deep-researched current NSE F&O microstructure (STT, VIX, intraday U-shape, costs) and audited all 14 evaluators + scoring + gates against it. Key finding: NSE hiked futures STT 0.02%→**0.05% sell-side on 1-Apr-2026** (Budget 2026-27), ~tripling it; all-in round-trip cost is now ~**0.06% of notional** (~14 NIFTY / ~31 BANKNIFTY pts). The `min_scalp_points` floors (15/40) were calibrated for the 0.02% era and had silently become break-even, and confidence scored **gross** R:R — overstating the edge a subscriber actually keeps. Fixes (owner-directed "fix paths and scoring"): (1) central `config.round_trip_cost_points()` cost model, single env knob `INDIA_ROUNDTRIP_COST_PCT` (0.06); (2) `min_scalp_points_for` now `max(absolute floor, cost × MIN_SCALP_COST_MULT[1.5])` so the floor tracks real costs and can't go break-even again (NIFTY 15→~22, BANKNIFTY 40→~47, stocks unchanged — their cost in points is tiny); (3) `_score_rr` scores **net-of-cost** R:R with recentred bands, favouring cost-efficient larger-target setups over thin scalps. Confirmed `LAST_SIGNAL_TIME` (15:20 no-late-entry) already enforced. Trade-off: fewer but genuinely-profitable index signals — the honest post-STT reality. Caps untouched (owner instruction). 270 tests green, ruff clean. **Follow-up flagged: time-of-day volume normalisation** (U-shape biases the 20-bar volume ratio near open/close) — deferred, own PR. |
| 10 | 2026-07-08 | **The 46-base universe was never live in prod.** Owner reported "still only BANKNIFTY, only 2". Traced to `ALLOWED_BASES=NIFTY,BANKNIFTY` in the VPS `.env` — seeded from `.env.example` line 34 on first deploy and never removed, so every merged universe expansion (8e / #42 / #44: 42 stocks + FINNIFTY + NIFTYNXT50, stock-scaled thresholds) has been dark: prod scanned 2 bases the whole time. Removed the pin from `.env.example` so the full 46-base code default applies; **owner must also delete the line from the live VPS `.env` + restart** (repo template does not overwrite an existing `.env`). Also recalibrated `CONFIDENCE_EMIT_FLOOR` 55→50: PR #44's cumulative-volume fix removed ~5-7pt of systematic score inflation (volume was 15/15 on every live signal), so the PR#39 floor of 55 had silently tightened to an effective ~60-62 and starved NIFTY (~0 signals). Daily/per-scan caps (10/3, ranked best-first) still bound flood risk. Owner sign-off given verbally ("turn on all 46 and tune score if needed"). 267 tests green (5 files need API deps unavailable in sandbox), ruff clean. **Watch the first true 46-symbol session: confirm stock futures actually tick, verify per-base emission spread, and check B-tier quality at the 50 floor.** |
| 8 | 2026-07-07 | **Signal-firing diagnosis + fix** (branch `claude/signal-paths-firing-issue-i1plic`). Owner flagged near-zero signals. Root-caused to intraday-state freeze (circuit gate silencing the day), 60m regime that could never form, stale prev-day levels, weekly-vs-monthly futures expiry, and stale Jan-2026 lot sizes; plus per-direction throughput cap + redundant double gate pass. All fixed with tests (273 passing, ruff+mypy clean). Market reality re-verified via web (NSE Tuesday-expiry regime, lot rebaseline, India VIX range). |
