# Strategy-Gap Rebuild + Arming Runbook

Verification of the ops-session `ENGINE_STRATEGY_GAP_HANDOFF2` (2026-07-21)
against the actual engine code + the live 07-15→07-21 ledger, and the code
shipped in response. **The handoff was written without engine-code access**, so
most of its §5 rebuild spec was already built (Sessions 9/19/20/21). This
documents what was verified, what was net-new this session, and the
owner-gated arming sequence that flips the already-built corrective levers.

## Verification verdicts (G1–G9)

| # | Handoff claim | Verdict | Where |
|---|---|---|---|
| G1 | Stops not ATR-normalized | **Refuted** | every evaluator `sl = ATR × mult`; `sl_noise_gate` floors at 0.45×ATR |
| G2 | Fixed 2R targets | **Confirmed (TP1)** — fixed | 63% carried rr=2.0; ORB/VSB now structure-anchored (`_structural_tp1`) |
| G3 | No cost gate | **Built-but-dark** | `_score_rr` net-of-cost; `min_scalp_gate`; `allocator_suppress_gate` (arm) |
| G4 | Confidence non-monotonic | **Confirmed, fixed in shadow** | scoring v2 (`SCORING_V2_ACTIVE=false`) — validate before flip |
| G5 | No OI input | **Refuted; walls added** | `_score_oi` 4-quadrant; OI walls now first-class S/R (new) |
| G6 | No direction filter | **Refuted in letter** | `direction_bias_gate` (on) — but inert on NEUTRAL tapes; allocator covers it |
| G7 | No session-phase routing | **Built-but-dark** | `phase_affinity_gate` + v2 phase term; data inverts the doc's assumption |
| G8 | No event/earnings gate | **Partial; earnings added** | macro/expiry existed; single-stock earnings blackout now added (new) |
| G9 | No VIX-regime switch | **Partial, defer** | VIX scores; no high-VIX data yet (window is 100% LOW vix) |

## The measured reality (engine's own Edge Matrix / Allocator, 7d)

The bleed is a **LONG book in a choppy/down week**, and the allocator already
sees it — it is dark, not absent:

- `NEUTRAL/LONG` 66 trades, 21.2% win, **−0.144% EV** → allocator **SUPPRESS**
- `LONG_BIASED/LONG` 53, 11.3%, **−0.174%** → **SUPPRESS**
- `TREND_PULLBACK_EMA/LONG` 51, 7.8%, **−0.196%** → **SUPPRESS**
- `VOLUME_SURGE_BREAKOUT/LONG` 37, **−0.096%** → **SUPPRESS**
- positive-EV shorts (`NEUTRAL/SHORT`, `*/SHORT` FAR/TPE/SRF) → **EMIT**

`direction_bias_gate` cannot catch `NEUTRAL/LONG` (its trigger needs a
*decisive* tape). Arming the allocator does.

## Shipped this session (branch `claude/new-session-jo0eon`)

1. **G8 — earnings blackout gate** (`src/session/earnings_calendar.py`,
   `_earnings_blackout_gate`). Populate `config/earnings_events.json` (or point
   `INDIA_EARNINGS_EVENTS_FILE` at a live NSE results feed) — inert until then.
2. **G2 — structural TP1** (`_structural_tp1`) for ORB/VSB/BDS. Env
   `INDIA_STRUCTURAL_TP1_ENABLED=true` (default); false = exact prior 2R.
3. **G5 — OI walls as S/R** — call/put walls stored in `IndiaMarketData`,
   stamped on context, fed to confluence scoring + structural targets.
4. **v2 calibration tool** (`tools/v2_calibration.py`) — the pre-activation
   monotonicity check for scoring v2.

All reversible, env-flagged, tested. `INDIA_SCORING_V2_ACTIVE` and
`INDIA_ALLOCATOR_ARMED` were **not** changed — they stay dark pending the
replay + owner flip below.

## Owner arming sequence (run on the VPS — needs prod DB + Fyers token)

Do NOT flip live flags without replaying on the ledger first.

```bash
# 1. Replay the corrected ledger (baseline vs candidate) — historical truth
python -m tools.replay --db data/india_db.sqlite3 --candles ./candle_cache \
    --fetch --resolution 1 --entry-trigger on --out replay_report.csv

# 2. Confirm the allocator's SUPPRESS/EMIT verdicts track outcomes
curl -H "Authorization: Bearer $API_STATIC_TOKEN" \
    https://lumintrade.app/api/allocator?days=30

# 3. ARM THE ALLOCATOR (highest leverage; self-reversing, sample-gated).
#    Set on the VPS env + restart; verdicts re-derive at each session open.
#    INDIA_ALLOCATOR_ARMED=true

# 4. Scoring v2 — validate monotonicity BEFORE activating; recalibrate tiers.
python -m tools.v2_calibration --db data/india_db.sqlite3 --days 30
#    Only if it prints "v2 READY": set INDIA_SCORING_V2_ACTIVE=true AND re-set
#    CONFIDENCE_A_PLUS / CONFIDENCE_A to the v2 distribution the tool reports.

# 5. Re-set geometry floors on the corrected ledger's MFE/MAE — not before.
```

**Recommendation:** arm the allocator first (step 3) — it is the single
highest-leverage lever, already built, and directly removes the bleeding LONG
book including the `NEUTRAL/LONG` cohort the direction gate misses. Hold v2
activation (step 4) until the calibration tool reports READY on a forward
window; this week's v1 tiers are not badly inverted, so v2 is not urgent.
