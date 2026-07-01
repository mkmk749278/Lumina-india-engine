# Lumin India — Complete Project Specification
### AI Handover Document · Version 2.0 · July 2026

**Changelog from v1.0:** Telegram removed — app-only signal delivery. Subscription model updated to confidence-tier access (not execution-tier). Repos named `lumin-india-engine`, `lumin-india-app`, `lumin-india-ops`. Dedicated new VPS (not shared with crypto engine). Secrets managed via GitHub Actions secrets — no `.env` files with credentials on disk. Fyers API v3 as sole broker integration at Phase 1 (Dhan deferred). Android app is standalone (separate Play Store listing from lumin-app crypto app).

**This document is self-contained. An AI reading it cold must be able to build the entire system without asking questions. Nothing is assumed. Every schema, every evaluator, every API call, every config value is specified here.**

---

## PART I: WHAT WE ARE BUILDING

### 1.1 Product Summary

**Lumin India** is an automated intraday signal engine for NSE (National Stock Exchange of India) F&O (Futures & Options) markets. It runs during Indian market hours (09:15–15:30 IST, Monday–Friday), scans NSE index futures (NIFTY 50 and BANKNIFTY) every 30 seconds using Smart Money Concepts (SMC) and order-flow evaluators, scores setups via a multi-component confidence pipeline, and dispatches signals to:

1. **lumin-india-app (Android)** — the sole delivery channel. FCM push notification alerts the subscriber; the app fetches full signal details (direction, entry, SL, TP, setup, confidence tier) via REST API. No Telegram. No web app.
2. **Server-side auto-execution** — Phase 2 only (locked until SEBI RA registration + NSE empanelment). Places trades on user's own broker account via their OAuth token.

**Signal delivery is app-only.** There is no Telegram channel at any tier, ever.

### 1.2 What This Is NOT

- It is not a replacement for the existing crypto engine (Lumin/360-v2 on Binance). That system continues to run 24/7 in parallel. Indian market hours do not conflict with it.
- It is not a copy-trading system. It is a signal + auto-execution SaaS under SEBI's Research Analyst + Algo Provider regulatory framework.
- It is not a portfolio management service. Each signal is a standalone intraday scalp. No carry-forward positions.

### 1.3 Reference System

This system is architecturally modelled on the existing Lumin crypto engine (`github.com/mkmk749278/360-v2`). The patterns for scanner, FSM, Redis bridge, signing service, API isolation, and Android app delivery are proven there. Where this document says "mirror the crypto pattern," it refers to that system's architecture. Where this document specifies something different, the difference is explicit.

---

## PART II: BUSINESS RULES

All rules are non-negotiable unless explicitly marked as tunable.

| # | Rule |
|---|---|
| IB1 | **Signal existence is visible to all users. Full details require a paid plan.** Free users see a signal card (symbol + direction) but entry price, SL, and TP are blurred. Paid users see the full signal. The paywall is on signal detail access, not on execution. |
| IB2 | **Two paid tiers (Razorpay billing — not Google Play Billing).** Tier B (₹999/mo) — access to all A and B confidence signals with full detail. Tier A+ (₹2,499/mo) — access to A+ confidence signals only (highest confidence, fewer, cleaner). Prices are owner-confirmed before launch and stored in config, not hardcoded. |
| IB3 | **Intraday only. All positions must be closed by 15:25 IST.** No overnight positions. No carry-forward. Positions held past 15:25 are force-closed at market by the reconciler. |
| IB4 | **Index futures only at launch.** NIFTY 50 weekly and BANKNIFTY weekly contracts. No stock futures. No options. No MCX. |
| IB5 | **Near-weekly contract only.** Always trade the nearest active weekly expiry. Roll to next weekly on expiry morning before 09:15. |
| IB6 | **Zero naked positions.** A position that opens without a confirmed stop-loss is immediately force-closed at market. No exceptions. |
| IB7 | **Each evaluator owns its SL/TP geometry.** No shared universal formulas. |
| IB8 | **All config is env-overridable.** Every threshold, every flag, every limit is settable via environment variable. |
| IB9 | **SEBI compliance is non-negotiable.** Algo-ID on every order. Static IP whitelisted with broker. Kill switch responding in <5s. RA registration and NSE algo provider empanelment required before auto-execution goes live. |
| IB10 | **No auto-execution until regulatory gates pass.** Signal delivery (free) ships first. Auto-execution ships only after: (a) SEBI RA registration complete, (b) NSE algo provider empanelment complete, (c) at least one broker OAuth integration approved. |
| IB11 | **Net-of-fees economics.** Round-trip cost for NIFTY futures is ~0.061% of notional. Minimum viable scalp is 15 index points. Every threshold and gate must be designed around this floor. |
| IB12 | **Blast-radius caps are non-negotiable.** Per-user position cap (₹5,00,000 notional), per-user rate limit (5 orders/min), global kill switch (<5s from ops dashboard), global circuit breaker (>10 rejections/60s → auto-disable). |
| IB13 | **Never store plaintext broker API secret in logs, files, or error traces.** Signing service only. |
| IB14 | **Honest outcome reporting.** SL hits displayed with same visual weight as TP wins in the app. No selective reporting. |
| IB15 | **1–5 paid signals per market session is the target.** Silence = churned subscriber. Quality gate must not strangle volume below 1/session. |
| IB16 | **Use Razorpay for billing.** Not Google Play Billing. Google's financial services policy bars investment-execution apps from Play billing. Razorpay subscription API is the billing layer. |
| IB17 | **Market hours gate is absolute.** Scanner does not run outside 09:00–15:30 IST. No signals emit outside this window. |
| IB18 | **Event risk filter.** On scheduled high-impact events (RBI policy, Union Budget, US FOMC), reduce confidence thresholds or pause auto-execution as configured. |

---

## PART III: REGULATORY COMPLIANCE (SEBI)

This section specifies what the software must implement to be SEBI-compliant. Legal registration (RA + empanelment) is a parallel business task — this section covers the technical implementation requirements.

### 3.1 SEBI Algo Trading Framework (April 2026)

**Source**: SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb 4, 2025), mandatory from April 1, 2026.

**What the software must implement:**

#### 3.1.1 Algo-ID on Every Order
Every order placed by the system must carry the exchange-assigned Algo-ID obtained during NSE algo provider empanelment.

```python
# Every order payload to broker API must include:
order_payload = {
    "tradingsymbol": "NIFTY25JULFUT",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "order_type": "LIMIT",
    "quantity": 65,
    "price": 24150.0,
    "product": "MIS",           # intraday only
    "algo_id": ALGO_ID,          # env: NSE_ALGO_ID — required by SEBI
    "tag": f"lumin_{signal_id}", # internal reference tag
}
```

`NSE_ALGO_ID` is assigned by NSE after empanelment. Until empanelment, the system runs in signal-delivery-only mode (no auto-execution).

#### 3.1.2 Static IP Whitelisting
The VPS's outbound IP must be registered with each broker before API calls are accepted. This is a broker-side configuration step. The system must:
- Log a startup warning if the configured IP doesn't match the server's outbound IP
- Expose `GET /api/system/network-info` (owner-only) returning outbound IP for verification

#### 3.1.3 Kill Switch
Must respond in <5s from ops dashboard trigger. Implementation: Redis key `india:kill_switch` monitored every 2s by the execution loop. When `true`, no new orders placed, all pending order intents discarded.

#### 3.1.4 Order-Per-Second Limit
System must stay below 10 OPS per exchange per client to remain in the non-registered-algo category for order placement. The rate limiter must hard-cap at 8 OPS (safety margin).

#### 3.1.5 Strategy Change Notification
When any evaluator's core logic changes (not threshold tuning, but structural logic), the system must:
- Log the change with a `STRATEGY_LOGIC_CHANGE` marker
- Owner must notify NSE within 24 hours of the change going live

#### 3.1.6 Broker OAuth Model
The system must NOT store broker API secrets in a master key model. Every user authorizes via their broker's OAuth flow. The system holds:
- `access_token` (short-lived, typically 1 day)
- `refresh_token` or re-auth mechanism per broker

Users must re-authorize daily (broker session tokens expire at broker close, ~15:30–23:59 IST). The app prompts re-auth if the token is stale at session start.

### 3.2 Brokers Supported at Launch

**Primary: Fyers** (fyers.in)
- Reason: 5,000 symbol WS subscription, free historical data, bracket + cover orders still available, v3 API released June 2026.
- OAuth endpoint: `https://api.fyers.in/api/v3/generate-authcode`
- Token endpoint: `https://api.fyers.in/api/v3/validate-authcode`
- Order API: `https://api.fyers.in/api/v3/orders`

**Secondary: Dhan** (dhanhq.co)
- Reason: 200-level WS depth, Super Order type, Forever OCO orders, 25 OPS limit.
- OAuth endpoint: `https://api.dhan.co/v2/token`
- Order API: `https://api.dhan.co/v2/orders`

Both brokers are integrated. At launch, users choose one broker. Multi-broker per user is a future feature.

---

## PART IV: SYSTEM ARCHITECTURE

### 4.1 High-Level Architecture

```
NSE via Fyers/Dhan WebSocket
        ↓
HistoricalDataStore (OHLCV 6 TFs) + OrderFlowStore (OI, Volume, VIX, PCR)
        ↓
Scanner — every 15s during market hours × 29 instruments
        ↓
ScalpChannel.evaluate() — 14 evaluators per instrument
        ↓
Gate chain (session, spread, OI, event-risk, circuit-check)
        ↓
Chartist-eye stack (LevelBook + VolumeProfile + StructureTracker)
        ↓
SignalScoringEngine — confidence 0–100
        ↓
_enqueue_signal (universal SL min 0.60%, min 12 index points for NIFTY)
        ↓
IndiaSignalRouter
  → india_signals SQLite write (persisted record)
  → FCM push notification (Firebase Admin SDK → subscriber device)
        ↓
┌──────────────────────────────────────────────────────────────┐
│ INDIA-ENGINE CONTAINER                                       │
│ IndiaTradeMonitor · IndiaSignalDispatch                     │
│ IndiaPositionFSM · IndiaPositionWorker (Phase 2)            │
│ IndiaReconciler · IndiaSessionManager · IndiaEventCalendar  │
│ IndiaSnapshotWriter ──→ Redis ──→ INDIA-API CONTAINER        │
│                              IndiaRedisEngineFacade          │
│                              HTTP on own event loop          │
└──────────────────────────────────────────────────────────────┘
        ↓ (Phase 2 only)
India Signing Service (separate container, Unix socket)
        ↓
Fyers REST API → NSE
```

### 4.2 Container Layout

| Container | Role |
|---|---|
| `india-engine` | Scanner, FSM workers, TradeMonitor, SnapshotWriter |
| `india-api` | HTTP server (FastAPI), RedisEngineFacade, user settings |
| `india-redis` | Bridge between engine and api containers |
| `india-signing` | Signing service (broker API secrets only) |

All containers in a single Docker Compose file. `API_PROCESS_ISOLATED=true` always (same pattern as crypto engine).

### 4.3 Process Isolation Model

Identical to the crypto engine:
- Engine writes state to Redis every scan cycle via `SnapshotWriter`
- API container reads Redis via `RedisEngineFacade`
- User settings written by API → SQLite shared volume → engine reads at dispatch
- Control commands (kill switch, mode flip) written by API → Redis → engine polls every 2s

### 4.4 Two-Process Boot

`src/main.py` — engine entry point
`src/api/main.py` — API entry point

Both are started by Docker Compose with `API_PROCESS_ISOLATED=true`. They share:
- `/app/data/` — SQLite files, JSON data files
- Redis on `india-redis:6379`
- Unix socket at `/app/sock/signing.sock`

---

## PART V: TECHNOLOGY STACK

### 5.1 Language & Runtime

```
Python 3.11+
asyncio (all I/O non-blocking)
uvicorn + FastAPI (API server)
```

### 5.2 Dependencies (requirements.txt)

```
# Core async
aiohttp==3.9.5
asyncio-mqtt==0.16.1

# FastAPI
fastapi==0.111.0
uvicorn[standard]==0.30.1
python-multipart==0.0.9

# Data
pandas==2.2.2
numpy==1.26.4
ta==0.11.0           # technical indicators (RSI, MACD, ATR, Bollinger)

# Redis
redis[hiredis]==5.0.4

# Database
aiosqlite==0.20.0

# Auth / JWT
python-jose[cryptography]==3.3.0
firebase-admin==6.5.0

# Logging
loguru==0.7.2

# HTTP client
httpx==0.27.0

# Scheduling / time
pytz==2024.1
apscheduler==3.10.4

# Config
python-dotenv==1.0.1

# Crypto / signing
cryptography==42.0.8

# Razorpay billing
razorpay==1.4.1

# Testing
pytest==8.2.2
pytest-asyncio==0.23.7
pytest-mock==3.14.0
pytest-cov==5.0.0

# Lint
ruff==0.4.9
mypy==1.10.0
```

### 5.3 Config Pattern

All configuration through `config/__init__.py`. Every value is env-overridable. Pattern:

```python
import os

def _safe_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _safe_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _safe_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default

def _safe_str(key: str, default: str) -> str:
    return os.environ.get(key, default)
```

---

## PART VI: MARKET DATA PIPELINE

### 6.1 Instruments

**At launch: 2 instruments only.**

