"""Automated token refresh — settings loading and validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "fyers_refresh", _SCRIPTS / "fyers_refresh.py"
)
fyers_refresh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fyers_refresh)


def _write_env(tmp_path, **values) -> str:
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return str(env)


def test_load_settings_complete(tmp_path) -> None:
    env = _write_env(
        tmp_path,
        FYERS_CLIENT_ID="APP-100",
        FYERS_SECRET_KEY="sek",
        FYERS_PIN="1234",
        FYERS_REFRESH_TOKEN="rtok",
    )
    settings = fyers_refresh.load_settings(env)
    assert settings["FYERS_CLIENT_ID"] == "APP-100"
    assert settings["FYERS_REFRESH_TOKEN"] == "rtok"


def test_load_settings_missing_raises_with_names(tmp_path) -> None:
    env = _write_env(tmp_path, FYERS_CLIENT_ID="APP-100")
    with pytest.raises(fyers_refresh.MissingSettings) as exc:
        fyers_refresh.load_settings(env)
    msg = str(exc.value)
    assert "FYERS_SECRET_KEY" in msg
    assert "FYERS_PIN" in msg
    assert "FYERS_REFRESH_TOKEN" in msg


def test_load_settings_empty_value_counts_as_missing(tmp_path) -> None:
    env = _write_env(
        tmp_path,
        FYERS_CLIENT_ID="APP-100",
        FYERS_SECRET_KEY="sek",
        FYERS_PIN="",
        FYERS_REFRESH_TOKEN="rtok",
    )
    with pytest.raises(fyers_refresh.MissingSettings, match="FYERS_PIN"):
        fyers_refresh.load_settings(env)
