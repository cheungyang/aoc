"""device_list: the Home Assistant devices home-steward may control, kept in the user's PKM.

The rules, with examples: agents/home-steward/WORKFLOW.md.
"""
import os
import stat
import tempfile
from typing import Callable, Optional

from agents.home_steward.lib.homeassistant.client import HomeAssistantError
from agents.home_steward.lib.homeassistant.entity_index import fetch, parse_file, render, render_file
from core.tooling import Report, agent_tool, get_local_now, pkm_path, require_permission

DEVICES_FILE = ("wallet", "home", "DEVICES.md")
WRITE_ACTION = "write"
# Reading the saved list checks nothing, and the one checked action writes, so the
# read-only replay (scripts/voice_dryrun.py) refuses it.
READ_ACTIONS = ()
# Listed only when home_assistant would let the agent do this to the entity.
CONTROL_ACTION = "call_service"
HOME_ASSISTANT = "Home Assistant"


@agent_tool
def device_list(report: Report, refresh: bool = False) -> dict:
    """Your device list: the Home Assistant devices you may control, by area, with their entity ids.

    Reads: pkm/wallet/home/DEVICES.md, which it writes; Home Assistant on a refresh.
    Computes: on a refresh, the live lights, switches, climate, fans and media players you may control, and their areas.
    Leaves out: states (get_state reads them live), and which device the user means.

    Returns `refreshed` and `devices`: one line per area, each entity id with its name in quotes
    when the id doesn't say it. Act on these ids directly; search_registry finds anything not listed.

    Args:
        refresh: Fetch the list from Home Assistant again and save it (the weekly inventory does).
    """
    path = pkm_path(*DEVICES_FILE)
    text = None if refresh else _read(path)
    if text is None:
        text = _refresh(report, path)
    saved = parse_file(text)
    report.read(path, records=len(saved.entries), skipped=len(saved.unrecognized))
    if saved.refreshed is None:
        report.problem(path, "no Refreshed line, so when the list was fetched is unknown")
    report.unparsed(path, saved.unrecognized)
    return {"refreshed": saved.refreshed, "devices": render(saved.entries).splitlines()}


def _refresh(report: Report, path: str) -> str:
    """Fetches the devices, keeps those the agent may control, and saves them; returns the file's text.

    The write is checked first, so a refused one asks Home Assistant nothing. A
    fetch that fails leaves the file as it was.
    """
    require_permission(WRITE_ACTION, path)
    try:
        fetched = fetch()
    except Exception as e:  # noqa: BLE001 - Home Assistant down or refusing: reported, the file untouched
        report.missing(HOME_ASSISTANT, _fetch_failed(e, saved=os.path.exists(path)))
        report.raise_if_failed()  # always raises: the issue is required
    may_control = _may_control()
    kept = [entry for entry in fetched if may_control(entry.entity_id)]
    report.read(HOME_ASSISTANT, records=len(fetched))
    report.trace(f"{len(kept)} of the {len(fetched)} live, user-facing devices are yours to control ({CONTROL_ACTION})")
    text = render_file(kept, get_local_now())
    _write_atomic(path, text)
    return text


def _fetch_failed(error: Exception, saved: bool) -> str:
    """The issue for a fetch that failed: the error, then what there is to go on."""
    text = str(error).strip().rstrip(".")
    if not isinstance(error, HomeAssistantError):
        text = f"{type(error).__name__}: {text}" if text else type(error).__name__
    if saved:
        return f"{text}. The device list was left as it was."
    return f"{text}. There is no device list yet: look devices up with search_registry."


def _may_control() -> Callable[[str], bool]:
    """Whether the agent may control an entity: its home_assistant grant allows call_service on it.

    A grant with no call_service anywhere, a blanket one ({}) or a list keeps
    nothing: home_assistant refuses writes under a blanket grant.
    """
    # The one call outside core.tooling: has_permission asks about the tool being run,
    # and this asks about home_assistant's grant. probe=True marks it as a question, not
    # a write, so the read-only replay lets it through.
    from core.loaders.tools_loader import ToolsLoader
    from core.runtime.execution_context import try_context

    loader, ctx = ToolsLoader(), try_context()
    grant = loader.get_tool_permissions(ctx, "home_assistant")
    if not isinstance(grant, dict) or not any(CONTROL_ACTION in actions for actions in grant.values()):
        return lambda entity_id: False
    return lambda entity_id: loader.check_permission(ctx, "home_assistant", CONTROL_ACTION, entity_id, probe=True)


def _read(path: str) -> Optional[str]:
    """The saved list's text, or None when there is none yet."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write_atomic(path: str, text: str) -> None:
    """Replaces the file in one step (temp file, then os.replace), creating its folder.

    Writes through a symlinked file to its target and keeps the old mode; a new
    file gets 644 (mkstemp alone would leave a private 600 file).
    """
    target = os.path.realpath(path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.basename(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
