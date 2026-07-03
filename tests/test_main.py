"""Engine entry-point — boot, scan loop, shutdown.

``serve_api`` is mocked in every test: booting a real uvicorn server on
port 8000 inside a test is nondeterministic (cancellation mid-startup
can deadlock — observed as a CI pytest hang) and can collide with any
other listener on the runner.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from src.main import _run


async def _idle_server(*args: object, **kwargs: object) -> None:
    """Stand-in for serve_api — parks forever, cancels instantly."""
    await asyncio.Event().wait()


async def test_engine_runs_and_shuts_down() -> None:
    """Start engine without Fyers creds, verify it runs then stops on cancel."""
    with (
        patch.dict(
            "os.environ", {"FYERS_CLIENT_ID": "", "FYERS_ACCESS_TOKEN": ""}
        ),
        patch("src.main.serve_api", _idle_server),
        patch("src.main.config.INDIA_DEV_MODE", True),
        patch("src.main.SCAN_INTERVAL_SEC", 0.01),
    ):
        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_engine_starts_feed_with_creds() -> None:
    """When Fyers creds are set, the feed should be started."""
    mock_feed_start = AsyncMock()
    mock_feed_stop = AsyncMock()

    with (
        patch.dict(
            "os.environ",
            {"FYERS_CLIENT_ID": "APP-100", "FYERS_ACCESS_TOKEN": "tok"},
        ),
        patch("src.main.FyersDataFeed.start", mock_feed_start),
        patch("src.main.FyersDataFeed.stop", mock_feed_stop),
        patch("src.main.serve_api", _idle_server),
        patch("src.main.config.INDIA_DEV_MODE", True),
        patch("src.main.SCAN_INTERVAL_SEC", 0.01),
    ):
        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_feed_start.assert_called_once()
        mock_feed_stop.assert_called_once()


async def test_engine_handles_feed_failure() -> None:
    """Feed start failure should not crash the engine."""
    mock_feed_start = AsyncMock(side_effect=ConnectionError("refused"))

    with (
        patch.dict(
            "os.environ",
            {"FYERS_CLIENT_ID": "APP-100", "FYERS_ACCESS_TOKEN": "tok"},
        ),
        patch("src.main.FyersDataFeed.start", mock_feed_start),
        patch("src.main.serve_api", _idle_server),
        patch("src.main.config.INDIA_DEV_MODE", True),
        patch("src.main.SCAN_INTERVAL_SEC", 0.01),
    ):
        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_feed_start.assert_called_once()
