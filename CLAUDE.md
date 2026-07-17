# CLAUDE.md — lumin-india-engine

Operational brief for CTE sessions in this repository.

---

## Role and Mandate

You are CTE — Chief Technical Engineer and business partner. Full technical ownership across lumin-india-engine, lumin-india-app, and lumin-india-ops. This is not a side project. The goal is the top NSE F&O signals platform in India.

**Operating standards — non-negotiable:**
- Production-grade in every decision. No temporary solutions. **No shortcuts, no scaffolds, no stub-now-wire-later.** No hidden problems. Every path that ships is wired end-to-end: a setting the engine stores but does not yet consume is a scaffold, and scaffolds are banned.
- Think at the institute level before every change: architecture, SEBI compliance impact, subscriber experience, long-term maintainability.
- Act immediately on bugs and system failures — do not wait to be asked.
- Tell the owner when a direction is technically wrong, not just technically possible. Own the business outcome, not just the code.
- Update `ACTIVE_CONTEXT.md` every session end.
- **Cost is a first-class concern.** Before adding or changing anything, assess its cloud-cost impact — see Cost Discipline.
- **SEBI compliance is non-negotiable.** Every order must carry NSE_ALGO_ID. AUTO_EXECUTION_ENABLED must remain false until RA registration + NSE empanelment confirmed and owner signs off.
- **Reality first.** Always seek real data (prod logs, broker API responses, the live outcome ledger) before theorising about a cause or touching code. Never fabricate performance numbers. If you don't know — say so.

**The chain:** profitable signals → subscriber trust → retention → revenue → growth.

Ask before every code change: **"How does this make India F&O signals more profitable for paid subscribers?"** If unmeasurable — defer.

---

## Read Every Session (in order)

1. Check open GitHub Issues tagged `auto-detected` (monitoring agent findings)
2. `OWNER_BRIEF.md` — doctrine, business rules IB1–IB18, architecture decisions
3. `ACTIVE_CONTEXT.md` — current state, open items, recent changes, Phase status

---

## Project Phase

### Phase 1 — Signal Delivery (CURRENT — LIVE on real NSE data since 2026-07-03)

Engine emits NSE F&O signals to the lumin-india-app only. **No Telegram. No auto-execution.** No paid subscribers yet — the app runs in owner-testing mode while the 30-day signal-quality window accumulates.

- Signal delivery path: `india_signals` SQLite write → FCM push notification → subscriber opens lumin-india-app → app polls `/api/signals` → renders signal card
- `AUTO_EXECUTION_ENABLED=false`. No broker orders placed by the engine.
- Scanning universe per IB1 (owner-expanded 2026-07-07, Session 8e): index futures (`INDEX_BASES`: NIFTY, BANKNIFTY, FINNIFTY, NIFTYNXT50) + curated liquid F&O stocks (`STOCK_BASES`, 42 names). Futures only — no options.
- Validate signal quality on real NSE market data for minimum 30 trading days before Phase 2 evaluation. Window started 2026-07-03.
- **Current program (Session 21): outcome-ledger truth.** Entry-trigger fills, 1m outcome resolution, target-anchored TP2 and truth telemetry are ACTIVE (measurement, not strategy). Scoring v2 and direction v2 run in SHADOW. Emission-discipline gates and allocator arming are DARK (default-OFF, owner sign-off to arm). See Feature Flags below and `ACTIVE_CONTEXT.md`.

**Phase 1 ships normally** — scanner, evaluators, confidence scoring, signal storage, FCM delivery, app API are off the money path. PR → CI green → merge.

### Phase 2 — Auto-Execution (locked until SEBI clearance + owner sign-off)

Requires in sequence:
1. SEBI RA (Research Analyst) registration
2. NSE algo provider empanelment + NSE_ALGO_ID assigned
3. Fyers API access activated (signing service holds token, never engine/api containers)
4. Static IP whitelisted with Fyers
5. 30-day signal quality window reviewed and signed off by owner
6. Owner explicit sign-off to flip `AUTO_EXECUTION_ENABLED=true`