```python
INSTRUMENTS = [
    {
        "symbol": "NIFTY",
        "exchange": "NSE",
        "segment": "FO",
        "lot_size": 65,
        "tick_size": 0.05,
        "expiry_type": "weekly",          # Tuesday
        "min_scalp_points": 15,           # min viable move after fees
        "span_margin_approx": 100000,     # ₹1,00,000 per lot approx
    },
    {
        "symbol": "BANKNIFTY",
        "exchange": "NSE",
        "segment": "FO",
        "lot_size": 30,
        "tick_size": 0.05,
        "expiry_type": "weekly",          # Tuesday
        "min_scalp_points": 25,
        "span_margin_approx": 55000,      # ₹55,000 per lot approx
    },
]
```

The trading symbol (e.g., `NIFTY25JULFUT`) is resolved at runtime from the active weekly expiry. See Section 7 (Expiry Management).

**Expansion path (post-validation):** FINNIFTY, MIDCPNIFTY, then top-30 stock futures. Do not add until index futures are validated across 3+ months of live data.

### 6.2 Data Sources

**Market data (OHLCV + OI + depth):** Fyers API v3 WebSocket (primary). Dhan API v2 WebSocket (fallback).

**India VIX:** Real-time via Fyers WebSocket. Symbol: `INDIA VIX` on NSE.

**PCR (Put-Call Ratio):** Computed from option chain OI. Source: Fyers option chain endpoint polled every 5 minutes during session.

**Historical data (bootstrap on startup):** Fyers historical API. Up to 2 years of minute-level data available free.

### 6.3 Fyers WebSocket Integration

**Connection:**
```python
# Fyers WS v3
WS_URL = "wss://api.fyers.in/socket/v3/data-socket"

# Auth: access_token obtained via OAuth
headers = {
    "Authorization": f"{FYERS_APP_ID}:{access_token}"
}

# Subscribe message format
subscribe_msg = {
    "T": "SUB_L2",           # subscribe level 2 (full depth)
    "L": [
        "NSE:NIFTY25JULFUT-FF",
        "NSE:BANKNIFTY25JULFUT-FF",
        "NSE:INDIA VIX",
    ],
    "S": "HQ",               # high quality feed
}

# Tick message received (example OHLCV tick)
# {
#   "T": "sf",
#   "tt": 1720000015,         # Unix timestamp
#   "n": "NSE:NIFTY25JULFUT-FF",
#   "o": 24150.0,
#   "h": 24180.0,
#   "l": 24130.0,
#   "c": 24165.0,
#   "v": 3400,
#   "oi": 12500000,           # open interest
#   "bp": [[24164.0, 65], [24163.5, 130], ...],   # bid price/qty 5 levels
#   "sp": [[24165.5, 65], [24166.0, 195], ...],   # ask price/qty 5 levels
# }
```

**Reconnect logic:** Exponential backoff starting at 1s, max 30s, unlimited retries during market hours. No reconnect outside market hours (no data needed).

### 6.4 Historical Data Bootstrap

On engine startup (before 09:15 IST), fetch historical candles for all timeframes:

```python
TIMEFRAMES = ["1", "5", "15", "30", "60", "D"]  # Fyers TF codes

# Fyers historical endpoint
# GET https://api.fyers.in/api/v3/history
# params: symbol, resolution, date_format, range_from, range_to, cont_flag

# Fetch 500 candles per TF per instrument at startup
# 500 × 6 TFs × 2 instruments = 6,000 candles total — fast
```

The `HistoricalDataStore` is initialized from this bootstrap. The live WebSocket then updates it on each tick.

### 6.5 HistoricalDataStore

```python
class HistoricalDataStore:
    """
    Holds OHLCV candle arrays for each (symbol, timeframe) pair.
    Thread-safe via asyncio.Lock.
    
    Structure:
        _data: dict[str, dict[str, pd.DataFrame]]
        Key: symbol ("NIFTY25JULFUT")
        Inner key: timeframe ("1", "5", "15", "30", "60", "D")
        Value: DataFrame with columns [timestamp, open, high, low, close, volume, oi]
    
    Methods:
        get_candles(symbol, tf, n=200) -> pd.DataFrame  # last n candles
        update_candle(symbol, tf, tick)                  # upsert from WS tick
        get_latest_price(symbol) -> float
        get_spread_pct(symbol) -> float                  # (ask1 - bid1) / mid
    """
```

### 6.6 OrderFlowStore

```python
class OrderFlowStore:
    """
    Holds order-flow derived data: OI, volume delta, VIX, PCR.
    
    Structure:
        _oi: dict[str, float]           # symbol -> current OI
        _oi_history: dict[str, deque]   # symbol -> last 50 OI readings (1-min interval)
        _vix: float                      # current India VIX
        _pcr: float                      # current PCR (NIFTY weekly)
        _pcr_updated_at: datetime       # when PCR was last fetched
    
    Methods:
        get_oi(symbol) -> float
        get_oi_change_pct(symbol, lookback_minutes=15) -> float
        get_vix() -> float
        get_pcr() -> float
        is_vix_extreme_high() -> bool    # VIX > VIX_EXTREME_HIGH (default 20)
        is_vix_extreme_low() -> bool     # VIX < VIX_EXTREME_LOW (default 12)
        is_pcr_extreme_bullish() -> bool # PCR < PCR_EXTREME_BULLISH (default 0.65)
        is_pcr_extreme_bearish() -> bool # PCR > PCR_EXTREME_BEARISH (default 1.30)
    """
```

### 6.7 PCR Fetch (Polled, Not Streamed)

PCR is not available on WebSocket — must be polled. Poll every 5 minutes during session:

```python
# Fyers option chain endpoint
# GET https://api.fyers.in/api/v3/options/chain
# params: symbol=NSE:NIFTY50-INDEX, strikecount=20, timestamp=""

# Parse response:
# total_put_oi = sum of all PUT OIs across all strikes
# total_call_oi = sum of all CALL OIs across all strikes
# pcr = total_put_oi / total_call_oi

# Store in OrderFlowStore._pcr
```

---

## PART VII: EXPIRY MANAGEMENT

### 7.1 Active Contract Resolution

NIFTY and BANKNIFTY trade weekly contracts expiring every Tuesday. The engine must always trade the nearest active weekly expiry.

```python
class ExpiryManager:
    """
    Resolves the active near-weekly trading symbol for each instrument.
    
    Rules:
    - Active contract = nearest weekly expiry that has NOT yet expired
    - On expiry day (Tuesday): roll to next week at 09:00 IST (before open)
    - Symbol format (Fyers): NSE:NIFTY25JULFUT-FF
    - Symbol format (Dhan): depends on Dhan symbol master
    
    Methods:
        get_active_symbol(base: str) -> str
        get_expiry_date(base: str) -> date
        is_expiry_day() -> bool
        days_to_expiry(base: str) -> int
        is_last_hour_before_expiry() -> bool  # Tuesday 14:30–15:30
    """
    
    def get_active_symbol(self, base: str) -> str:
        today = date.today()
        # Find nearest Tuesday >= today
        days_ahead = (1 - today.weekday()) % 7  # 1 = Tuesday
        if days_ahead == 0 and datetime.now(IST).hour >= 9:
            days_ahead = 7  # already expired today, use next
        expiry = today + timedelta(days=days_ahead)
        # Format: NIFTY25JULFUT (NIFTY + 2-digit year + 3-char month + FUT)
        month_code = expiry.strftime("%b").upper()[:3]
        year_code = expiry.strftime("%y")
        return f"NSE:{base}{year_code}{month_code}FUT-FF"
```

### 7.2 Expiry Day Special Handling

On expiry day (Tuesday), from 14:30 IST onward:
- Reduce `MIN_SIGNAL_CONFIDENCE` from 65 to 70 (higher bar — expiry gamma makes moves violent)
- `EXPIRY_DAY_GAMMA_SQUEEZE_ENABLED` evaluator activates (see Evaluator 14)
- All open positions must be force-closed by 15:20 IST (10 minutes earlier than normal)

---

## PART VIII: SESSION MANAGEMENT

### 8.1 Market Calendar

```python
import pytz
from datetime import time, date, timedelta

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PREOPEN_START = time(9, 0)
LAST_SIGNAL_TIME = time(15, 20)    # no new signals after this
FORCE_CLOSE_TIME = time(15, 25)    # force-close all positions
EXPIRY_FORCE_CLOSE = time(15, 20)  # earlier on expiry day

# NSE holidays: maintain a list updated annually
# Source: https://www.nseindia.com/products-services/equity-market-trading-holidays
# Store in config/nse_holidays.json as list of "YYYY-MM-DD" strings
```

### 8.2 SessionManager

```python
class SessionManager:
    """
    Controls scanner lifecycle and session-aware logic.
    
    States: PRE_OPEN | OPEN | CLOSING | CLOSED
    
    Transitions:
        09:00 → PRE_OPEN: fetch historical data bootstrap, compute reference levels
        09:15 → OPEN: start scanner loop, start WS
        15:20 → CLOSING: stop new signal generation, begin force-close sweep
        15:30 → CLOSED: stop scanner, stop WS, write session summary
    
    Methods:
        is_open() -> bool
        is_closing() -> bool
        minutes_to_close() -> int
        is_nse_holiday(d: date) -> bool
        get_state() -> str
    """
    
    async def run(self):
        """Main session loop. Runs 24/7 as an async task. 
        Wakes at 09:00, runs market session, sleeps until next day 09:00."""
        while True:
            now = datetime.now(IST)
            if self.is_nse_holiday(now.date()):
                await self._sleep_until_tomorrow_preopen()
                continue
            if now.weekday() >= 5:  # Saturday/Sunday
                await self._sleep_until_tomorrow_preopen()
                continue
            if now.time() < PREOPEN_START:
                await self._sleep_until(PREOPEN_START)
                continue
            await self._run_session()
            await self._sleep_until_tomorrow_preopen()
```

### 8.3 Reference Level Computation (Pre-Open Phase)

During PRE_OPEN (09:00–09:15), before any signals emit:

```python
async def compute_reference_levels(symbol: str) -> dict:
    """
    Computed once per session from previous-day data.
    Used by evaluators as reference levels.
    
    Returns:
        prev_day_high: float
        prev_day_low: float
        prev_day_close: float
        prev_day_open: float
        call_auction_price: float    # price at which pre-open auction settled (09:08)
        gap_pct: float               # (call_auction_price - prev_day_close) / prev_day_close
        gap_direction: str           # "UP", "DOWN", "FLAT" (|gap_pct| < 0.1%)
        opening_range_high: float    # set after first 15min of session (09:15–09:30)
        opening_range_low: float     # set after first 15min of session
        vwap: float                  # rolling VWAP, updated every 5min
    """
```

---

## PART IX: SCANNER

### 9.1 Scanner Loop

```python
class IndiaScanner:
    """
    Runs every SCAN_INTERVAL_SEC (15) during OPEN session state.
    
    For each instrument in INSTRUMENTS:
        1. Build scan context (candles, indicators, OI, reference levels)
        2. Run all enabled evaluators
        3. For each candidate signal: run gate chain
        4. For candidates passing gates: score, enqueue
    """
    
    SCAN_INTERVAL_SEC = 15   # env: INDIA_SCAN_INTERVAL_SEC
    
    async def _scan_cycle(self):
        for instrument in self.active_instruments:
            ctx = await self._build_context(instrument)
            candidates = await self._evaluate_all(ctx)
            for candidate in candidates:
                if await self._passes_gates(candidate, ctx):
                    scored = self._score(candidate, ctx)
                    if scored.confidence >= MIN_SIGNAL_CONFIDENCE:
                        await self._enqueue_signal(scored)
```

### 9.2 Scan Context (IndiaContext)

```python
@dataclass
class IndiaContext:
    symbol: str                    # e.g. "NIFTY25JULFUT"
    base: str                      # e.g. "NIFTY"
    lot_size: int
    tick_size: float
    
    # OHLCV candles per timeframe (last 200 candles each)
    candles_1m: pd.DataFrame
    candles_5m: pd.DataFrame
    candles_15m: pd.DataFrame
    candles_30m: pd.DataFrame
    candles_60m: pd.DataFrame
    candles_daily: pd.DataFrame
    
    # Latest tick
    last_price: float
    bid1: float
    ask1: float
    spread_pct: float
    
    # OI
    current_oi: float
    oi_change_15m_pct: float       # OI % change over last 15 minutes
    
    # India-specific
    india_vix: float
    pcr: float
    vix_is_extreme_high: bool
    vix_is_extreme_low: bool
    pcr_is_extreme_bullish: bool
    pcr_is_extreme_bearish: bool
    
    # Session reference levels
    prev_day_high: float
    prev_day_low: float
    prev_day_close: float
    call_auction_price: float
    gap_pct: float
    gap_direction: str
    opening_range_high: float      # None until 09:30
    opening_range_low: float       # None until 09:30
    vwap: float
    
    # Derived indicators (computed once per context build)
    # 5-minute indicators
    ema8_5m: float
    ema21_5m: float
    ema55_5m: float
    ema200_5m: float
    rsi14_5m: float
    atr14_5m: float
    atr_pct_5m: float              # atr / last_price * 100
    bb_upper_5m: float
    bb_lower_5m: float
    bb_mid_5m: float
    volume_avg_5m_20: float        # 20-bar rolling avg volume
    
    # 15-minute indicators
    ema21_15m: float
    ema55_15m: float
    ema200_15m: float
    rsi14_15m: float
    atr14_15m: float
    
    # 60-minute indicators
    ema21_60m: float
    ema50_60m: float
    ema200_60m: float
    rsi14_60m: float
    atr14_60m: float
    regime_60m: str                # TRENDING_UP | TRENDING_DOWN | RANGING | QUIET
    
    # Daily indicators
    ema20_daily: float
    ema50_daily: float
    ema200_daily: float
    regime_daily: str
    
    # Market structure
    swing_high_5m: float           # last significant swing high (last 20 bars)
    swing_low_5m: float
    swing_high_15m: float
    swing_low_15m: float
    
    # SMC
    order_blocks: list[dict]       # recent unmitigated OBs on 5m/15m
    fvgs: list[dict]               # recent fair value gaps
    liquidity_levels: list[float]  # swing highs/lows as liquidity targets
    
    # Session state
    session_state: str             # OPEN | CLOSING
    minutes_to_close: int
    is_expiry_day: bool
    days_to_expiry: int
    
    # Event risk
    is_event_day: bool             # RBI/Budget/FOMC today
    event_description: str
```

