"""Which host is ticking, and whether this one is allowed to.

The coding graph's durable state is the manifest (in the pkm repo, synced by
git), the task branches on GitHub, and nothing else. That is what lets the
pipeline move between hosts — the NAS in production, a dev box when you want to
watch it — but only one host may tick at a time: two hosts writing the same
manifest would each commit their own copy and the hourly pkm sync would keep
one of them, silently dropping the other's progress.

*Which* host runs the scheduled tick is already decided elsewhere: the tick is a
`script-executor` schedule, and the schedule runner only runs an agent's
schedules on the host that owns it (`core/channel/discord/ownership.py`,
switched with `[claim script-executor]` / `[release]`). What ownership cannot do
is carry state across the switch — the old host's last manifest writes and
unpushed work must reach GitHub and pkm before the new host's first tick. The
switches here bridge that gap:

- The pause file (`sessions/coding_tick.paused`) is the operational one that
  `coding_admin handoff` sets and `resume` clears. `sessions/` is gitignored, so
  pausing one host never pauses the other. It also stops manual
  `coding_tick.py` runs, which ownership does not gate.
- `CODING_TICK_ENABLED` (environment) is the hard, per-deployment setting. Set it
  to `0` on a host that must never tick.

Leases carry the host name (`nas:tick_ab12cd`), so `status` says *where* a task
is being worked, and a handoff can tell its own in-flight ticks from another
host's.
"""
import os
import socket
import time
import uuid
from typing import Optional, Tuple

TICK_ENABLED_ENV = "CODING_TICK_ENABLED"
HOST_NAME_ENV = "CODING_HOST_NAME"
PAUSE_FILE_ENV = "CODING_TICK_PAUSE_FILE"

_FALSY = ("0", "false", "no", "off")


def host_name() -> str:
    """This host's name as it appears in leases. `CODING_HOST_NAME` overrides it.

    The override exists because a container's hostname is its random id, which
    says nothing to a human reading `status`.
    """
    configured = (os.environ.get(HOST_NAME_ENV) or "").strip()
    name = configured or socket.gethostname().split(".")[0] or "host"
    # `:` separates the host from the tick id in a lease owner.
    return name.replace(":", "_")


def new_lease_owner() -> str:
    return f"{host_name()}:tick_{uuid.uuid4().hex[:6]}"


def owner_host(owner: Optional[str]) -> Optional[str]:
    """The host part of a lease owner, or None for a legacy owner without one."""
    if not owner or ":" not in owner:
        return None
    return owner.split(":", 1)[0]


def is_this_host(owner: Optional[str]) -> bool:
    """Whether a lease was taken here. A legacy owner (no host) counts as *maybe*,
    i.e. True, because guessing "not mine" would let a handoff leave a live tick behind."""
    host = owner_host(owner)
    return host is None or host == host_name()


def pause_file_path() -> str:
    configured = (os.environ.get(PAUSE_FILE_ENV) or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    from graphs.coding.utils.repo import project_root
    return os.path.join(project_root(), "sessions", "coding_tick.paused")


def tick_enabled() -> Tuple[bool, str]:
    """(enabled, reason). The reason is empty when enabled."""
    if (os.environ.get(TICK_ENABLED_ENV) or "").strip().lower() in _FALSY:
        return False, f"{TICK_ENABLED_ENV} is off on this host."
    path = pause_file_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                note = f.read().strip()
        except OSError:
            note = ""
        return False, f"Ticking is paused on this host ({note or path})."
    return True, ""


def pause(reason: str) -> str:
    """Stops this host from starting new ticks. Idempotent. Returns the file path."""
    path = pause_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{reason} — {stamp} on {host_name()}\n")
    return path


def unpause() -> bool:
    """Lets this host tick again. Returns whether it was paused."""
    path = pause_file_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def pkm_dir() -> str:
    """The pkm checkout the manifests live in (`pkm/wiki/software/...`)."""
    from graphs.coding.utils.dag import SOFTWARE_ROOT
    from graphs.coding.utils.repo import project_root

    local = os.path.realpath(os.path.join(project_root(), SOFTWARE_ROOT.split("/")[0]))
    if os.path.isdir(os.path.join(local, ".git")):
        return local
    from core.util.config import Config
    return os.path.abspath(os.path.expanduser(Config().pkm_dir))


def sync_pkm() -> Tuple[bool, str]:
    """Commits, pulls and pushes the pkm repo now, instead of at the hourly sync.

    A handoff is only as good as the manifest the other host reads, and the
    scheduled sync could be up to an hour away.
    """
    from core.util.git_sync import sync_all

    try:
        result = sync_all(pkm_dir=pkm_dir(), skip_codebase=True)
    except Exception as e:
        return False, f"pkm sync failed: {e}"
    if result.get("success"):
        return True, "pkm synced."
    return False, "pkm sync failed: " + "; ".join(result.get("errors") or ["unknown error"])
