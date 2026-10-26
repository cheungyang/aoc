"""Visual, multi-step web interaction (bookings, reservations) via browser-use.

The browser is the "Agent Chrome" on the user's Windows desktop, not one in
this container: the user watches it, types what the agent cannot (2FA codes,
choices), and makes the final click. See scripts/desktop_agent_chrome/windows/.

The desktop is often off, so "offline" is an expected outcome, reported with a
status the skill keys on, never retried or papered over:
    ONLINE                 check_only=True found Agent Chrome listening
    DESKTOP_OFFLINE        nothing listening before the task started
    DESKTOP_DISCONNECTED   the desktop went away mid-task
    NOT_CONFIGURED         endpoint or personal-info file missing or invalid

Two invariants matter more than anything else here:
  * The desktop browser is never closed. The session is created with
    keep_alive=True and only ever stop()-ed (disconnect), never kill()-ed:
    with keep_alive=False, browser-use's Agent.close() calls kill() itself.
  * Personal details reach the page, not the LLM. They are passed as
    browser-use `sensitive_data`, so the model only sees placeholder names.

Configuration (paths, never values, like HA_TOKEN_FILE):
    BROWSER_CDP_URL_FILE        One line, e.g. http://192.168.1.20:9223
    BROWSER_PERSONAL_INFO_FILE  Optional JSON:
                                  {"fields": {"full_name": "...", "phone": "..."},
                                   "allowed_domains": ["*.resy.com", ...]}
"""

import asyncio
import ipaddress
import json
import os
import socket
import threading
from urllib.parse import urlsplit, urlunsplit

import requests
from langchain_core.tools import tool
from browser_use import Agent, ChatGoogle
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

from core.prompts.browser import BrowserHandoffPrompt
from core.tooling import format_tool_response
from core.config.models import DEFAULT_BROWSER_MODEL


DEFAULT_CDP_URL_FILE = "/app/browser_cdp_url"
DEFAULT_PERSONAL_INFO_FILE = "/app/browser_personal_info.json"
DEFAULT_MAX_STEPS = 25
MAX_MAX_STEPS = 60
# Wall clock for one task. Every step is a Gemini call plus a page render.
TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 4
# A vanished desktop must not hang the bot on the final disconnect.
DISCONNECT_TIMEOUT_SECONDS = 10

ONLINE = "ONLINE"
DESKTOP_OFFLINE = "DESKTOP_OFFLINE"
DESKTOP_DISCONNECTED = "DESKTOP_DISCONNECTED"
NOT_CONFIGURED = "NOT_CONFIGURED"

WAIT_FOR_USER = (
    "Do not retry. Tell the user plainly, restate the pending request, and wait until they "
    "say Agent Chrome is on (the 'Agent Chrome (AOC)' shortcut on their desktop); then "
    "confirm with check_only=True before running it."
)

_session_lock = threading.Lock()


class BrowserStatusError(Exception):
    """An expected, reportable state (offline, not configured); `status` is the code."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _error(status: str, message: str) -> str:
    return format_tool_response("browser", payload="", errors=f"Status: {status}. {message}")


def _read_cdp_url() -> str:
    path = os.environ.get("BROWSER_CDP_URL_FILE", "").strip() or DEFAULT_CDP_URL_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            url = handle.read().strip()
    except FileNotFoundError:
        raise BrowserStatusError(
            NOT_CONFIGURED,
            f"The desktop browser is not configured: {path} does not exist. "
            "See INSTALL.md, 'Agent Browser'.",
        ) from None
    except OSError as exc:
        raise BrowserStatusError(NOT_CONFIGURED, f"Cannot read {path}: {exc}") from exc
    if not url:
        raise BrowserStatusError(NOT_CONFIGURED, f"{path} is empty; it should hold e.g. http://<desktop IP>:9223.")
    return url


def _with_ip_host(url: str) -> str:
    """`url` with its hostname replaced by an IP address.

    Chrome's DevTools server rejects any Host header that is not an IP literal
    or localhost (DNS-rebinding protection), so a name like winpc.lan would
    fail with an opaque 500. Resolving here lets the config file use either.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
        return url
    except ValueError:
        pass
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        # A desktop name that does not resolve usually means the PC is off.
        raise BrowserStatusError(
            DESKTOP_OFFLINE, f"Cannot resolve the desktop '{host}'; it is probably off. {WAIT_FOR_USER}"
        ) from None
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _reachable(url: str) -> bool:
    try:
        response = requests.get(f"{url.rstrip('/')}/json/version", timeout=PROBE_TIMEOUT_SECONDS)
        return response.ok
    except requests.RequestException:
        return False