### 9.3 Gate Chain

Gates run in order. First failure drops the candidate. All gates are logged for telemetry.

```python
GATE_CHAIN = [
    "session_gate",           # is market OPEN and time < LAST_SIGNAL_TIME?
    "spread_gate",            # spread_pct < MAX_SPREAD_PCT (0.05% for index futures)
    "cooldown_gate",          # no signal on same instrument in last COOLDOWN_SEC (300)
    "event_risk_gate",        # if event_day and not EVENT_RISK_TRADING_ENABLED: block
    "circuit_check_gate",     # instrument not currently circuit-broken or halted
    "min_atr_gate",           # atr14_5m > MIN_ATR_POINTS (env, default 8 NIFTY points)
    "oi_liquidity_gate",      # current_oi > MIN_OI (env, default 5,000,000)
    "duplicate_direction_gate", # no existing open position in same direction on same base
    "confidence_floor_gate",  # post-scoring gate: confidence >= MIN_SIGNAL_CONFIDENCE (65)
]
```

---

## PART X: SIGNAL EVALUATORS

Each evaluator returns `None` (no setup detected) or an `IndiaSignal` (candidate for gate + scoring).

```python
@dataclass
class IndiaSignal:
    signal_id: str                 # uuid4
    symbol: str                    # trading symbol e.g. "NIFTY25JULFUT"
    base: str                      # "NIFTY"
    direction: str                 # "LONG" or "SHORT"
    setup_class: str               # evaluator name (enum)
    entry: float                   # entry price level
    sl: float                      # stop loss price
    tp1: float                     # take profit 1
    tp2: float                     # take profit 2 (optional, same as tp1 if single TP)
    sl_pct: float                  # abs(entry - sl) / entry * 100
    tp1_pct: float                 # abs(tp1 - entry) / entry * 100
    rr_ratio: float                # tp1_pct / sl_pct
    lot_size: int
    confidence: float              # 0–100, filled by scorer
    regime_60m: str                # from context
    regime_daily: str              # from context
    atr_at_entry: float
    vix_at_entry: float
    pcr_at_entry: float
    expiry_date: date
    days_to_expiry: int
    dispatch_timestamp: float      # unix timestamp when emitted
    
    # Scoring inputs (filled by evaluator, used by scorer)
    htf_trend_aligned: bool        # True if evaluator confirmed 60m trend alignment
    breakout_volume_ratio: float   # volume at breakout vs 20-bar avg (for breakout setups)
    setup_reason: str              # human-readable: "5m OB reclaim + 15m BOS"
    suppression_reason: str        # gate that would block (for telemetry)
```

### 10.1 Evaluator 1: LIQUIDITY_SWEEP_REVERSAL

**Thesis:** Price sweeps (takes out) a prior swing high/low (a liquidity level), then immediately reverses, indicating smart money used retail stops as liquidity to enter the opposite trade.

**Direction:** LONG when a swing LOW is swept and reclaimed. SHORT when a swing HIGH is swept and reclaimed.

**Logic:**
```
1. Identify the most recent swing LOW (LONG setup) or swing HIGH (SHORT setup)
   on the 15-minute timeframe (last 30 bars).
   - Swing low: candle[i].low < candle[i-1].low and candle[i].low < candle[i+1].low
   - Swing high: candle[i].high > candle[i-1].high and candle[i].high > candle[i+1].high

2. Check if the current 5-minute candle swept the level:
   - LONG: current_candle.low < swing_low AND current_candle.close > swing_low
   - SHORT: current_candle.high > swing_high AND current_candle.close < swing_high
   (The wick went through; the candle body closed back above/below — the sweep failed)

3. The sweep candle must have volume > 1.2 × volume_avg_5m_20 (above average volume 
   confirms institutional activity, not noise).

4. Confirmation (optional, raises confidence):
   - LONG: next candle (or same candle body) closes above the 5m EMA8
   - SHORT: next candle closes below the 5m EMA8

SL:
   - LONG: sweep_candle.low - (atr14_5m * 0.3), minimum 1 tick below the sweep low
   - SHORT: sweep_candle.high + (atr14_5m * 0.3), minimum 1 tick above the sweep high
   - sl_pct must be >= MIN_SL_PCT (0.15%) and <= MAX_SL_PCT (1.0%)

TP1:
   - LONG: swing_high_15m (next significant resistance)
   - SHORT: swing_low_15m (next significant support)
   - If swing level not available: entry + (sl_distance * 2.0) for LONG
                                   entry - (sl_distance * 2.0) for SHORT
   - tp1_pct must be >= sl_pct * 1.5 (minimum 1.5R)

rr_ratio = tp1_pct / sl_pct (must be >= 1.5 to pass)

htf_trend_aligned:
   - LONG: regime_60m in ["TRENDING_UP", "RANGING"] → True; TRENDING_DOWN → False
   - SHORT: regime_60m in ["TRENDING_DOWN", "RANGING"] → True; TRENDING_UP → False
```

### 10.2 Evaluator 2: OPENING_RANGE_BREAKOUT (ORB)

**Thesis:** Price consolidates in the first 15 minutes of the session (09:15–09:30), establishing an Opening Range. A breakout above the range high signals continuation LONG; breakout below signals continuation SHORT. This is India's most-documented and backtested intraday strategy.

**Note:** This evaluator only activates after 09:30 IST (when the opening range is established). It does NOT fire before 09:30.

**Logic:**
```
Pre-condition: session_state == OPEN and current_time >= 09:30 IST
               opening_range_high is not None (set by SessionManager at 09:30)

1. Opening Range:
   OR_HIGH = max(high of all 5m candles from 09:15 to 09:30)
   OR_LOW  = min(low of all 5m candles from 09:15 to 09:30)
   OR_range_pct = (OR_HIGH - OR_LOW) / last_price * 100
   
   If OR_range_pct < 0.10%: range too tight, skip (noise, not a real auction range)
   If OR_range_pct > 1.5%: range too wide, skip (wild open, no clean ORB)

2. Breakout check (current 5m candle CLOSING above/below the range):
   LONG: current_candle.close > OR_HIGH + (atr14_5m * 0.1)  # slight buffer
   SHORT: current_candle.close < OR_LOW - (atr14_5m * 0.1)
   
3. Volume confirmation:
   breakout_volume_ratio = current_candle.volume / volume_avg_5m_20
   Require: breakout_volume_ratio >= 1.3 (30% above average)

4. HTF confirmation (optional, raises confidence):
   LONG: ema21_60m line is rising (60m candles[-1].ema21 > 60m candles[-3].ema21)
   SHORT: ema21_60m is falling

5. Gap handling:
   If gap_direction == "UP" and signal_direction == "SHORT": reduce confidence by 10
   If gap_direction == "DOWN" and signal_direction == "LONG": reduce confidence by 10
   (Trading against the gap bias requires more confirmation)

Entry:
   LONG: OR_HIGH + (atr14_5m * 0.1) — just above the range
   SHORT: OR_LOW - (atr14_5m * 0.1) — just below the range

SL:
   LONG: OR_LOW - (atr14_5m * 0.1)  — below the range low (invalidation: back in range)
   SHORT: OR_HIGH + (atr14_5m * 0.1)  — above the range high
   sl_pct clamps: [0.20%, 1.20%]

TP1:
   LONG: entry + (sl_distance * 2.0)
   SHORT: entry - (sl_distance * 2.0)

ORB fires at most ONCE per instrument per session. After it fires (either direction), 
the evaluator is disabled for that instrument for the rest of the session.

htf_trend_aligned: True if 60m regime matches direction, False otherwise.
```

### 10.3 Evaluator 3: TREND_PULLBACK_EMA

**Thesis:** In a trending market (confirmed on 60-minute timeframe), price pulls back to the EMA21 or EMA55 on the 5-minute chart and shows a bullish/bearish reclaim candle — an entry with the trend at a discounted price.

**Logic:**
```
Pre-condition: regime_60m in ["TRENDING_UP", "TRENDING_DOWN"]

LONG setup (regime_60m == TRENDING_UP):
1. Price has pulled back toward ema21_5m or ema55_5m from above
   (last_price is within atr14_5m * 1.5 of ema21_5m or ema55_5m)
2. Current 5m candle: low wick touches or crosses the EMA, but CLOSES above it
   (candle.low < ema21_5m or ema55_5m) AND (candle.close > ema21_5m or ema55_5m)
3. EMA stack intact on 60m: ema21_60m > ema55_60m (trend not broken at HTF)
4. RSI14_5m between 35–60 (pulled back but not oversold crash)

SHORT setup (regime_60m == TRENDING_DOWN): mirror of LONG

SL:
   LONG: candle.low - (atr14_5m * 0.3), but minimum 8 index points below entry
   SHORT: candle.high + (atr14_5m * 0.3), but minimum 8 index points above entry
   sl_pct clamps: [0.15%, 0.80%]

TP1:
   LONG: swing_high_15m (next resistance)
   SHORT: swing_low_15m (next support)
   Fallback: entry + sl_distance * 2.0 (LONG) / entry - sl_distance * 2.0 (SHORT)

htf_trend_aligned: True (this evaluator only fires in aligned 60m trend — always True)
```

### 10.4 Evaluator 4: VOLUME_SURGE_BREAKOUT (VSB)

**Thesis:** Price breaks a significant swing high with a volume surge, signalling institutional accumulation / momentum continuation.

**Logic:**
```
LONG only (breakout ABOVE prior swing high):
1. swing_high_15m is the level to watch (last 20 bars of 15m)
2. Current 5m candle breaks above swing_high_15m:
   candle.high > swing_high_15m AND candle.close > swing_high_15m
3. Volume surge: breakout_volume_ratio = candle.volume / volume_avg_5m_20 >= 2.0
   (at least 2× average — institutional volume)
4. OI buildup confirmation: oi_change_15m_pct > 0.5% (fresh longs building)

SHORT mirror: breakout BELOW swing_low_15m with volume surge + OI increase.

Entry: 
   LONG: swing_high_15m + (atr14_5m * 0.05) — just above the breakout
   SHORT: swing_low_15m - (atr14_5m * 0.05)

SL:
   LONG: candle.open (if candle opened below level, use swing_high_15m - atr14_5m * 0.3)
   SHORT: candle.open (mirror)
   sl_pct clamps: [0.15%, 1.0%]

TP1:
   LONG: next resistance from LevelBook, or entry + sl_distance * 2.0
   SHORT: next support from LevelBook, or entry - sl_distance * 2.0

htf_trend_aligned: True if 60m regime matches direction
```

### 10.5 Evaluator 5: BREAKDOWN_SHORT (BDS)

**Mirror of VSB for shorts.** Already covered as the short direction of VSB above. Kept as a separate evaluator to allow independent tuning and telemetry.

Same logic as VSB, SHORT direction only, independent enable flag: `BDS_ENABLED` (default True).

### 10.6 Evaluator 6: SR_FLIP_RETEST

**Thesis:** A previously significant support level that was broken becomes resistance (or vice versa). Price returns to retest this flipped level and is rejected.

**Logic:**
```
1. Find a "flipped" level: A price level that was significant support, 
   was cleanly broken (close below by > atr14_15m * 0.5), and is now 
   acting as resistance on the retest.
   Use LevelBook for S/R levels (see Section 11).

2. Retest detection:
   SHORT: price rises back to the flipped level (within atr14_5m * 0.3) 
          and forms a bearish rejection candle (bearish engulfing, pin bar, 
          or doji with significant upper wick)
   LONG: price drops back to a flipped resistance-turned-support level 
         and forms a bullish rejection candle

3. At-index-levels, round numbers are extra-significant:
   For NIFTY: levels divisible by 50 (24000, 24050, 24100...)
   For BANKNIFTY: levels divisible by 100

SHORT ONLY default (IB reasoning — SR_FLIP longs are historically 
weak in Indian markets too, not just crypto):
   SR_FLIP_LONG_ENABLED = False (env, default False)
   SR_FLIP_SHORT_ENABLED = True (env, default True)

SL:
   SHORT: rejection_candle.high + (atr14_5m * 0.3)
   LONG: rejection_candle.low - (atr14_5m * 0.3)
   sl_pct clamps: [0.20%, 1.50%]

TP1:
   SHORT: next significant support from LevelBook
   LONG: next significant resistance from LevelBook

htf_trend_aligned:
   SHORT: True if regime_60m in ["TRENDING_DOWN", "RANGING"]
   LONG: True if regime_60m in ["TRENDING_UP", "RANGING"]
```

### 10.7 Evaluator 7: INDIA_VIX_EXTREME

**Thesis:** India VIX at extreme high (>20) signals fear — contrarian long entry after a sharp drop. India VIX at extreme low (<12) signals complacency before a breakout.

**This is an India-specific evaluator with no crypto equivalent.**