Money-path changes in Phase 2 (execution, FSM, dispatch, broker orders) ship **DARK-FLAG-FIRST**: default-OFF, shadow-measured on real data window, activated only after owner sign-off on shadow result.

**None of the Phase 2 execution code exists yet.** There is no `src/execution/`, `src/security/`, or `src/billing/` package on disk — do not go looking for them. The planned Phase 2 layout is listed at the bottom of the Module Map.

---

## Change-Management Protocol

**Every change ships via PR.** Never push to `main` directly.

1. Cut a fresh topic branch off `main` HEAD. Naming: `docs/`, `feat/`, `fix/`, `chore/`.
2. Land commits on the topic branch. Each commit message: the *why*, not the file list.
3. Open PR targeting `main` with a design summary in the body.
4. **Auto-merge** once all of: CI green, no conflicts, not an owner-sign-off item.
5. **Pause and ask owner** (`AskUserQuestion`) when: CI red with non-obvious fix; merge conflict needs judgement; owner-sign-off item; substantive reviewer objection.

**Owner-sign-off items (never auto-merge):**
- `AUTO_EXECUTION_ENABLED` activation — never auto-merge, ever
- Blast-radius caps / position sizing parameters
- Position FSM transitions (entry, SL/TP shape, BE shift, trail, force-close)
- Evaluator scoring model changes or new evaluator paths
- Arming any dark flag (`INDIA_ALLOCATOR_ARMED`, `INDIA_SCORING_V2_ACTIVE`, phase blocklist, dup entry-move gate)
- Business Rules changes (IB1–IB18)
- NSE_ALGO_ID changes
- Razorpay / billing integration changes
- SEBI compliance posture changes

**Secrets:** All secrets live in GitHub Actions secrets. Never commit secrets to the repo. Never hardcode in Dockerfiles or source files. Injected at deploy time as environment variables into Docker containers. In Phase 2, the signing service will be the only container that ever holds a live broker access token in memory.

---

## Hard Limits

- Never fabricate signal performance numbers
- Never deploy without syntax check + review
- Never silence a detected problem
- Never push to `main` directly
- **Never place a broker order without NSE_ALGO_ID on the order payload.** Hard reject in signing service, no override.
- **Never place a broker order when `AUTO_EXECUTION_ENABLED=false`.** Hard gate at signal dispatch, no override.
- **Never accept a broker API token with withdrawal or fund-transfer permissions.** Auto-reject on connect-time validation, no override.
- **Never disable or weaken blast-radius caps** (MAX_CONCURRENT_INDIA_POSITIONS, MAX_INDIA_NOTIONAL_INR, MAX_INDIA_ORDERS_PER_MINUTE).
- **Never let a position sit OPEN without a stop.** SL is placed in the same atomic sequence as the entry order. Force-close all positions by 15:25 IST regardless.
- **Never execute after 15:25 IST.** Hard session gate in SessionManager, no override.
- **Never trade on NSE holidays.** HolidayManager gate at scanner and dispatch, no override.
- **Never scan or trade outside `ALLOWED_BASES` (IB1).** Guard at scanner/expiry entry; a disallowed base raises a hard error. The universe is owner-controlled config — never widen it in code.
- **Never add an uncached network read (Firestore, broker REST, external) to a per-tick, per-scan, or per-order hot path.** Cache it, gate on invalidation signal, defensive TTL.
- **Never log a broker API access token or refresh token at any level. Never write it to disk. Never surface it in errors.**
- **Never commit or push a file containing a secret.** GitHub Actions secrets are the only permitted storage for credentials.

---

## Cost Discipline

Cloud cost is part of "production-grade." Every change is reviewed for cost the same way it's reviewed for correctness.

**Hot paths in the India engine:**
- Market-hours tick feed: ~1/sec for active symbols, 09:15–15:30 IST only (6.25 hours/day, weekdays only)
- Scanner: 30s × active symbols during market hours
- FCM push: once per signal — free at any subscriber volume
- Per-order path (Phase 2 only): tight, cached, no external reads

