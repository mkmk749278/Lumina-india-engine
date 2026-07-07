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
import time
from datetime import datetime
from pathlib import Path

import config
from src.api.server import (
    build_app,
    serve_api,
    set_engine_refs,
    set_token_refresh_callback,
)
from src.broker import token_store
from src.broker.angel_feed import AngelDataFeed
from src.broker.fyers_feed import FyersDataFeed
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.db import close_db
from src.scanner import SCAN_INTERVAL_SEC, IndiaScanner
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.signal_router import IndiaSignalRouter
from src.signal_store import (
    get_unresolved_signals_today,
    init_tables,
    insert_outcome,
    write_session_summary,
)
from src.trade_monitor import IndiaTradeMonitor
from src.utils import get_logger

logger = get_logger("main")

_HEARTBEAT_PATH = Path("/tmp/india_engine_heartbeat")
_API_PORT: int = int(os.environ.get("API_PORT", "8000"))


async def _run() -> None:
    await init_tables()

    from src.fcm_dispatcher import init_fcm_tables

    await init_fcm_tables()

    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()
    session = SessionManager()

    ctx_builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    scanner = IndiaScanner(ctx_builder, session, expiry)
    router = IndiaSignalRouter()
    monitor = IndiaTradeMonitor(tick)
    monitor.resume(
        await get_unresolved_signals_today(), datetime.now(config.IST)
    )

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
            tick, oi, mkt, expiry, on_prev_day=ctx_builder.set_prev_day
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
        return {
            "feed_connected": feed_active[0],
            "feed_symbols": list(symbols.values()),
            "data_age_seconds": int(min(ages)) if ages else None,
            "suppressed_today": len(scanner.gates.suppressions),
        }

    app = build_app()
    set_engine_refs(
        boot_time, scan_count_ref, session_state_ref, _engine_status
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
    # otherwise every scan would re-insert the same rows.
    persisted_suppressions = 0

    try:
        while not shutdown.is_set():
            now = datetime.now(config.IST)
            state = session.current_state(now)
            session_state_ref[0] = state.value

            if state != prev_state:
                logger.info("session state -> {}", state.value)
                if (
                    state == SessionState.OPEN
                    and prev_state in (SessionState.PRE_OPEN, None)
                ):
                    scanner.reset_day()
                    persisted_suppressions = 0
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
                    for oc in monitor.force_close_all(now):
                        await insert_outcome(
                            oc.signal_id,
                            oc.outcome,
                            oc.exit_price,
                            oc.points,
                            oc.resolved_at,
                        )
                    summary = await write_session_summary()
                    logger.info(
                        "session summary written: {} signals, "
                        "{} suppressed, {:+.1f} points",
                        summary["signal_count"],
                        summary["total_suppressed"],
                        summary["total_points"],
                    )
                prev_state = state

            if state == SessionState.OPEN or config.INDIA_DEV_MODE:
                symbols = feed.symbols if feed_active[0] else {}
                signals = scanner.scan(symbols, now)
                scan_count_ref[0] += 1

                all_suppressions = scanner.gates.suppressions
                new_suppressions = all_suppressions[persisted_suppressions:]
                if signals or new_suppressions:
                    await router.route(signals, new_suppressions)
                    persisted_suppressions = len(all_suppressions)

                monitor.register(signals, now)
                for oc in monitor.check(now):
                    await insert_outcome(
                        oc.signal_id,
                        oc.outcome,
                        oc.exit_price,
                        oc.points,
                        oc.resolved_at,
                    )

                for s in signals:
                    logger.info(
                        "EMIT {} {} {} conf={:.0f} tier={}",
                        s.setup_class,
                        s.direction,
                        s.symbol,
                        s.confidence,
                        s.tier,
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
