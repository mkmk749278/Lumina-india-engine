"""/fyers/callback — the daily one-tap token refresh endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import src.api.server as server
from src.broker import token_store
from src.db import close_db


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    import src.db

    src.db._conn = None
    yield
    await close_db()
    server._token_refresh_cb = None


@pytest.fixture
async def client():
    app = server.build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_CODE = "x" * 40  # plausible-length auth code


async def test_success_persists_token_and_hot_swaps(client):
    swap = AsyncMock()
    server.set_token_refresh_callback(swap)

    with patch.object(
        server, "_exchange_auth_code", AsyncMock(return_value="tok-live")
    ):
        resp = await client.get(f"/fyers/callback?auth_code={_CODE}")

    assert resp.status_code == 200
    assert "Token refreshed" in resp.text
    assert token_store.load_token() == "tok-live"
    swap.assert_awaited_once_with("tok-live")
    assert "tok-live" not in resp.text


async def test_exchange_failure_shows_reason(client):
    with patch.object(
        server,
        "_exchange_auth_code",
        AsyncMock(side_effect=ValueError("token exchange failed — invalid auth code")),
    ):
        resp = await client.get(f"/fyers/callback?auth_code={_CODE}")

    assert resp.status_code == 400
    assert "invalid auth code" in resp.text
    assert token_store.load_token() is None


async def test_missing_code_is_400(client):
    resp = await client.get("/fyers/callback")
    assert resp.status_code == 400
    assert "No auth code" in resp.text


async def test_hot_swap_failure_reports_but_token_saved(client):
    server.set_token_refresh_callback(
        AsyncMock(side_effect=RuntimeError("boom"))
    )
    with patch.object(
        server, "_exchange_auth_code", AsyncMock(return_value="tok-live")
    ):
        resp = await client.get(f"/fyers/callback?auth_code={_CODE}")

    assert resp.status_code == 500
    assert "feed" in resp.text
    # Token persisted regardless — a container restart will pick it up.
    assert token_store.load_token() == "tok-live"