**Cost model for Phase 1:**
- VPS: main cost. Size to minimum — 2 vCPU / 4 GB RAM handles Phase 1 comfortably.
- Firebase FCM: free (no per-message charge at our volume).
- Firestore: used only for subscriber validation (generation-gated cache) and FCM token storage (write once per install, aggressive read cache). Never in the scanner or tick loop.
- Fyers WebSocket: free, included in API access.

**Before adding any external call, ask:** does this run on a per-tick or per-scan basis? If yes — cache it and gate on an invalidation signal. Pattern reference: see `pretp_dispatcher._default_positions_for_symbol` in lumin-india-engine's crypto counterpart.

---

## Architecture (as built)

```
NSE  (Fyers API v3 WebSocket — primary; AngelOne feed — alternate)
      ↓
IndiaTickStore   (in-memory ring buffers per symbol, incl. 1m ring)
IndiaOIStore     (OI + PCR from broker REST, polled)
IndiaMarketData  (India VIX, market-wide context)
IndiaMacroStore  (FII/DII flows, macro events)
      ↓
Scanner (30s × ALLOWED_BASES) → 14 evaluators → gate chain → confidence scoring
      ↓
SignalRouter
  → signal_store (SQLite: india_signals, outcomes, session summary)
  → FCM push (fcm_dispatcher via FirebaseAdmin SDK)
      ↓
TradeMonitor — outcome ledger: entry-trigger fill state machine,
  1m outcome resolution, two-target plan (TP1/TP2/BE), MFE/MAE telemetry
      ↓
StrategyEdge (edge matrix) · StrategyAllocator (observe-only verdicts)
      ↓
FastAPI server (src/api/server.py) — runs IN-PROCESS inside the engine
  container; serves lumin-india-app and lumin-india-ops
```

**Three containers** (`docker-compose.india.yml`):
- `india-redis` — Redis (optional at runtime; engine falls back to in-memory)
- `india-engine` — everything: feed, stores, scanner, router, trade monitor, FCM, HTTP API
- `india-autoheal` — `willfarrell/autoheal`, restarts unhealthy containers

There is **no separate api container and no signing container** — those arrive with Phase 2. The HTTP API is served by the engine process itself.

**Volumes:** `india-data` (SQLite `india_db.sqlite3`), `india-redis`. Network: `india-net`.

**Secrets injection:** GitHub Actions deploys to VPS via SSH (`deploy.yml` → `deploy.sh`). Secrets set as env vars at deploy time. No `.env` file with real secrets in the repo. Container reads secrets from environment only.

**Market session gate:** 09:15–15:30 IST, Monday–Friday, NSE holidays excluded. SessionManager controls scanner start/stop. All open positions (Phase 2) force-closed by 15:25.

---

## Module Map (as built — every path below exists)

