#!/usr/bin/env python3
"""One-time OAuth consent for the google_health tool.

Runs Google's installed-app OAuth flow and writes a refresh token to
`google_health_credentials.json` at the project root (mode 600). Needed once per
Google account -- not per machine or container: the file reaches the container
through the `.:/app` bind mount, and can be copied to another host as-is.

Usage:
    # On a machine with a browser (opens it, catches the redirect locally):
    python scripts/google_health_auth.py

    # Headless host or inside the container (prints a URL; paste the redirect back):
    docker exec -it <container> python scripts/google_health_auth.py --no-browser

Options:
    --client-secret PATH   OAuth client JSON (default: the single client_secret_*.json
                           at the project root). Must be a "Desktop app" client.
    --out PATH             Where to write credentials (default:
                           $GOOGLE_HEALTH_CREDENTIALS_FILE or ./google_health_credentials.json).
    --force                Overwrite an existing credentials file.

Deliberately stdlib + requests only, so it runs on a bare host without the rest
of the project's dependencies. SCOPES is therefore duplicated from
tools/google_health.py; a unit test keeps the two identical.
"""
import argparse
import base64
import glob
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone

import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "google_health_credentials.json")

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
VERIFY_URL = "https://health.googleapis.com/v4/users/me/dataTypes/weight/dataPoints"

SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def load_client(path: str) -> dict:
    if not path:
        matches = sorted(glob.glob(os.path.join(PROJECT_ROOT, "client_secret_*.json")))
        if not matches:
            fail("No client_secret_*.json at the project root. Download a 'Desktop app' OAuth "
                 "client from Google Cloud Console > APIs & Services > Credentials, or pass --client-secret.")
        if len(matches) > 1:
            fail("Several client_secret_*.json files found; choose one with --client-secret.")
        path = matches[0]
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    client = data.get("installed")
    if not client:
        fail(f"{path} is not a 'Desktop app' (installed) OAuth client.")
    return client


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client: dict, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # offline + consent is what makes Google issue a refresh token every time.
        "access_type": "offline",
        "prompt": "consent",
    })


def code_from_query(query: str, state: str) -> str:
    params = urllib.parse.parse_qs(query)
    if params.get("error"):
        fail(f"Authorisation was refused: {params['error'][0]}")
    if params.get("state", [""])[0] != state:
        fail("State mismatch in the redirect; start again.")
    code = params.get("code", [""])[0]
    if not code:
        fail("No authorisation code in the redirect URL.")
    return code


def browser_flow(client: dict, state: str, challenge: str):
    """Loopback redirect: a one-shot local server catches Google's redirect."""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            result["query"] = urllib.parse.urlparse(self.path).query
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>Google Health authorised. You can close this tab.</h3>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://localhost:{server.server_port}"
    url = auth_url(client, redirect_uri, state, challenge)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser to authorise Google Health access...")
    print(f"If it does not open, visit:\n\n  {url}\n")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    if "query" not in result:
        fail("Timed out waiting for the browser redirect (5 minutes).")
    return code_from_query(result["query"], state), redirect_uri


def manual_flow(client: dict, state: str, challenge: str):
    """No local browser: the user opens the URL anywhere and pastes the redirect back."""
    redirect_uri = "http://localhost"
    url = auth_url(client, redirect_uri, state, challenge)
    print("1. Open this URL in a browser on any device and approve access:\n")
    print(f"   {url}\n")
    print("2. The browser then fails to load a http://localhost/?code=... page. That is")
    print("   expected. Copy the full URL from the address bar and paste it here.\n")
    pasted = input("Redirect URL: ").strip()
    return code_from_query(urllib.parse.urlparse(pasted).query, state), redirect_uri


def exchange(client: dict, code: str, redirect_uri: str, verifier: str) -> dict:
    response = requests.post(client.get("token_uri") or TOKEN_URI, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }, timeout=30)
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code >= 400 or "refresh_token" not in body:
        fail(f"Token exchange failed ({response.status_code}): {body.get('error_description') or body.get('error') or 'no refresh token returned'}")
    granted = set((body.get("scope") or "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("warning: these scopes were not granted (unticked on the consent screen?):", file=sys.stderr)
        for scope in missing:
            print(f"  {scope}", file=sys.stderr)
    return body


def write_credentials(path: str, client: dict, token: dict) -> None:
    """Atomic write, created 600 from the start: never briefly world-readable."""
    data = {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": token["refresh_token"],
        "token_uri": client.get("token_uri") or TOKEN_URI,
        "scopes": token.get("scope", " ".join(SCOPES)).split(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def verify(access_token: str) -> None:
    """One cheap read, so a project without API access is discovered now, not by an agent."""
    try:
        response = requests.get(VERIFY_URL, params={"pageSize": 1},
                                headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    except requests.RequestException as exc:
        print(f"warning: could not verify access ({type(exc).__name__}).", file=sys.stderr)
        return
    if response.status_code == 200:
        print("Verified: the Google Health API answered a test read.")
    else:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        print(f"warning: credentials saved, but a test read returned {response.status_code}: {message}", file=sys.stderr)
        print("         The Cloud project may not have Google Health API access yet.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorise the google_health tool.")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--out", default=os.environ.get("GOOGLE_HEALTH_CREDENTIALS_FILE") or DEFAULT_OUT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out = os.path.abspath(os.path.expanduser(args.out))
    if os.path.exists(out) and not args.force:
        fail(f"{out} already exists. Pass --force to replace it.")

    client = load_client(args.client_secret)
    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce_pair()

    if args.no_browser:
        code, redirect_uri = manual_flow(client, state, challenge)
    else:
        code, redirect_uri = browser_flow(client, state, challenge)

    token = exchange(client, code, redirect_uri, verifier)
    write_credentials(out, client, token)
    print(f"Wrote {out} (mode 600).")
    if token.get("access_token"):
        verify(token["access_token"])


if __name__ == "__main__":
    main()
