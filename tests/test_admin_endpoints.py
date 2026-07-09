"""Admin maintenance endpoints (ops Control panel backend).

/api/admin/clear-history and /api/admin/reset-gates: static-token-only auth
(a subscriber's Firebase ID token must never authorise maintenance), the
CLEAR confirmation contract, the DB wipe itself, and the in-memory state
reset callback.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

import config
from src.api.server import build_app, set_admin_state_reset, set_engine_refs
from src.db import close_db
from src.fcm_dispatcher import init_fcm_tables
from src.signal_store import (
    clear_history,
    get_signal_count_today,
    get_suppressions,
    init_tables,
    insert_outcome,
    insert_signal,
    insert_suppression,
)
from src.signals.model import IndiaSignal


@pytest.fixture(autouse=True)
async def _setup_db(tmp_path, monkeypatch):
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
def admin_token(monkeypatch):
    import src.api.server as server_mod

    monkeypatch.setattr(server_mod, "_STATIC_TOKEN", "owner-token")
    monkeypatch.setattr(server_mod, "_firebase_auth_ready", True)
    monkeypatch.setattr(server_mod, "_firebase_auth_module", None)
    return "owner-token"


@pytest.fixture
async def client(admin_token):
    set_engine_refs(
        boot_time=1000000.0,
        scan_count_ref=[1],
        session_state_ref=["OPEN"],
    )
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _sig(sid: str) -> IndiaSignal:
    return IndiaSignal(
        signal_id=sid,
        symbol="NSE:NIFTY26JULFUT",
        base="NIFTY",
        direction="LONG",
        setup_class="TREND_PULLBACK_EMA",
        entry=24500.0,
        sl=24400.0,
        tp1=24700.0,
        sl_pct=0.41,
        tp1_pct=0.82,
        rr_ratio=2.0,
        lot_size=65,
    )


async def _seed_rows() -> None:
    await insert_signal(_sig("sig-1"))
    await insert_signal(_sig("sig-2"))
    await insert_outcome(
        "sig-1", "TP1_HIT", 24700.0, 200.0, 0.82, datetime.now(config.IST)
    )
    await insert_suppression(
        "NIFTY", "warmup_gate", "test", "TREND_PULLBACK_EMA", "LONG",
        datetime.now(config.IST),
    )


# ── store-level ──────────────────────────────────────────────────────


async def test_clear_history_all_wipes_every_table() -> None:
    await _seed_rows()
    deleted = await clear_history("all")
    assert deleted["india_signals"] == 2
    assert deleted["india_signal_outcomes"] == 1
    assert deleted["india_suppressions"] == 1
    assert await get_signal_count_today() == 0
    assert await get_suppressions() == []


async def test_clear_history_today_scopes_to_current_date() -> None:
    await _seed_rows()
    deleted = await clear_history("today")
    # Rows were just inserted (localtime today) — all in scope.
    assert deleted["india_signals"] == 2
    assert await get_signal_count_today() == 0


async def test_clear_history_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError):
        await clear_history("everything")


# ── endpoint auth ────────────────────────────────────────────────────


async def test_admin_requires_static_token(client):
    resp = await client.post(
        "/api/admin/clear-history",
        json={"scope": "all", "confirm": "CLEAR"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


async def test_admin_rejects_missing_auth(client):
    resp = await client.post(
        "/api/admin/clear-history", json={"scope": "all", "confirm": "CLEAR"}
    )
    assert resp.status_code == 403


async def test_admin_requires_confirm_word(client, admin_token):
    resp = await client.post(
        "/api/admin/clear-history",
        json={"scope": "all", "confirm": "yes"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


async def test_admin_rejects_bad_scope(client, admin_token):
    resp = await client.post(
        "/api/admin/clear-history",
        json={"scope": "everything", "confirm": "CLEAR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


# ── endpoint behaviour ───────────────────────────────────────────────


async def test_clear_history_endpoint_wipes_and_resets_state(client, admin_token):
    await _seed_rows()
    reset_calls = []
    set_admin_state_reset(
        lambda: reset_calls.append(1) or {"tracked_signals_dropped": 3, "gates_reset": True}
    )
    try:
        resp = await client.post(
            "/api/admin/clear-history",
            json={"scope": "all", "confirm": "CLEAR"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["deleted"]["india_signals"] == 2
        assert body["tracked_signals_dropped"] == 3
        assert reset_calls == [1]
        assert await get_signal_count_today() == 0
    finally:
        import src.api.server as server_mod

        server_mod._admin_state_reset_cb = None


async def test_reset_gates_endpoint_calls_state_reset(client, admin_token):
    set_admin_state_reset(lambda: {"tracked_signals_dropped": 0, "gates_reset": True})
    try:
        resp = await client.post(
            "/api/admin/reset-gates",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["gates_reset"] is True
    finally:
        import src.api.server as server_mod

        server_mod._admin_state_reset_cb = None
