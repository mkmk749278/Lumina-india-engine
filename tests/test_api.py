"""API server endpoint tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.server import build_app, set_engine_refs
from src.db import close_db
from src.fcm_dispatcher import init_fcm_tables
from src.signal_store import init_tables, insert_signal
from src.signals.model import IndiaSignal


@pytest.fixture(autouse=True)
async def _setup_db(tmp_path, monkeypatch):
    """Use a temporary SQLite database for each test."""
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    import src.db

    src.db._conn = None
    src.db._DB_DIR = tmp_path
    src.db._DB_PATH = tmp_path / "india_db.sqlite3"
    await init_tables()
    await init_fcm_tables()
    yield
    await close_db()


@pytest.fixture
def app():
    set_engine_refs(
        boot_time=1000000.0,
        scan_count_ref=[42],
        session_state_ref=["CLOSED"],
    )
    return build_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_signal(**overrides) -> IndiaSignal:
    defaults = dict(
        signal_id="sig-001",
        symbol="NSE:NIFTY26JULFUT",
        base="NIFTY",
        direction="LONG",
        setup_class="OPENING_RANGE_BREAKOUT",
        entry=24500.0,
        sl=24400.0,
        tp1=24700.0,
        sl_pct=0.41,
        tp1_pct=0.82,
        rr_ratio=2.0,
        lot_size=75,
        confidence=78.0,
        tier="B",
        dispatch_timestamp=1719900000.0,
    )
    defaults.update(overrides)
    return IndiaSignal(**defaults)


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "ts" in data


async def test_pulse_with_auth(client):
    resp = await client.get("/api/pulse")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["scan_count"] == 42
    assert data["session_state"] == "CLOSED"
    # Feed diagnostics present even without a provider wired (null defaults).
    assert data["feed_connected"] is None
    assert data["feed_symbols"] == []


async def test_pulse_exposes_feed_diagnostics():
    set_engine_refs(
        boot_time=1000.0,
        scan_count_ref=[7],
        session_state_ref=["OPEN"],
        status_provider=lambda: {
            "feed_connected": True,
            "feed_symbols": ["NSE:NIFTY26JULFUT", "NSE:BANKNIFTY26JULFUT"],
            "data_age_seconds": 12,
            "suppressed_today": 3,
        },
    )
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        data = (await c.get("/api/pulse")).json()

    assert data["feed_connected"] is True
    assert data["feed_symbols"] == ["NSE:NIFTY26JULFUT", "NSE:BANKNIFTY26JULFUT"]
    assert data["data_age_seconds"] == 12
    assert data["suppressed_today"] == 3
    # Reset the module global so later tests see no provider.
    set_engine_refs(1000.0, [0], ["CLOSED"])


async def test_pulse_no_auth(app):
    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = "secret123"
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_ready = True
    src.api.server._firebase_auth_module = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/pulse")
            assert resp.status_code == 401
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_ready = orig_ready


async def test_static_token_accepted(app):
    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = "owner-token-xyz"
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_ready = True
    src.api.server._firebase_auth_module = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/pulse",
                headers={"Authorization": "Bearer owner-token-xyz"},
            )
            assert resp.status_code == 200
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_ready = orig_ready


async def test_invalid_token_rejected(app):
    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = "correct-token"
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_ready = True
    src.api.server._firebase_auth_module = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/pulse",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 403
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_ready = orig_ready


async def test_firebase_token_accepted(app):
    """Simulate a valid Firebase ID token via mocked verify_id_token."""
    from unittest.mock import MagicMock

    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = ""
    mock_auth = MagicMock()
    mock_auth.verify_id_token.return_value = {"uid": "firebase-user-123"}
    orig_module = src.api.server._firebase_auth_module
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_module = mock_auth
    src.api.server._firebase_auth_ready = True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/pulse",
                headers={"Authorization": "Bearer fake-firebase-id-token"},
            )
            assert resp.status_code == 200
            mock_auth.verify_id_token.assert_called_once_with(
                "fake-firebase-id-token"
            )
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_module = orig_module
        src.api.server._firebase_auth_ready = orig_ready


async def test_firebase_token_rejected(app):
    """Invalid Firebase token falls through to 403."""
    from unittest.mock import MagicMock

    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = ""
    mock_auth = MagicMock()
    mock_auth.verify_id_token.side_effect = Exception("token expired")
    orig_module = src.api.server._firebase_auth_module
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_module = mock_auth
    src.api.server._firebase_auth_ready = True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/pulse",
                headers={"Authorization": "Bearer expired-token"},
            )
            assert resp.status_code == 403
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_module = orig_module
        src.api.server._firebase_auth_ready = orig_ready


async def test_fcm_token_uses_firebase_uid(app):
    """FCM token registration should use authenticated UID from Firebase."""
    from unittest.mock import MagicMock

    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = ""
    mock_auth = MagicMock()
    mock_auth.verify_id_token.return_value = {"uid": "fb-user-456"}
    orig_module = src.api.server._firebase_auth_module
    orig_ready = src.api.server._firebase_auth_ready
    src.api.server._firebase_auth_module = mock_auth
    src.api.server._firebase_auth_ready = True
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/fcm-token",
                json={"token": "a" * 152},
                headers={"Authorization": "Bearer valid-fb-token"},
            )
            assert resp.status_code == 200
    finally:
        src.api.server._STATIC_TOKEN = original
        src.api.server._firebase_auth_module = orig_module
        src.api.server._firebase_auth_ready = orig_ready


async def test_signals_empty(client):
    resp = await client.get("/api/signals")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_signals_with_data(client):
    await insert_signal(_make_signal())
    resp = await client.get("/api/signals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["signal_id"] == "sig-001"
    assert data[0]["base"] == "NIFTY"


async def test_signal_detail(client):
    await insert_signal(_make_signal())
    resp = await client.get("/api/signals/sig-001")
    assert resp.status_code == 200
    assert resp.json()["setup_class"] == "OPENING_RANGE_BREAKOUT"


async def test_signal_not_found(client):
    resp = await client.get("/api/signals/nonexistent")
    assert resp.status_code == 404


async def test_signals_filter_tier(client):
    await insert_signal(_make_signal(signal_id="s1", tier="A+", confidence=85.0))
    await insert_signal(_make_signal(signal_id="s2", tier="B", confidence=72.0))
    resp = await client.get("/api/signals?tier=A%2B")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["tier"] == "A+"


async def test_suppressed_empty(client):
    resp = await client.get("/api/suppressed")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_fcm_token_register(client):
    resp = await client.post(
        "/api/fcm-token",
        json={"token": "a" * 152, "uid": "user123"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_fcm_token_invalid(client):
    resp = await client.post(
        "/api/fcm-token",
        json={"token": "short"},
    )
    assert resp.status_code == 400
