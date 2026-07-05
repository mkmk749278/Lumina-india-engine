"""FastAPI application for the Lumin India engine.

Serves signal data to the lumin-india-app and the ops dashboard.
Runs inside the engine process (single-process mode) on a background
thread so the scan loop and HTTP server share one container.

Auth: dual-mode. Static Bearer token (ops dashboard, owner testing) or
Firebase ID token (app subscribers via Phone Auth). Both accepted on all
protected endpoints. Firebase ID tokens are verified server-side via
firebase-admin SDK.

Endpoints:
  GET /api/health           — liveness probe (no auth)
  GET /api/pulse            — engine state snapshot
  GET /api/signals          — paginated signal list with filters
  GET /api/signals/{id}     — single signal detail
  GET /api/suppressed       — recent gate suppressions
  POST /api/fcm-token       — register FCM device token
  GET /fyers/callback       — Fyers OAuth redirect target (daily login)
"""

from __future__ import annotations

import hashlib
import json as _json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from src import fcm_dispatcher, signal_store
from src.broker import token_store
from src.utils import get_logger

logger = get_logger("api")


class _FcmTokenBody(BaseModel):
    token: str
    uid: str = ""

_STATIC_TOKEN = os.environ.get("API_STATIC_TOKEN", "")
_firebase_auth_module: Any = None
_firebase_auth_ready = False
_FYERS_API_BASE = "https://api-t1.fyers.in/api/v3"

_boot_time: float = 0.0
_scan_count_ref: list[int] | None = None
_session_state_ref: list[str] | None = None
_token_refresh_cb: Callable[[str], Awaitable[None]] | None = None


def set_token_refresh_callback(cb: Callable[[str], Awaitable[None]]) -> None:
    """Engine registers its feed hot-swap here (called on new daily token)."""
    global _token_refresh_cb
    _token_refresh_cb = cb


async def _exchange_auth_code(auth_code: str) -> str:
    """Exchange a Fyers auth code for a verified access token.

    Raises ``ValueError`` with a safe, token-free message on any failure.
    """
    client_id = os.environ.get("FYERS_CLIENT_ID", "")
    secret = os.environ.get("FYERS_SECRET_KEY", "")
    if not client_id or not secret:
        raise ValueError("engine is missing FYERS_CLIENT_ID / FYERS_SECRET_KEY")

    app_id_hash = hashlib.sha256(f"{client_id}:{secret}".encode()).hexdigest()

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            f"{_FYERS_API_BASE}/validate-authcode",
            json={
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code,
            },
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        if data.get("s") != "ok" or not data.get("access_token"):
            raise ValueError(
                f"token exchange failed — {data.get('message', f'HTTP {resp.status_code}')}"
            )
        token: str = data["access_token"]

        profile = await http.get(
            f"{_FYERS_API_BASE}/profile",
            headers={"Authorization": f"{client_id}:{token}"},
        )
        pdata = profile.json()
        if pdata.get("s") != "ok":
            raise ValueError(
                f"token verification failed — {pdata.get('message', 'unknown')}"
            )
    return token


def _callback_page(ok: bool, detail: str) -> str:
    colour = "#4ADE80" if ok else "#F87171"
    icon = "✓" if ok else "✕"
    title = "Token refreshed" if ok else "Refresh failed"
    return f"""<!doctype html><html><head><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Lumin India</title></head>
<body style="background:#0A0E1A;color:#F8FAFC;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:95vh;margin:0">
<div style="text-align:center;padding:24px">
<div style="font-size:64px;color:{colour}">{icon}</div>
<h1 style="font-weight:400">{title}</h1>
<p style="color:#94A3B8;line-height:1.6">{detail}</p>
</div></body></html>"""


