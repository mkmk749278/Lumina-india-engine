# OWNER_BRIEF.md — Lumin India

Operating doctrine, business rules, and architecture decisions. Read every session before touching code.

---

## What This System Is

**Lumin India** is an NSE F&O (Futures and Options) signals platform targeting Indian retail traders. It scans NIFTY and BANKNIFTY index futures using Smart Money Concepts (SMC) and technical analysis evaluators, scores signals by confidence, and delivers them to subscribers via the lumin-india-app Android application.

This system is the Indian market complement to the Lumin crypto signals platform (360-v2). Same CTE operating standards. Same production-grade doctrine.

**NSE F&O context (mandatory reading for every AI session):**
- NSE is the world's largest derivatives exchange by contract count
- NIFTY 50 (lot size: 75 units) and BANKNIFTY (lot size: 35 units) are index futures — no physical settlement risk
- Trading hours: 09:15–15:30 IST, Monday–Friday only
- Weekly expiry: NIFTY and BANKNIFTY expire every Tuesday (near-weekly contract is the primary trading instrument)
- STT (Securities Transaction Tax) on sell side: 0.05% of notional (April 2026 hike). On 1 NIFTY lot at ₹24,000 index: ₹900 per trade. This sets a minimum viable scalp of 15 NIFTY points.
- India VIX: NSE-published volatility index. Above 20 = elevated, above 25 = extreme. Gate on extremes.
- PCR (Put-Call Ratio): market-wide option sentiment. Below 0.7 = extreme bearish, above 1.3 = extreme bullish.

---

## Role Boundaries

| Role | Who | Responsibilities |
|---|---|---|
| Owner | mkmk749278@gmail.com | Business decisions, regulatory posture, Phase 2 sign-off, VPS provisioning, broker account management |
| CTE | Claude Code (this AI) | All technical decisions, architecture, code, deployment, cost optimization, signal quality analysis |

CTE speaks up when a direction is technically wrong. Owner makes business calls. Neither overrides the other's domain without discussion.

---

## Business Rules (IB1–IB18)

These rules are non-negotiable. Any code that violates them is a bug.

**IB1 — Index futures only at launch.**
NIFTY and BANKNIFTY weekly near-expiry contracts only. No stock F&O, no index options (short delta risk). `ALLOWED_BASES = ["NIFTY", "BANKNIFTY"]` enforced at scanner entry. Expand only with explicit owner decision and architecture review.

**IB2 — SEBI compliance is a hard gate on auto-execution.**
AUTO_EXECUTION_ENABLED must be false until: SEBI RA (Research Analyst) registration complete + NSE algo provider empanelment + NSE_ALGO_ID assigned + Fyers static IP whitelisted. Signal delivery (Phase 1) does not require RA registration. Auto-execution (Phase 2) does.

**IB3 — NSE_ALGO_ID on every order.**
Every order sent to Fyers must include the NSE_ALGO_ID field. This is a SEBI mandate (Feb 2025 circular, mandatory April 2026). Signing service validates presence before forwarding. Hard reject if absent, no override.

**IB4 — Kill switch latency < 5 seconds.**
Regulatory requirement. Kill switch must halt all new orders AND cancel all open orders within 5 seconds. Engine maintains this as a hard invariant.

**IB5 — No withdrawal permissions on broker tokens.**
Fyers API token accepted only if it does not include withdrawal or fund-transfer scope. Validated at connect time. Auto-reject, no override.

**IB6 — Session hard gate.**
No scanning, no signals, no execution outside 09:15–15:30 IST, Monday–Friday. NSE holidays excluded (HolidayManager, updated annually). The engine wakes at 09:10, scans from 09:15, and force-closes all positions by 15:25.

**IB7 — Force-close by 15:25 IST.**
No intraday position may remain open past 15:25 IST. If it's still open at 15:25, the engine sends a market close order regardless of PnL. No exceptions. Prevents STT trap (F&O physical settlement STT on exercised options).

**IB8 — Blast-radius caps (non-negotiable).**
```
MAX_CONCURRENT_INDIA_POSITIONS = 2
MAX_INDIA_NOTIONAL_INR        = 500,000   (₹5 lakhs)
MAX_INDIA_ORDERS_PER_MINUTE   = 5
MAX_INDIA_DAILY_LOSS_INR      = 15,000    (₹15,000)
```
Daily loss cap triggers an automatic session kill switch. Tripwires enforce all caps. No override.

**IB9 — Whole lots only.**
Always trade in whole lots. NIFTY: 75 units/lot. BANKNIFTY: 35 units/lot. Never partial lots. NSE enforces this; we validate before order placement.

**IB10 — Signal quality gate before Phase 2.**
Minimum 30 trading days of live Phase 1 signal data must be reviewed by owner before Phase 2 (auto-execution) is considered. Owner explicit sign-off required to flip AUTO_EXECUTION_ENABLED=true. CTE presents the data; owner decides.

**IB11 — STT-aware minimum scalp.**
Minimum viable signal: 15 NIFTY points or 40 BANKNIFTY points. This floor covers round-trip STT + brokerage + slippage and leaves a real margin. Signals below this R:R floor are suppressed at the confidence floor gate.

**IB12 — TP1-full exit model.**
100% position closed at TP1. No partial exits. No TP2/TP3 complexity. Stop moved to break-even after +1% MFE (Maximum Favorable Excursion). Same model as the crypto engine's Session-34 default. Revisit with data if win rate warrants it.