**Logic:**
```
VIX_EXTREME_HIGH_THRESHOLD = 20.0   # env: INDIA_VIX_EXTREME_HIGH
VIX_EXTREME_LOW_THRESHOLD = 12.0    # env: INDIA_VIX_EXTREME_LOW

LONG setup (VIX extreme high + price reversal):
1. india_vix > VIX_EXTREME_HIGH_THRESHOLD
2. NIFTY has dropped > 1.5% from today's open
3. Current 5m candle is bullish engulfing or pin bar with bullish close
4. RSI14_5m < 35 (oversold on entry timeframe)
5. regime_60m != TRENDING_DOWN (can't be confirmed downtrend)

SHORT setup (VIX spike after compression):
1. india_vix was below VIX_EXTREME_LOW_THRESHOLD in the last 30 minutes
   AND india_vix is now rising (current > 30-min-ago by > 1.0 point)
2. NIFTY has risen > 1.5% from today's open
3. Current 5m candle is bearish

Entry: current price (market order intent — use last_price as entry reference)
SL:
   LONG: intraday_low - (atr14_5m * 0.3)
   SHORT: intraday_high + (atr14_5m * 0.3)
   sl_pct clamps: [0.30%, 1.50%]

TP1:
   LONG: prev_day_close (a reasonable recovery target)
   SHORT: prev_day_close

htf_trend_aligned: False by design (this is a reversal/contrarian signal)
```

### 10.8 Evaluator 8: PCR_EXTREME

**Thesis:** Put-Call Ratio at extremes signals crowded positioning. Extreme bearish PCR (>1.3) = everyone has puts = contrarian LONG. Extreme bullish PCR (<0.65) = everyone has calls = contrarian SHORT.

**This is an India-specific evaluator with no crypto equivalent.**

**Logic:**
```
PCR_EXTREME_BEARISH = 1.30   # env: PCR_EXTREME_BEARISH
PCR_EXTREME_BULLISH = 0.65   # env: PCR_EXTREME_BULLISH

LONG setup (PCR extreme bearish = contrarian long):
1. pcr > PCR_EXTREME_BEARISH
2. Price is near a significant support level (within atr14_15m of swing_low_15m 
   or prev_day_low or prev_day_close)
3. Current 5m candle showing bullish rejection (pin bar, engulfing)
4. OI change in last 15m is positive (new positions being built at the level)

SHORT setup: mirror with pcr < PCR_EXTREME_BULLISH

SL:
   LONG: support_level - (atr14_5m * 0.5)
   SHORT: resistance_level + (atr14_5m * 0.5)
   sl_pct clamps: [0.20%, 1.0%]

TP1:
   LONG: swing_high_15m or OR_HIGH (whichever is closer)
   SHORT: swing_low_15m or OR_LOW

htf_trend_aligned: False (contrarian by nature)
```

### 10.9 Evaluator 9: FAILED_AUCTION_RECLAIM (FAR)

**Thesis:** Price fails to sustain above a prior auction high (or below a prior auction low) and reclaims it. In Indian markets, this includes the pre-open call auction level.

**Logic:**
```
LONG setup (failed auction ABOVE, reclaim = bullish):
1. Price attempted to break above OR_HIGH or a prior session high 
   but was rejected (false breakout: went above but closed below)
2. Price then reclaims the level (close above) with volume
3. This is the "trap and reverse" — weak longs flushed, strong hands take over

SHORT setup: mirror (false breakdown below OR_LOW, then reclaims downward)

Special case — Call Auction Gap Reclaim:
If gap_direction == "UP" (NIFTY gapped up at open):
   SHORT: price rises to fill gap from above (back to gap edge), 
          fails to extend, forms bearish candle → short the gap fill rejection

If gap_direction == "DOWN":
   LONG: price drops to the downside gap edge, holds and bounces → gap-fill trade

Entry: breakout reclaim candle close
SL:
   Beyond the false breakout wick: 
   LONG: lowest low of the 3 candles during failed breakout
   SHORT: highest high of the 3 candles during failed breakdown
   sl_pct clamps: [0.15%, 1.0%]

TP1:
   LONG: next resistance
   SHORT: next support

htf_trend_aligned:
   Based on 60m regime matching direction.
```

### 10.10 Evaluator 10: DIVERGENCE_CONTINUATION

