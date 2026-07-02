# ACTIVE_CONTEXT.md — Lumin India

**Last updated:** 2026-07-02 (Session 5)

---

## Current Phase

**Phase 1 — Engine DEPLOYED and LIVE on the VPS. Awaiting Fyers access token for live data.**

Phase 1 goal: Signal delivery to lumin-india-app via FCM + REST. No Telegram. No auto-execution.
Phase 2 (auto-execution) is locked until SEBI RA registration + NSE empanelment + 30-day signal quality review.

**Live right now on `95.111.241.97`:**
- `india-engine` container — scanner + API server, healthy, session-gated
- `india-redis` container — internal
- nginx on port 80 → API on 127.0.0.1:8000
- `curl http://95.111.241.97/api/health` → 200 OK
- Engine runs **without a data feed** until `FYERS_ACCESS_TOKEN` is provided (daily OAuth token — see Open Queue #1)

---

## Repos

| Repo | Purpose | Branch convention | Status |
|---|---|---|---|
| `mkmk749278/Lumina-india-engine` | NSE F&O scanner, evaluators, API server, execution (Phase 2) | `feat/`, `fix/`, `docs/`, `chore/` | ACTIVE — 19 PRs merged, deployed to VPS |
| `mkmk749278/lumin-india-app` | Flutter Android app (standalone Play Store listing) | same | ACTIVE — foundation merged (PR #2: signal feed + detail vs live API; PR #3: testing-APK workflow, first build green). Owner device test pending. |
| `mkmk749278/lumin-india-ops` | Ops dashboard (extends ops.luminapp.org pattern) | same | Needs CLAUDE.md |

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

### Signal delivery path (wired end-to-end as of PR #16)

```
FyersDataFeed → stores → IndiaScanner (30s) → 14 evaluators → 9 gates → scoring
    → IndiaSignalRouter → SQLite (india_signals, india_suppressions)
    → FastAPI (/api/signals, /api/pulse, /api/suppressed) → nginx :80
```

FCM push (router's second fan-out) is NOT built yet — needs Firebase project.

### API surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/health` | none | liveness |
| `GET /api/pulse` | Bearer | uptime, session state, scan count, signals today |
| `GET /api/signals` | Bearer | signal list; filters: date, tier, setup_class, limit |
| `GET /api/signals/{id}` | Bearer | single signal |
| `GET /api/suppressed` | Bearer | recent gate suppressions |

Auth: static Bearer token (`API_STATIC_TOKEN` GitHub secret → env). Firebase auth for app users comes with the app build.

### Test suite

**207 tests**, all passing. `ruff` + `mypy` clean.

---

## Infrastructure Status

| Item | Status | Notes |
|---|---|---|
| VPS provisioned | DONE | IP: `95.111.241.97` (fresh Ubuntu reinstall 2026-07-02), domain: `lumintrade.app` (not yet pointed) |
| GitHub Actions deploy | DONE | Push to `main` → SSH deploy → rebuild containers. ssh-action v1.2.2. |
| GitHub Secrets | DONE | `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `API_STATIC_TOKEN`, `GH_PAT`, `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_DEPLOY_PUBKEY` |
| nginx reverse proxy | DONE | Installed by deploy workflow; `tools/setup-nginx.sh`; rate-limit 60 r/m |
| NSE holiday calendar | DONE | Verified against NSE circular NSE/CMTR/71775 (all 15 weekday holidays for 2026) |
| Fyers API app created | DONE | App ID: `PKMQRMWUZG-100`. Secret key regenerated after chat exposure. |
| **Fyers access token** | **PENDING — blocks live data** | Daily OAuth token. Workflow injects `FYERS_ACCESS_TOKEN` secret, which does not exist yet. |
| Domain → VPS | PENDING | Point `lumintrade.app` (or subdomain) at `95.111.241.97` via Cloudflare |
| Firebase project | PENDING | Needed for FCM dispatcher + app auth |
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

### SL-floor tension (owner decision needed)

Multiple evaluators (SR_FLIP, FAR, TPE, VSB, PCR) produce SL distances under their configured % floors at normal NIFTY 5m ATR (~15–30 pts). This means they'll fire rarely or never in calm market conditions. Test fixtures use elevated ATR (60–100 pts) to exercise full paths.

**Options for owner:**
1. Lower SL-% floors (accept tighter stops)
2. Change SL basis to wider structural stops (prev-bar swing, multi-bar lookback)
3. Accept selective firing (evaluators only emit in volatile conditions)

Decision affects ~5 evaluators' real-market emission rates. Must be resolved before the 30-day quality window starts.

### Fyers access token expires daily

Fyers OAuth access tokens are valid for one trading day. Until the signing-service daily-refresh automation is built (Phase 2 design), the owner runs `scripts/fyers_token.py` on the VPS each trading morning (PR #19 — exchanges the pasted auth code, verifies against the profile endpoint, writes `.env`, prints the restart command; deploys no longer wipe a VPS-set token). This is the **current blocker for live scanning**.

### Spec inconsistencies (flagged, not blocking)

- Lot sizes: spec says 65/30 in one place, 75/35 in another. Using 75/35 (current NSE-mandated).
- BANKNIFTY min-scalp: spec says 40 in one place, 25 in another. Using 40 (STT-aware).
- Futures expiry cadence: weekly vs monthly varies by spec section. Using weekly (current NSE schedule) — reconciliation with NSE contract reality still open (see config note).

---

## Open Queue (in priority order)

1. **Owner: Fyers access token** — run `python3 scripts/fyers_token.py` in `/opt/lumin-india` on a trading morning (engine PR #19 helper), restart engine. Unblocks live NSE data.
2. **CTE: FCM dispatcher** — Firebase Admin SDK push on signal emit. Needs Firebase project (owner creates, service-account JSON as GitHub secret).
3. **Owner: point domain** — `lumintrade.app` (or `api.lumintrade.app`) → `95.111.241.97` via Cloudflare, then confirm HTTPS.
4. **Owner: app device test** — add `INDIA_API_TOKEN` secret on the app repo (same value as engine `API_STATIC_TOKEN`), run the "Build testing APK" workflow, install the artifact, walk feed → detail. Mandatory before further signal-screen work.
5. **CTE: app auth + FCM screens** — Firebase Phone OTP login + push handling; lands once the Firebase project exists.
6. **Owner: SL-floor decision** — affects ~5 evaluators (see Known Issues).
7. **CTE: Razorpay integration** — in-app billing, server-side verification.
8. **Owner: Phase 1 go-live review** — watch scanner on real NSE data, approve signal delivery quality window start.

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
| 3 | 2026-07-01 | Market substrate, signal model + scoring, all 14 evaluators (PRs #3–#10). 104 tests. PR #9 merged (owner sign-off). Security incident: Fyers secret exposed in chat — regenerated. SL-floor tension identified. |
| 4 | 2026-07-01 | ACTIVE_CONTEXT sync. PR #10 approved + merged. Data stores (#11), scanner + gates (#12), VPS bootstrap (#13), Fyers feed (#14). |
| 5 | 2026-07-02 | VPS reinstalled fresh; deploy pipeline fixed (SSH key) and green. Deploy workflow (#15). API server + SQLite persistence + nginx (#16), Dockerfile volume fix (#17). **Engine LIVE on VPS** — `/api/health` + `/api/pulse` responding through nginx. NSE 2026 holiday calendar verified from official circular (#18). Fyers daily-token helper + deploy wipe fix (#19). **App foundation built** (app PRs #2–#3): signal feed + detail vs live API, testing-APK workflow — first APK build green. Engine 207 tests. |
