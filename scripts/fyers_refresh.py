"""Automated daily Fyers access-token refresh (cron, no human).

Fyers access tokens expire daily, but the refresh token issued alongside
them is valid ~15 days and can be exchanged for a fresh access token with
no interactive login (validate-refresh-token: appIdHash + refresh_token +
trading PIN). The deploy workflow installs a cron entry that runs this
every trading morning at 08:45 IST:

    python3 scripts/fyers_refresh.py --restart

Reads from the engine .env (written by scripts/fyers_token.py and the
deploy secret injection):
    FYERS_CLIENT_ID, FYERS_SECRET_KEY, FYERS_PIN, FYERS_REFRESH_TOKEN

Writes back: FYERS_ACCESS_TOKEN (and a rotated FYERS_REFRESH_TOKEN when
Fyers returns one). With --restart it recreates the engine container so
the new token is picked up before the 09:15 open.

When the refresh token itself has expired (~15 days), this exits non-zero
with a clear message — the owner runs the interactive fyers_token.py once
to mint a new one, and the cron takes over again.

Security: tokens/PIN are never printed. Only Fyers error messages are
logged. Stdlib only — runs on the bare VPS host.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from datetime import datetime

# Running as `python3 scripts/fyers_refresh.py` puts scripts/ on sys.path,
# so the interactive helper's building blocks are importable directly.
from fyers_token import (
    API_BASE,
    DEFAULT_ENV_FILE,
    _get_json,
    _post_json,
    _read_env_value,
    _write_env_value,
)

_RESTART_CMD = [
    "docker", "compose", "-f", "docker-compose.india.yml",
    "up", "-d", "--force-recreate", "engine",
]


class MissingSettings(Exception):
    """Raised when required .env keys are absent."""


def load_settings(env_file: str) -> dict[str, str]:
    """Read and validate the four values the refresh exchange needs."""
    keys = (
        "FYERS_CLIENT_ID",
        "FYERS_SECRET_KEY",
        "FYERS_PIN",
        "FYERS_REFRESH_TOKEN",
    )
    settings = {k: _read_env_value(env_file, k) for k in keys}
    missing = [k for k, v in settings.items() if not v]
    if missing:
        raise MissingSettings(
            f"missing in {env_file}: {', '.join(missing)} — "
            "FYERS_SECRET_KEY/FYERS_PIN are injected by deploy from GitHub "
            "secrets; FYERS_REFRESH_TOKEN is written by scripts/fyers_token.py"
        )
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the Fyers access token via refresh token")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Path to the engine .env")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Recreate the engine container after a successful refresh",
    )
    parser.add_argument(
        "--workdir",
        default="/opt/lumin-india",
        help="Directory containing docker-compose.india.yml (for --restart)",
    )
    args = parser.parse_args()

    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{stamp}] fyers_refresh: starting")

    try:
        settings = load_settings(args.env_file)
    except MissingSettings as e:
        print(f"error: {e}")
        return 2

    app_id_hash = hashlib.sha256(
        f"{settings['FYERS_CLIENT_ID']}:{settings['FYERS_SECRET_KEY']}".encode()
    ).hexdigest()

    result = _post_json(
        f"{API_BASE}/validate-refresh-token",
        {
            "grant_type": "refresh_token",
            "appIdHash": app_id_hash,
            "refresh_token": settings["FYERS_REFRESH_TOKEN"],
            "pin": settings["FYERS_PIN"],
        },
    )
    if result.get("s") != "ok" or not result.get("access_token"):
        message = result.get("message", result.get("s"))
        print(f"error: refresh exchange failed — {message}")
        print(
            "If the refresh token has expired (~15 days), run the interactive "
            "login once: FYERS_REDIRECT_URI=<app redirect> "
            "python3 scripts/fyers_token.py"
        )
        return 1

    token = result["access_token"]

    profile = _get_json(
        f"{API_BASE}/profile",
        {"Authorization": f"{settings['FYERS_CLIENT_ID']}:{token}"},
    )
    if profile.get("s") != "ok":
        print(
            "error: refreshed token failed profile verification — "
            f"{profile.get('message', profile.get('s'))}"
        )
        return 1

    _write_env_value(args.env_file, "FYERS_ACCESS_TOKEN", token)
    if result.get("refresh_token"):
        _write_env_value(args.env_file, "FYERS_REFRESH_TOKEN", result["refresh_token"])
    print("access token refreshed and verified (not displayed)")

    if args.restart:
        proc = subprocess.run(
            _RESTART_CMD, cwd=args.workdir, capture_output=True, text=True
        )
        if proc.returncode != 0:
            print(f"error: engine restart failed — {proc.stderr.strip()[:300]}")
            return 1
        print("engine container recreated with the new token")

    return 0


if __name__ == "__main__":
    sys.exit(main())
