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


async def test_refresh_parses_nse_report_list_shape(monkeypatch):
    """NSE's /api/fiidiiTradeReact returns a LIST of category rows — the
    deploy can point INDIA_FII_DII_URL straight at it."""
    import config as _config
    from src.data.india_macro_store import IndiaMacroStore

    monkeypatch.setattr(_config, "FII_DII_URL", "https://example.test/fiidii")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "category": "DII **",
                    "date": "16-Jul-2026",
                    "buyValue": "12,000.00",
                    "sellValue": "11,000.00",
                    "netValue": "1,000.00",
                },
                {
                    "category": "FII/FPI *",
                    "date": "16-Jul-2026",
                    "netValue": "-2,500.50",
                },
            ]

    class _Client:
        async def get(self, url):
            return _Resp()

    store = IndiaMacroStore()
    assert await store.refresh(http_client=_Client()) is True
    snap = store.snapshot()
    assert snap["available"] is True
    assert snap["fii_net_cr"] == -2500.50
    assert snap["dii_net_cr"] == 1000.00
    assert store.get_net_cr() == -1500.50
    assert snap["as_of"] == "16-Jul-2026"
