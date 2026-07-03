"""Fyers token persistence — save/load semantics."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta

import config
from src.broker import token_store


def test_roundtrip_same_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    token_store.save_token("tok-abc")
    assert token_store.load_token() == "tok-abc"


def test_file_is_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    token_store.save_token("tok-abc")
    mode = stat.S_IMODE((tmp_path / "fyers_token.json").stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_stale_token_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    yesterday = (datetime.now(config.IST) - timedelta(days=1)).date().isoformat()
    (tmp_path / "fyers_token.json").write_text(
        json.dumps({"access_token": "old", "saved_date": yesterday})
    )
    assert token_store.load_token() is None


def test_missing_file_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    assert token_store.load_token() is None


def test_corrupt_file_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    (tmp_path / "fyers_token.json").write_text("not json{")
    assert token_store.load_token() is None