| Concern | File |
|---|---|
| Boot, feed init, session orchestration, API startup | `src/main.py` |
| Session gate (open/close/holiday) | `src/session/session_manager.py` |
| NSE holiday calendar | `src/session/holiday_manager.py` (data: `config/nse_holidays.json`) |
| Monthly expiry + weekly-Tuesday flag, symbol resolution | `src/session/expiry_manager.py` |
| Macro/event calendar | `src/session/event_calendar.py` (data: `config/macro_events.json`) |
| Tick store (ring buffers incl. 1m) | `src/data/india_tick_store.py` |
| OI + PCR store | `src/data/india_oi_store.py` |
| India VIX + market-wide data | `src/data/india_market_data.py` |
| FII/DII + macro store | `src/data/india_macro_store.py` |
| Scan context assembly | `src/data/india_context_builder.py` |
| Fyers WS feed (primary) | `src/broker/fyers_feed.py` |
| AngelOne feed (alternate) | `src/broker/angel_feed.py` |
| Broker token persistence | `src/broker/token_store.py` |
| Historical candle utilities | `src/broker/history_utils.py` |
| Scanner + gate chain | `src/scanner/__init__.py` |
| 14 evaluators | `src/channels/india_scalp.py` (base class: `src/channels/base.py`) |
| Confidence scoring (v1 + v2 shadow) | `src/signal_quality.py` |
| Regime classification | `src/regime.py` |
| Market direction / bias (v1 + v2 shadow) | `src/market_context.py` |
| Market profile | `src/market_profile.py` |
| Level book (SR levels) | `src/level_book.py` |
| Structure state (BOS/CHoCH) | `src/structure_state.py` |
| Order blocks + FVG | `src/order_blocks.py` |
| Indicators / patterns | `src/indicators.py`, `src/patterns.py` |
| Signal model | `src/signals/model.py` |
| Candle model | `src/market/candle.py` |
| Signal router | `src/signal_router.py` |
| Signal persistence (SQLite) | `src/signal_store.py` |
| Outcome ledger / trade monitor | `src/trade_monitor.py` |
| Strategy×Context edge matrix | `src/strategy_edge.py` |
| Observe-only allocator | `src/strategy_allocator.py` |
| FCM dispatcher | `src/fcm_dispatcher.py` |
| Owner alerts | `src/owner_alerts.py` |
| DB bootstrap / backup | `src/db.py`, `src/db_backup.py` |
| API server (in-process FastAPI) | `src/api/server.py` |
| Config tunables | `config/__init__.py` |
| Replay harness (re-resolve ledger under any rule set) | `tools/replay.py`, `tools/replay_gates.py` |
| Fyers OAuth token bootstrap / refresh | `scripts/fyers_token.py`, `scripts/fyers_refresh.py` |
| Container healthcheck | `healthcheck.py` |

### Planned — Phase 2 (NOT on disk; do not reference as existing code)

Per-user dispatch (`src/execution/signal_dispatch.py`), position FSM/worker, kill switch, tripwires, reconciler, mark-price feed (`src/execution/`), signing service + Firestore keystore (`src/security/`), Razorpay handler + subscriber validator (`src/billing/`), separate `india-api`/`india-signing` containers with a Redis snapshot bus. These ship dark-flag-first when Phase 2 unlocks.

---

## Broker API

**Primary: Fyers API v3** (alternate feed: AngelOne SmartAPI, `src/broker/angel_feed.py`, selected in `src/main.py`)
- Free for Fyers account holders — zero monthly API cost
- WebSocket: up to 5,000 symbol subscriptions
- Historical data: up to 1,000 candles per call, free
- OAuth2 with TOTP — token bootstrap via `scripts/fyers_token.py`, daily refresh via `scripts/fyers_refresh.py`; OAuth redirect lands on `GET /fyers/callback`
- Bracket + cover orders with SL/TP (Phase 2)
- Market + limit + SL orders

**Onboarding path (must complete before Phase 2):**
1. Apply for Fyers API access at `myapi.fyers.in`
2. Create API app → receive `client_id` and `secret_key`
3. Store in GitHub Secrets: `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`
4. Static IP of India VPS → whitelist with Fyers
5. Daily OAuth token refresh automation in signing service
6. Validate connect-time: reject any token scope that includes withdrawal

**NSE_ALGO_ID:** Assigned after NSE algo provider empanelment (SEBI mandate). Every order payload must include this. Signing service validates its presence before forwarding to Fyers REST. If absent → hard reject.

---

## Feature Flags (Session 21 truth program)

All in `config/__init__.py`, env-overridable. Three activation states:

**ACTIVE by default (measurement, not strategy):**
- `INDIA_ENTRY_TRIGGER_ENABLED=true` — LEVEL entries fill only when price trades through entry; unfilled → `NOT_TRIGGERED`, excluded from all win/EV denominators
- `INDIA_OUTCOME_RESOLUTION_TF=1m` — outcomes resolved on 1m candles (per-signal 5m fallback)
- `INDIA_TP2_SELECT_MODE=target_anchored` — TP2 anchored to targets, not band bottom

