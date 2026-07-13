"""Central configuration for the Lumin India engine.

Every value is overridable via an environment variable so one image runs
identically in dev, CI, and on the VPS with only env changes (OWNER_BRIEF IB8).
Mirrors the crypto engine's config pattern.

Scope discipline (CLAUDE.md — no scaffolds): this module holds only settings
that shipped code actually consumes. Evaluator thresholds, scoring weights, and
blast-radius caps land alongside the modules that read them — not here as
unused constants.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pytz

# --- env helpers ---------------------------------------------------------


def _safe_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError, TypeError):
        return default


def _safe_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError, TypeError):
        return default


def _safe_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return default


def _safe_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _safe_time(key: str, default: time) -> time:
    """Parse an ``HH:MM`` env value, falling back to ``default``."""
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return default


# --- timezone ------------------------------------------------------------
# IST everywhere (CLAUDE.md). Never construct naive datetimes for session logic.
IST = pytz.timezone("Asia/Kolkata")

# --- runtime mode --------------------------------------------------------
# In dev mode the session gate reports OPEN regardless of clock/holiday so the
# scanner can be exercised off-hours. Never enable on the VPS.
INDIA_DEV_MODE: bool = _safe_bool("INDIA_DEV_MODE", False)

# --- regulatory / safety posture ----------------------------------------
# Phase 1 is signal-delivery only. Auto-execution stays hard-off until SEBI RA
# registration + NSE empanelment + owner sign-off (OWNER_BRIEF IB2/IB10).
AUTO_EXECUTION_ENABLED: bool = _safe_bool("AUTO_EXECUTION_ENABLED", False)
INDEX_FUTURES_ONLY: bool = _safe_bool("INDEX_FUTURES_ONLY", True)

# Scanning universe (Session 8e — universe expansion, owner-approved widening of
# IB1). Two groups:
#   * INDEX_BASES — index futures. The index-only evaluators (PCR_EXTREME,
#     EXPIRY_GAMMA_SQUEEZE, which use market-wide PCR / index max-pain) run only
#     for these; the scanner skips them for stocks.
#   * STOCK_BASES — a curated set of the most liquid intraday F&O stocks
#     (turnover/liquidity chosen). Names only: per-symbol lot size/tick resolve
#     from the broker and are display-only in Phase 1 (lot shows 0 until a
#     dynamic-resolution follow-up lands, before Phase-2 execution makes it
#     money-critical). Stock futures expire monthly on the last Tuesday, same as
#     index — ExpiryManager already handles the symbol/roll generically.
# All three are env-overridable so the universe is a config change, not a code one.
INDEX_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str("INDIA_INDEX_BASES", "NIFTY,BANKNIFTY,FINNIFTY,NIFTYNXT50").split(",")
    if b.strip()
)

_DEFAULT_STOCK_UNIVERSE = (
    "RELIANCE,HDFCBANK,ICICIBANK,SBIN,AXISBANK,KOTAKBANK,INFY,TCS,HCLTECH,WIPRO,"
    "TECHM,LT,TATAMOTORS,TATASTEEL,JSWSTEEL,HINDALCO,VEDL,SAIL,ITC,HINDUNILVR,"
    "NESTLEIND,BRITANNIA,BAJFINANCE,BAJAJFINSV,MARUTI,EICHERMOT,SUNPHARMA,DRREDDY,"
    "CIPLA,DIVISLAB,ADANIENT,ADANIPORTS,BHARTIARTL,ONGC,COALINDIA,POWERGRID,NTPC,"
    "ULTRACEMCO,GRASIM,TITAN,ASIANPAINT,DLF"
)
STOCK_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str("INDIA_STOCK_BASES", _DEFAULT_STOCK_UNIVERSE).split(",")
    if b.strip()
)

ALLOWED_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str(
        "ALLOWED_BASES", ",".join((*INDEX_BASES, *STOCK_BASES))
    ).split(",")
    if b.strip()
)

# Index bases that actually carry WEEKLY options. Since SEBI's Nov-2024
# one-weekly-per-exchange rationalisation, NSE's weekly is NIFTY only —
# BANKNIFTY / FINNIFTY / NIFTYNXT50 trade monthly options (last Tuesday).
# Before this existed, every index base was treated as weekly-Tuesday
# expiring: the IB16 +5 confidence bump fired on non-expiry Tuesdays and
# EXPIRY_GAMMA_SQUEEZE armed on days with no expiring options (no gamma
# pinning exists on such days). Env-overridable for the next SEBI revision.
WEEKLY_OPTION_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str("INDIA_WEEKLY_OPTION_BASES", "NIFTY").split(",")
    if b.strip()
)

# --- session clock (IST) -------------------------------------------------
PREOPEN_START: time = _safe_time("INDIA_PREOPEN_START", time(9, 0))
MARKET_OPEN: time = _safe_time("INDIA_MARKET_OPEN", time(9, 15))
# Last new-signal time. Was 15:20 — live 2026-07-10: signals emitted at
# 15:01/15:19 had 11-29 minutes to the 15:30 close and either expired
# worthless or forced a subscriber (1-3 min from FCM push to order) into a
# no-time trade; the 14:45-15:30 bucket ran 16.7% win. 15:00 leaves a scalp
# 30 minutes to resolve; the tp_feasibility_gate handles the remainder.
LAST_SIGNAL_TIME: time = _safe_time("INDIA_LAST_SIGNAL_TIME", time(15, 0))
FORCE_CLOSE_TIME: time = _safe_time("INDIA_FORCE_CLOSE_TIME", time(15, 25))
MARKET_CLOSE: time = _safe_time("INDIA_MARKET_CLOSE", time(15, 30))
# Expiry-day positions close 5 minutes earlier (Phase 2; OWNER_BRIEF IB16).
EXPIRY_FORCE_CLOSE_TIME: time = _safe_time("INDIA_EXPIRY_FORCE_CLOSE", time(15, 20))

# --- intraday session-phase boundaries (INDIA_MARKET_DOCTRINE §3) ---------
# The tape's character changes through the day: the power-hour drive
# (breakouts/momentum pay), the midday chop where breakouts fail, and the
# closing repositioning window. MarketContext labels each signal's phase for
# the edge matrix (and, later, phase-aware selectivity). Env-overridable.
POWER_HOUR_END: time = _safe_time("INDIA_POWER_HOUR_END", time(10, 30))
MIDDAY_END: time = _safe_time("INDIA_MIDDAY_END", time(13, 30))
# India VIX regime floor: below this is low-volatility complacency (small
# ranges, breakouts fail for lack of follow-through). ELEVATED/EXTREME reuse
# VIX_EXTREME_HIGH (20) and the VIX event threshold (25).
VIX_LOW_THRESHOLD: float = _safe_float("INDIA_VIX_LOW_THRESHOLD", 14.0)

# --- expiry --------------------------------------------------------------
# NSE expiry weekday (Mon=0 .. Sun=6). Since the SEBI-driven 1-Sep-2025 revision
# every NSE equity-derivative contract expires on a TUESDAY: weekly options each
# Tuesday, monthly futures/options on the *last* Tuesday of the contract month.
# ExpiryManager derives the monthly futures expiry (the traded instrument) from
# this weekday and the weekly-expiry flag (gamma-squeeze / IB16) separately.
EXPIRY_WEEKDAY: int = _safe_int("INDIA_EXPIRY_WEEKDAY", 1)
# Hour (IST) at/after which an expiry-day contract is treated as rolled to next.
EXPIRY_ROLL_HOUR: int = _safe_int("INDIA_EXPIRY_ROLL_HOUR", 9)


# --- instruments ---------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    """Static contract metadata for a tradable index base."""

    base: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: float
    expiry_type: str
    min_scalp_points: int
    # Round-number step treated as an extra S/R level (NIFTY 50, BANKNIFTY 100).
    round_step: float


# Lot sizes are NSE-mandated and revised periodically — verify at bootstrap
# (OWNER_BRIEF IB9). NSE rebaselined index-derivative lot sizes with the
# January 2026 series (circular FAOP70616): NIFTY 75 -> 65, BANKNIFTY 35 -> 30,
# to keep contract value aligned with elevated index levels. These are the
# live values as of the Jan-2026 monthly series; env-overridable so the next
# revision is a config change, not a code change. min_scalp_points per
# OWNER_BRIEF IB11 (15 NIFTY / 40 BANKNIFTY, covering round-trip STT + brokerage).
# expiry_type is "monthly": NSE index *futures* are monthly contracts (last
# Tuesday); weekly cadence is an options-only construct (see ExpiryManager).
INSTRUMENTS: dict[str, Instrument] = {
    "NIFTY": Instrument(
        base="NIFTY",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("NIFTY_LOT_SIZE", 65),
        tick_size=_safe_float("NIFTY_TICK_SIZE", 0.05),
        expiry_type="monthly",
        min_scalp_points=_safe_int("NIFTY_MIN_SCALP_POINTS", 15),
        round_step=_safe_float("NIFTY_ROUND_STEP", 50.0),
    ),
    "BANKNIFTY": Instrument(
        base="BANKNIFTY",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("BANKNIFTY_LOT_SIZE", 30),
        tick_size=_safe_float("BANKNIFTY_TICK_SIZE", 0.05),
        expiry_type="monthly",
        min_scalp_points=_safe_int("BANKNIFTY_MIN_SCALP_POINTS", 40),
        round_step=_safe_float("BANKNIFTY_ROUND_STEP", 100.0),
    ),
    # Additional index futures (Session 8e). Lot sizes are the current NSE
    # values (FinNifty 60, Nifty Next 50 25); env-overridable per the next NSE
    # revision. Stock instruments are intentionally not enumerated here — their
    # lot size/tick resolve from the broker (display-only, Phase 1) and unknown
    # bases fall back to a 0.05 tick, which is correct for all NSE equity.
    "FINNIFTY": Instrument(
        base="FINNIFTY",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("FINNIFTY_LOT_SIZE", 60),
        tick_size=_safe_float("FINNIFTY_TICK_SIZE", 0.05),
        expiry_type="monthly",
        min_scalp_points=_safe_int("FINNIFTY_MIN_SCALP_POINTS", 15),
        round_step=_safe_float("FINNIFTY_ROUND_STEP", 50.0),
    ),
    "NIFTYNXT50": Instrument(
        base="NIFTYNXT50",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("NIFTYNXT50_LOT_SIZE", 25),
        tick_size=_safe_float("NIFTYNXT50_TICK_SIZE", 0.05),
        expiry_type="monthly",
        min_scalp_points=_safe_int("NIFTYNXT50_MIN_SCALP_POINTS", 30),
        round_step=_safe_float("NIFTYNXT50_ROUND_STEP", 100.0),
    ),
}

# --- regime classification ----------------------------------------------
# ATR as a % of price below which the 60m/daily regime is judged QUIET.
REGIME_QUIET_ATR_PCT: float = _safe_float("REGIME_QUIET_ATR_PCT", 0.15)
# Minimum EMA21/EMA55 separation, as a multiple of ATR, before a trend label is
# awarded. An ordered-but-flat stack is chop: without this floor the regime
# component (the single largest score input) and the trend evaluators keyed off
# statistically meaningless EMA ordering.
REGIME_MIN_EMA_SEP_ATR: float = _safe_float("REGIME_MIN_EMA_SEP_ATR", 0.25)


# --- price-relative scaling for the stock universe ------------------------
# The index instruments carry absolute-point tunables (min scalp points, round
# step). Stocks span ₹100–₹25,000, so absolute points are meaningless there —
# these helpers give every consumer one price-aware answer for any base.

# --- round-trip cost model (STT-aware, IB11) -----------------------------
# All-in cost of a futures round trip as a % of notional. Used to (a) floor the
# minimum viable TP1 distance and (b) score reward *net* of cost. Dominated by
# STT, which NSE hiked on futures from 0.02% to 0.05% (sell side) effective
# 1-Apr-2026 (Budget 2026-27): the legacy 15/40-point floors were calibrated
# against the 0.02% era and became break-even overnight. Round-trip components:
#   STT           0.050%  (sell leg only — futures have no buy-side STT)
#   exchange txn  ~0.0019% x2 legs
#   stamp duty    0.002%  (buy leg only)
#   SEBI + GST    ~0.001%
#   brokerage     flat ~Rs40/lot-set — negligible vs notional, ignored here
# ≈ 0.06% of notional. One env knob so the next STT/charge revision is a config
# change, not a code change. (Sources: NSE Clearing STT schedule, Budget 2026-27.)
ROUNDTRIP_COST_PCT: float = _safe_float("INDIA_ROUNDTRIP_COST_PCT", 0.06)


def round_trip_cost_points(price: float) -> float:
    """All-in round-trip cost in points for a futures trade at *price*."""
    return max(0.0, price) * ROUNDTRIP_COST_PCT / 100.0


# TP1 must clear this multiple of the round-trip cost, so a winner keeps a real
# margin after costs instead of paying its whole target back in STT.
MIN_SCALP_COST_MULT: float = _safe_float("INDIA_MIN_SCALP_COST_MULT", 1.5)

# --- two-target trade plan (owner-directed, Session 18; revises IB12) ------
# At TP1 the subscriber books TP1_EXIT_FRACTION of the position and moves the
# stop on the remainder to break-even; the runner targets TP2. TP2 is the next
# structural level beyond TP1 (when one sits in a sane band) or a multiple of
# the TP1 distance. The feasibility gate applies to TP1 only — TP2 is a
# stretch target with the runner protected at BE.
TP2_ENABLED: bool = _safe_bool("INDIA_TP2_ENABLED", True)
# Fallback TP2 distance as a multiple of the TP1 distance (2.0 = twice as far).
TP2_DIST_MULT: float = _safe_float("INDIA_TP2_DIST_MULT", 2.0)
# A mapped structural level only becomes TP2 inside this band of TP1 distances
# (too close adds nothing over TP1; too far is a hope, not a target).
TP2_LEVEL_MIN_MULT: float = _safe_float("INDIA_TP2_LEVEL_MIN_MULT", 1.5)
TP2_LEVEL_MAX_MULT: float = _safe_float("INDIA_TP2_LEVEL_MAX_MULT", 3.0)
# Fraction of the position booked at TP1 (the rest runs to TP2 behind BE).
TP1_EXIT_FRACTION: float = _safe_float("INDIA_TP1_EXIT_FRACTION", 0.5)
# Break-even stop offset: True places BE one round-trip cost beyond entry so a
# "scratch" runner nets ~0 after STT instead of a hidden loss; False = entry.
BE_COST_BUFFER: bool = _safe_bool("INDIA_BE_COST_BUFFER", True)

# Minimum viable TP1 distance for stock bases, % of entry (IB11 equivalent).
MIN_SCALP_PCT: float = _safe_float("INDIA_MIN_SCALP_PCT", 0.10)


def min_scalp_points_for(base: str, price: float) -> float:
    """IB11 minimum viable TP1 distance in points for *base* at *price*.

    The floor is the larger of (a) the instrument's NSE-verified absolute floor
    (or, for stocks, the price-relative ``MIN_SCALP_PCT`` floor) and (b) a
    cost-relative floor (``round-trip cost x MIN_SCALP_COST_MULT``). The
    cost-relative term keeps the floor honest after the Apr-2026 STT hike: at
    NIFTY ~24,000 the round-trip cost alone is ~14 points, so the legacy
    15-point floor left ~0 net. The cost floor lifts TP1 to a genuinely
    profitable distance and auto-tracks any future STT/charge change.
    """
    inst = INSTRUMENTS.get(base)
    if inst is not None:
        absolute = float(inst.min_scalp_points)
    else:
        absolute = price * MIN_SCALP_PCT / 100.0
    cost_floor = round_trip_cost_points(price) * MIN_SCALP_COST_MULT
    return max(absolute, cost_floor)


# --- broker-resolved lot sizes -------------------------------------------
# Stock F&O bases carry no static lot size (INSTRUMENTS covers only the four
# indices), so their cards showed "lot 0". The Fyers symbol master publishes the
# NSE-mandated lot size for every F&O underlying; the feed resolves it once a day
# at seed and populates this registry. Broker value wins over the static
# INSTRUMENTS fallback; env `FYERS_SYMBOL_MASTER_URL` overrides the source.
FYERS_SYMBOL_MASTER_URL: str = _safe_str(
    "FYERS_SYMBOL_MASTER_URL", "https://public.fyers.in/sym_details/NSE_FO.csv"
)

_RESOLVED_LOT_SIZES: dict[str, int] = {}


def set_resolved_lot_sizes(mapping: dict[str, int]) -> None:
    """Merge broker-resolved lot sizes (base -> units/lot) into the registry.

    Zero/negative values are ignored so a partial or malformed master can never
    wipe a good static fallback.
    """
    for base, lot in mapping.items():
        if lot and lot > 0:
            _RESOLVED_LOT_SIZES[base.upper()] = int(lot)


def lot_size_for(base: str) -> int:
    """NSE F&O lot size (whole units/lot) for *base*.

    Broker symbol master (refreshed daily at seed) wins; falls back to the
    static INSTRUMENTS value for the four indices; 0 if still unknown (a stock
    base before the master has resolved).
    """
    b = base.upper()
    if b in _RESOLVED_LOT_SIZES:
        return _RESOLVED_LOT_SIZES[b]
    inst = INSTRUMENTS.get(b)
    return inst.lot_size if inst is not None else 0


def round_step_for(base: str, price: float) -> float:
    """Psychological round-number step for *base* at *price*.

    Indices use their instrument-configured step (NIFTY 50, BANKNIFTY 100).
    Stocks use a price-banded step matching how NSE equity actually clusters
    (₹1 under ₹150, ₹5 under ₹750, ₹10 under ₹1,500, ₹50 under ₹7,500,
    ₹100 above).
    """
    inst = INSTRUMENTS.get(base)
    if inst is not None:
        return inst.round_step
    if price < 150:
        return 1.0
    if price < 750:
        return 5.0
    if price < 1500:
        return 10.0
    if price < 7500:
        return 50.0
    return 100.0

# --- session warm-up (Session 15) -----------------------------------------
# No signal emission before this IST time. The first minutes after open are
# the worst tape of the day (auction noise, spreads, half-formed ranges) and
# the live data proved it: the 2026-07-09 open burst emitted 10 signals inside
# 09:15-09:16 — exhausting the whole daily budget — and every one hit SL.
WARMUP_END: time = _safe_time("INDIA_WARMUP_END", time(9, 30))

# A breakout candidate whose current price has already run more than this many
# ATRs beyond its stated entry level is a chase — the subscriber cannot get the
# printed entry, and the measured outcome would be fiction (reality-first).
MAX_CHASE_ATR: float = _safe_float("INDIA_MAX_CHASE_ATR", 0.5)

# --- data freshness (Session 16) -------------------------------------------
# Live 2026-07-10: the Fyers WebSocket died silently after the morning token
# hot-swap. The scanner kept scanning the frozen seed, emitted duplicate
# signals with identical hour-old entries, outcomes never resolved, and the
# app showed +0.00% running P&L all session. Three layers of defence:
#
# 1. MAX_TICK_AGE_SEC — a symbol whose newest live tick is older than this
#    (or that never received one) is suppressed by the scanner's
#    stale_data_gate and excluded from the /api/signals live-price overlay.
# 2. FEED_STALL_RESTART_SEC — if NO symbol has ticked for this long while the
#    session is OPEN/CLOSING, the watchdog force-restarts the data feed
#    (full reseed + fresh WebSocket), healing every known silent-death mode
#    of the broker SDKs (abandoned reconnects, auth failures, lost
#    subscriptions).
# 3. FEED_RESTART_COOLDOWN_SEC — minimum spacing between watchdog restarts so
#    a genuinely dead broker session cannot thrash the engine.
MAX_TICK_AGE_SEC: int = _safe_int("INDIA_MAX_TICK_AGE_SEC", 120)
FEED_STALL_RESTART_SEC: int = _safe_int("INDIA_FEED_STALL_RESTART_SEC", 180)
FEED_RESTART_COOLDOWN_SEC: int = _safe_int(
    "INDIA_FEED_RESTART_COOLDOWN_SEC", 300
)
# Consecutive watchdog restarts that fail to revive ticks before the engine
# exits the process entirely. `restart: always` then boots a clean process —
# the only guaranteed cure for a wedged broker-SDK thread (its socket object
# is a singleton; an in-process restart can inherit its corpse). 0 disables.
FEED_SUICIDE_AFTER_RESTARTS: int = _safe_int(
    "INDIA_FEED_SUICIDE_AFTER_RESTARTS", 3
)

# --- input staleness TTLs (Session 18) --------------------------------------
# Same doctrine as the tick-freshness layer, applied to the remaining live
# inputs: a value that has stopped updating must read as *unavailable*, not
# as its last observation. Consumers already fail safe on the zero value
# (VIX 0 blocks the event-risk trip and the low-VIX bonus; OI 0 skips the
# OI gates/scoring; stale PCR reads neutral).
VIX_TTL_SEC: int = _safe_int("INDIA_VIX_TTL_SEC", 600)
OI_TTL_SEC: int = _safe_int("INDIA_OI_TTL_SEC", 600)
PCR_TTL_SEC: int = _safe_int("INDIA_PCR_TTL_SEC", 1800)

# --- owner alerts (Session 18) ----------------------------------------------
# Engine-health FCM pushes (distinct Android channel from signals). Phase 1
# has a single user — the owner — so alerts go to every registered token;
# set INDIA_OWNER_UIDS (comma-separated Firebase UIDs) BEFORE subscriber
# onboarding so operational alerts stay owner-only.
OWNER_ALERT_UIDS: tuple[str, ...] = tuple(
    u.strip()
    for u in _safe_str("INDIA_OWNER_UIDS", "").split(",")
    if u.strip()
)
# Minimum spacing between alerts of the same kind — a flapping feed must not
# turn the owner's phone into a siren.
OWNER_ALERT_COOLDOWN_SEC: int = _safe_int("INDIA_OWNER_ALERT_COOLDOWN_SEC", 1800)

# --- database backup (Session 18) -------------------------------------------
# Nightly VACUUM INTO copy at session close. The SQLite file is the 30-day
# quality window — the Phase-2 sign-off evidence — and previously had no
# backup at all. Retention prunes to this many newest copies.
DB_BACKUP_KEEP: int = _safe_int("INDIA_DB_BACKUP_KEEP", 14)

# Stop distance below this many ATRs is inside one bar's noise — the trade is
# structurally a coin flip on the next wick regardless of the setup's logic.
# Live data 2026-07-08/09: the dense SL_HIT cluster sat at 0.08-0.20% stops
# (fractions of one 5m bar). Gate, not geometry: evaluators keep their own
# SL/TP shapes; candidates whose shape degenerates are suppressed with
# telemetry instead of silently emitted.
MIN_SL_ATR_MULT: float = _safe_float("INDIA_MIN_SL_ATR_MULT", 0.45)

# --- trigger quality (Session 17, from the first clean half-day) -----------
# A rejection/sweep trigger bar smaller than this many ATRs is lunch-doji
# noise, not a rejection — a 3-point NIFTY doji and a 40-point capitulation
# bar previously qualified identically for every pattern-triggered path.
MIN_TRIGGER_RANGE_ATR: float = _safe_float("INDIA_MIN_TRIGGER_RANGE_ATR", 0.5)
# Pattern-triggered paths (sweep/reclaim/rejection: LSR, SRF, DIV, OIS, PCR,
# VIX) only judge the forming 5m bar once it is at least this fraction
# elapsed — a "reclaim" or "pin bar" seen 40 seconds into a bar routinely
# un-forms by the close. Live 2026-07-10 (first clean window): LSR went 1/9
# with every loss a forming-bar reclaim that evaporated, and one SRF
# candidate re-fired every 30s scan for 5 minutes straight. 0.8 means the
# last ~minute of each 5m bar; a completed bar always qualifies.
PATTERN_BAR_MIN_ELAPSED: float = _safe_float("INDIA_PATTERN_BAR_MIN_ELAPSED", 0.8)

# --- confidence tiers ----------------------------------------------------
# Emit floor and A+ cutoff on the 0-100 confidence score (spec §11/§13.1).
# Below the floor a candidate is FILTERED (no FCM, no DB write).
# LOOSEN PASS (Session 8b): floor dropped 65 -> 55 to restore signal flow now
# that the 60m regime forms (so regime/HTF components score honestly).
# RECALIBRATION (Session 10): floor 55 -> 50. PR #44 fixed the cumulative-volume
# bug that inflated the volume component to 15/15 on every live signal — every
# score carried a systematic ~5-7pt of inflation. The 55 floor was set (PR #39)
# against those inflated scores, so post-#44 its *effective* selectivity rose to
# ~60-62 and starved genuine A/B setups (NIFTY emitting ~0). Dropping to 50
# restores the owner's intended PR#39 selectivity against honest scores. The
# daily/per-scan caps (10/3, ranked best-first) bound the flood risk regardless.
# This is the primary quality knob — raise it back toward 60-65 once the 30-day
# outcome data shows the B-tier win rate. A+ cutoff (80) is unchanged: A+ scarce.
# RAISED 50 -> 55 (Session 17): the first clean post-watchdog window
# (2026-07-10, 88 signals, 62 resolved) split cleanly at 55 — the 50-54 band
# ran 27.8% win / -0.99% cumulative while 55-59 ran 46.2% / +1.02%. The 50-54
# band was 27% of all emissions and all of it was subscriber-visible noise.
CONFIDENCE_EMIT_FLOOR: float = _safe_float("INDIA_CONFIDENCE_EMIT_FLOOR", 55.0)
CONFIDENCE_A_PLUS: float = _safe_float("INDIA_CONFIDENCE_A_PLUS", 80.0)
# The A tier (IB14: the ₹999 plan carries A and B signals; the app colour-codes
# A+/A/B). tier_for() emitted only A+/B before Session 15 — the A band the
# business rules and the app contract both reference simply did not exist.
CONFIDENCE_A: float = _safe_float("INDIA_CONFIDENCE_A", 65.0)

# SL-floor recalibration (LOOSEN PASS, Session 8b) -----------------------
# Every evaluator's MIN_SL_PCT was 0.15-0.30%, i.e. a 42-84 pt NIFTY stop at
# ~27,900. But IB11's STT-viable minimum is 15 NIFTY / 40 BANKNIFTY points =
# ~0.054% / ~0.065%. So the floors sat 3-5x above the actual compliance floor
# and rejected most setups at normal 5m ATR (15-30 pts) — the documented
# "SL-floor tension". Floors are dropped to ~0.06% (== IB11, ~17 NIFTY /
# ~40 BANKNIFTY pts), so a signal still clears STT+brokerage but the geometry
# is no longer over-constrained. Each stays independently env-overridable so a
# noisy evaluator can be re-tightened one at a time from the outcome data.
# (VIX-extreme keeps a wider floor: capitulation stops are naturally large.)

# --- evaluator geometry: LIQUIDITY_SWEEP_REVERSAL (spec §10.1) -----------
# Each evaluator owns its SL/TP geometry (CLAUDE.md). These are LSR's.
# SL ATR pads recalibrated 0.3 -> 0.5 (Session 15): a 0.3-ATR pad put the stop
# inside a single bar's expected range — live outcomes showed the SL_HIT cluster
# concentrated exactly in those sub-bar stops. 0.5 ATR beyond the structural
# level is still a tight scalp stop but survives ordinary bar noise. Applies to
# the evaluators whose stop hugs a level/bar (LSR/VSB/TPE/SRF/DIV/MAC); the
# structurally-wide stops (VIX capitulation, PCR/OIS level pads at 0.5, EGS at
# 1.0) are unchanged.
LSR_SWING_LOOKBACK: int = _safe_int("LSR_SWING_LOOKBACK", 30)
LSR_VOLUME_MULT: float = _safe_float("LSR_VOLUME_MULT", 1.2)
LSR_SL_ATR_MULT: float = _safe_float("LSR_SL_ATR_MULT", 0.5)
LSR_MIN_SL_PCT: float = _safe_float("LSR_MIN_SL_PCT", 0.06)
LSR_MAX_SL_PCT: float = _safe_float("LSR_MAX_SL_PCT", 1.0)
LSR_MIN_RR: float = _safe_float("LSR_MIN_RR", 1.5)
# The swept swing must BE a key level, not any 15m wiggle. LSR's thesis is
# resting liquidity beyond an obvious level; a sweep of a nobody-swing carries
# no such liquidity. Live 2026-07-10 (13:49-15:19, first post-#52 window): LSR
# went 0/6 for -0.79% and its inflated A-tier scores drove the tier inversion
# (A 26.7% win vs B 42.1%). The swept level must sit within
# LSR_KEY_LEVEL_ATR_TOL x ATR of PDH/PDL/PDC, the locked opening range, or
# session VWAP. Round numbers are deliberately excluded — a 0.25-ATR tolerance
# on NIFTY's 50-pt round grid would qualify a large share of arbitrary swings
# and gut the requirement.
LSR_REQUIRE_KEY_LEVEL: bool = _safe_bool("INDIA_LSR_REQUIRE_KEY_LEVEL", True)
LSR_KEY_LEVEL_ATR_TOL: float = _safe_float("INDIA_LSR_KEY_LEVEL_ATR_TOL", 0.25)

# --- evaluator geometry: OPENING_RANGE_BREAKOUT (spec §10.2) -------------
ORB_MIN_RANGE_PCT: float = _safe_float("ORB_MIN_RANGE_PCT", 0.10)
ORB_MAX_RANGE_PCT: float = _safe_float("ORB_MAX_RANGE_PCT", 1.50)
ORB_ATR_BUFFER_MULT: float = _safe_float("ORB_ATR_BUFFER_MULT", 0.1)
ORB_VOLUME_MULT: float = _safe_float("ORB_VOLUME_MULT", 1.3)
ORB_MIN_SL_PCT: float = _safe_float("ORB_MIN_SL_PCT", 0.06)
ORB_MAX_SL_PCT: float = _safe_float("ORB_MAX_SL_PCT", 1.20)
ORB_TP_RR: float = _safe_float("ORB_TP_RR", 2.0)
# Opening-range breakout is only meaningful while the 09:15-09:30 range is still
# the reference level. A "breakout" of it at midday is a stale-level trade — cap
# the latest entry (the 12:22 BHARTIARTL ORB that prompted this).
ORB_WINDOW_END: time = _safe_time("INDIA_ORB_WINDOW_END", time(11, 0))

# --- evaluator geometry: VOLUME_SURGE_BREAKOUT / BREAKDOWN_SHORT (§10.4/§10.5)
BDS_ENABLED: bool = _safe_bool("BDS_ENABLED", True)
VSB_SWING_LOOKBACK: int = _safe_int("VSB_SWING_LOOKBACK", 20)
VSB_VOLUME_MULT: float = _safe_float("VSB_VOLUME_MULT", 1.5)
VSB_OI_MIN_PCT: float = _safe_float("VSB_OI_MIN_PCT", 0.0)
VSB_ENTRY_ATR_MULT: float = _safe_float("VSB_ENTRY_ATR_MULT", 0.05)
VSB_SL_ATR_MULT: float = _safe_float("VSB_SL_ATR_MULT", 0.5)
VSB_MIN_SL_PCT: float = _safe_float("VSB_MIN_SL_PCT", 0.06)
VSB_MAX_SL_PCT: float = _safe_float("VSB_MAX_SL_PCT", 1.0)
VSB_TP_RR: float = _safe_float("VSB_TP_RR", 2.0)

# --- evaluator geometry: INDIA_VIX_EXTREME (spec §10.7) -----------------
# LONG contrarian only for now; the VIX-compression SHORT needs a VIX
# time-series in context and lands with that data source.
VIX_EXTREME_HIGH: float = _safe_float("INDIA_VIX_EXTREME_HIGH", 20.0)
VIX_EXTREME_MIN_DROP_PCT: float = _safe_float("VIX_EXTREME_MIN_DROP_PCT", 1.5)
VIX_EXTREME_RSI_MAX: float = _safe_float("VIX_EXTREME_RSI_MAX", 35.0)
VIX_SL_ATR_MULT: float = _safe_float("VIX_SL_ATR_MULT", 0.3)
VIX_MIN_SL_PCT: float = _safe_float("VIX_MIN_SL_PCT", 0.15)
VIX_MAX_SL_PCT: float = _safe_float("VIX_MAX_SL_PCT", 1.50)

# --- evaluator geometry: PCR_EXTREME (spec §10.8) -----------------------
PCR_NEAR_LEVEL_ATR_MULT: float = _safe_float("PCR_NEAR_LEVEL_ATR_MULT", 1.0)
PCR_SL_ATR_MULT: float = _safe_float("PCR_SL_ATR_MULT", 0.5)
PCR_MIN_SL_PCT: float = _safe_float("PCR_MIN_SL_PCT", 0.06)
PCR_MAX_SL_PCT: float = _safe_float("PCR_MAX_SL_PCT", 1.0)
PCR_MIN_RR: float = _safe_float("PCR_MIN_RR", 1.5)

# --- evaluator geometry: TREND_PULLBACK_EMA (spec §10.3) ----------------
TPE_PULLBACK_ATR_MULT: float = _safe_float("TPE_PULLBACK_ATR_MULT", 1.5)
TPE_RSI_MIN: float = _safe_float("TPE_RSI_MIN", 35.0)
TPE_RSI_MAX: float = _safe_float("TPE_RSI_MAX", 60.0)
TPE_SL_ATR_MULT: float = _safe_float("TPE_SL_ATR_MULT", 0.5)
TPE_MIN_SL_POINTS: float = _safe_float("TPE_MIN_SL_POINTS", 8.0)
TPE_MIN_SL_PCT: float = _safe_float("TPE_MIN_SL_PCT", 0.06)
TPE_MAX_SL_PCT: float = _safe_float("TPE_MAX_SL_PCT", 0.80)
TPE_TP_RR: float = _safe_float("TPE_TP_RR", 2.0)
TPE_MIN_RR: float = _safe_float("TPE_MIN_RR", 1.5)

# --- evaluator geometry: OI_SPIKE_REVERSAL (spec §10.13) ----------------
OIS_OI_SPIKE_PCT: float = _safe_float("OIS_OI_SPIKE_PCT", 3.0)
OIS_MIN_OI: float = _safe_float("OIS_MIN_OI", 5_000_000.0)
OIS_NEAR_LEVEL_ATR_MULT: float = _safe_float("OIS_NEAR_LEVEL_ATR_MULT", 1.0)
OIS_SL_ATR_MULT: float = _safe_float("OIS_SL_ATR_MULT", 0.5)
OIS_MIN_SL_PCT: float = _safe_float("OIS_MIN_SL_PCT", 0.06)
OIS_MAX_SL_PCT: float = _safe_float("OIS_MAX_SL_PCT", 1.0)
OIS_MIN_RR: float = _safe_float("OIS_MIN_RR", 1.5)

# --- evaluator geometry: SR_FLIP_RETEST (spec §10.6) --------------------
SRF_LONG_ENABLED: bool = _safe_bool("SR_FLIP_LONG_ENABLED", False)
SRF_SHORT_ENABLED: bool = _safe_bool("SR_FLIP_SHORT_ENABLED", True)
SRF_FLIP_ATR_MULT: float = _safe_float("SRF_FLIP_ATR_MULT", 0.5)
SRF_RETEST_ATR_MULT: float = _safe_float("SRF_RETEST_ATR_MULT", 0.3)
SRF_SL_ATR_MULT: float = _safe_float("SRF_SL_ATR_MULT", 0.5)
SRF_MIN_SL_PCT: float = _safe_float("SRF_MIN_SL_PCT", 0.06)
SRF_MAX_SL_PCT: float = _safe_float("SRF_MAX_SL_PCT", 1.50)
SRF_MIN_RR: float = _safe_float("SRF_MIN_RR", 1.5)
# A flip trade needs a mapped destination. Live 2026-07-10 (first clean
# window): all 26 SRF emissions carried rr == exactly 1.5 — the LevelBook
# target never once qualified, every signal shot the synthetic 1.5R fallback,
# and the setup netted negative at 30% of total volume. When True, a candidate
# whose book target does not clear SRF_MIN_RR is rejected instead of falling
# back — SRF only fires when the flip has somewhere structural to go.
SRF_REQUIRE_BOOK_TARGET: bool = _safe_bool("SRF_REQUIRE_BOOK_TARGET", True)

# --- evaluator geometry: FAILED_AUCTION_RECLAIM (spec §10.9) -----------
FAR_VOLUME_MULT: float = _safe_float("FAR_VOLUME_MULT", 1.2)
FAR_SL_LOOKBACK: int = _safe_int("FAR_SL_LOOKBACK", 3)
FAR_MIN_SL_PCT: float = _safe_float("FAR_MIN_SL_PCT", 0.06)
FAR_MAX_SL_PCT: float = _safe_float("FAR_MAX_SL_PCT", 1.0)
FAR_MIN_RR: float = _safe_float("FAR_MIN_RR", 1.5)

# --- evaluator geometry: DIVERGENCE_CONTINUATION (spec §10.10) ----------
# Tightened (Session 15): DIV was 48% of all live emissions at a 15.6% win rate.
# Any new price extreme with marginally weaker RSI qualified — a condition that
# stays true bar after bar in a steady drift and fired on 15+ correlated bases
# at once. A real exhaustion divergence needs (a) the first extreme printed at
# a genuinely stretched RSI, (b) a material RSI fade, (c) an actual rejection
# candle at the new extreme — not just a red/green close.
DIV_LOOKBACK: int = _safe_int("DIV_LOOKBACK", 10)
# RSI at the *prior* extreme: >= this for a bearish setup (first peak was
# overbought), <= (100 - this) mirrored for bullish.
DIV_RSI_EXTREME: float = _safe_float("DIV_RSI_EXTREME", 60.0)
# Minimum RSI fade between the prior extreme and now.
DIV_MIN_RSI_MARGIN: float = _safe_float("DIV_MIN_RSI_MARGIN", 5.0)
DIV_SL_ATR_MULT: float = _safe_float("DIV_SL_ATR_MULT", 0.5)
DIV_MIN_SL_PCT: float = _safe_float("DIV_MIN_SL_PCT", 0.06)
DIV_MAX_SL_PCT: float = _safe_float("DIV_MAX_SL_PCT", 1.20)
DIV_MIN_RR: float = _safe_float("DIV_MIN_RR", 1.5)

# --- evaluator geometry: QUIET_COMPRESSION_BREAK (spec §10.11) ---------
QCB_BB_SQUEEZE_THRESHOLD: float = _safe_float("QCB_BB_SQUEEZE_THRESHOLD", 0.002)
QCB_MIN_SQUEEZE_BARS: int = _safe_int("QCB_MIN_SQUEEZE_BARS", 6)
QCB_VOLUME_MULT: float = _safe_float("QCB_VOLUME_MULT", 1.5)
QCB_SL_ATR_MULT: float = _safe_float("QCB_SL_ATR_MULT", 0.1)
QCB_MIN_SL_PCT: float = _safe_float("QCB_MIN_SL_PCT", 0.06)
QCB_MAX_SL_PCT: float = _safe_float("QCB_MAX_SL_PCT", 0.60)
QCB_MIN_RR: float = _safe_float("QCB_MIN_RR", 2.0)

# --- evaluator geometry: MA_CROSS_TREND_SHIFT (spec §10.12) ------------
MAC_VOLUME_MULT: float = _safe_float("MAC_VOLUME_MULT", 1.2)
MAC_SL_ATR_MULT: float = _safe_float("MAC_SL_ATR_MULT", 0.5)
MAC_MIN_SL_PCT: float = _safe_float("MAC_MIN_SL_PCT", 0.06)
MAC_MAX_SL_PCT: float = _safe_float("MAC_MAX_SL_PCT", 1.0)
MAC_MIN_RR: float = _safe_float("MAC_MIN_RR", 1.5)

# --- evaluator geometry: EXPIRY_GAMMA_SQUEEZE (spec §10.14) ------------
EGS_ENABLED: bool = _safe_bool("EXPIRY_GAMMA_SQUEEZE_ENABLED", True)
EGS_MIN_DISTANCE_PCT: float = _safe_float("EGS_MIN_DISTANCE_PCT", 0.20)
EGS_MAX_DISTANCE_PCT: float = _safe_float("EGS_MAX_DISTANCE_PCT", 1.0)
EGS_SL_ATR_MULT: float = _safe_float("EGS_SL_ATR_MULT", 1.0)
EGS_MIN_SL_PCT: float = _safe_float("EGS_MIN_SL_PCT", 0.06)
EGS_MAX_SL_PCT: float = _safe_float("EGS_MAX_SL_PCT", 0.80)

# --- expiry-day emission posture (IB16) -----------------------------------
# On the weekly (index) / contract (stock) expiry day the confidence emit
# floor is raised by this many points — harder to emit into gamma noise.
EXPIRY_CONFIDENCE_BUMP: float = _safe_float("INDIA_EXPIRY_CONFIDENCE_BUMP", 5.0)

# --- data files ----------------------------------------------------------
_CONFIG_DIR = Path(__file__).resolve().parent
NSE_HOLIDAYS_FILE: str = _safe_str(
    "NSE_HOLIDAYS_FILE", str(_CONFIG_DIR / "nse_holidays.json")
)
# Macro binary-event dates (IB13): RBI MPC announcement days, Union Budget.
# The event-risk gate suppresses all signals on these dates.
MACRO_EVENTS_FILE: str = _safe_str(
    "MACRO_EVENTS_FILE", str(_CONFIG_DIR / "macro_events.json")
)