def _ensure_firebase_auth() -> bool:
    """Lazily load firebase_admin.auth for ID token verification."""
    global _firebase_auth_module, _firebase_auth_ready
    if _firebase_auth_ready:
        return _firebase_auth_module is not None
    _firebase_auth_ready = True

    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", ""):
        return False

    try:
        import firebase_admin
        try:
            firebase_admin.get_app()
        except ValueError:
            from firebase_admin import credentials
            cred = credentials.Certificate(
                _json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"])
            )
            firebase_admin.initialize_app(cred)
        from firebase_admin import auth as fb_auth
        _firebase_auth_module = fb_auth
        return True
    except Exception:
        logger.opt(exception=True).warning("Firebase auth verification unavailable")
        return False


def _verify_firebase_id_token(token: str) -> str | None:
    """Verify a Firebase ID token. Returns the UID on success, None on failure."""
    if not _ensure_firebase_auth():
        return None
    try:
        decoded = _firebase_auth_module.verify_id_token(token)
        return decoded.get("uid")
    except Exception:
        return None


def _check_token(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        if not _STATIC_TOKEN and not _ensure_firebase_auth():
            return
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[len("Bearer "):]

    if _STATIC_TOKEN and token == _STATIC_TOKEN:
        request.state.firebase_uid = None
        return

    uid = _verify_firebase_id_token(token)
    if uid is not None:
        request.state.firebase_uid = uid
        return

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
        allow_methods=["GET", "POST", "OPTIONS"],
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

    @app.get("/api/session-summary", dependencies=[Depends(_check_token)])
    async def session_summary(
        limit: int = Query(30, ge=1, le=90),
    ) -> list[dict]:
        """Daily session summaries — the 30-day quality-window ledger."""
        return await signal_store.get_session_summaries(limit=limit)

    @app.get("/api/outcomes", dependencies=[Depends(_check_token)])
    async def outcomes(
        date: str | None = Query(None, description="Filter by date (YYYY-MM-DD)"),
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict]:
        """Signal outcomes (TP1_HIT / SL_HIT / EXPIRED) joined onto signals."""
        return await signal_store.get_outcomes(date=date, limit=limit)

    @app.post("/api/fcm-token")
    async def register_fcm_token(
        request: Request,
        body: _FcmTokenBody,
        _: None = Depends(_check_token),
    ) -> dict:
        """Register or refresh an FCM device token for push notifications."""
        if not body.token or len(body.token) < 20:
            raise HTTPException(status_code=400, detail="Invalid FCM token")
        uid = getattr(request.state, "firebase_uid", None) or body.uid
        await fcm_dispatcher.register_token(body.token, uid)
        return {"status": "ok"}

    @app.get("/fyers/callback", response_class=HTMLResponse)
    async def fyers_callback(request: Request) -> HTMLResponse:
        """Fyers OAuth redirect target — the daily one-tap token refresh.

        Fyers appends ``auth_code`` after the owner's interactive login.
        The engine exchanges it server-side (it holds the app secret),
        verifies it, persists it, and hot-swaps the live data feed.
        No auth needed: a foreign/garbage code fails our appIdHash
        exchange, and nginx rate-limits the path.
        """
        auth_code = request.query_params.get(
            "auth_code"
        ) or request.query_params.get("code", "")
        if not auth_code or len(auth_code) < 20:
            return HTMLResponse(
                _callback_page(
                    False,
                    "No auth code in the URL. Start from the Fyers login "
                    "link and let it redirect here.",
                ),
                status_code=400,
            )

        try:
            token = await _exchange_auth_code(auth_code)
        except ValueError as e:
            logger.warning("fyers callback exchange failed: {}", e)
            return HTMLResponse(_callback_page(False, str(e)), status_code=400)

        token_store.save_token(token)

        if _token_refresh_cb is not None:
            try:
                await _token_refresh_cb(token)
            except Exception:
                logger.opt(exception=True).error("feed hot-swap failed")
                return HTMLResponse(
                    _callback_page(
                        False,
                        "Token is valid and saved, but the data feed "
                        "restart failed — check engine logs.",
                    ),
                    status_code=500,
                )

        logger.info("daily Fyers token refreshed via callback — feed live")
        return HTMLResponse(
            _callback_page(
                True,
                "Engine is connected to live NSE data. "
                "You can close this page.",
            )
        )

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