**IB13 — No signal on event risk.**
Major macro events (RBI MPC meeting days, Union Budget, NSE circuit breaker events, India VIX > 25) trigger event_risk_gate suppression for the session. No signals emitted. Better to miss a day than blow a position on binary event risk.

**IB14 — Subscriber tiers.**
```
Tier B  (₹999/month)   — A and B confidence signals
Tier A+ (₹2,499/month) — A+ confidence signals only (highest confidence, fewer, cleaner)
Free                    — signal exists visible, entry/SL/TP blurred
```
Route to subscriber only if their plan covers the signal's tier. Route decision at IndiaSignalDispatch, same pattern as crypto engine.

**IB15 — No Telegram.**
Signal delivery is app-only. FCM push notification → lumin-india-app → REST API. No Telegram channel at any tier, ever. The app is the product.

**IB16 — Expiry-day behaviour.**
On Tuesday (weekly expiry), scanning continues but:
- `EXPIRY_GAMMA_SQUEEZE` evaluator activates after 13:30 IST (last 2 hours of trading)
- Confidence threshold raised by 5 points (harder to emit)
- Force-close moved from 15:25 to 15:20 IST (extra buffer for expiry settlement)
- Never carry a position through the 15:30 expiry settlement

**IB17 — Opening Range (ORB window).**
Opening Range is defined as 09:15–09:45 IST (first 30 minutes). ORB evaluator uses the high and low of this window as breakout levels. High/low is locked at 09:45 and does not change for the rest of the day.

**IB18 — Cost discipline.**
VPS cost is the dominant cost at Phase 1. Firestore cost is controlled by aggressive caching (subscriber validation cache, FCM token cache). Add zero uncached external calls to per-tick or per-scan paths. FCM is free at our subscriber volume and never on the hot path (one call per signal, not per tick).

---

## Architecture (summary — see CLAUDE.md for full detail)

**Four containers (docker-compose.india.yml):**
- `india-redis` — snapshot bus
- `india-engine` — scanner, evaluators, FSM (Phase 2), snapshot writer
- `india-api` — HTTP REST for lumin-india-app and ops dashboard
- `india-signing` — broker token isolation and order signing (Phase 2)

**Signal delivery (Phase 1):**
Scanner → evaluators → gate chain → scoring → `india_signals` SQLite write → FCM push → app REST poll

**Secrets:** GitHub Actions secrets only. Injected at deploy time. Never on disk.

**VPS:** Dedicated India VPS (separate from crypto engine). Ubuntu 22.04. Static IP for Fyers whitelist.

---

## Subscription Architecture

```
User installs lumin-india-app
      ↓
Phone OTP login (Firebase Auth)
      ↓
Free tier: signal list visible, detail blurred
      ↓
User buys plan via Razorpay (in-app)
      ↓
App calls POST /api/india/subscription/verify (payment_id + signature)
      ↓
Engine verifies with Razorpay server-side
      ↓
india_subscriptions SQLite: plan + expiry written
      ↓
Signal dispatch checks plan at dispatch time (fresh SELECT per dispatch)
```

Google Play Billing is not available for financial/trading services in India. Razorpay is the payment layer.

---

## Cost Model

| Item | Phase 1 cost/month |
|---|---|
| India VPS (2 vCPU / 4 GB) | ₹800–1,200 |
| Fyers API | ₹0 |
| Firebase FCM | ₹0 |
| Firestore (subscriber validation) | ₹0–50 |
| Razorpay | 2% per transaction |
| **Fixed cost** | **~₹1,000–1,200** |

Break-even: 2 Tier B subscribers (₹999 × 2 = ₹1,998/month) covers fixed infra cost.

---

## Broker: Fyers API v3

- **Cost:** Free for Fyers account holders
- **WebSocket:** 5,000 symbol subscriptions (far more than we need for 2 instruments)
- **Historical data:** 1,000 candles per call, free
- **Orders:** Market, limit, bracket (SL+TP in one order), cover orders
- **OAuth2 + TOTP:** Daily token refresh required. Signing service handles this automatically.
- **NSE_ALGO_ID:** Mandatory on every order payload (SEBI)
- **Onboarding:** apply at myapi.fyers.in → create app → get client_id + secret_key

**Fyers symbol format:** `NSE:NIFTY25AUGFUT-FF` (base + year + month + FUT + -FF)

---

## Infrastructure

| Component | Detail |
|---|---|
| India VPS | Dedicated, Ubuntu 22.04, Docker, static IP. Separate from 360-v2 crypto VPS. |
| Termux | Owner SSHes into VPS from Android phone via Termux app. All deploy/diagnostic commands must work in a plain SSH terminal. |
| GitHub repos | lumin-india-engine, lumin-india-app, lumin-india-ops (all under mkmk749278) |
| GitHub Actions | CI/CD for all three repos. Secrets injected at build/deploy time. |
| Firebase | FCM for push notifications. Firestore for subscriber validation (generation-gated cache). |

---

## What Success Looks Like

**Phase 1 success (30 days post-launch):**
- 3+ signals/day average
- Average confidence ≥ 70
- A+ win rate ≥ 60% (would-be, measured against TP1 price level)
- Subscriber acquisition begins

**Phase 2 gate (after Phase 1 data reviewed):**
- Owner reviews 30-day signal data
- Decision: activate auto-execution or continue Phase 1 with improvements

**Business goal:**
Top NSE F&O signals platform in India. Profitable signals → subscriber trust → retention → revenue → growth.
