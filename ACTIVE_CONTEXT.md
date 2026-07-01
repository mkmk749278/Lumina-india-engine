# ACTIVE_CONTEXT.md — Lumin India

**Last updated:** 2026-07-01 (Session 1 — initial documentation)

---

## Current Phase

**Pre-launch — Documentation complete. No code written yet. No repos created yet.**

Phase 1 goal: Signal delivery to lumin-india-app via FCM + REST. No Telegram. No auto-execution.
Phase 2 (auto-execution) is locked until SEBI RA registration + NSE empanelment + 30-day signal quality review.

---

## Repos (to be created)

| Repo | Purpose | Branch convention | Status |
|---|---|---|---|
| `mkmk749278/lumin-india-engine` | NSE F&O scanner, evaluators, API server, execution (Phase 2) | `feat/`, `fix/`, `docs/`, `chore/` | NOT CREATED |
| `mkmk749278/lumin-india-app` | Flutter Android app (standalone Play Store listing) | same | NOT CREATED |
| `mkmk749278/lumin-india-ops` | Ops dashboard (extends ops.luminapp.org pattern) | same | NOT CREATED |

**Creation order:** engine first (CLAUDE.md, OWNER_BRIEF.md, ACTIVE_CONTEXT.md bootstrap), then app, then ops.

---

## Infrastructure To-Do (in order)

| Item | Owner | Status | Notes |
|---|---|---|---|
| Provision new VPS | Owner | PENDING | Ubuntu 22.04, min 2 vCPU / 4 GB RAM. Dedicated to India engine. Static IP required for Fyers whitelist. |
| Apply for Fyers API access | Owner | PENDING | myapi.fyers.in → create app → client_id + secret_key |
| Store Fyers creds in GitHub Secrets | Owner + CTE | PENDING | After repo created: `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY` |
| Set up Firebase project for India | Owner | PENDING | Can reuse existing Firebase project (add india-engine service account + FCM config) or create new project |
| Set up Razorpay account | Owner | PENDING | razorpay.com → get `RAZORPAY_KEY_ID` (public) + `RAZORPAY_KEY_SECRET` |
| NSE algo provider empanelment | Owner | PENDING | SEBI RA registration required first. Phase 2 blocker only. |
| Create Play Store listing (Lumin India) | Owner | PENDING | Phase 1 launch blocker. App name to confirm. |
| SSH deploy key for VPS → GitHub Actions | CTE | PENDING | After VPS provisioned. `VPS_SSH_KEY`, `VPS_HOST`, `VPS_USER` in GitHub Secrets. |

---

## Key Decisions (Session 1 — locked)

| Decision | Detail |
|---|---|
| No Telegram | Signal delivery is app-only. FCM push notification → subscriber opens lumin-india-app → REST API. No Telegram channel at any tier. |
| Standalone Android app | New repo `lumin-india-app`, separate Play Store listing. Not an extension of the crypto lumin-app. |
| New VPS | Dedicated server for India engine. Separate from the crypto engine VPS. Fyers requires static IP whitelist — isolation avoids entanglement. |
| Repo naming | `lumin-india-engine`, `lumin-india-app`, `lumin-india-ops` |
| Primary broker API | Fyers API v3. Free for account holders. WebSocket 5,000 symbols. Historical data free. Bracket + cover orders. |
| Secrets management | GitHub Actions secrets only. No `.env` files with real secrets on disk. Injected at deploy time. |
| Index futures only (Phase 1) | NIFTY and BANKNIFTY weekly near-expiry contracts. No stock F&O until Phase 2+ review. |
| AUTO_EXECUTION_ENABLED | Locked false until: SEBI RA registration + NSE empanelment + Fyers API + static IP + 30-day signal quality window + owner explicit sign-off. |
| Lot sizes | NIFTY: 75 units/lot. BANKNIFTY: 35 units/lot (current NSE-mandated sizes). Verify at engine bootstrap — NSE may revise. |
| Subscription billing | Razorpay (Google Play Billing disallowed for financial/trading services in India) |

---

## Architecture Summary

