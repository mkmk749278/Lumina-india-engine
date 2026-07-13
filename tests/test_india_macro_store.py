"""IndiaMacroStore — prev-day FII/DII, freshness-gated, never fabricated."""

from __future__ import annotations

import config
from src.data.india_macro_store import IndiaMacroStore


def test_unavailable_before_any_fetch():
    store = IndiaMacroStore()
    assert store.get_net_cr() == 0.0
    assert store.snapshot()["available"] is False


def test_set_and_read_net():
    store = IndiaMacroStore()
    store.set_fii_dii(1500.0, -400.0, "2026-07-13")
    assert store.get_net_cr() == 1100.0
    snap = store.snapshot()
    assert snap["available"] is True and snap["fii_net_cr"] == 1500.0


def test_stale_reads_as_unavailable():
    store = IndiaMacroStore(ttl_sec=-1)  # anything set is immediately stale
    store.set_fii_dii(1500.0, 0.0)
    assert store.get_net_cr() == 0.0
    assert store.snapshot()["available"] is False


async def test_refresh_no_url_is_noop(monkeypatch):
    monkeypatch.setattr(config, "FII_DII_URL", "")
    store = IndiaMacroStore()
    assert await store.refresh() is False
    assert store.get_net_cr() == 0.0


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url):
        return _FakeResp(self._payload)


async def test_refresh_parses_and_sets(monkeypatch):
    monkeypatch.setattr(config, "FII_DII_URL", "http://example/fii")
    store = IndiaMacroStore()
    ok = await store.refresh(
        http_client=_FakeClient({"fii_net_cr": 2000, "dii_net_cr": -500,
                                 "date": "2026-07-13"})
    )
    assert ok is True
    assert store.get_net_cr() == 1500.0


async def test_refresh_unrecognised_payload_stays_unavailable(monkeypatch):
    monkeypatch.setattr(config, "FII_DII_URL", "http://example/fii")
    store = IndiaMacroStore()
    ok = await store.refresh(http_client=_FakeClient({"unrelated": 1}))
    assert ok is False
    assert store.get_net_cr() == 0.0
