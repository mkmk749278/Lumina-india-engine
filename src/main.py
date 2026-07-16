"""Engine entry point — session-gated scan loop with Fyers data feed + API.

Boot sequence:
  1. Init SQLite tables (signal store)
  2. Init in-memory data stores (tick, OI, market data)
  3. Init session/expiry managers, context builder, scanner
  4. Connect Fyers data feed (if credentials available)
  5. Start HTTP API server (background task)
  6. Run 30s scan loop (scanner's session gate handles market hours)
  7. Clean shutdown on SIGTERM/SIGINT
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from src import owner_alerts, strategy_edge
from src.api.server import (
    build_app,
    serve_api,
    set_admin_state_reset,
    set_engine_refs,
    set_token_refresh_callback,
)
from src.broker import token_store
from src.broker.angel_feed import AngelDataFeed
from src.broker.fyers_feed import FyersDataFeed
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_macro_store import IndiaMacroStore
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.db import close_db
from src.db_backup import backup_database
from src.scanner import SCAN_INTERVAL_SEC, IndiaScanner
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.signal_router import IndiaSignalRouter
from src.signal_store import (
    get_signals_today_for_gates,
    get_unresolved_signals_today,
    init_tables,
    insert_outcome,
    mark_tp1_touched,
    mark_triggered,
    write_session_summary,
)
from src.trade_monitor import IndiaTradeMonitor, SignalOutcome
from src.utils import get_logger

logger = get_logger("main")


async def _persist_outcome(oc: SignalOutcome) -> None:
    """One write path for every resolved outcome (walk telemetry included)."""
    await insert_outcome(
        oc.signal_id,
        oc.outcome,
        oc.exit_price,
        oc.points,
        oc.pct,
        oc.resolved_at,
        mfe_pct=oc.mfe_pct,
        mae_pct=oc.mae_pct,
        bars_to_resolve=oc.bars_to_resolve,
        resolution_tf=oc.resolution_tf,
        ambiguous_tie=oc.ambiguous_tie,
    )

_HEARTBEAT_PATH = Path("/tmp/india_engine_heartbeat")
_API_PORT: int = int(os.environ.get("API_PORT", "8000"))


async def _run() -> None:
    await init_tables()

    from src.fcm_dispatcher import init_fcm_tables

    await init_fcm_tables()

    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    macro = IndiaMacroStore()
    expiry = ExpiryManager()
    session = SessionManager()

    ctx_builder = IndiaContextBuilder(tick, oi, mkt, expiry, macro_store=macro)
    scanner = IndiaScanner(ctx_builder, session, expiry)
    router = IndiaSignalRouter()
    monitor = IndiaTradeMonitor(tick)
    boot_now = datetime.now(config.IST)
    monitor.resume(await get_unresolved_signals_today(), boot_now)
    # Restore today's emission state so a mid-session restart (deploys
    # included) cannot re-open the daily budget or wipe cooldowns — the
    # 2026-07-09 restart bursts quadrupled the daily cap.
    scanner.rehydrate(await get_signals_today_for_gates(), boot_now)

    feed_kind = os.environ.get("DATA_FEED", "fyers").strip().lower()
    feed_active = [False]

    feed: AngelDataFeed | FyersDataFeed
    if feed_kind == "angel":
        angel_feed = AngelDataFeed(
            tick, oi, mkt, expiry, on_prev_day=ctx_builder.set_prev_day
        )
        feed = angel_feed
        if AngelDataFeed.has_credentials():
            try:
                await angel_feed.start()
                feed_active[0] = True
                logger.info("Angel One data feed connected (zero-touch auth)")
            except Exception:
                logger.opt(exception=True).error(
                    "Angel One data feed failed to start"
                )
        else:
            logger.warning(
                "DATA_FEED=angel but ANGEL_API_KEY / ANGEL_CLIENT_CODE / "
                "ANGEL_PIN / ANGEL_TOTP_SECRET not all set — no data feed"
            )
    else:
        fyers_feed = FyersDataFeed(
            tick,
            oi,
            mkt,
            expiry,
            on_prev_day=ctx_builder.set_prev_day,
            on_daily_regime=ctx_builder.set_daily_regime,
        )
        feed = fyers_feed
        client_id = os.environ.get("FYERS_CLIENT_ID", "")
        # Prefer a token delivered via /fyers/callback while a previous
        # container was running — it is fresher than the env snapshot
        # taken at container create.
        access_token = token_store.load_token() or os.environ.get(
            "FYERS_ACCESS_TOKEN", ""
        )

        if client_id and access_token:
            try:
                await fyers_feed.start(client_id, access_token)
                feed_active[0] = True
                logger.info("Fyers data feed connected")
            except Exception:
                logger.opt(exception=True).error(
                    "Fyers data feed failed to start"
                )
        else:
            logger.warning(
                "FYERS_CLIENT_ID / FYERS_ACCESS_TOKEN not set"
                " — running without data feed (use /fyers/callback to connect)"
            )

        async def _on_token_refresh(token: str) -> None:
            """Hot-swap the Fyers feed onto a new daily token (no restart)."""
            if not client_id:
                raise RuntimeError(
                    "FYERS_CLIENT_ID not configured on the engine"
                )
            if feed_active[0]:
                feed_active[0] = False
                await fyers_feed.stop()
            await fyers_feed.start(client_id, token)
            feed_active[0] = True
            logger.info("data feed hot-swapped onto refreshed token")

        set_token_refresh_callback(_on_token_refresh)

    boot_time = time.time()
    scan_count_ref = [0]
    session_state_ref = ["UNKNOWN"]

    def _engine_status() -> dict:
        """Feed/data diagnostics for /api/pulse (see set_engine_refs)."""
        symbols = feed.symbols if feed_active[0] else {}
        now_ist = datetime.now(config.IST)
        ages: list[float] = []
        for sym in symbols.values():
            candles = tick.get_candles_5m(sym)
            if candles:
                ages.append((now_ist - candles[-1].ts).total_seconds())
        tick_age = feed.seconds_since_last_tick()
        return {
            "feed_connected": feed_active[0],
            "feed_symbols": list(symbols.values()),
            "data_age_seconds": int(min(ages)) if ages else None,
            "last_tick_age_seconds": int(tick_age) if tick_age is not None else None,
            "suppressed_today": len(scanner.gates.suppressions),
        }

    def _live_prices() -> dict[str, float]:
        """Latest *fresh* price per active symbol, for the /api/signals live
        overlay. A symbol whose newest live tick is stale (or that never
        ticked) is omitted — overlaying a frozen price renders a lying
        +0.00% on every open signal card (live 2026-07-10)."""
        symbols = feed.symbols if feed_active[0] else {}
        now_ist = datetime.now(config.IST)
        prices: dict[str, float] = {}
        for sym in symbols.values():
            last_ts = tick.get_last_tick_ts(sym)
            if (
                last_ts is None
                or (now_ist - last_ts).total_seconds() > config.MAX_TICK_AGE_SEC
            ):
                continue
            price = tick.get_last_price(sym)
            if price > 0:
                prices[sym] = price
        return prices

    app = build_app()
    set_engine_refs(
        boot_time,
        scan_count_ref,
        session_state_ref,
        _engine_status,
        _live_prices,
    )
    api_task = asyncio.create_task(serve_api(app, _API_PORT))

    logger.info(
        "engine started — scan_interval={}s dev_mode={} api_port={}",
        SCAN_INTERVAL_SEC,
        config.INDIA_DEV_MODE,
        _API_PORT,
    )

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    prev_state: SessionState | None = None
    # How many of the gate chain's (day-cumulative) suppressions have
    # already been persisted — only the tail beyond this goes to SQLite,
    # otherwise every scan would re-insert the same rows. One-element list
    # so the admin state-reset callback (below) can zero it too.
    persisted_ref = [0]

    # Feed watchdog state. market_live_since: when the session last became
    # OPEN — the stall clock cannot start before there is a market to tick.
    # last_feed_restart: cooldown anchor so a genuinely dead broker session
    # cannot thrash the engine with restarts. consecutive_stall_restarts:
    # restarts in a row that failed to revive ticks — past the configured
    # limit the process exits and `restart: always` boots it clean (the only
    # guaranteed cure for a wedged broker-SDK singleton).
    market_live_since: float | None = None
    last_feed_restart = float("-inf")
    consecutive_stall_restarts = 0

    def _admin_state_reset() -> dict:
        """Ops Control panel hook: align in-memory state with a wiped DB —
        drop tracked signals and reset the gate chain's day state."""
        dropped = monitor.clear()
        scanner.reset_day()
        persisted_ref[0] = 0
        return {"tracked_signals_dropped": dropped, "gates_reset": True}

    set_admin_state_reset(_admin_state_reset)

    try:
        while not shutdown.is_set():
            now = datetime.now(config.IST)
            state = session.current_state(now)
            session_state_ref[0] = state.value

            if state != prev_state:
                logger.info("session state -> {}", state.value)
                if state == SessionState.OPEN:
                    # Watchdog stall clock starts here — covers both the
                    # daily PRE_OPEN->OPEN transition and a boot straight
                    # into an open session.
                    market_live_since = time.monotonic()
                    # Prev-day FII/DII, once per session open (IB18 — off the
                    # tick/scan path). Self-defensive: URL unset/unreachable
                    # leaves the macro vote NEUTRAL, never fabricated.
                    try:
                        await macro.refresh()
                    except Exception:
                        logger.opt(exception=True).warning("macro refresh failed")
                    # Load the measured-edge index once per open (not per scan);
                    # empty until cohorts pass the sample floor → inert scoring.
                    try:
                        scanner.set_edge_index(await strategy_edge.get_edge_index())
                    except Exception:
                        logger.opt(exception=True).warning("edge index load failed")
                    if not feed_active[0]:
                        # The most likely daily failure: the Fyers token was
                        # never tapped this morning. Page the owner instead
                        # of scanning a dead buffer in silence.
                        await owner_alerts.alert(
                            "no_feed_at_open",
                            "Lumin India: no data feed",
                            "Market is OPEN but the engine has no data feed"
                            " — tap the Fyers login link to connect.",
                        )
                # Reset only on the genuine daily-open transition. A boot
                # straight into OPEN (mid-session restart) must NOT reset —
                # the gate chain was just rehydrated from today's persisted
                # emissions, and wiping it re-opens the daily budget.
                if (
                    state == SessionState.OPEN
                    and prev_state == SessionState.PRE_OPEN
                ):
                    scanner.reset_day()
                    persisted_ref[0] = 0
                    # On a genuine daily open (not the first boot — start()
                    # already seeded then), re-derive prev-day levels and
                    # re-seed the higher-timeframe buffers so a long-running
                    # container never serves stale levels or a frozen regime.
                    if prev_state == SessionState.PRE_OPEN and feed_active[0]:
                        try:
                            await feed.refresh_daily(now)
                        except Exception:
                            logger.opt(exception=True).warning(
                                "daily feed refresh failed"
                            )
                if state == SessionState.CLOSED and prev_state is not None:
                    # Resolve any TP/SL touch that landed during CLOSING
                    # before scoring the remainder as EXPIRED.
                    for oc in monitor.check(now):
                        await _persist_outcome(oc)
                    for sid, triggered_at in monitor.drain_trigger_marks():
                        await mark_triggered(sid, triggered_at)
                    for sid, touched_at in monitor.drain_tp1_marks():
                        await mark_tp1_touched(sid, touched_at)
                    for oc in monitor.force_close_all(now):
                        await _persist_outcome(oc)
                    summary = await write_session_summary()
                    logger.info(
                        "session summary written: {} signals, "
                        "{} suppressed, {:+.1f} points",
                        summary["signal_count"],
                        summary["total_suppressed"],
                        summary["total_points"],
                    )
                    # Nightly backup — once per day, right after the close
                    # bookkeeping. The DB is the 30-day quality evidence.
                    await backup_database()
                prev_state = state

            if state == SessionState.OPEN or config.INDIA_DEV_MODE:
                symbols = feed.symbols if feed_active[0] else {}
                signals = scanner.scan(symbols, now)
                scan_count_ref[0] += 1

                all_suppressions = scanner.gates.suppressions
                new_suppressions = all_suppressions[persisted_ref[0]:]
                if signals or new_suppressions:
                    await router.route(signals, new_suppressions)
                    persisted_ref[0] = len(all_suppressions)

                monitor.register(signals, now)

                for s in signals:
                    logger.info(
                        "EMIT {} {} {} conf={:.0f} tier={}",
                        s.setup_class,
                        s.direction,
                        s.symbol,
                        s.confidence,
                        s.tier,
                    )

            # Outcome tracking runs through CLOSING too — a TP/SL touch
            # between 15:20 and 15:30 is a real outcome, not an expiry.
            if (
                state in (SessionState.OPEN, SessionState.CLOSING)
                or config.INDIA_DEV_MODE
            ):
                for oc in monitor.check(now):
                    await _persist_outcome(oc)
                # Persist entry triggers + runner armings so a fill and a
                # banked TP1 both survive an engine restart.
                for sid, triggered_at in monitor.drain_trigger_marks():
                    await mark_triggered(sid, triggered_at)
                for sid, touched_at in monitor.drain_tp1_marks():
                    await mark_tp1_touched(sid, touched_at)

            # Feed watchdog — the 2026-07-10 incident: the WebSocket died
            # silently, the scanner ran all session on the frozen morning
            # seed, and nothing noticed. A market that is OPEN/CLOSING with
            # zero ticks across the whole universe for FEED_STALL_RESTART_SEC
            # is a dead feed, whatever the SDK claims — force a full restart
            # (fresh WebSocket + reseed heals the data hole), with a cooldown
            # so a dead broker session cannot thrash the engine.
            if (
                state in (SessionState.OPEN, SessionState.CLOSING)
                and feed_active[0]
                and market_live_since is not None
            ):
                mono_now = time.monotonic()
                tick_age = feed.seconds_since_last_tick()
                stall = (
                    min(tick_age, mono_now - market_live_since)
                    if tick_age is not None
                    else mono_now - market_live_since
                )
                if (
                    stall > config.FEED_STALL_RESTART_SEC
                    and mono_now - last_feed_restart
                    >= config.FEED_RESTART_COOLDOWN_SEC
                ):
                    # A tick arriving after the previous restart means this
                    # is a fresh incident; none means that restart failed to
                    # revive the feed — count it toward process suicide.
                    revived_since_last = (
                        tick_age is not None
                        and tick_age < mono_now - last_feed_restart
                    )
                    consecutive_stall_restarts = (
                        1 if revived_since_last else consecutive_stall_restarts + 1
                    )
                    last_feed_restart = mono_now
                    logger.error(
                        "FEED STALLED — no tick for {:.0f}s with session {}:"
                        " restarting data feed (attempt {} in a row)",
                        stall,
                        state.value,
                        consecutive_stall_restarts,
                    )
                    await owner_alerts.alert(
                        "feed_stall",
                        "Lumin India: data feed stalled",
                        f"No ticks for {stall:.0f}s during market hours —"
                        f" auto-restarting the feed"
                        f" (attempt {consecutive_stall_restarts}).",
                    )
                    if (
                        config.FEED_SUICIDE_AFTER_RESTARTS > 0
                        and consecutive_stall_restarts
                        > config.FEED_SUICIDE_AFTER_RESTARTS
                    ):
                        # In-process restarts aren't reviving ticks — the
                        # broker SDK is likely wedged beyond repair (its
                        # socket object is a process-wide singleton). Exit;
                        # `restart: always` boots a clean process, which
                        # reseeds and reconnects on the freshest token.
                        logger.critical(
                            "{} watchdog restarts failed to revive the feed"
                            " — exiting for a clean process restart",
                            consecutive_stall_restarts - 1,
                        )
                        await owner_alerts.alert(
                            "engine_restart",
                            "Lumin India: engine self-restarting",
                            "Feed restarts are not reviving ticks — the"
                            " engine is restarting itself. If this repeats,"
                            " re-tap the Fyers login link.",
                        )
                        sys.exit(1)
                    feed_active[0] = False
                    try:
                        await feed.restart()
                        feed_active[0] = True
                        logger.warning("data feed restarted by watchdog")
                    except Exception:
                        logger.opt(exception=True).error(
                            "watchdog feed restart FAILED — feed marked down"
                            " (owner: re-tap the Fyers login link)"
                        )
                        await owner_alerts.alert(
                            "feed_down",
                            "Lumin India: data feed DOWN",
                            "The watchdog could not restart the data feed —"
                            " re-tap the Fyers login link.",
                        )

            _HEARTBEAT_PATH.write_text(str(now.timestamp()))

            try:
                await asyncio.wait_for(
                    shutdown.wait(), timeout=SCAN_INTERVAL_SEC
                )
                break
            except TimeoutError:
                pass
    finally:
        api_task.cancel()
        try:
            await api_task
        except asyncio.CancelledError:
            pass
        if feed_active[0]:
            await feed.stop()
        await close_db()
        logger.info("engine stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
