# ACTIVE_CONTEXT.md — Lumin India

**Last updated:** 2026-07-07 (Session 8)

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

### SL-floor tension (owner decision needed)

Multiple evaluators (SR_FLIP, FAR, TPE, VSB, PCR) produce SL distances under their configured % floors at normal NIFTY 5m ATR (~15–30 pts). This means they'll fire rarely or never in calm market conditions. Test fixtures use elevated ATR (60–100 pts) to exercise full paths.

**Options for owner:**
1. Lower SL-% floors (accept tighter stops)
2. Change SL basis to wider structural stops (prev-bar swing, multi-bar lookback)
3. Accept selective firing (evaluators only emit in volatile conditions)

Decision affects ~5 evaluators' real-market emission rates. Must be resolved before the 30-day quality window starts.

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

## Session Log

| Session | Date | Key outcomes |
|---|---|---|
| 1 | 2026-07-01 | Market research complete. Full AI handover spec (27 parts). Architecture locked. CLAUDE.md + ACTIVE_CONTEXT.md. No Telegram. Standalone app. Fyers API v3. |
| 2 | 2026-07-01 | Bootstrapped repos (PR #1 each). Engine skeleton: config, SessionManager, HolidayManager, ExpiryManager. 22 tests. |
| 3 | 2026-07-01 | Market substrate, signal model + scoring, all 14 evaluators (PRs #3–#10). 104 tests. PR #9 merged (owner sign-off). Security incident: Fyers secret exposed in chat — regenerated. SL-floor tension identified. |
| 4 | 2026-07-01 | ACTIVE_CONTEXT sync. PR #10 approved + merged. Data stores (#11), scanner + gates (#12), VPS bootstrap (#13), Fyers feed (#14). |
| 5 | 2026-07-02 | VPS reinstalled fresh; deploy pipeline fixed (SSH key) and green. Deploy workflow (#15). API server + SQLite persistence + nginx (#16), Dockerfile volume fix (#17). **Engine LIVE on VPS** — `/api/health` + `/api/pulse` responding through nginx. NSE 2026 holiday calendar verified from official circular (#18). Fyers daily-token helper + deploy wipe fix (#19). **App foundation built** (app PRs #2–#3): signal feed + detail vs live API, testing-APK workflow — first APK build green. Engine 207 tests. |
| 6 | 2026-07-03 | Domain live (Cloudflare Flexible after 521 diagnosis). Fyers token flow debugged end-to-end: redirect-URI mismatch → Cloudflare UA block (#21) → data endpoints under /data/, futures symbol -FF removed, fyers-apiv3 dep (#22). **ENGINE LIVE ON REAL NSE DATA 12:59 IST** — 45 candles seeded/base, WebSocket ticks, scanner OPEN, verified via /api/pulse over HTTPS. Fyers app recreated: QHX93US4FU-100. |
| 6b | 2026-07-03 (eve) | Broker research: SEBI Feb-2025 circular forces daily token expiry on ALL brokers. One-tap /fyers/callback (#25) replaces Termux ritual after Fyers disabled refresh API (#24). Angel One zero-touch feed shipped default-off (#26). Day-1 review: engine stable 163 scans, 0 signals + 0 suppressions exposed unwired prev-day levels — fixed (#27). Monday is first full-context session. |
| 7 | 2026-07-05 | Firebase project created (`lumin-india-d887d`). **Engine FCM dispatcher** (PR #33): Firebase Admin SDK push on signal emit, `POST /api/fcm-token`, token storage + auto-cleanup, deploy secret injection. 253 tests. **App Firebase + FCM** (PR #7): `firebase_core` + `firebase_messaging`, `FcmService`, `registerFcmToken()`, `build-apk.yml` patched for google-services. **Ops dashboard** explored — all 5 views already built (Pulse, Signals, Suppressed, Outcomes, Quality), 5 tests, auth working. Signal delivery pipeline end-to-end complete. |
| 8 | 2026-07-07 | **Signal-firing diagnosis + fix** (branch `claude/signal-paths-firing-issue-i1plic`). Owner flagged near-zero signals. Root-caused to intraday-state freeze (circuit gate silencing the day), 60m regime that could never form, stale prev-day levels, weekly-vs-monthly futures expiry, and stale Jan-2026 lot sizes; plus per-direction throughput cap + redundant double gate pass. All fixed with tests (273 passing, ruff+mypy clean). Market reality re-verified via web (NSE Tuesday-expiry regime, lot rebaseline, India VIX range). |