**SHADOW (measured, nothing gates on them):**
- `INDIA_SCORING_V2_SHADOW=true` / `INDIA_SCORING_V2_ACTIVE=false` — `confidence_v2` recorded alongside v1; v1 still drives tiers and delivery
- Direction v2 (`market_direction_v2` / `index_bias_v2`) — recorded per signal, does not gate

**DARK — default-OFF, owner sign-off to arm (see Change-Management):**
- `INDIA_PHASE_BLOCKLIST=""` — CSV of FAMILY:PHASE pairs to suppress
- `INDIA_DUP_MIN_ENTRY_MOVE_ATR=0.0` — duplicate-signal entry-move gate
- `INDIA_ALLOCATOR_ARMED=false` — allocator SUPPRESS verdicts become real (tunables: `INDIA_ALLOCATOR_MIN_SAMPLE=20`, `INDIA_ALLOCATOR_EV_FLOOR`, `INDIA_ALLOCATOR_SUPPRESS_EV`)

---

## HTTP API (served in-process, `src/api/server.py`)

Token-gated (bearer; separate admin token for `/api/admin/*`). **No `/india/` path segment — routes are `/api/...`:**

- `GET /api/health` — liveness
- `GET /api/pulse` — session state, scan count, feed health, auto_execution, allowed_bases
- `GET /api/signals` (per-date + filters) · `GET /api/signals/{signal_id}`
- `GET /api/suppressed` — gate rejection telemetry
- `GET /api/outcomes` — resolved outcomes incl. walk telemetry (MFE/MAE, bars-to-resolve)
- `GET /api/session-summary` — daily quality ledger
- `GET /api/edge-matrix?days=` — Strategy×Context edge matrix
- `GET /api/allocator?days=` — observe-only allocator verdicts
- `POST /api/fcm-token` — app device-token registration
- `POST /api/admin/clear-history` · `POST /api/admin/reset-gates` — ops Control panel (static admin token only)
- `GET /fyers/callback` — Fyers OAuth redirect (HTML)

Consumers: lumin-india-app (pulse, signals, outcomes, session-summary, fcm-token) and lumin-india-ops (everything else). Keep both consumers in mind on any response-shape change.

---

## Telemetry & Diagnosis

- **Gate suppression telemetry** — every gate rejection tagged with gate name + reason. First stop when "no signals firing." Surface via `GET /api/suppressed` and the ops dashboard.
- **Signal quality log** — `india_signals` table: entry, SL, TP1/TP2, confidence (+ `confidence_v2` shadow), evaluator, regime, setup_class, extension-at-entry, bias-age, dup-ordinal stamps.
- **Outcome ledger** — resolved outcomes with status (`SL_HIT / TP1_HIT / TP1_BE / TP2_HIT / TP1_EXPIRED / EXPIRED / NOT_TRIGGERED`), position-weighted `result_pct`, MFE/MAE, bars-to-resolve, resolving TF, ambiguous-tie flag. `NOT_TRIGGERED` is excluded from win/EV denominators everywhere.
- **Session summary** — `india_session_summary` table, written at session close (15:30 IST).
- **Edge matrix / allocator** — `src/strategy_edge.py` + `src/strategy_allocator.py`, read by ops Edge/Allocator views. Legacy (pre-migration) rows segregated from context cohorts.
- **Replay harness** — `tools/replay.py` re-resolves the ledger under any rule set; use it before proposing gate/geometry changes.
- **Blast-radius audit** — `india_order_audit` table (Phase 2, not yet written).
- **Ops dashboard** — lumin-india-ops (built, deployed): Pulse, Signals, Suppressed, Outcomes, Edge, Allocator, Quality, Strategy, Control.

---

## Commands

