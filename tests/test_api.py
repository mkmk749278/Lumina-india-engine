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


async def test_pulse_no_auth(app):
    import src.api.server

    original = src.api.server._STATIC_TOKEN
    src.api.server._STATIC_TOKEN = "secret123"
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/pulse")
            assert resp.status_code == 401
    finally:
        src.api.server._STATIC_TOKEN = original


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
