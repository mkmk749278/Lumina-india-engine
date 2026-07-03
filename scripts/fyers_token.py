"""Daily Fyers access-token refresh helper (Phase 1 manual flow).

Fyers OAuth access tokens expire daily. Until the Phase 2 signing service
owns automated refresh, the owner runs this on the VPS each trading
morning (from Termux over SSH):

    python3 scripts/fyers_token.py

Flow:
  1. Prints the Fyers login URL (open it on the phone, log in).
  2. Fyers redirects to the app's configured redirect URI with
     ``auth_code=...`` in the URL — paste that code (or the whole URL) here.
  3. Exchanges the auth code for an access token (validate-authcode).
  4. Verifies the token against the profile endpoint.
  5. Writes ``FYERS_ACCESS_TOKEN=...`` into the .env file and prints the
     container restart command.

Security (CLAUDE.md hard limits):
  - The secret key is prompted via getpass (never echoed) and is used only
    to compute the appIdHash. It is never stored or printed.
  - The access token is never printed or logged; it is written only to the
    .env file the deploy pipeline already manages (mode 0600).

Stdlib only — runs on the bare VPS host without pip installs.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api-t1.fyers.in/api/v3"
DEFAULT_ENV_FILE = "/opt/lumin-india/.env"
DEFAULT_REDIRECT_URI = "https://trade.fyers.in/api-login/redirect-uri/index.html"

# Fyers sits behind Cloudflare, which 403-blocks the default
# "Python-urllib/3.x" user agent. Send a normal client identity.
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "lumin-india-engine/1.0",
    "Accept": "application/json",
}


def _error_payload(e: urllib.error.HTTPError) -> dict:
    """Best-effort decode of an HTTP error response.

    Returns the JSON body when there is one; otherwise includes a snippet
    of the raw body so WAF/HTML blocks are visible instead of a bare code.
    """
    try:
        body = e.read().decode(errors="replace")
    except Exception:
        body = ""
    try:
        return json.loads(body)
    except Exception:
        snippet = " ".join(body.split())[:160]
        return {"s": "error", "message": f"HTTP {e.code}: {snippet or 'empty body'}"}


def _read_env_value(env_file: str, key: str) -> str:
    if not os.path.exists(env_file):
        return ""
    with open(env_file, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def _write_env_value(env_file: str, key: str, value: str) -> None:
    lines: list[str] = []
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as fh:
            lines = fh.readlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    with open(env_file, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=dict(_HEADERS),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return _error_payload(e)


def _get_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers={**_HEADERS, **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return _error_payload(e)


def _extract_auth_code(raw: str) -> str:
    """Accept either the bare auth code or the full redirect URL."""
    raw = raw.strip()
    if raw.startswith("http"):
        query = urllib.parse.urlparse(raw).query
        params = urllib.parse.parse_qs(query)
        codes = params.get("auth_code") or params.get("code")
        if not codes:
            raise ValueError("no auth_code parameter found in that URL")
        return codes[0]
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the daily Fyers access token")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Path to the engine .env")
    args = parser.parse_args()

    client_id = _read_env_value(args.env_file, "FYERS_CLIENT_ID") or os.environ.get(
        "FYERS_CLIENT_ID", ""
    )
    if not client_id:
        client_id = input("Fyers client ID (e.g. ABCDE12345-100): ").strip()
    if not re.match(r"^[A-Z0-9]+-\d+$", client_id):
        print(f"warning: '{client_id}' does not look like a Fyers client ID (APPID-100)")

    redirect_uri = os.environ.get("FYERS_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    login_url = (
        f"{API_BASE}/generate-authcode?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "state": "lumin",
            }
        )
    )
    print("\n1. Open this URL on your phone and log in to Fyers:\n")
    print(f"   {login_url}\n")
    print("2. After login you land on the redirect page — copy the auth_code")
    print("   from the address bar (or copy the whole URL).\n")

    try:
        auth_code = _extract_auth_code(input("Paste auth code (or full redirect URL): "))
    except ValueError as e:
        print(f"error: {e}")
        return 1

    secret = getpass.getpass("Fyers secret key (input hidden, not stored): ").strip()
    if not secret:
        print("error: secret key is required to compute the appIdHash")
        return 1

    app_id_hash = hashlib.sha256(f"{client_id}:{secret}".encode()).hexdigest()
    del secret

    print("\nExchanging auth code for access token...")
    result = _post_json(
        f"{API_BASE}/validate-authcode",
        {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code,
        },
    )
    if result.get("s") != "ok" or not result.get("access_token"):
        print(f"error: token exchange failed — {result.get('message', result.get('s'))}")
        return 1
    token = result["access_token"]

    print("Verifying token against the profile endpoint...")
    profile = _get_json(
        f"{API_BASE}/profile", {"Authorization": f"{client_id}:{token}"}
    )
    if profile.get("s") != "ok":
        print(f"error: token verification failed — {profile.get('message', profile.get('s'))}")
        return 1
    name = profile.get("data", {}).get("name", "unknown")
    print(f"Token valid — logged in as: {name}")

    _write_env_value(args.env_file, "FYERS_ACCESS_TOKEN", token)
    print(f"\nToken written to {args.env_file} (not displayed).")
    print("Restart the engine to pick it up:\n")
    print(
        "   cd /opt/lumin-india && docker compose -f docker-compose.india.yml"
        " up -d --force-recreate engine\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