def _personal_info():
    """(fields, allowed_domains) from the personal-info file; ({}, None) if absent."""
    path = os.environ.get("BROWSER_PERSONAL_INFO_FILE", "").strip() or DEFAULT_PERSONAL_INFO_FILE
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BrowserStatusError(NOT_CONFIGURED, f"Cannot read personal info file {path}: {exc}") from exc
    fields = data.get("fields") or {}
    if not isinstance(fields, dict) or not all(isinstance(v, str) for v in fields.values()):
        raise BrowserStatusError(NOT_CONFIGURED, f"{path}: 'fields' must map names to strings.")
    fields = {k: v for k, v in fields.items() if v.strip()}
    domains = data.get("allowed_domains") or None
    return fields, domains


def _endpoint() -> str:
    """The reachable desktop endpoint, or BrowserStatusError(DESKTOP_OFFLINE)."""
    url = _with_ip_host(_read_cdp_url())
    if not _reachable(url):
        raise BrowserStatusError(
            DESKTOP_OFFLINE,
            f"Agent Chrome on the desktop is not reachable at {url}; the PC is off, asleep, "
            f"or Agent Chrome is closed. Nothing was done. {WAIT_FOR_USER}",
        )
    return url


def _check() -> str:
    url = _endpoint()
    fields, domains = _personal_info()
    lines = [
        f"Status: {ONLINE}. Agent Chrome is reachable at {url}.",
        f"Personal fields available: {', '.join(sorted(fields)) or 'none'}",
        f"Allowed domains: {', '.join(domains) if domains else 'unrestricted'}",
    ]
    if _session_lock.locked():
        lines.append("A browser task is running right now.")
    return "\n".join(lines)


@tool
def browser(goal: str = "", max_steps: int = DEFAULT_MAX_STEPS, check_only: bool = False) -> str:
    """
    Operate the user's desktop "Agent Chrome" to complete a multi-step web task, such as
    finding and staging a restaurant reservation or event seats. The user can watch the
    window. The agent stops before any final Reserve/Pay click, and when it needs a login,
    a verification code, a missing required detail, or a choice. Tabs stay open, so a
    follow-up call can continue where the last one stopped.

    The desktop is often off. Errors start with a status: DESKTOP_OFFLINE or
    DESKTOP_DISCONNECTED mean wait for the user to turn Agent Chrome on (never retry
    on your own); NOT_CONFIGURED means a setup problem to report.

    Args:
        goal: The specific objective, including site, date, time, party size and preferences
              (e.g., 'On resy.com find Balthazar NYC, Fri Oct 24, 2 guests, 7-8pm; select the best
              slot and fill contact details, then stop on the confirm page').
              To continue after the user helped, describe the next step on the current page.
        max_steps: Maximum browser actions before giving up (default 25, max 60).
        check_only: Only check whether Agent Chrome is reachable (fast, no model calls).
                    Use it when the user says the desktop is ready, before running a goal.
    """
    if check_only:
        try:
            return format_tool_response("browser", payload=_check(), errors="None")
        except BrowserStatusError as e:
            return _error(e.status, str(e))
    if not goal.strip():
        return format_tool_response("browser", payload="", errors="Error: goal is required unless check_only=True.")

    max_steps = max(1, min(MAX_MAX_STEPS, int(max_steps or DEFAULT_MAX_STEPS)))
    if not _session_lock.acquire(blocking=False):
        return format_tool_response(
            "browser", payload="",
            errors="Error: another browser task is already running. Try again when it finishes.",
        )
    try:
        # LangChain runs sync tools on a worker thread with no event loop.
        payload = asyncio.run(_run_browser(goal, max_steps))
        return format_tool_response("browser", payload=payload, errors="None")
    except BrowserStatusError as e:
        return _error(e.status, str(e))
    except asyncio.TimeoutError:
        return format_tool_response(
            "browser", payload="",
            errors=f"Error performing browser action: timed out after {TIMEOUT_SECONDS}s. "
                   "Nothing was submitted; the tab is still open on the desktop.",
        )
    except Exception as e:
        return format_tool_response("browser", payload="", errors=f"Error performing browser action: {e}")
    finally:
        _session_lock.release()


