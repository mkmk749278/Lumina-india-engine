"""Engine entry point — session-gated scan loop with Fyers data feed.

Boot sequence:
  1. Init in-memory data stores (tick, OI, market data)
  2. Init session/expiry managers, context builder, scanner
  3. Connect Fyers data feed (if credentials available)
  4. Run 30s scan loop (scanner's session gate handles market hours)
  5. Clean shutdown on SIGTERM/SIGINT

Phase 1: scanner + signal emission only. No order execution, no HTTP API.
"""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime
from pathlib import Path

import config
from src.broker.fyers_feed import FyersDataFeed
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.scanner import SCAN_INTERVAL_SEC, IndiaScanner
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.utils import get_logger

logger = get_logger("main")

_HEARTBEAT_PATH = Path("/tmp/india_engine_heartbeat")


async def _run() -> None:
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()
    session = SessionManager()

    ctx_builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    scanner = IndiaScanner(ctx_builder, session, expiry)
    feed = FyersDataFeed(tick, oi, mkt, expiry)

    client_id = os.environ.get("FYERS_CLIENT_ID", "")
    access_token = os.environ.get("FYERS_ACCESS_TOKEN", "")

    feed_active = False
    if client_id and access_token:
        try:
            await feed.start(client_id, access_token)
            feed_active = True
            logger.info("Fyers data feed connected")
        except Exception:
            logger.opt(exception=True).error(
                "Fyers data feed failed to start"
            )
    else:
        logger.warning(
            "FYERS_CLIENT_ID / FYERS_ACCESS_TOKEN not set"
            " — running without data feed"
        )

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info(
        "engine started — scan_interval={}s dev_mode={}",
        SCAN_INTERVAL_SEC,
        config.INDIA_DEV_MODE,
    )

    prev_state: SessionState | None = None

    try:
        while not shutdown.is_set():
            now = datetime.now(config.IST)
            state = session.current_state(now)

            if state != prev_state:
                logger.info("session state -> {}", state.value)
                if (
                    state == SessionState.OPEN
                    and prev_state in (SessionState.PRE_OPEN, None)
                ):
                    scanner.reset_day()
                prev_state = state

            if state == SessionState.OPEN or config.INDIA_DEV_MODE:
                symbols = feed.symbols if feed_active else {}
                signals = scanner.scan(symbols, now)
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
        if feed_active:
            await feed.stop()
        logger.info("engine stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
