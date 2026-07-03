"""Persist the daily Fyers access token across container restarts.

The token arrives via the ``/fyers/callback`` endpoint while the engine
is running. If the container restarts later that day, env alone would
hold yesterday's token — so the freshest token is also written to the
shared data volume (mirroring the accepted ``.env`` persistence pattern;
file is chmod 0600 and never logged).

Tokens are valid for one trading day only, so ``load_token`` returns
``None`` unless the stored token was saved today (IST).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import config

_FILENAME = "fyers_token.json"


def _path() -> Path:
    return Path(os.environ.get("INDIA_DATA_DIR", "/app/data")) / _FILENAME


def save_token(token: str) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token,
        "saved_date": datetime.now(config.IST).date().isoformat(),
    }
    path.write_text(json.dumps(payload))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_token() -> str | None:
    """Return the stored token only if it was saved today (IST)."""
    path = _path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    today = datetime.now(config.IST).date().isoformat()
    if payload.get("saved_date") != today:
        return None
    token = payload.get("access_token", "")
    return token or None
