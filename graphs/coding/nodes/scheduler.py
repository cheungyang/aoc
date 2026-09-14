"""scheduler — the entry node of a tick.

One tick advances the system by as much as it safely can and then stops. This
node decides *what* to advance:

1. reclaim leases whose owner died (crash recovery),
2. preflight, before any token is spent,
3. pick one task — a review to sync, or the next runnable task,
4. claim it with a lease and make sure its worktree exists,
5. route by the task's recorded stage, so work already done is not redone.

Everything it writes goes through the manifest, because the manifest is the API:
hand-editing it and running another tick is a supported operation.
"""
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import get_runnable_tasks, resolve_manifest_path
from graphs.coding.utils.preflight import preflight_tick
from graphs.coding.utils.repo import ensure_repo_available, get_repo_descriptor
from graphs.coding.utils.shell import run_in_worktree
from core.util import git_ops

# Routes the scheduler can emit. `done` ends the tick.
ROUTE_IMPLEMENT = "implement"
ROUTE_PUBLISH = "publish"
ROUTE_SYNC = "sync_review"
ROUTE_DONE = "done"


def _branch_name(task: Dict[str, Any], run_id: str, project_name: str) -> str:
    feature = task.get("feature_name") or task.get("task_id") or "feat"
    clean_project = str(project_name).replace(" ", "_").replace("/", "_")
    clean_feature = str(feature).replace(" ", "_").replace("/", "_")
    return f"feat/{clean_project}/{clean_feature}_{run_id}"


