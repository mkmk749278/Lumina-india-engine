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

# Index futures only at launch (OWNER_BRIEF IB1). Guarded at scanner/expiry entry.
ALLOWED_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str("ALLOWED_BASES", "NIFTY,BANKNIFTY").split(",")
    if b.strip()
)

# --- session clock (IST) -------------------------------------------------
PREOPEN_START: time = _safe_time("INDIA_PREOPEN_START", time(9, 0))
MARKET_OPEN: time = _safe_time("INDIA_MARKET_OPEN", time(9, 15))
LAST_SIGNAL_TIME: time = _safe_time("INDIA_LAST_SIGNAL_TIME", time(15, 20))
FORCE_CLOSE_TIME: time = _safe_time("INDIA_FORCE_CLOSE_TIME", time(15, 25))
MARKET_CLOSE: time = _safe_time("INDIA_MARKET_CLOSE", time(15, 30))
# Expiry-day positions close 5 minutes earlier (Phase 2; OWNER_BRIEF IB16).
EXPIRY_FORCE_CLOSE_TIME: time = _safe_time("INDIA_EXPIRY_FORCE_CLOSE", time(15, 20))

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
}

# --- regime classification ----------------------------------------------
# ATR as a % of price below which the 60m/daily regime is judged QUIET.
REGIME_QUIET_ATR_PCT: float = _safe_float("REGIME_QUIET_ATR_PCT", 0.15)

# --- confidence tiers ----------------------------------------------------
# Emit floor and A+ cutoff on the 0-100 confidence score (spec §11/§13.1).
# Below the floor a candidate is FILTERED (no FCM, no DB write).
# LOOSEN PASS (Session 8b): floor dropped 65 -> 55 to restore signal flow now
# that the 60m regime forms (so regime/HTF components score honestly). This is
# the primary quality knob — raise it back toward 65-70 once the 30-day outcome
# data shows the B-tier win rate. A+ cutoff (80) is unchanged: A+ stays scarce.
CONFIDENCE_EMIT_FLOOR: float = _safe_float("INDIA_CONFIDENCE_EMIT_FLOOR", 55.0)
CONFIDENCE_A_PLUS: float = _safe_float("INDIA_CONFIDENCE_A_PLUS", 80.0)

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
LSR_SWING_LOOKBACK: int = _safe_int("LSR_SWING_LOOKBACK", 30)
LSR_VOLUME_MULT: float = _safe_float("LSR_VOLUME_MULT", 1.2)
LSR_SL_ATR_MULT: float = _safe_float("LSR_SL_ATR_MULT", 0.3)
LSR_MIN_SL_PCT: float = _safe_float("LSR_MIN_SL_PCT", 0.06)
LSR_MAX_SL_PCT: float = _safe_float("LSR_MAX_SL_PCT", 1.0)
LSR_MIN_RR: float = _safe_float("LSR_MIN_RR", 1.5)

# --- evaluator geometry: OPENING_RANGE_BREAKOUT (spec §10.2) -------------
ORB_MIN_RANGE_PCT: float = _safe_float("ORB_MIN_RANGE_PCT", 0.10)
ORB_MAX_RANGE_PCT: float = _safe_float("ORB_MAX_RANGE_PCT", 1.50)
ORB_ATR_BUFFER_MULT: float = _safe_float("ORB_ATR_BUFFER_MULT", 0.1)
ORB_VOLUME_MULT: float = _safe_float("ORB_VOLUME_MULT", 1.3)
ORB_MIN_SL_PCT: float = _safe_float("ORB_MIN_SL_PCT", 0.06)
ORB_MAX_SL_PCT: float = _safe_float("ORB_MAX_SL_PCT", 1.20)
ORB_TP_RR: float = _safe_float("ORB_TP_RR", 2.0)

# --- evaluator geometry: VOLUME_SURGE_BREAKOUT / BREAKDOWN_SHORT (§10.4/§10.5)
BDS_ENABLED: bool = _safe_bool("BDS_ENABLED", True)
VSB_SWING_LOOKBACK: int = _safe_int("VSB_SWING_LOOKBACK", 20)
VSB_VOLUME_MULT: float = _safe_float("VSB_VOLUME_MULT", 1.5)
VSB_OI_MIN_PCT: float = _safe_float("VSB_OI_MIN_PCT", 0.0)
VSB_ENTRY_ATR_MULT: float = _safe_float("VSB_ENTRY_ATR_MULT", 0.05)
VSB_SL_ATR_MULT: float = _safe_float("VSB_SL_ATR_MULT", 0.3)
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
TPE_SL_ATR_MULT: float = _safe_float("TPE_SL_ATR_MULT", 0.3)
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
SRF_SL_ATR_MULT: float = _safe_float("SRF_SL_ATR_MULT", 0.3)
SRF_MIN_SL_PCT: float = _safe_float("SRF_MIN_SL_PCT", 0.06)
SRF_MAX_SL_PCT: float = _safe_float("SRF_MAX_SL_PCT", 1.50)
SRF_MIN_RR: float = _safe_float("SRF_MIN_RR", 1.5)

# --- evaluator geometry: FAILED_AUCTION_RECLAIM (spec §10.9) -----------
FAR_VOLUME_MULT: float = _safe_float("FAR_VOLUME_MULT", 1.2)
FAR_SL_LOOKBACK: int = _safe_int("FAR_SL_LOOKBACK", 3)
FAR_MIN_SL_PCT: float = _safe_float("FAR_MIN_SL_PCT", 0.06)
FAR_MAX_SL_PCT: float = _safe_float("FAR_MAX_SL_PCT", 1.0)
FAR_MIN_RR: float = _safe_float("FAR_MIN_RR", 1.5)

# --- evaluator geometry: DIVERGENCE_CONTINUATION (spec §10.10) ----------
DIV_LOOKBACK: int = _safe_int("DIV_LOOKBACK", 10)
DIV_SL_ATR_MULT: float = _safe_float("DIV_SL_ATR_MULT", 0.3)
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
MAC_SL_ATR_MULT: float = _safe_float("MAC_SL_ATR_MULT", 0.3)
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

# --- data files ----------------------------------------------------------
_CONFIG_DIR = Path(__file__).resolve().parent
NSE_HOLIDAYS_FILE: str = _safe_str(
    "NSE_HOLIDAYS_FILE", str(_CONFIG_DIR / "nse_holidays.json")
)
