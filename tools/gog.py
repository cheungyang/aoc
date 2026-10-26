"""The gog tool, and the gog CLI helpers calendar code shares.

`gog` relays gog's text to the model. Code that computes with the result
(`calendar_add`, excursion-planner's `weekend_availability`) calls `run_json`,
`list_events` or `create_event` instead: they parse gog's JSON and raise
`GogError` on failure. Both paths find the binary and set the keyring
environment the same way (`gog_binary`, `gog_env`).
"""
import json
import os
import shlex
import subprocess
from datetime import datetime
from typing import Any, List

from langchain_core.tools import tool

from core.tooling import format_tool_response
from core.config.settings import Config
from core.util.local_time import utc_text

TIMEOUT_SECONDS = 30.0
_WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BINARY_PATHS = (os.path.join(_WORKSPACE_ROOT, "bin", "gog"), "/usr/local/bin/gog")
_KEYRING_FAILURE = "aes.KeyUnwrap(): integrity check failed"
_KEYRING_HINT = (
    "Keyring decryption failed: check that GOG_KEYRING_PASSWORD in .env matches the "
    "passphrase gogcli was initialized with."
)


class GogError(RuntimeError):
    """gog is missing, failed, or printed something other than JSON."""


def gog_binary() -> str:
    for path in BINARY_PATHS:
        if os.path.exists(path):
            return path
    raise GogError(f"gog binary not found at {' or '.join(BINARY_PATHS)}")


def gog_env() -> dict:
    env = os.environ.copy()
    config = Config()
    env["GOG_KEYRING_BACKEND"] = config.gog_keyring_backend or env.get("GOG_KEYRING_BACKEND", "file")
    password = config.gog_keyring_password or env.get("GOG_KEYRING_PASSWORD", "")
    if password:
        env["GOG_KEYRING_PASSWORD"] = password
    return env


def run_json(args: List[str]) -> Any:
    """Runs `gog <args> --json` and returns the parsed output, or raises GogError."""
    command = " ".join(args[:2])
    try:
        result = subprocess.run(
            [gog_binary(), *args, "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=gog_env(),
        )
    except subprocess.TimeoutExpired:
        raise GogError(
            f"gog {command} timed out after {TIMEOUT_SECONDS:.0f} s; check that gog is "
            "authenticated and never waits for input"
        ) from None
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or output or f"exit code {result.returncode}").strip()
        if _KEYRING_FAILURE in detail:
            detail = f"{detail} ({_KEYRING_HINT})"
        raise GogError(f"gog {command} failed: {detail[-500:]}")
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise GogError(f"gog {command} printed no JSON: {output[:200]!r}") from None


def list_events(start: datetime, end: datetime, calendar: str = "primary", max_results: int = 250) -> Any:
    """The events overlapping [start, end) on one calendar, as gog's JSON."""
    return run_json([
        "calendar", "events", calendar,
        "--from", utc_text(start), "--to", utc_text(end), "--max", str(max_results),
    ])


def create_event(
    calendar: str, title: str, start: str, end: str, *,
    all_day: bool = False, location: str = "", description: str = "",
) -> Any:
    """Creates one event and returns gog's JSON ({"event": {...}}). `start` and `end`
    are UTC RFC3339, or dates with an exclusive end when `all_day`."""
    args = ["calendar", "create", calendar, "--summary", title, "--from", start, "--to", end]
    if all_day:
        args.append("--all-day")
    if location:
        args += ["--location", location]
    if description:
        args += ["--description", description]
    return run_json(args)


@tool
def gog(command: str) -> str:
    """
    Execute a gog CLI command using gogcli (supports Google Calendar and Gmail).

    The command argument should be the rest of the command after 'gog'.

    Example commands:
    # Calendar operations:
    - calendar calendars (List all calendars)
    - calendar events primary --today (List events on primary calendar)
    
    # To add events to a specific calendar, replace 'primary' with the calendar name or ID:
    - calendar create 'Work Calendar' --summary 'Meeting' --from 2026-04-07T10:00:00Z --to 2026-04-07T11:00:00Z
    
    # To set event color, use the --color flag (Google Calendar uses color IDs 1-11):
    - calendar create primary --summary 'Meeting' --color 1 --from 2026-04-07T10:00:00Z --to 2026-04-07T11:00:00Z

    # Gmail operations:
    - gmail search 'newer_than:7d' --max 10 --json (Search emails matching query)
    - gmail get <messageId> --sanitize-content --json (Get email message content by ID)
    - gmail thread get <threadId> --sanitize-content --json (Get email thread content by ID)
    - gmail settings filters export | list | create | delete (Manage Gmail filters)
    """
    try:
        gog_bin = gog_binary()
    except GogError:
        return format_tool_response(
            "gog", payload="",
            errors=f"Error: gog binary not found at {BINARY_PATHS[0]} or {BINARY_PATHS[1]}. Please ensure it is installed.",
        )

    try:
        # Split command safely
        args = shlex.split(command)
        result = subprocess.run(
            [gog_bin] + args,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=gog_env(),
        )

        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(result.stderr)

        output_str = "\n".join(output)
        if _KEYRING_FAILURE in output_str:
            output_str += (
                "\n[Gog Tool Hint]: Keyring decryption failed. Please verify that "
                "GOG_KEYRING_PASSWORD in .env matches the keyring passphrase used "
                "when gogcli was initialized."
            )

        return format_tool_response("gog", payload=output_str, errors="None")

    except subprocess.TimeoutExpired:
        return format_tool_response(
            "gog",
            payload="",
            errors="Error: gog command timed out after 30 seconds. In headless Docker environments, ensure gog is authenticated (OAuth credentials in .gogcli) and the command does not wait for interactive input."
        )
    except Exception as e:
        return format_tool_response("gog", payload="", errors=f"Error performing gog action: {e}")