```
Fyers WebSocket (free, account holder)
      ↓
India Tick Store + Historical Store + OI Store + VIX/PCR
      ↓
India Scanner (30s) → 14 evaluators → 9-gate chain → confidence scoring
      ↓
India Signal Router → SQLite write + FCM push
      ↓
[india-engine] ──Redis──> [india-api] ──HTTP──> lumin-india-app
                                   ──HTTP──> lumin-india-ops

[india-signing] (Unix socket, Phase 2 only) → Fyers REST → NSE
```

**Containers:** india-redis, india-engine, india-api, india-signing

---

## 14 Evaluators (Phase 1 — signal delivery)

| # | Name | Crypto analogue |
|---|---|---|
| 1 | LSR (Level Support/Resistance) | direct port |
| 2 | ORB (Opening Range Breakout) | India-specific, no crypto analogue |
| 3 | TPE (Trapped Price Exhaustion) | direct port |
| 4 | VSB (Volume Spike Breakout) | direct port |
| 5 | BDS (Bearish/Bullish Divergence Signal) | direct port |
| 6 | SR_FLIP (Support/Resistance Flip) | direct port |
| 7 | INDIA_VIX_EXTREME | replaces funding rate extreme |
| 8 | PCR_EXTREME (Put-Call Ratio) | replaces funding rate |
| 9 | FAR (Fibonacci After Retracement) | direct port |
| 10 | DIV_CONT (Divergence Continuation) | direct port |
| 11 | QCB (Quiet Consolidation Breakout) | direct port |
| 12 | MA_CROSS (EMA crossover) | direct port |
| 13 | OI_SPIKE_REVERSAL | replaces liquidation cascade |
| 14 | EXPIRY_GAMMA_SQUEEZE | India-specific (F&O expiry) |

---

## 9-Gate Chain

```
session_gate → spread_gate → cooldown_gate → event_risk_gate
→ circuit_check_gate → min_atr_gate → oi_liquidity_gate
→ duplicate_direction_gate → confidence_floor_gate
```

Confidence floor: emit ≥ 65. A+ tier: ≥ 80.

---

## GitHub Secrets Required (per repo)

### lumin-india-engine
```
FYERS_CLIENT_ID          # Fyers API app client ID
FYERS_SECRET_KEY         # Fyers API app secret (signing service only)
FIREBASE_SERVICE_ACCOUNT # JSON key for Firebase Admin SDK (FCM + Firestore)
RAZORPAY_KEY_SECRET      # Razorpay server-side secret
VPS_HOST                 # India VPS IP or hostname
VPS_USER                 # SSH user
VPS_SSH_KEY              # Private key for GitHub Actions → VPS deploy
```

### lumin-india-app
```
RAZORPAY_KEY_ID          # Public Razorpay key (included in build, not secret but stored here for CI)
GOOGLE_SERVICES_JSON     # Firebase google-services.json for Android
KEYSTORE_FILE            # Android release keystore (base64 encoded)
KEYSTORE_PASSWORD        # Keystore password
KEY_ALIAS                # Key alias
KEY_PASSWORD             # Key password
```

### lumin-india-ops
```
VPS_HOST                 # Same India VPS
VPS_USER                 #
VPS_SSH_KEY              #
OPS_AUTH_TOKEN           # Single-password auth gate for ops dashboard
OPS_SESSION_SECRET       # Session signing secret
```

---

## Phase 1 Exit Criteria (signal quality gate for Phase 2 evaluation)

Signal quality data must accumulate for minimum 30 trading days before Phase 2 can be evaluated. At that point, owner reviews and signs off (never auto-proceed).

Target thresholds (indicative — owner sets final bar):
- Signal count: minimum 3 signals/day average over 30 days
- Average confidence: ≥ 70
- A+ tier win rate: ≥ 60% (measured at TP1 price level reached, even if auto-execution not live)
- No more than 3 consecutive would-be-loss signals in any 5-trading-day window
- Average R:R on emitted signals: ≥ 1.5

These are evaluation criteria, not hard cutoffs. Owner decides Phase 2 activation with full data in hand.

---

## Cost Estimate (Phase 1)