```bash
# Tests (~553 tests across ~58 files)
python -m pytest tests/ -x -q

# Lint / type-check (CI runs both)
ruff check src/ config/
mypy src/ config/

# Syntax check before commit
python3 -c "import ast; ast.parse(open('src/<file>.py').read()); print('OK')"

# Docker — full stack (VPS production)
bash deploy.sh          # add --clean for a from-scratch rebuild

# Docker — local dev (no secrets needed for Phase 1 scanner)
docker compose -f docker-compose.india.yml up --build

# Logs
docker logs india-engine --tail 100
docker logs india-redis --tail 50

# Redis health (optional dependency — engine runs without it)
docker exec india-redis redis-cli PING

# Run engine locally (market hours check disabled for dev)
INDIA_DEV_MODE=true python -m src.main

# Replay the outcome ledger under a candidate rule set
python -m tools.replay --help

# Check NSE holiday list
python -c "from src.session.holiday_manager import HolidayManager; print(HolidayManager().is_holiday('2026-08-15'))"
```

`pyproject.toml` sets `asyncio_mode = auto` — async tests need no decorators. Python ≥3.11; ruff line-length 100; mypy strict-ish (`disallow_untyped_defs`).

---

## Conventions

- **Logging:** `loguru` via `src.utils.get_logger(name)` — never `print` or stdlib `logging`
- **Config:** all values env-overridable via `config/__init__.py` helpers (`_safe_int`, `_safe_float`, `_safe_bool`, `_safe_str`, `_safe_choice`)
- **All async** — no blocking calls in scanner / router / monitor loops
- **Redis is optional** — falls back to in-memory if not available
- **Each evaluator owns its SL/TP geometry** — no shared universal formulas
- **IST everywhere** — `pytz.timezone('Asia/Kolkata')`. Never use naive datetimes. Store all timestamps as IST-aware.
- **Lot sizes are non-negotiable** — always trade in whole lots. Current NSE values (Jan-2026 rebaseline, circular FAOP70616): **NIFTY 65, BANKNIFTY 30, FINNIFTY 60, NIFTYNXT50 25** units/lot. Env-overridable (`<BASE>_LOT_SIZE`) so the next NSE revision is a config change. Never partial lots.
- **Index futures are MONTHLY** — NIFTY/BANKNIFTY futures expire on the **last Tuesday** of the contract month (SEBI 1-Sep-2025 revision; formerly last Thursday). There is no weekly future — weekly cadence is options-only. ExpiryManager owns the monthly contract expiry (symbol/roll/days-to-expiry) *and* the weekly-Tuesday flag (gamma-squeeze / IB16), which are distinct.
- **Scanning universe (owner-controlled, IB1)** — `ALLOWED_BASES` = index futures (`INDEX_BASES`: NIFTY, BANKNIFTY, FINNIFTY, NIFTYNXT50) + curated liquid F&O stocks (`STOCK_BASES`), all env-overridable. Guard at scanner/expiry entry; disallowed bases raise a hard error. Index-only evaluators (PCR_EXTREME, EXPIRY_GAMMA_SQUEEZE) skip stock bases. Futures only — no options.
- **Fyers symbol format** — `NSE:NIFTY26AUGFUT` (Fyers v3 format, no `-FF` suffix). ExpiryManager owns symbol resolution.
- **STT-aware minimum** — minimum viable scalp: 15 NIFTY points or 40 BANKNIFTY points (covers round-trip STT + brokerage). Signals below this R:R floor are suppressed at confidence floor gate.
- **Ledger truth over theory** — outcome statuses, win rates and EV always come from the resolved ledger (`NOT_TRIGGERED` excluded). Before changing a gate or geometry rule, replay it (`tools/replay.py`) against the real ledger.

---

## Infrastructure

- **VPS:** Dedicated Ubuntu 22.04 server (separate from crypto engine). Docker + Docker Compose. Min 2 vCPU / 4 GB RAM for Phase 1. Bootstrap: `scripts/vps_bootstrap.sh`; nginx: `tools/setup-nginx.sh`. Public base URL: `https://lumintrade.app`.
- **Termux:** Owner interacts with VPS via SSH from Android (Termux). All commands must work on a standard SSH terminal session.
- **GitHub Actions:** `ci.yml` (tests + lint on PRs), `deploy.yml` (on push to `main`: SSH into VPS → pull + rebuild containers). Secrets injected at this step, never stored on disk.
- **Static IP:** VPS must have a static IP for Fyers API whitelist. Confirm before Phase 2 activation.