**Thesis:** RSI divergence on the 5-minute chart (price makes new high but RSI doesn't, or vice versa) signals momentum exhaustion and continuation in the opposite direction after a pullback.

**Logic:**
```
BEARISH divergence → SHORT setup:
1. price makes a new high (candle[0].high > candle[-5 to -10].high range)
2. rsi14_5m at current high is LOWER than rsi14_5m at the prior high
   (RSI diverges from price)
3. Confirmation: current candle closes bearishly after the divergence high
4. Not in extreme oversold (rsi > 40)

BULLISH divergence → LONG setup: mirror

SL:
   SHORT: divergence high + (atr14_5m * 0.3)
   LONG: divergence low - (atr14_5m * 0.3)
   sl_pct clamps: [0.20%, 1.20%]

TP1:
   SHORT: prev_day_low or OR_LOW (nearest support)
   LONG: prev_day_high or OR_HIGH (nearest resistance)

htf_trend_aligned:
   LONG divergence: True if regime_60m != TRENDING_DOWN
   SHORT divergence: True if regime_60m != TRENDING_UP
   (divergences work even counter-trend, but aligned adds confidence)
```

### 10.11 Evaluator 11: QUIET_COMPRESSION_BREAK (QCB)

**Thesis:** NIFTY enters a tight compression range (Bollinger Bands squeeze) during mid-session. A breakout from the squeeze with volume signals a directional move.

**Logic:**
```
Pre-condition: current_time between 10:00 and 14:00 IST 
               (mid-session only; morning and afternoon trends excluded)

1. Bollinger Band squeeze: 
   bb_width = (bb_upper_5m - bb_lower_5m) / bb_mid_5m
   squeeze: bb_width < BB_SQUEEZE_THRESHOLD (default 0.002 = 0.2%)
   The squeeze must have persisted for at least 6 consecutive 5m candles

2. Breakout: current candle closes outside the Bollinger Band
   LONG: candle.close > bb_upper_5m
   SHORT: candle.close < bb_lower_5m

3. Volume confirmation:
   current candle volume > 1.5 × volume_avg_5m_20

Entry: candle.close (or limit slightly above/below the band)

SL:
   LONG: bb_mid_5m - (atr14_5m * 0.1)  (return to mid = invalidation)
   SHORT: bb_mid_5m + (atr14_5m * 0.1)
   sl_pct clamps: [0.10%, 0.60%]  (compression breaks are tight; keep SL small)

TP1:
   LONG: OR_HIGH or swing_high_15m
   SHORT: OR_LOW or swing_low_15m
   Minimum: entry + sl_distance * 2.0

htf_trend_aligned: True if 60m regime matches direction, False otherwise
```

### 10.12 Evaluator 12: MA_CROSS_TREND_SHIFT

**Thesis:** EMA21/EMA55 crossover on 15-minute chart signals a trend shift. Long signal when 21 crosses above 55; short when 21 crosses below 55. Filtered by 60-minute trend agreement (must not fire against the 60m trend).

**Logic:**
```
1. Detect cross on 15m: 
   LONG: ema21_15m crosses ABOVE ema55_15m
         (prev bar: ema21 < ema55; current bar: ema21 > ema55)
   SHORT: ema21_15m crosses BELOW ema55_15m

2. HTF filter (required, not optional):
   LONG: regime_60m != TRENDING_DOWN
   SHORT: regime_60m != TRENDING_UP
   If HTF opposes: reject (log as "ma_cross_htf_misaligned")

3. Volume confirmation:
   Cross candle volume > 1.2 × volume_avg (15m, 20-bar average)

4. 24-hour cooldown: only fire this evaluator once per instrument per session
   (crosses are rare events; multiple fires = whipsaw, not signal)

Entry: current 15m close
SL:
   LONG: ema55_15m - (atr14_15m * 0.3)
   SHORT: ema55_15m + (atr14_15m * 0.3)
   sl_pct clamps: [0.20%, 1.0%]

TP1:
   LONG: swing_high_15m or prev_day_high
   SHORT: swing_low_15m or prev_day_low

htf_trend_aligned: True if regime_60m matches direction (per HTF filter above)
```

### 10.13 Evaluator 13: OI_SPIKE_REVERSAL

**Thesis:** A sudden surge in open interest (>3% increase in 15 minutes) at a key price level, followed by price rejection, signals institutional position-building that will drive a reversal. This is the India-specific replacement for LIQUIDATION_REVERSAL (which requires Binance's liquidation feed).

**Logic:**
```
1. OI spike: oi_change_15m_pct > OI_SPIKE_THRESHOLD (default 3.0%)
   AND current_oi > MIN_OI (5,000,000)

2. Spike at a key level: price is within (atr14_5m * 1.0) of:
   - OR_HIGH or OR_LOW
   - swing_high_15m or swing_low_15m
   - prev_day_high or prev_day_low or prev_day_close
   - Round number (NIFTY: divisible by 50)

3. Price rejection at the level:
   LONG: OI spike occurred as price FELL to support AND price is now showing
         reversal (current candle.close > open, wick below)
   SHORT: OI spike occurred as price ROSE to resistance AND price showing
          bearish rejection

Entry: rejection candle close
SL:
   LONG: level - (atr14_5m * 0.5)
   SHORT: level + (atr14_5m * 0.5)
   sl_pct clamps: [0.20%, 1.0%]

TP1:
   LONG: next resistance (OR_HIGH, swing_high)
   SHORT: next support (OR_LOW, swing_low)

htf_trend_aligned:
   LONG: True if regime_60m in ["TRENDING_UP", "RANGING"]
   SHORT: True if regime_60m in ["TRENDING_DOWN", "RANGING"]
```

### 10.14 Evaluator 14: EXPIRY_GAMMA_SQUEEZE

**Thesis:** On weekly expiry day (Tuesday), price gravitates toward the "max pain" strike (the strike where options sellers lose the least money). As price approaches max pain, option sellers delta-hedge aggressively, creating momentum toward that level.

**This evaluator is ONLY active on expiry day (is_expiry_day == True) between 13:00–15:00 IST.**

**Logic:**
```
Pre-conditions:
  is_expiry_day == True
  current_time between 13:00 and 15:00 IST
  EXPIRY_GAMMA_SQUEEZE_ENABLED == True (env, default True)

1. Compute max pain (requires option chain from PCR fetch):
   For each strike, calculate what options sellers pay out:
   max_pain_strike = strike that minimizes total payout to option buyers
   (Standard max pain formula: for each strike K, 
    sum over all strikes: call_oi * max(0, K - current_price) + put_oi * max(0, current_price - K))
   max_pain_strike = argmin over all K of the above sum

2. Max pain gap: max_pain_distance_pct = abs(last_price - max_pain_strike) / last_price * 100
   Only fire if max_pain_distance_pct between 0.20% and 1.0%
   (price is approaching but not yet at max pain)

3. Direction: toward max pain
   LONG if max_pain_strike > last_price (price needs to go up)
   SHORT if max_pain_strike < last_price

Entry: current price
SL:
   LONG: current_price - (atr14_5m * 1.0)
   SHORT: current_price + (atr14_5m * 1.0)
   sl_pct clamps: [0.20%, 0.80%]  (expiry day moves are sharp; tight SL)

TP1: max_pain_strike

htf_trend_aligned: False (expiry-day specific mechanism, not trend-based)

This evaluator fires at most ONCE per instrument on expiry day.
```

---

## PART XI: CONFIDENCE SCORING

### 11.1 Scoring Engine

Every candidate signal that passes the gate chain is scored by `IndiaSignalScoringEngine`. Returns a `confidence` score from 0–100.

```python
class IndiaSignalScoringEngine:
    """
    Multi-component confidence scorer.
    
    Components (each returns 0–20 points, except as noted):
    1. regime_score       (0–20): How well does the 60m regime support this setup?
    2. htf_score          (0–15): Daily trend alignment bonus
    3. volume_score       (0–15): Volume confirmation quality
    4. rr_score           (0–15): Risk/reward quality
    5. level_confluence   (0–10): Is entry near a key S/R level?
    6. oi_score           (0–10): OI change direction confirmation
    7. vix_pcr_score      (0–10): VIX/PCR context alignment
    8. structure_score    (0–5):  Clean market structure (no competing signals)
    
    Total max: 100
    Emit threshold: >= 65 (A+/B tier)
    A+ tier: >= 80
    B tier: 65–79
    """
    
    def score(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
        s = 0.0
        s += self._score_regime(signal, ctx)
        s += self._score_htf(signal, ctx)
        s += self._score_volume(signal, ctx)
        s += self._score_rr(signal, ctx)
        s += self._score_level_confluence(signal, ctx)
        s += self._score_oi(signal, ctx)
        s += self._score_vix_pcr(signal, ctx)
        s += self._score_structure(signal, ctx)
        return min(100.0, max(0.0, s))
```

### 11.2 Regime Score (0–20)

```python
REGIME_AFFINITY = {
    # setup_class → (points_if_aligned, points_if_neutral, points_if_opposing)
    "LIQUIDITY_SWEEP_REVERSAL":  (18, 12, 8),
    "OPENING_RANGE_BREAKOUT":    (16, 14, 8),    # ORB fires in any context
    "TREND_PULLBACK_EMA":        (20, 8, 0),      # only fires in trend; must be aligned
    "VOLUME_SURGE_BREAKOUT":     (14, 14, 10),    # breakout: neutral floor
    "BREAKDOWN_SHORT":           (14, 14, 10),
    "SR_FLIP_RETEST":            (16, 12, 8),
    "INDIA_VIX_EXTREME":         (10, 10, 10),    # reversal: regime-neutral
    "PCR_EXTREME":               (10, 10, 10),    # reversal: regime-neutral
    "FAILED_AUCTION_RECLAIM":    (18, 12, 8),
    "DIVERGENCE_CONTINUATION":   (16, 12, 8),
    "QUIET_COMPRESSION_BREAK":   (14, 14, 10),    # breakout: neutral floor
    "MA_CROSS_TREND_SHIFT":      (16, 14, 0),     # cross at regime boundary: neutral floor
    "OI_SPIKE_REVERSAL":         (14, 12, 8),
    "EXPIRY_GAMMA_SQUEEZE":      (12, 12, 12),    # expiry mechanic: regime-neutral
}

def _score_regime(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    regime = ctx.regime_60m
    direction = signal.direction
    setup = signal.setup_class
    
    aligned, neutral, opposing = REGIME_AFFINITY.get(setup, (14, 12, 8))
    
    if direction == "LONG":
        if regime == "TRENDING_UP":
            return aligned
        elif regime in ["RANGING", "QUIET"]:
            return neutral
        else:  # TRENDING_DOWN
            return opposing
    else:  # SHORT
        if regime == "TRENDING_DOWN":
            return aligned
        elif regime in ["RANGING", "QUIET"]:
            return neutral
        else:  # TRENDING_UP
            return opposing
```

### 11.3 HTF (Daily) Score (0–15)

```python
def _score_htf(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    # If evaluator confirmed HTF alignment: full points
    if signal.htf_trend_aligned:
        return 15
    # Daily regime alignment (secondary)
    regime_daily = ctx.regime_daily
    direction = signal.direction
    if direction == "LONG" and regime_daily == "TRENDING_UP":
        return 10
    if direction == "SHORT" and regime_daily == "TRENDING_DOWN":
        return 10
    if regime_daily in ["RANGING", "QUIET"]:
        return 8
    # Daily opposes:
    return 4
```

### 11.4 Volume Score (0–15)

```python
def _score_volume(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    # Breakout setups score on breakout candle volume ratio
    breakout_setups = {"VOLUME_SURGE_BREAKOUT", "BREAKDOWN_SHORT", "OPENING_RANGE_BREAKOUT"}
    
    if signal.setup_class in breakout_setups and signal.breakout_volume_ratio > 0:
        ratio = signal.breakout_volume_ratio
    else:
        # For non-breakout setups: use current candle volume vs average
        try:
            ratio = ctx.candles_5m.iloc[-1]["volume"] / ctx.volume_avg_5m_20
        except:
            return 8  # neutral on data error
    
    if ratio >= 3.0: return 15
    if ratio >= 2.0: return 13
    if ratio >= 1.5: return 11
    if ratio >= 1.2: return 9
    if ratio >= 1.0: return 8   # neutral floor for non-volume-dependent setups
    return 5
```

### 11.5 R:R Score (0–15)

```python
def _score_rr(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    rr = signal.rr_ratio
    if rr >= 3.0: return 15
    if rr >= 2.5: return 13
    if rr >= 2.0: return 11
    if rr >= 1.8: return 9
    if rr >= 1.5: return 7   # minimum acceptable
    return 4
```

### 11.6 Level Confluence Score (0–10)

```python
def _score_level_confluence(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    """
    Is the entry price near a significant level?
    Significant levels: OR_HIGH/LOW, prev_day_H/L/C, round numbers (50-point for NIFTY)
    """
    entry = signal.entry
    tolerance = ctx.atr14_5m * 0.5
    
    key_levels = [
        ctx.opening_range_high, ctx.opening_range_low,
        ctx.prev_day_high, ctx.prev_day_low, ctx.prev_day_close,
    ]
    # Add round numbers
    base_round = round(entry / 50) * 50 if ctx.base == "NIFTY" else round(entry / 100) * 100
    key_levels.extend([base_round - 50, base_round, base_round + 50])
    
    confluences = sum(1 for lvl in key_levels if lvl and abs(entry - lvl) <= tolerance)
    
    if confluences >= 3: return 10
    if confluences == 2: return 8
    if confluences == 1: return 5
    return 2
```

### 11.7 OI Score (0–10)

```python
def _score_oi(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    """OI change direction should confirm the signal direction."""
    oi_chg = ctx.oi_change_15m_pct
    direction = signal.direction
    
    # Rising OI + rising price = LONG confirmation
    # Rising OI + falling price = SHORT confirmation
    # (in futures, OI rises when new money enters; the direction tells the bias)
    last_price_chg = (ctx.candles_5m.iloc[-1]["close"] - ctx.candles_5m.iloc[-3]["close"]) / ctx.candles_5m.iloc[-3]["close"] * 100
    
    oi_rising = oi_chg > 0.5
    price_rising = last_price_chg > 0
    
    if direction == "LONG" and oi_rising and price_rising: return 10
    if direction == "SHORT" and oi_rising and not price_rising: return 10
    if oi_chg > 0.2: return 7  # some OI confirmation
    if abs(oi_chg) < 0.2: return 5  # neutral
    return 3  # OI declining (unwinding, not fresh entry)
```

### 11.8 VIX/PCR Score (0–10)

```python
def _score_vix_pcr(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    """
    VIX and PCR context alignment.
    High VIX = high vol = wider moves = favour signals with wider TP targets
    PCR extreme matching direction = crowd is on the other side = extra confidence
    """
    score = 5  # neutral base
    direction = signal.direction
    
    # VIX context
    if ctx.india_vix < 14:  # low VIX: calm trending moves; breakout/trend setups get bonus
        breakout_trend = {"OPENING_RANGE_BREAKOUT", "TREND_PULLBACK_EMA", "VOLUME_SURGE_BREAKOUT",
                          "BREAKDOWN_SHORT", "MA_CROSS_TREND_SHIFT"}
        if signal.setup_class in breakout_trend:
            score += 2
    elif ctx.india_vix > 18:  # high VIX: volatility; reversal setups get bonus
        reversal = {"LIQUIDITY_SWEEP_REVERSAL", "INDIA_VIX_EXTREME", "PCR_EXTREME",
                    "FAILED_AUCTION_RECLAIM", "OI_SPIKE_REVERSAL"}
        if signal.setup_class in reversal:
            score += 2
    
    # PCR context
    if direction == "LONG" and ctx.pcr_is_extreme_bearish:
        score += 3  # crowd is bearish → contrarian LONG gets bonus
    if direction == "SHORT" and ctx.pcr_is_extreme_bullish:
        score += 3  # crowd is bullish → contrarian SHORT gets bonus
    if direction == "SHORT" and ctx.pcr_is_extreme_bearish:
        score -= 2  # going with the fearful crowd (risky)
    
    return min(10.0, max(0.0, score))
```

### 11.9 Structure Score (0–5)

```python
def _score_structure(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
    """
    Is the market structure clean for this signal?
    Penalise: choppy candles, conflicting signals, extreme ATR (chaotic bars)
    """
    # ATR relative to expected
    base_atr = 10 if ctx.base == "NIFTY" else 25  # expected quiet-day ATR
    atr_ratio = ctx.atr14_5m / base_atr
    
    if atr_ratio < 0.5: return 2   # too quiet (flat market)
    if atr_ratio > 3.0: return 1   # chaotic bars
    if 0.8 <= atr_ratio <= 2.0: return 5  # ideal volatility range
    return 3
```

---

## PART XII: LEVEL BOOK (S/R INFRASTRUCTURE)

### 12.1 LevelBook

```python
class IndiaLevelBook:
    """
    Maintains significant price levels for each instrument.
    Used by evaluators for TP targets and confluence scoring.
    
    Level sources:
    1. Daily pivots (Classic + Camarilla)
    2. Previous day H/L/C/O
    3. Weekly high/low
    4. Swing highs/lows on 15m and 60m
    5. Round numbers (NIFTY: every 50 points; BANKNIFTY: every 100 points)
    6. OR_HIGH / OR_LOW (set at 09:30)
    7. VWAP
    
    Methods:
        nearest_resistance(price, direction="LONG") -> float
        nearest_support(price, direction="SHORT") -> float
        get_all_levels(price_range=(price*0.98, price*1.02)) -> list[float]
        
    Refresh: every 60 minutes during session (levels don't change intraday 
             except VWAP and swing highs/lows)
    """
    
    def _compute_pivot_levels(self, prev_h, prev_l, prev_c):
        """Classic pivot point formula."""
        pivot = (prev_h + prev_l + prev_c) / 3
        r1 = 2 * pivot - prev_l
        r2 = pivot + (prev_h - prev_l)
        s1 = 2 * pivot - prev_h
        s2 = pivot - (prev_h - prev_l)
        return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}
```

### 12.2 Regime Classification

```python
def classify_regime(candles: pd.DataFrame, 
                    ema_fast: int = 21, 
                    ema_slow: int = 55, 
                    adx_period: int = 14) -> str:
    """
    Classify market regime for a given OHLCV DataFrame.
    Returns: "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "QUIET"
    
    Logic:
    1. Compute EMA(fast) and EMA(slow)
    2. Compute ADX(14)
    3. Compute ATR percentage (ATR/close * 100)
    
    QUIET: atr_pct < 0.08% (dead market)
    
    TRENDING_UP: 
        ema_fast > ema_slow AND adx >= 20 AND 
        (last 5 closes are mostly above ema_fast)
    
    TRENDING_DOWN:
        ema_fast < ema_slow AND adx >= 20 AND
        (last 5 closes are mostly below ema_fast)
    
    RANGING: 
        adx < 20 OR (ema_fast is crossing ema_slow repeatedly in last 10 bars)
    """
```

---

## PART XIII: SIGNAL ROUTING & DELIVERY

### 13.1 Signal Tiers

| Confidence | Tier | Routing |
|---|---|---|
| 80–100 | A+ | App only — FCM push + REST. Visible to Tier A+ and Tier B subscribers (full detail). Free users see blurred card. |
| 65–79 | B | App only — FCM push + REST. Visible to Tier B subscribers (full detail). Free users see blurred card. |
| < 65 | FILTERED | Dropped silently. No FCM push. No DB write. |

**No Telegram.** Signal delivery is exclusively through lumin-india-app via FCM + REST API.

### 13.2 FCM Push Notification Payload

When a signal is emitted, the engine calls Firebase Admin SDK to send a data-only push to all subscribed devices for that user.

```python
# FCM notification payload — data fields only (no notification field)
# This lets the app control notification display and handle background taps
fcm_message = {
    "data": {
        "type": "india_signal",
        "signal_id": signal.signal_id,
        "symbol": "NIFTY",
        "direction": "LONG",
        "confidence_tier": "A+",     # A+, B — never show raw score in notification
        "setup_class": "OPENING_RANGE_BREAKOUT",
        "session_date": "2026-07-01",
    },
    "token": user_fcm_token,         # per-device token registered at app login
    "android": {
        "priority": "high",          # wake screen for time-sensitive signals
        "ttl": "300s",               # discard if not delivered within 5 min (signal is stale)
    }
}

# App notification display (composed by the app, not the engine):
# Title: "NIFTY LONG — A+ Signal"
# Body:  "Opening Range Breakout | Tap to view"
# On tap: navigate to /signal/<signal_id>

# Outcome notification (when SL or TP1 is hit — Phase 2 only):
# Title: "NIFTY LONG — TP1 Hit"   OR   "NIFTY LONG — SL Hit"
# Body: "Entry 24,185 → TP1 24,260 | +₹487/lot | 23 min"
```

**Why data-only push (no notification field):** Lets the app decide display format, handle foreground vs background differently, and ensures no signal detail leaks in the OS notification shade for free users.

### 13.3 In-App Signal Payload

Signal delivered to the Lumin Android app via the API server's `/api/india/signals` endpoint. The app subscribes to signals via Firebase Cloud Messaging (FCM) push notifications — the API server sends a push notification on each new signal, then the app fetches the full signal details.

```json
{
  "signal_id": "uuid",
  "base": "NIFTY",
  "symbol": "NIFTY25JULFUT",
  "direction": "LONG",
  "setup_class": "OPENING_RANGE_BREAKOUT",
  "confidence": 82,
  "tier": "A+",
  "entry": 24185.0,
  "sl": 24140.0,
  "tp1": 24260.0,
  "tp2": 24320.0,
  "sl_pct": 0.186,
  "tp1_pct": 0.310,
  "rr_ratio": 1.67,
  "lot_size": 65,
  "regime_60m": "TRENDING_UP",
  "india_vix": 14.2,
  "pcr": 0.98,
  "expiry_date": "2026-07-15",
  "days_to_expiry": 2,
  "setup_reason": "OR breakout above 24180 with 2.1× volume, 60m uptrend confirmed",
  "dispatch_timestamp": 1720000015.0,
  "status": "ACTIVE"
}
```

---

## PART XIV: POSITION FSM (EXECUTION)

### 14.1 Position States

```
PENDING → ACTIVE → [TP1_HIT | SL_HIT | EXPIRED | FORCE_CLOSED]
              ↓
          BE_SHIFT_FIRED (intermediate sub-state, position still ACTIVE)
```

### 14.2 Position Dataclass

```python
@dataclass
class IndiaPosition:
    position_id: str            # uuid
    signal_id: str              # source signal
    user_id: str
    broker: str                 # "fyers" | "dhan"
    base: str                   # "NIFTY"
    symbol: str                 # "NIFTY25JULFUT"
    direction: str              # "LONG" | "SHORT"
    lot_size: int
    num_lots: int               # how many lots (user-defined, min 1)
    entry: float                # entry price
    sl: float                   # current stop loss price
    tp1: float
    original_sl: float          # SL at entry (never modified)
    state: str                  # PENDING | ACTIVE | TP1_HIT | SL_HIT | EXPIRED | FORCE_CLOSED
    
    # Order IDs from broker
    entry_order_id: str
    sl_order_id: str
    tp_order_id: str
    
    # Execution tracking
    entry_fill_price: float
    entry_fill_time: float      # unix timestamp
    close_price: float
    close_time: float
    close_reason: str           # TP1 | SL | EXPIRY | FORCE_CLOSE | BE_SL
    
    # BE shift
    be_shift_fired: bool        # True if SL has been moved to breakeven
    be_shift_price: float       # price at which BE shift was triggered
    
    # Timestamps
    created_at: float
    updated_at: float
    
    # P/L (computed on close)
    pnl_points: float           # in index points
    pnl_pct: float              # as % of entry
    pnl_inr: float              # in ₹ (points × lot_size × num_lots)
```

### 14.3 Default Exit Model (TP1-Full)

Mirrors the crypto engine's Session-34 default:
- **TP1-full**: Close 100% at TP1 via resting LIMIT order
- **Fixed SL**: Stop-Loss MARKET order placed at entry
- **BE shift at +1%**: When price moves +1% in favour, shift SL to entry (breakeven)
- **Force close at 15:25**: Any position open at 15:25 IST is closed at market

This is the default. Per-user opt-ins for pre-TP partials may be added in v2.

### 14.4 Order Placement (Fyers)

```python
async def place_entry_order(position: IndiaPosition, token: str) -> str:
    """Place intraday MARKET order for entry. Returns order_id."""
    
    transaction_type = "BUY" if position.direction == "LONG" else "SELL"
    
    payload = {
        "symbol": f"NSE:{position.symbol}-FF",
        "qty": position.lot_size * position.num_lots,
        "type": 2,           # 2 = MARKET
        "side": 1 if transaction_type == "BUY" else -1,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "orderTag": f"lumin_{position.signal_id[:8]}",
        "algoId": NSE_ALGO_ID,   # SEBI requirement
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.fyers.in/api/v3/orders/sync",
            json=payload,
            headers={"Authorization": f"{FYERS_APP_ID}:{token}"}
        )
    resp.raise_for_status()
    return resp.json()["data"]["id"]


async def place_sl_order(position: IndiaPosition, token: str) -> str:
    """Place SL-MARKET order (stop loss). Returns order_id."""
    
    # For LONG position: sell to close, triggered at SL price
    transaction_type = "SELL" if position.direction == "LONG" else "BUY"
    
    payload = {
        "symbol": f"NSE:{position.symbol}-FF",
        "qty": position.lot_size * position.num_lots,
        "type": 4,           # 4 = SL-MARKET (stop-loss triggered, execute at market)
        "side": -1 if transaction_type == "SELL" else 1,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": position.sl,
        "validity": "DAY",
        "orderTag": f"lumin_sl_{position.signal_id[:8]}",
        "algoId": NSE_ALGO_ID,
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.fyers.in/api/v3/orders/sync",
            json=payload,
            headers={"Authorization": f"{FYERS_APP_ID}:{token}"}
        )
    resp.raise_for_status()
    return resp.json()["data"]["id"]


async def place_tp_order(position: IndiaPosition, token: str) -> str:
    """Place TP LIMIT order (reduce-only equivalent). Returns order_id."""
    
    transaction_type = "SELL" if position.direction == "LONG" else "BUY"
    
    payload = {
        "symbol": f"NSE:{position.symbol}-FF",
        "qty": position.lot_size * position.num_lots,
        "type": 1,           # 1 = LIMIT
        "side": -1 if transaction_type == "SELL" else 1,
        "productType": "INTRADAY",
        "limitPrice": position.tp1,
        "stopPrice": 0,
        "validity": "DAY",
        "orderTag": f"lumin_tp_{position.signal_id[:8]}",
        "algoId": NSE_ALGO_ID,
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.fyers.in/api/v3/orders/sync",
            json=payload,
            headers={"Authorization": f"{FYERS_APP_ID}:{token}"}
        )
    resp.raise_for_status()
    return resp.json()["data"]["id"]
```

### 14.5 BE Shift

```python
async def maybe_fire_be_shift(position: IndiaPosition, current_price: float, token: str):
    """
    Called on every mark price update for active positions.
    Shifts SL to entry (breakeven) when price moves BE_SHIFT_TRIGGER_PCT in favour.
    """
    if position.be_shift_fired:
        return
    if position.state != "ACTIVE":
        return
    
    BE_SHIFT_TRIGGER_PCT = 1.0  # env: BE_SHIFT_TRIGGER_PCT
    
    if position.direction == "LONG":
        profit_pct = (current_price - position.entry) / position.entry * 100
    else:
        profit_pct = (position.entry - current_price) / position.entry * 100
    
    if profit_pct < BE_SHIFT_TRIGGER_PCT:
        return
    
    # Cancel existing SL order, place new SL at entry
    await cancel_order(position.sl_order_id, token)
    position.sl = position.entry
    position.be_shift_fired = True
    new_sl_id = await place_sl_order(position, token)
    position.sl_order_id = new_sl_id
    
    logger.info(f"be_shift fired for {position.position_id}: SL moved to {position.entry}")
```

### 14.6 Force Close (15:25 IST)

```python
async def force_close_all_positions(token_map: dict[str, str]):
    """
    Called at 15:25 IST. Cancels all open limit/SL orders and 
    places MARKET close orders for all ACTIVE positions.
    token_map: {user_id → access_token}
    """
    for position in get_all_active_positions():
        token = token_map.get(position.user_id)
        if not token:
            logger.error(f"No token for user {position.user_id}, cannot force close")
            continue
        
        # Cancel resting TP and SL orders
        await cancel_order(position.sl_order_id, token)
        await cancel_order(position.tp_order_id, token)
        
        # Place market close
        await place_market_close(position, reason="FORCE_CLOSE_EOD", token=token)
```

---

## PART XV: RECONCILER

### 15.1 Purpose

The reconciler runs every 60 seconds during the session. It diffs the engine's internal position state against actual broker positions and detects:
- Positions open in engine that don't exist at broker (ghost positions)
- Positions at broker not tracked by engine (untracked positions)
- SL/TP orders that got cancelled without the engine knowing (orphaned positions = naked positions)

```python
class IndiaReconciler:
    """
    Runs every RECONCILER_INTERVAL_SEC (60) during OPEN session.
    
    Steps:
    1. Fetch all open positions from broker API for each user with active positions
    2. Diff against engine internal state
    3. For ghost positions (engine has them, broker doesn't): mark CLOSED in engine
    4. For orphaned positions (broker has open, no SL order): 
       immediately place SL, log NAKED_POSITION_DETECTED
    5. For positions older than MAX_POSITION_AGE_MIN (90 min): force close
    6. For positions that approach 15:25 cutoff: add to force-close queue
    """
    
    MAX_POSITION_AGE_MIN = 90   # env: INDIA_MAX_POSITION_AGE_MIN
```

---

## PART XVI: DATABASE SCHEMAS

### 16.1 SQLite Schema (Shared Volume: /app/data/india.db)

```sql
-- User records and tier management
CREATE TABLE IF NOT EXISTS india_users (
    user_id TEXT PRIMARY KEY,
    firebase_uid TEXT UNIQUE NOT NULL,
    email TEXT,
    tier TEXT NOT NULL DEFAULT 'free',     -- free | assist | auto
    paid_until INTEGER,                     -- unix timestamp
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Per-user broker credentials (OAuth tokens, NOT API secrets)
CREATE TABLE IF NOT EXISTS india_broker_tokens (
    user_id TEXT NOT NULL,
    broker TEXT NOT NULL,                   -- fyers | dhan
    access_token TEXT NOT NULL,             -- short-lived (1 day)
    refresh_token TEXT,
    token_expires_at INTEGER NOT NULL,      -- unix timestamp
    client_id TEXT NOT NULL,                -- user's broker client ID
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, broker)
);

-- Per-user auto-trade settings
CREATE TABLE IF NOT EXISTS india_user_settings (
    user_id TEXT PRIMARY KEY,
    preferred_broker TEXT DEFAULT 'fyers',
    num_lots INTEGER DEFAULT 1,             -- lots per signal
    max_concurrent_positions INTEGER DEFAULT 2,
    instruments_enabled TEXT DEFAULT 'NIFTY,BANKNIFTY',  -- JSON array
    auto_trade_enabled INTEGER DEFAULT 0,   -- 0 = off (SEBI gate), 1 = on
    be_shift_enabled INTEGER DEFAULT 1,
    updated_at INTEGER NOT NULL
);

-- Live and historical positions
CREATE TABLE IF NOT EXISTS india_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    broker TEXT NOT NULL,
    base TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    lot_size INTEGER NOT NULL,
    num_lots INTEGER NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp1 REAL NOT NULL,
    original_sl REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    entry_order_id TEXT,
    sl_order_id TEXT,
    tp_order_id TEXT,
    entry_fill_price REAL,
    entry_fill_time INTEGER,
    close_price REAL,
    close_time INTEGER,
    close_reason TEXT,
    be_shift_fired INTEGER DEFAULT 0,
    be_shift_price REAL,
    pnl_points REAL,
    pnl_pct REAL,
    pnl_inr REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_india_positions_user ON india_positions(user_id, state);
CREATE INDEX idx_india_positions_signal ON india_positions(signal_id);

-- Signal history
CREATE TABLE IF NOT EXISTS india_signals (
    signal_id TEXT PRIMARY KEY,
    base TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    setup_class TEXT NOT NULL,
    confidence REAL NOT NULL,
    tier TEXT NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp1 REAL NOT NULL,
    sl_pct REAL,
    tp1_pct REAL,
    rr_ratio REAL,
    lot_size INTEGER NOT NULL,
    regime_60m TEXT,
    india_vix REAL,
    pcr REAL,
    expiry_date TEXT,
    days_to_expiry INTEGER,
    setup_reason TEXT,
    dispatch_timestamp INTEGER NOT NULL,
    outcome TEXT,                           -- TP1 | SL | EXPIRED | NULL (still active)
    outcome_price REAL,
    outcome_time INTEGER,
    session_date TEXT NOT NULL              -- YYYY-MM-DD of the trading session
);
CREATE INDEX idx_india_signals_date ON india_signals(session_date);

-- Audit log for all control actions
CREATE TABLE IF NOT EXISTS india_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    action TEXT NOT NULL,
    payload TEXT,               -- JSON
    result TEXT,
    created_at INTEGER NOT NULL
);

-- Order dispatch audit (SEBI compliance — every order placed must be logged)
CREATE TABLE IF NOT EXISTS india_order_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    broker TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_type TEXT NOT NULL,   -- ENTRY | SL | TP | BE_SL | FORCE_CLOSE | CANCEL
    algo_id TEXT NOT NULL,      -- NSE_ALGO_ID (SEBI requirement)
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL,
    stop_price REAL,
    status TEXT NOT NULL,       -- PLACED | FILLED | CANCELLED | REJECTED
    broker_response TEXT,       -- JSON
    created_at INTEGER NOT NULL
);

-- Session summary (one row per trading day)
CREATE TABLE IF NOT EXISTS india_session_summary (
    session_date TEXT PRIMARY KEY,
    signals_generated INTEGER DEFAULT 0,
    signals_emitted INTEGER DEFAULT 0,
    positions_opened INTEGER DEFAULT 0,
    positions_tp1 INTEGER DEFAULT 0,
    positions_sl INTEGER DEFAULT 0,
    positions_force_closed INTEGER DEFAULT 0,
    total_pnl_inr REAL DEFAULT 0,
    nifty_open REAL,
    nifty_close REAL,
    india_vix_open REAL,
    india_vix_close REAL,
    session_notes TEXT
);

-- Per-user Razorpay billing
CREATE TABLE IF NOT EXISTS india_subscriptions (
    user_id TEXT PRIMARY KEY,
    razorpay_subscription_id TEXT UNIQUE,
    razorpay_customer_id TEXT,
    plan TEXT NOT NULL,         -- assist | auto
    status TEXT NOT NULL,       -- active | paused | cancelled | expired
    current_period_end INTEGER, -- unix timestamp
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

### 16.2 Redis Key Structure

```
# Engine snapshot (written by engine, read by API)
india:snapshot:pulse        → JSON: {auto_mode, session_state, active_positions_count, 
                                     last_scan_at, signals_today, uptime_sec}
india:snapshot:signals      → JSON: list of last 20 signals
india:snapshot:positions    → JSON: all ACTIVE positions (across all users)
india:snapshot:session      → JSON: {state, minutes_to_close, is_expiry_day, nifty_price, 
                                     banknifty_price, india_vix, pcr}

# Control keys (written by API/ops, read by engine)
india:kill_switch           → "true" | "false"
india:auto_mode             → "true" | "false"

# Per-user state
india:user:{user_id}:token_valid  → "true" | "false"  (TTL 300s)

# Rate limiting (per-user per-minute order count)
india:ratelimit:{user_id}:{minute_bucket}  → integer count (TTL 90s)
```

---

## PART XVII: API SERVER

### 17.1 Endpoints

All endpoints are under `/api/india/`. Auth via Firebase JWT except where noted.

```
PUBLIC (no auth):
  GET  /api/india/health                    Engine health (for broker/NSE webhook probes)

USER (Firebase JWT required):
  GET  /api/india/signals                   Latest signals (all tiers, for free users)
  GET  /api/india/signals/{signal_id}       Single signal detail
  GET  /api/india/positions                 User's own positions
  GET  /api/india/session                   Current session state (VIX, PCR, regime)

  POST /api/india/broker/auth               Initiate OAuth flow for broker
  POST /api/india/broker/callback           OAuth callback → store token
  GET  /api/india/broker/status             Is broker token valid?

  GET  /api/india/settings                  User auto-trade settings
  PUT  /api/india/settings                  Update auto-trade settings

BILLING (Firebase JWT required):
  POST /api/india/billing/verify            Verify Razorpay payment → set tier
  POST /api/india/billing/webhook           Razorpay webhook (subscription events)

OWNER ONLY (Bearer token = OPS_AUTH_TOKEN):
  GET  /api/india/admin/users               List users with tiers
  POST /api/india/admin/kill-switch         {enabled: true|false}
  POST /api/india/admin/mode                {auto_mode: true|false}
  GET  /api/india/admin/audit               Last 100 audit log entries
  GET  /api/india/admin/order-audit         Last 100 order audit entries
```

### 17.2 Auth Middleware

```python
async def verify_firebase_jwt(token: str) -> str:
    """Verify Firebase ID token. Returns user_id (firebase UID)."""
    decoded = firebase_admin.auth.verify_id_token(token)
    return decoded["uid"]

async def require_auto_tier(user_id: str) -> None:
    """Raise 403 if user is not on 'auto' tier."""
    settings = await get_user_settings(user_id)
    if settings.tier != "auto":
        raise HTTPException(403, "Auto tier required")
```

---

## PART XVIII: RAZORPAY BILLING

### 18.1 Plans

```python
RAZORPAY_PLANS = {
    "assist": {
        "plan_id": "plan_india_assist",    # created in Razorpay dashboard
        "amount": 99900,                    # ₹999 in paise
        "interval": "monthly",
        "description": "Lumin India Assist — one-tap trade placement",
    },
    "auto": {
        "plan_id": "plan_india_auto",      # created in Razorpay dashboard
        "amount": 199900,                   # ₹1,999 in paise
        "interval": "monthly",
        "description": "Lumin India Auto — hands-off auto-execution",
    }
}
```

### 18.2 Verification Flow

```
App → POST /api/india/billing/verify  {razorpay_payment_id, razorpay_subscription_id, 
                                        razorpay_signature, plan}

Server:
1. Verify HMAC signature: 
   expected = HMAC-SHA256(razorpay_subscription_id + "|" + razorpay_payment_id, 
                          RAZORPAY_KEY_SECRET)
   if expected != razorpay_signature: raise 400

2. Fetch subscription from Razorpay API to confirm status == "active"
3. Set user tier in india_users and india_subscriptions
4. Return new JWT with updated tier claim (or instruct app to refresh Firebase token)
```

### 18.3 Webhook Events

Razorpay subscription lifecycle events → `POST /api/india/billing/webhook`:

```python
WEBHOOK_EVENTS = {
    "subscription.activated":  → set tier, set paid_until
    "subscription.charged":    → extend paid_until
    "subscription.cancelled":  → set tier = "free" at period_end
    "subscription.halted":     → set tier = "free" immediately
    "subscription.completed":  → set tier = "free" at period_end
}
```

---

## PART XIX: BLAST-RADIUS CAPS (NON-NEGOTIABLE)

All limits are env-overridable but must have safe defaults. Any change to these values requires owner sign-off.

```python
# Per-user
MAX_CONCURRENT_POSITIONS_PER_USER = 2          # env: MAX_CONCURRENT_INDIA_POSITIONS
MAX_NOTIONAL_PER_USER_INR = 500_000            # ₹5 lakh notional; env: MAX_INDIA_NOTIONAL_INR
MAX_ORDERS_PER_MINUTE_PER_USER = 5             # env: MAX_INDIA_ORDERS_PER_MINUTE

# System-wide
MAX_ORDERS_PER_SECOND_SYSTEM = 8              # hard cap, <10 OPS per SEBI requirement
KILL_SWITCH_RESPONSE_SEC = 5                   # max seconds from trigger to no-new-orders

# Circuit breaker
CIRCUIT_BREAKER_REJECTION_THRESHOLD = 10       # >10 broker rejections/60s → auto-disable
CIRCUIT_BREAKER_WINDOW_SEC = 60

# Naked position invariant
# If SL order placement fails: immediately fire market close for the entry position.
# A position without a confirmed SL order is never allowed to persist.
NAKED_POSITION_MAX_AGE_SEC = 10                # env: NAKED_POSITION_MAX_AGE_SEC
```

---

## PART XX: CONFIGURATION (COMPLETE env REFERENCE)

```bash
# ──────────────────────────────────────────
# SYSTEM
# ──────────────────────────────────────────
API_PROCESS_ISOLATED=true
INDIA_ENGINE_PORT=8001
INDIA_API_PORT=8002
REDIS_URL=redis://india-redis:6379/0
DATA_DIR=/app/data
LOG_LEVEL=INFO

# ──────────────────────────────────────────
# MARKET / SESSION
# ──────────────────────────────────────────
INDIA_SCAN_INTERVAL_SEC=15
NSE_HOLIDAYS_FILE=/app/config/nse_holidays.json
BE_SHIFT_TRIGGER_PCT=1.0
EXPIRY_FORCE_CLOSE_ADVANCE_MIN=10     # close positions this many minutes before expiry close

# ──────────────────────────────────────────
# SIGNAL QUALITY
# ──────────────────────────────────────────
MIN_SIGNAL_CONFIDENCE=65
SIGNAL_COOLDOWN_SEC=300               # no duplicate on same instrument within 5 min
MAX_SPREAD_PCT=0.05                   # max bid-ask spread (0.05% for index futures)
MIN_ATR_POINTS_NIFTY=8               # minimum ATR14 in index points for NIFTY
MIN_ATR_POINTS_BANKNIFTY=20
MIN_OI=5000000                        # minimum open interest to trade
EVENT_RISK_TRADING_ENABLED=false      # block all signals on RBI/Budget days by default

# ──────────────────────────────────────────
# EVALUATOR FLAGS (all default ON unless marked)
# ──────────────────────────────────────────
LSR_ENABLED=true
ORB_ENABLED=true
TPE_ENABLED=true
VSB_ENABLED=true
BDS_ENABLED=true
SR_FLIP_ENABLED=true
SR_FLIP_LONG_ENABLED=false            # default OFF (longs historically weak)
SR_FLIP_SHORT_ENABLED=true
VIX_EXTREME_ENABLED=true
PCR_EXTREME_ENABLED=true
FAR_ENABLED=true
DIV_CONT_ENABLED=true
QCB_ENABLED=true
MA_CROSS_ENABLED=true
OI_SPIKE_REVERSAL_ENABLED=true
EXPIRY_GAMMA_SQUEEZE_ENABLED=true

# ──────────────────────────────────────────
# INDIA VIX / PCR THRESHOLDS
# ──────────────────────────────────────────
INDIA_VIX_EXTREME_HIGH=20.0
INDIA_VIX_EXTREME_LOW=12.0
PCR_EXTREME_BEARISH=1.30
PCR_EXTREME_BULLISH=0.65
PCR_FETCH_INTERVAL_SEC=300

# ──────────────────────────────────────────
# BLAST-RADIUS CAPS
# ──────────────────────────────────────────
MAX_CONCURRENT_INDIA_POSITIONS=2
MAX_INDIA_NOTIONAL_INR=500000
MAX_INDIA_ORDERS_PER_MINUTE=5
CIRCUIT_BREAKER_REJECTION_THRESHOLD=10
CIRCUIT_BREAKER_WINDOW_SEC=60
NAKED_POSITION_MAX_AGE_SEC=10
INDIA_MAX_POSITION_AGE_MIN=90

# ──────────────────────────────────────────
# BROKER — FYERS
# ──────────────────────────────────────────
FYERS_APP_ID=                         # Fyers API App ID
FYERS_SECRET_KEY=                     # Fyers API Secret (held by signing service only)
FYERS_REDIRECT_URI=https://api.luminapp.org/api/india/broker/callback/fyers

# ──────────────────────────────────────────
# BROKER — DHAN
# ──────────────────────────────────────────
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=                    # per-user tokens in DB; this is the engine's own data account token

# ──────────────────────────────────────────
# SEBI COMPLIANCE
# ──────────────────────────────────────────
NSE_ALGO_ID=                          # assigned by NSE after empanelment; blank until empanelled
AUTO_EXECUTION_ENABLED=false          # master gate — false until SEBI registration complete

# ──────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────
FIREBASE_PROJECT_ID=
FIREBASE_SA_JSON=                     # path to service account JSON
OPS_AUTH_TOKEN=                       # bearer token for owner-only endpoints

# ──────────────────────────────────────────
# BILLING
# ──────────────────────────────────────────
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=

# ──────────────────────────────────────────
# SIGNING SERVICE
# ──────────────────────────────────────────
SIGNING_SOCK=/app/sock/india_signing.sock
```

---

## PART XXI: DOCKER COMPOSE

```yaml
# docker-compose.india.yml
# Secrets are NOT read from an env_file. They are injected by GitHub Actions
# at deploy time via environment variables set in the SSH deploy step.
# On the VPS, run: docker compose --env-file /dev/stdin up -d < <(env)
# or pass --env flags explicitly. Never write a .env file with secrets to disk.
version: "3.9"

services:
  india-redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --save "" --appendonly no
    networks: [india-net]

  india-engine:
    build:
      context: .
      dockerfile: Dockerfile.india
    restart: unless-stopped
    environment:
      # Non-secret config — safe to have in compose file
      - API_PROCESS_ISOLATED=true
      - REDIS_URL=redis://india-redis:6379/0
      - AUTO_EXECUTION_ENABLED=${AUTO_EXECUTION_ENABLED:-false}
      - NSE_ALGO_ID=${NSE_ALGO_ID:-}
      - INDIA_DEV_MODE=${INDIA_DEV_MODE:-false}
      # Secrets — injected by GitHub Actions, never hardcoded here
      - FYERS_APP_ID=${FYERS_APP_ID}
      - FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
      - FIREBASE_SA_JSON=${FIREBASE_SA_JSON}
      - RAZORPAY_KEY_SECRET=${RAZORPAY_KEY_SECRET}
      - RAZORPAY_WEBHOOK_SECRET=${RAZORPAY_WEBHOOK_SECRET}
      - OPS_AUTH_TOKEN=${OPS_AUTH_TOKEN}
    volumes:
      - india-data:/app/data
      - india-sock:/app/sock
    depends_on: [india-redis, india-signing]
    networks: [india-net]
    command: python -m src.india.main

  india-api:
    build:
      context: .
      dockerfile: Dockerfile.india
    restart: unless-stopped
    environment:
      - API_PROCESS_ISOLATED=true
      - REDIS_URL=redis://india-redis:6379/0
      - FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
      - FIREBASE_SA_JSON=${FIREBASE_SA_JSON}
      - RAZORPAY_KEY_ID=${RAZORPAY_KEY_ID}
      - RAZORPAY_KEY_SECRET=${RAZORPAY_KEY_SECRET}
      - RAZORPAY_WEBHOOK_SECRET=${RAZORPAY_WEBHOOK_SECRET}
      - OPS_AUTH_TOKEN=${OPS_AUTH_TOKEN}
    volumes:
      - india-data:/app/data
      - india-sock:/app/sock
    ports:
      - "8002:8002"
    depends_on: [india-redis]
    networks: [india-net]
    command: python -m src.india.api.main

  india-signing:
    build:
      context: .
      dockerfile: Dockerfile.india.signing
    restart: unless-stopped
    environment:
      # Signing service is the only container that ever holds the Fyers secret key
      - FYERS_APP_ID=${FYERS_APP_ID}
      - FYERS_SECRET_KEY=${FYERS_SECRET_KEY}
    volumes:
      - india-sock:/app/sock
    networks: [india-net]

volumes:
  india-data:
  india-sock:

networks:
  india-net:
```

---

## PART XXII: SIGNING SERVICE

### 22.1 Purpose

The signing service is the only place where broker API secrets (Fyers secret key) exist in plaintext. It runs as a separate container and receives order requests via Unix socket. It signs/authenticates the request and forwards to the broker API.

**For Fyers:** The signing service holds `FYERS_SECRET_KEY`. It generates the OAuth checksum signature required for token generation, and can proxy authenticated order requests.

**For Dhan:** Per-user tokens are short-lived OAuth tokens (not API secrets). The signing service validates and forwards these tokens but does not hold a master secret.

```python
# Signing service Unix socket protocol (same pattern as crypto engine)
# Request: JSON line over socket
{
    "op": "fyers_place_order",
    "user_id": "uid_123",
    "access_token": "user_oauth_token",     # user's own OAuth token
    "payload": { ...order_payload... }
}
# Response: JSON line
{
    "ok": true,
    "order_id": "12345678"
}
```

---

## PART XXIII: ANDROID APP (LUMIN INDIA)

### 23.1 Overview

The India signal features integrate into the existing Lumin app as a new section. The app already has Firebase auth, signal feed, subscription page, and auto-trade settings from the crypto product. The India section adds:

1. **India Signals tab** — same as Signals tab but for NIFTY/BANKNIFTY
2. **India Auto-trade settings** — broker OAuth link, lot size, instrument selection
3. **Broker connect flow** — OAuth web view for Fyers/Dhan
4. **Subscription** — Razorpay payment sheet (not Google Play Billing)

### 23.2 New Screens

```
India Signals Feed (list)
  → Signal Detail Card
    → "Take Trade" button (Assist tier, client-side one-tap)
    → "Trade automated" badge (Auto tier, server-side)

India Auto-Trade Settings
  → Broker Connect (Fyers / Dhan OAuth)
  → Number of lots
  → Instruments (NIFTY / BANKNIFTY / Both)
  → Auto-trade toggle (disabled until broker connected + Auto tier)
  → Daily P/L summary

India Subscription Page
  → Assist plan (₹999/mo) — Razorpay checkout
  → Auto plan (₹1,999/mo) — Razorpay checkout
  → Current status

India Performance
  → Session P/L
  → Win rate, avg R:R
  → Signal history
```

### 23.3 Razorpay Integration (Flutter)

```dart
// Use razorpay_flutter package
import 'package:razorpay_flutter/razorpay_flutter.dart';

void openRazorpayCheckout(String planId, int amountPaise) {
  var options = {
    'key': RAZORPAY_KEY_ID,           // from env/config
    'subscription_id': subscriptionId, // create subscription first via API
    'name': 'Lumin India',
    'description': planId == 'assist' ? 'Assist Plan — ₹999/mo' : 'Auto Plan — ₹1,999/mo',
    'prefill': {
      'contact': userPhone,
      'email': userEmail,
    }
  };
  _razorpay.open(options);
}

void _handlePaymentSuccess(PaymentSuccessResponse response) async {
  // POST /api/india/billing/verify
  await apiClient.verifyIndiaBilling(
    paymentId: response.paymentId!,
    subscriptionId: response.orderId!,
    signature: response.signature!,
    plan: selectedPlan,
  );
}
```

### 23.4 Broker OAuth (Fyers) in Flutter

```dart
// Launch Fyers OAuth in an in-app WebView
// URL: https://api.fyers.in/api/v3/generate-authcode
//      ?client_id={FYERS_APP_ID}&redirect_uri={REDIRECT_URI}&response_type=code&state={user_id}

// After OAuth, Fyers redirects to the redirect_uri with ?code=AUTH_CODE
// The API server's /api/india/broker/callback/fyers endpoint:
//   1. Receives code
//   2. Exchanges for access_token + refresh_token
//   3. Stores in india_broker_tokens
//   4. Returns success to app
```

---

## PART XXIV: OPS DASHBOARD (360 CE INDIA)

The ops dashboard (`ops.luminapp.org`) gains an "India" section. Pattern mirrors the existing crypto ops dashboard (360ce-ops).

### 24.1 New Routes (add to 360ce-ops)

```
/india                   → India overview (session state, active positions, today's signals)
/india/signals           → Signal history + what-if simulator
/india/positions         → Live positions table (user + position + entry/current/SL/TP)
/india/performance       → Daily/weekly P/L, win rate, per-setup breakdown
/india/control           → Kill switch, auto-mode toggle
/india/compliance        → Algo-ID status, broker empanelment status, order audit
```

### 24.2 Compliance Panel

Critical for SEBI compliance visibility:

```
NSE_ALGO_ID:          [configured / NOT CONFIGURED ⚠️]
AUTO_EXECUTION:       [ENABLED / DISABLED (pending registration)]
RA Registration:      [active / pending / not started]
NSE Empanelment:      [active / pending / not started]
Broker OAuth (Fyers): [connected / not connected]
Today's order count:  45 orders  (limit: 480/session at 8 OPS × 60s × 60min × 6h 15min)
Static IP:            [143.x.x.x — whitelisted ✓]
```

---

## PART XXV: TESTING REQUIREMENTS

### 25.1 Test Coverage Targets

| Module | Minimum Coverage |
|---|---|
| Evaluators (each) | 90% — test: fires correctly, does NOT fire on invalid setups, SL/TP math correct |
| Scoring engine | 85% — test: each component returns correct points for given inputs |
| Gate chain | 90% — test: each gate blocks correctly |
| Position FSM | 95% — test: every state transition |
| Reconciler | 80% |
| Session manager | 80% |
| Expiry manager | 95% — test: roll logic on Tuesday, symbol resolution |
| Blast-radius caps | 100% |
| Billing verification | 90% |
| API endpoints | 80% |

### 25.2 Key Tests (Must Exist)

```python
# Evaluator tests
def test_lsr_fires_on_sweep_and_reclaim_long():
def test_lsr_does_not_fire_without_volume_confirmation():
def test_lsr_sl_is_below_sweep_wick():
def test_orb_does_not_fire_before_0930():
def test_orb_fires_only_once_per_session():
def test_orb_does_not_fire_on_range_too_tight():
def test_sr_flip_long_disabled_by_default():
def test_vix_extreme_long_requires_oversold_rsi():

# FSM tests
def test_position_force_closed_at_1525():
def test_naked_position_force_closed_within_10_seconds():
def test_be_shift_does_not_double_fire():
def test_be_shift_not_fired_below_threshold():

# Blast-radius tests
def test_position_cap_blocks_third_concurrent_position():
def test_kill_switch_prevents_new_orders():
def test_rate_limit_blocks_above_5_per_minute():

# Expiry tests
def test_active_symbol_is_nearest_tuesday():
def test_rolls_to_next_week_on_tuesday_morning():
def test_expiry_day_detected_correctly():

# Compliance tests
def test_every_order_has_algo_id():
def test_every_order_logged_to_audit():
def test_naked_position_detection_fires_alert():
```

### 25.3 Test Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests/india"]
```

---

## PART XXVI: DEPLOYMENT SEQUENCE

### 26.1 Infrastructure Setup

```
VPS: Ubuntu 22.04, dedicated (not shared with crypto engine)
     Min 2 vCPU / 4 GB RAM for Phase 1
     Static IP required (Fyers API whitelist in Phase 2)
     Docker + Docker Compose v2 installed

Owner accesses VPS via SSH from Termux (Android) or terminal.
GitHub Actions deploys via SSH using VPS_SSH_KEY stored in repo secrets.
```

### 26.2 GitHub Actions Secrets (store in lumin-india-engine repo settings)

```
VPS_HOST              IP or hostname of India VPS
VPS_USER              SSH username (e.g. ubuntu)
VPS_SSH_KEY           Private key (PEM) for GitHub Actions → VPS SSH

FYERS_APP_ID          Fyers API client ID
FYERS_SECRET_KEY      Fyers API secret (signing service only — never exposed to engine/api)
FIREBASE_PROJECT_ID   Firebase project ID
FIREBASE_SA_JSON      Firebase service account JSON (base64-encoded or inline JSON string)
RAZORPAY_KEY_ID       Razorpay public key (also safe in app, but stored here for consistency)
RAZORPAY_KEY_SECRET   Razorpay server-side secret
RAZORPAY_WEBHOOK_SECRET  Razorpay webhook verification secret
OPS_AUTH_TOKEN        Single password for ops dashboard auth gate
```

**Never create a `.env` file with real secrets on the VPS.** GitHub Actions injects secrets as environment variables at deploy time. The deploy workflow:

```yaml
# .github/workflows/deploy-india.yml (simplified)
- name: Deploy to India VPS
  env:
    FYERS_APP_ID: ${{ secrets.FYERS_APP_ID }}
    FYERS_SECRET_KEY: ${{ secrets.FYERS_SECRET_KEY }}
    FIREBASE_PROJECT_ID: ${{ secrets.FIREBASE_PROJECT_ID }}
    FIREBASE_SA_JSON: ${{ secrets.FIREBASE_SA_JSON }}
    RAZORPAY_KEY_ID: ${{ secrets.RAZORPAY_KEY_ID }}
    RAZORPAY_KEY_SECRET: ${{ secrets.RAZORPAY_KEY_SECRET }}
    RAZORPAY_WEBHOOK_SECRET: ${{ secrets.RAZORPAY_WEBHOOK_SECRET }}
    OPS_AUTH_TOKEN: ${{ secrets.OPS_AUTH_TOKEN }}
  run: |
    ssh ${{ secrets.VPS_USER }}@${{ secrets.VPS_HOST }} "
      cd /app/lumin-india-engine &&
      git pull origin main &&
      FYERS_APP_ID=$FYERS_APP_ID \
      FYERS_SECRET_KEY=$FYERS_SECRET_KEY \
      FIREBASE_PROJECT_ID=$FIREBASE_PROJECT_ID \
      FIREBASE_SA_JSON='$FIREBASE_SA_JSON' \
      RAZORPAY_KEY_ID=$RAZORPAY_KEY_ID \
      RAZORPAY_KEY_SECRET=$RAZORPAY_KEY_SECRET \
      RAZORPAY_WEBHOOK_SECRET=$RAZORPAY_WEBHOOK_SECRET \
      OPS_AUTH_TOKEN=$OPS_AUTH_TOKEN \
      AUTO_EXECUTION_ENABLED=false \
      docker compose -f docker-compose.india.yml up -d --build --remove-orphans
    "
```

### 26.3 First Deploy (Phase 1 — Signal Delivery Only)

```bash
# On VPS (via Termux SSH or GitHub Actions):

# 1. Clone engine repo
git clone https://github.com/mkmk749278/lumin-india-engine /app/lumin-india-engine
cd /app/lumin-india-engine

# 2. Add NSE holiday list (download from NSE website annually)
# Place at config/nse_holidays.json — format: ["2026-01-26", "2026-08-15", ...]

# 3. Start Phase 1 stack (no signing service — auto-execution not live)
# Secrets injected by GitHub Actions workflow or set manually for first boot:
AUTO_EXECUTION_ENABLED=false \
FIREBASE_PROJECT_ID=... \
  docker compose -f docker-compose.india.yml up -d india-redis india-engine india-api

# 4. Verify
docker logs india-engine --tail 50
# Expected outside market hours: "Session state: CLOSED"
# Expected at 09:00 next trading day: "Session state: PRE_OPEN — fetching historical data"
# Expected at 09:15: "Session state: OPEN — scanner started"
```

### 26.4 Phase 2 Activation (After SEBI Registration + Owner Sign-Off)

```bash
# GitHub Actions workflow — update secret:
# Set NSE_ALGO_ID in GitHub repo secrets (assigned by NSE after empanelment)

# Add to deploy workflow env:
NSE_ALGO_ID: ${{ secrets.NSE_ALGO_ID }}
AUTO_EXECUTION_ENABLED: "true"

# On VPS — start signing service:
docker compose -f docker-compose.india.yml up -d india-signing

# Run integration test (dry-run — no real order placed):
docker exec india-engine python scripts/india_order_integration_test.py --dry-run

# After dry-run confirms stack is wired end-to-end:
# Owner explicitly approves → deploy with AUTO_EXECUTION_ENABLED=true
```

---

## PART XXVII: KNOWN LIMITATIONS & FUTURE WORK

### 27.1 Not In Scope for v1

- Options (calls/puts) — requires FSM redesign for strike/expiry selection
- Stock futures — physical settlement risk; add in v2
- FINNIFTY, MIDCPNIFTY — add after NIFTY/BANKNIFTY validated (3+ months)
- Multi-broker per user
- Strategy notifications to NSE on logic changes (manual process for now)
- Automated NSE holiday calendar update (manual annual update)
- Basket order for hedged positions

### 27.2 Architecture Decisions That May Need Revisiting

1. **PCR polling every 5 minutes**: Fine for now. If options chain API rate limits tighten, move to 15-minute poll.
2. **Single Fyers data account for engine**: Works for 2 instruments. When expanding to 30 instruments, may need dedicated market-data subscription from TrueData.
3. **SQLite for user settings**: Scales to ~10,000 users without issue. Beyond that, consider PostgreSQL.
4. **No RA registration yet**: `AUTO_EXECUTION_ENABLED=false` gates everything. The system is fully built for auto-execution but the gate must remain closed until registration is complete.

### 27.3 Regulatory Watch Items

- **SEBI algo registration process**: New rules are still being operationalised (April 2026). NSE empanelment procedures may change. Monitor NSE circulars.
- **Broker ToS changes**: Fyers/Dhan may change API terms. Have Dhan as fallback.
- **PCR/option chain data**: If Fyers restricts option chain API calls, TrueData is the backup.

---

---

## PART XXVIII: INFRASTRUCTURE & REPO REFERENCE

### 28.1 GitHub Repositories

| Repo | URL | Purpose |
|---|---|---|
| `lumin-india-engine` | `github.com/mkmk749278/lumin-india-engine` | Python engine: scanner, evaluators, FSM, API server |
| `lumin-india-app` | `github.com/mkmk749278/lumin-india-app` | Flutter Android app (standalone Play Store listing) |
| `lumin-india-ops` | `github.com/mkmk749278/lumin-india-ops` | FastAPI ops dashboard (India tab + control plane) |

### 28.2 Foundation Documents

Each repo contains at root:
- `CLAUDE.md` — CTE operating brief for AI sessions in that repo
- `OWNER_BRIEF.md` — business rules, architecture doctrine, subscription model (in engine repo)
- `ACTIVE_CONTEXT.md` — current state, open items, session log (in engine repo)

### 28.3 Infrastructure

| Component | Detail |
|---|---|
| India VPS | Dedicated Ubuntu 22.04, Docker, static IP. Separate from 360-v2 crypto VPS. |
| Owner access | SSH via Termux (Android) for manual VPS interaction |
| CI/CD | GitHub Actions → SSH deploy to India VPS |
| Secrets | GitHub Actions repo secrets only. Never `.env` files with credentials on disk. |
| Firebase | FCM (push notifications) + Firestore (subscriber validation, generation-gated cache) |
| Primary broker | Fyers API v3 (free for account holders) |
| Billing | Razorpay subscription API |

---

*End of Specification v2.0. This document contains the complete blueprint for Lumin India.*
*Reference system (360-v2 crypto engine): `github.com/mkmk749278/360-v2`*
*Companion documents: CLAUDE.md (engine + app), OWNER_BRIEF.md, ACTIVE_CONTEXT.md*
