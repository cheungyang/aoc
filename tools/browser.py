"""Visual, multi-step web interaction (bookings, reservations) via browser-use.

The browser is the "Agent Chrome" on the user's desktop, not one in this
container: the user watches it, types what the agent cannot (2FA codes,
choices), and makes the final click. See scripts/desktop_agent_chrome/.

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
                                allowed_domains (optional) restricts where the
                                agent may navigate while the fields are loaded.
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

from core.util import format_tool_response, DEFAULT_BROWSER_MODEL


DEFAULT_CDP_URL_FILE = "/app/browser_cdp_url"
DEFAULT_PERSONAL_INFO_FILE = "/app/browser_personal_info.json"
DEFAULT_MAX_STEPS = 25
MAX_MAX_STEPS = 60
# Wall clock for one task. Every step is a Gemini call plus a page render.
TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 4
START_HINT = (
    "Start it on the desktop with scripts/desktop_agent_chrome/start_agent_chrome.sh "
    "(or log in to the Mac so its launch agent starts it), then try again."
)
# Appended to browser-use's system prompt: the agent works in a window the
# user is watching, and hands back control instead of guessing or committing.
HANDOFF_RULES = (
    "You are operating a Chrome window on the user's own desktop while they may be watching. "
    "Work in the current tab or open a new one; never close other tabs or the window. "
    "STOP and report, without clicking, before any action that commits money or a booking "
    "(Reserve, Book, Confirm, Pay, Purchase, Place order) - the user clicks it. "
    "Also stop and say exactly what you need if you hit a login, 2FA/verification code, CAPTCHA, "
    "or a choice the task did not specify. Never invent personal details; only use the provided "
    "placeholders. Never enter payment card numbers."
)

_session_lock = threading.Lock()


class BrowserConfigError(Exception):
    """Setup problem the user has to fix; reported verbatim."""


def _read_cdp_url() -> str:
    path = os.environ.get("BROWSER_CDP_URL_FILE", "").strip() or DEFAULT_CDP_URL_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            url = handle.read().strip()
    except FileNotFoundError:
        raise BrowserConfigError(
            f"The desktop browser is not configured: {path} does not exist. "
            "See INSTALL.md, 'Agent browser (desktop Chrome)'."
        ) from None
    except OSError as exc:
        raise BrowserConfigError(f"Cannot read {path}: {exc}") from exc
    if not url:
        raise BrowserConfigError(f"{path} is empty; it should hold e.g. http://<desktop IP>:9223.")
    return url


def _with_ip_host(url: str) -> str:
    """`url` with its hostname replaced by an IP address.

    Chrome's DevTools server rejects any Host header that is not an IP literal
    or localhost (DNS-rebinding protection), so a name like mac.lan would fail
    with an opaque 500. Resolving here lets the config file use either.
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
    except OSError as exc:
        raise BrowserConfigError(f"Cannot resolve desktop browser host '{host}': {exc}") from exc
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _probe(url: str) -> None:
    """Fails fast, with a useful message, when Agent Chrome is not reachable."""
    try:
        response = requests.get(f"{url.rstrip('/')}/json/version", timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BrowserConfigError(
            f"Agent Chrome on the desktop is not reachable at {url} ({type(exc).__name__}). {START_HINT}"
        ) from None


def _personal_info():
    """(fields, allowed_domains) from the personal-info file; ({}, None) if absent."""
    path = os.environ.get("BROWSER_PERSONAL_INFO_FILE", "").strip() or DEFAULT_PERSONAL_INFO_FILE
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BrowserConfigError(f"Cannot read personal info file {path}: {exc}") from exc
    fields = data.get("fields") or {}
    if not isinstance(fields, dict) or not all(isinstance(v, str) for v in fields.values()):
        raise BrowserConfigError(f"{path}: 'fields' must map names to strings.")
    fields = {k: v for k, v in fields.items() if v.strip()}
    domains = data.get("allowed_domains") or None
    return fields, domains


def _endpoint() -> str:
    url = _with_ip_host(_read_cdp_url())
    _probe(url)
    return url


@tool
def browser(goal: str, max_steps: int = DEFAULT_MAX_STEPS) -> str:
    """
    Operate the user's desktop "Agent Chrome" to complete a multi-step web task, such as
    finding and staging a restaurant reservation or event seats. The user can watch the
    window. The agent stops before any final Reserve/Pay click, and when it needs a login,
    a verification code, or a choice. Tabs stay open, so a follow-up call can continue
    where the last one stopped.

    Args:
        goal: The specific objective, including site, date, time, party size and preferences
              (e.g., 'On resy.com find Balthazar NYC, Fri Oct 24, 2 guests, 7-8pm; select the best
              slot and fill contact details, then stop on the confirm page').
              To continue after the user helped, describe the next step on the current page.
        max_steps: Maximum browser actions before giving up (default 25, max 60).
    """
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
    except BrowserConfigError as e:
        return format_tool_response("browser", payload="", errors=f"Error: {e}")
    except asyncio.TimeoutError:
        return format_tool_response(
            "browser", payload="",
            errors=f"Error performing browser action: timed out after {TIMEOUT_SECONDS}s. "
                   "The tab is still open on the desktop.",
        )
    except Exception as e:
        return format_tool_response("browser", payload="", errors=f"Error performing browser action: {e}")
    finally:
        _session_lock.release()


async def _run_browser(goal: str, max_steps: int) -> str:
    url = _endpoint()
    fields, allowed_domains = _personal_info()
    profile_kwargs = {"cdp_url": url, "is_local": False, "keep_alive": True}
    if allowed_domains:
        profile_kwargs["allowed_domains"] = allowed_domains
    session = BrowserSession(browser_profile=BrowserProfile(**profile_kwargs))
    try:
        # browser-use requires a multimodal visual LLM - using Gemini as native fallback
        llm = ChatGoogle(model=DEFAULT_BROWSER_MODEL)
        agent = Agent(
            task=goal,
            llm=llm,
            browser=session,
            sensitive_data=fields or None,
            extend_system_message=HANDOFF_RULES,
        )
        history = await asyncio.wait_for(agent.run(max_steps=max_steps), timeout=TIMEOUT_SECONDS)
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
        # the user's browser.
        try:
            await session.stop()
        except Exception:
            pass