| Item | Est. monthly cost |
|---|---|
| New VPS (2 vCPU / 4 GB, India region) | ₹800–1,200/month (DigitalOcean/Hetzner/Linode) |
| Fyers API | ₹0 (free for account holders) |
| Firebase FCM | ₹0 (free tier, unlimited messages) |
| Firebase Firestore | ₹0–50 (subscriber validation only, aggressively cached) |
| GitHub Actions | ₹0 (public repo) or minimal (private) |
| Razorpay | 2% per transaction (no monthly fee) |
| **Total fixed** | **~₹1,000–1,200/month before subscriber revenue** |

---

## Open Queue (in priority order)

1. **Owner action: Provision India VPS** — Ubuntu 22.04, static IP, Docker installed
2. **Owner action: Apply for Fyers API** — myapi.fyers.in
3. ~~**CTE: Create GitHub repos**~~ — **DONE (Session 2).** `lumina-india-engine` + `lumin-india-app` bootstrapped with operating docs + self-scaling CI (PR #1 each, merged). `lumin-india-ops` still needs its CLAUDE.md (no ops brief in the handover set — pending owner/draft).
4. **CTE: Bootstrap lumin-india-engine** — **IN PROGRESS (Session 2).** Landed: `pyproject.toml`, `config/__init__.py`, `src/utils.py`, session/holiday/**expiry** managers + full test suite (22 tests, ruff/mypy clean). **Deferred (no-scaffold discipline):** `docker-compose.india.yml` + Dockerfile land with the first runnable entrypoint (`src/main.py`).
5. **CTE: Fyers WebSocket integration** — tick store, historical data fetch at session open
6. **CTE: IndiaContext builder** — assemble all indicators from tick + historical + OI + VIX/PCR data
7. **CTE: 14 evaluators** — port from crypto engine, add India-specific (ORB, VIX_EXTREME, PCR_EXTREME, OI_SPIKE_REVERSAL, EXPIRY_GAMMA_SQUEEZE)
8. **CTE: Gate chain** — 9 gates, suppression telemetry
9. **CTE: Confidence scoring** — 8-component, 100-point system
10. **CTE: Signal router + FCM dispatch** — SQLite write + Firebase Admin SDK push
11. **CTE: API server** — `/api/india/signals`, `/api/india/session`, `/api/india/pulse`, FCM token registration
12. **CTE: lumin-india-app scaffold** — Flutter project, auth, home/signal list, signal detail, subscription screen
13. **CTE: Razorpay integration** — in-app billing, server-side verification
14. **Owner: Phase 1 go-live decision** — review scanner on real NSE data (paper mode), approve signal delivery

---

## Crypto Engine (360-v2) Open Items

(Carry forward from Session 38 until addressed in a 360-v2 session)
- Run `btc_state_backfill.py` on VPS after PR #676 deployment confirmed stable
- If BTC-State thesis confirms on backfill data: bring graded soft-confirmation wiring design for owner sign-off
- Layer-2 ops for Profit page: BTC-State-conditioned signal filters
- Scorer calibration: investigate 75–80 band inversion

---

## Session Log

| Session | Date | Key outcomes |
|---|---|---|
| 1 | 2026-07-01 | Market research complete. Full AI handover spec written (27 parts). Architectural decisions locked. CLAUDE.md × 2 + ACTIVE_CONTEXT.md written. No Telegram. Standalone app. New VPS. lumin-india-* repos. Fyers API v3. GitHub Actions secrets. |
| 2 | 2026-07-01 | Bootstrapped `lumina-india-engine` + `lumin-india-app` (operating docs + self-scaling CI; PR #1 each, merged). Started engine skeleton: config (env-overridable, no-scaffold scoped), IST-aware SessionManager (state machine + hard session gate), HolidayManager (verified-flag guard on incomplete calendar), ExpiryManager (weekly resolution + intraday roll + holiday shift + Fyers symbol). 22 tests, ruff+mypy clean. Flagged for owner: NIFTY/BANKNIFTY futures weekly-vs-monthly, BANKNIFTY min-scalp 40-vs-25 (spec), lot sizes 75/35-vs-65/30 (spec), engine repo canonical name `Lumina-india-engine`. |