def _disconnected(url: str, history, steps: int) -> BrowserStatusError:
    urls = [u for u in (history.urls() if history is not None else []) if u]
    where = f" on {urls[-1]}" if urls else ""
    return BrowserStatusError(
        DESKTOP_DISCONNECTED,
        f"Lost Agent Chrome at {url} after step {steps}{where}. Nothing was submitted (the final "
        f"click is always the user's); the tab may still be there when the PC is back. {WAIT_FOR_USER}",
    )


async def _run_browser(goal: str, max_steps: int) -> str:
    url = _endpoint()
    fields, allowed_domains = _personal_info()
    profile_kwargs = {"cdp_url": url, "is_local": False, "keep_alive": True}
    if allowed_domains:
        profile_kwargs["allowed_domains"] = allowed_domains
    session = BrowserSession(browser_profile=BrowserProfile(**profile_kwargs))
    lost = {"value": False}

    async def watch_desktop(agent) -> None:
        # browser-use records a dead CDP connection as step failures and keeps
        # going; checking after each step ends the run as soon as it is gone.
        if not await asyncio.to_thread(_reachable, url):
            lost["value"] = True
            agent.stop()

    agent = None
    try:
        # browser-use requires a multimodal visual LLM - using Gemini as native fallback
        llm = ChatGoogle(model=DEFAULT_BROWSER_MODEL)
        agent = Agent(
            task=goal,
            llm=llm,
            browser=session,
            sensitive_data=fields or None,
            extend_system_message=BrowserHandoffPrompt().render(),
        )
        try:
            history = await asyncio.wait_for(
                agent.run(max_steps=max_steps, on_step_end=watch_desktop), timeout=TIMEOUT_SECONDS
            )
        except Exception:
            # Any failure while the desktop is gone is a disconnect, not a bug.
            if lost["value"] or not await asyncio.to_thread(_reachable, url):
                history = getattr(agent, "history", None)
                raise _disconnected(url, history, _steps(history)) from None
            raise
        if lost["value"]:
            raise _disconnected(url, history, history.number_of_steps())

        urls = [u for u in history.urls() if u]
        lines = [
            f"Browsing {'completed' if history.is_done() else 'stopped before finishing'} "
            f"after {history.number_of_steps()} steps.",
            f"Final URL: {urls[-1] if urls else 'unknown'}",
            f"Final Result: {history.final_result()}",
        ]
        errors = [e for e in history.errors() if e]
        if errors:
            lines.append(f"Step errors: {errors[-3:]}")
        if fields:
            lines.append(f"Personal fields available: {', '.join(sorted(fields))}")
        return "\n".join(lines)
    finally:
        # Disconnect only. stop() honours keep_alive; kill() would force-close
        # the user's browser. Bounded, since the desktop may already be gone.
        try:
            await asyncio.wait_for(session.stop(), timeout=DISCONNECT_TIMEOUT_SECONDS)
        except Exception:
            pass


def _steps(history) -> int:
    try:
        return history.number_of_steps()
    except Exception:
        return 0