def select_task(
    queue: List[Dict[str, Any]],
    handled: List[str],
    now: float
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Chooses the one task this tick will work on, and the route for it.

    Reviews come first: they are cheap, they unblock dependents, and a merge may
    make another task runnable within the same tick.
    """
    for task in queue:
        if task.get("task_id") in handled:
            continue
        if task.get("status") != "awaiting_review":
            continue
        if manifest_store.lease_is_active(task, now):
            continue
        # Outside its polling window the task is dormant: no GitHub call, no
        # Discord noise, until a nudge or a manual tick reopens the window.
        poll_until = task.get("poll_until")
        if poll_until is not None and float(poll_until) < now:
            continue
        return task, ROUTE_SYNC

    runnable = [t for t in get_runnable_tasks(queue, max_count=len(queue) or 1)
                if t.get("task_id") not in handled]
    if not runnable:
        return None, ROUTE_DONE

    task = runnable[0]
    stage = task.get("stage") or "queued"
    # Resume where the task got to. A task that already published just needs its
    # review synced; one that verified only needs publishing — no LLM either way.
    if stage in ("awaiting_review", "published"):
        return task, ROUTE_SYNC
    if stage in ("verified", "audited"):
        return task, ROUTE_PUBLISH
    return task, ROUTE_IMPLEMENT


async def scheduler_node(state: CodingState) -> Dict[str, Any]:
    now = time.time()
    # Which project this tick is for is an input, not a default. Reported rather
    # than raised: a caller that forgot it should see the sentence that tells
    # them what to pass, not a traceback in the channel.
    if not state.get("build_request_path"):
        return {
            "route": ROUTE_DONE,
            "current_task": None,
            "tick_report": list(state.get("tick_report") or []),
            "tick_handled": list(state.get("tick_handled") or []),
            "error_message": state.get("error_message") or (
                "Missing required build_request.json: the coding graph works on "
                "one project at a time."
            ),
        }

    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    owner = state.get("lease_owner") or f"tick_{uuid.uuid4().hex[:6]}"
    handled = list(state.get("tick_handled") or [])
    report = list(state.get("tick_report") or [])

    # 1. Crash recovery. A task whose owner died is requeued at its recorded
    #    stage, which is what stops "stuck in_progress forever".
    reclaimed = manifest_store.reclaim_expired_leases(manifest_path, now=now)
    for task_id in reclaimed:
        report.append(f"♻️ Reclaimed abandoned task `{task_id}` (lease expired).")

    manifest = manifest_store.load_manifest(manifest_path)
    queue = manifest.get("queue") or []
    project_name = state.get("project_name") or manifest.get("project_name") or "coding_project"
    repo_descriptor = get_repo_descriptor(manifest)
    if state.get("repo"):
        repo_descriptor = {**repo_descriptor, **dict(state["repo"])}

    def _idle(**extra: Any) -> Dict[str, Any]:
        base = {
            "build_request_path": manifest_path,
            "project_name": project_name,
            "repo": repo_descriptor,
            "queue": queue,
            "current_task": None,
            "route": ROUTE_DONE,
            "lease_owner": owner,
            "tick_handled": handled,
            "tick_report": report,
            "error_message": ""
        }
        base.update(extra)
        return base

    # 2. Respect the concurrency bound before picking anything up. Counting live
    #    leases is what makes the bound real across processes — a per-process
    #    counter would let two ticks each believe they were the only one.
    max_concurrency = int(
        state.get("max_concurrency") or manifest.get("max_concurrency") or 1
    )
    in_flight = [t for t in queue
                 if manifest_store.lease_is_active(t, now) and t.get("lease_owner") != owner]
    if len(in_flight) >= max_concurrency:
        return _idle(completed_tasks=[], failed_tasks=[])

    task, route = select_task(queue, handled, now)
    if task is None:
        return _idle(
            completed_tasks=[t["task_id"] for t in queue if t.get("status") == "done"],
            failed_tasks=[t["task_id"] for t in queue
                          if t.get("status") in ("failed", "halted", "blocked")]
        )

    # 3. Preflight once we know there is work. Running it earlier would make an
    #    empty queue cost a network round trip on every scheduled tick.
    ok, message, push_identity = await preflight_tick(
        repo_descriptor=repo_descriptor,
        graph_id=state.get("graph_id") or "coding",
        required_tools=state.get("required_tools") or {}
    )
    if not ok:
        report.append(f"⚠️ {message}")
        return _idle(error_message=message)

    # 4. Resolve the repository the mode asks for: this process's own checkout,
    #    a managed clone, or a repository created on the spot.
    repo_root, repo_error = await ensure_repo_available(repo_descriptor, push_identity)
    if repo_error:
        report.append(f"⚠️ {repo_error}")
        return _idle(error_message=repo_error)

    task_id = task["task_id"]

    # 5. Claim it. Losing the race simply means another tick owns it; this tick
    #    moves on rather than doing the work twice.
    if not manifest_store.acquire_lease(manifest_path, task_id, owner=owner, now=now):
        report.append(f"⏭️ `{task_id}` is claimed by another run; skipping.")
        handled.append(task_id)
        return _idle()

    handled.append(task_id)

    run_id = task.get("run_id") or f"run_{uuid.uuid4().hex[:4].upper()}"
    branch_name = task.get("branch_name") or _branch_name(task, run_id, project_name)
    workspace_path = os.path.join(repo_root, "workspaces", "runs", run_id)
    stage = task.get("stage") or "queued"

    # 6. Provision. Idempotent, and only for work that still needs a worktree —
    #    re-creating it under a task waiting on review would throw the code away.
    if route != ROUTE_SYNC and (
        not manifest_store.stage_at_or_past(task, "provisioned")
        or not os.path.exists(workspace_path)
    ):
        base_ref = _resolve_base_ref(task, queue, state, repo_descriptor)
        success, message = await git_ops.provision_worktree(
            repo_path=repo_root,
            workspace_path=workspace_path,
            branch_name=branch_name,
            base_ref=base_ref
        )
        if not success:
            manifest_store.persist_task(
                manifest_path, task_id,
                status="halted",
                last_error={"stage": "provision", "kind": "git", "message": message, "at": now},
                lease_owner=None, lease_expires_at=None
            )
            report.append(f"⚠️ `{task_id}` halted: {message}")
            return _idle(error_message=message)
        # Provisioning wipes the worktree, so any digest recorded against the old
        # one is stale; keeping it would skip an implement that must happen.
        # The same applies to setup: a fresh worktree has no `node_modules`.
        stage = "provisioned" if stage == "queued" else stage
        task = manifest_store.persist_task(
            manifest_path, task_id,
            status="active", stage=stage, run_id=run_id, branch_name=branch_name,
            setup_done=False
        ) or task
    elif route != ROUTE_SYNC:
        task = manifest_store.persist_task(
            manifest_path, task_id, status="active", run_id=run_id, branch_name=branch_name
        ) or task

    # 7. Set the worktree up once: scaffold, install dependencies. This is an
    #    environment concern, so a failure here halts as `environment` and never
    #    touches the implement budget — the code is not what went wrong.
    setup_command = task.get("setup_command") or manifest.get("setup_command")
    if route != ROUTE_SYNC and setup_command and not task.get("setup_done"):
        code, out, err = await run_in_worktree(str(setup_command), cwd=workspace_path)
        if code != 0:
            detail = (err or out or "").strip()[-800:]
            message = f"Setup command failed (exit {code}): {setup_command}\n{detail}"
            manifest_store.persist_task(
                manifest_path, task_id,
                status="halted",
                last_error={"stage": "setup", "kind": "environment",
                            "message": message, "at": now},
                lease_owner=None, lease_expires_at=None
            )
            report.append(f"⚠️ `{task_id}` halted in setup: {detail.splitlines()[-1] if detail else code}")
            return _idle(error_message=message)
        task = manifest_store.persist_task(manifest_path, task_id, setup_done=True) or task

    updated_task = dict(task)
    updated_task.update({"run_id": run_id, "branch_name": branch_name, "stage": stage})

    return {
        "build_request_path": manifest_path,
        "project_name": project_name,
        "repo": repo_descriptor,
        "repo_root": repo_root,
        "queue": queue,
        "current_task": updated_task,
        "route": route,
        "stage": stage,
        "run_id": run_id,
        "branch_name": branch_name,
        "workspace_path": workspace_path,
        "spec_path": task.get("spec_path", ""),
        "pr_url": task.get("pr_url") or "",
        "lease_owner": owner,
        "tick_handled": handled,
        "tick_report": report,
        "error_message": ""
    }



def _resolve_base_ref(
    task: Dict[str, Any],
    queue: List[Dict[str, Any]],
    state: CodingState,
    repo_descriptor: Dict[str, Any]
) -> str:
    """Base branch for a new worktree.

    Dependents branch from the default branch, not from the prerequisite's
    branch: by the time a dependent runs, its prerequisite is merged and that
    branch is deleted, which used to fall back silently to HEAD (F3).
    """
    default_branch = repo_descriptor.get("default_branch") or "main"
    return state.get("base_branch") or state.get("base_ref") or f"origin/{default_branch}"
