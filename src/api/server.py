"""FastAPI application for the Lumin India engine.

Serves signal data to the lumin-india-app and the ops dashboard.
Runs inside the engine process (single-process mode) on a background
thread so the scan loop and HTTP server share one container.

Endpoints:
  GET /api/health           — liveness probe (no auth)
  GET /api/pulse            — engine state snapshot
  GET /api/signals          — paginated signal list with filters
  GET /api/signals/{id}     — single signal detail
  GET /api/suppressed       — recent gate suppressions
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

import config
from src import signal_store
from src.utils import get_logger

logger = get_logger("api")

_STATIC_TOKEN = os.environ.get("API_STATIC_TOKEN", "")

_boot_time: float = 0.0
_scan_count_ref: list[int] | None = None
_session_state_ref: list[str] | None = None


def _check_token(request: Request) -> None:
    if not _STATIC_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[len("Bearer "):]
    if token != _STATIC_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


def build_app() -> FastAPI:
    """Construct the FastAPI application."""
    app = FastAPI(
        title="Lumin India Engine API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "ts": datetime.now(config.IST).isoformat()}

    @app.get("/api/pulse", dependencies=[Depends(_check_token)])
    async def pulse() -> dict:
        uptime = time.time() - _boot_time if _boot_time else 0
        scan_count = _scan_count_ref[0] if _scan_count_ref else 0
        session_state = _session_state_ref[0] if _session_state_ref else "UNKNOWN"
        signals_today = await signal_store.get_signal_count_today()

        return {
            "status": "running",
            "uptime_seconds": int(uptime),
            "session_state": session_state,
            "scan_count": scan_count,
            "signals_today": signals_today,
            "dev_mode": config.INDIA_DEV_MODE,
            "auto_execution": config.AUTO_EXECUTION_ENABLED,
            "allowed_bases": list(config.ALLOWED_BASES),
            "ts": datetime.now(config.IST).isoformat(),
        }

    @app.get("/api/signals", dependencies=[Depends(_check_token)])
    async def signals(
        date: str | None = Query(None, description="Filter by date (YYYY-MM-DD)"),
        tier: str | None = Query(None, description="Filter by tier (A+, B)"),
        setup_class: str | None = Query(None, description="Filter by setup class"),
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict]:
        return await signal_store.get_signals(
            date=date, tier=tier, setup_class=setup_class, limit=limit
        )

    @app.get("/api/signals/{signal_id}", dependencies=[Depends(_check_token)])
    async def signal_detail(signal_id: str) -> dict:
        result = await signal_store.get_signal_by_id(signal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        return result

    @app.get("/api/suppressed", dependencies=[Depends(_check_token)])
    async def suppressed(
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict]:
        return await signal_store.get_suppressions(limit=limit)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.opt(exception=True).error("unhandled error on {}", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


def set_engine_refs(
    boot_time: float,
    scan_count_ref: list[int],
    session_state_ref: list[str],
) -> None:
    """Wire live engine state into the API for the /pulse endpoint."""
    global _boot_time, _scan_count_ref, _session_state_ref
    _boot_time = boot_time
    _scan_count_ref = scan_count_ref
    _session_state_ref = session_state_ref


async def serve_api(app: FastAPI, port: int = 8000) -> None:
    """Start uvicorn in-process (called from the engine's event loop)."""
    import uvicorn

    uvi_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        timeout_keep_alive=65,
        limit_concurrency=100,
    )
    server = uvicorn.Server(uvi_config)
    await server.serve()
