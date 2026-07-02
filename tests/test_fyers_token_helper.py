"""Fyers token helper — .env editing and auth-code extraction."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "fyers_token", Path(__file__).parent.parent / "scripts" / "fyers_token.py"
)
fyers_token = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fyers_token)


def test_extract_bare_code() -> None:
    assert fyers_token._extract_auth_code("  eyJhbGciOi.abc.def  ") == "eyJhbGciOi.abc.def"


def test_extract_from_redirect_url() -> None:
    url = (
        "https://trade.fyers.in/api-login/redirect-uri/index.html"
        "?s=ok&code=200&auth_code=THECODE&state=lumin"
    )
    assert fyers_token._extract_auth_code(url) == "THECODE"


def test_extract_from_url_without_code_raises() -> None:
    with pytest.raises(ValueError):
        fyers_token._extract_auth_code("https://example.com/?s=error")


def test_write_env_value_replaces_existing(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("FYERS_CLIENT_ID=APP-100\nFYERS_ACCESS_TOKEN=old\nAPI_PORT=8000\n")
    fyers_token._write_env_value(str(env), "FYERS_ACCESS_TOKEN", "new-token")
    content = env.read_text()
    assert "FYERS_ACCESS_TOKEN=new-token\n" in content
    assert "old" not in content
    assert "API_PORT=8000\n" in content


def test_write_env_value_appends_missing(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("API_PORT=8000")
    fyers_token._write_env_value(str(env), "FYERS_ACCESS_TOKEN", "tok")
    assert env.read_text() == "API_PORT=8000\nFYERS_ACCESS_TOKEN=tok\n"


def test_write_env_value_sets_owner_only_mode(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("")
    fyers_token._write_env_value(str(env), "FYERS_ACCESS_TOKEN", "tok")
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_read_env_value(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("FYERS_CLIENT_ID=APP-100\n")
    assert fyers_token._read_env_value(str(env), "FYERS_CLIENT_ID") == "APP-100"
    assert fyers_token._read_env_value(str(env), "MISSING") == ""
    assert fyers_token._read_env_value(str(tmp_path / "nope"), "X") == ""
