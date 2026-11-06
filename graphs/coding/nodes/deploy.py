"""deploy — puts what was merged where you can open it.

Deployment belongs to the project, not to a task. It follows the default
branch: one deployment can carry several merges, a failed deploy doesn't hold
back dependent tasks (they wait for `done`, which means merged), and its state
lives at the manifest's top level (`deployment`), beside the `deploy` block
that says where it goes. Where is the backend's business (`deploy.backend`,
utils/deploy): `github_pages` today, `github_deployment` for any deploy that
reports to a GitHub environment (e.g. Fly.io from Actions), and a driver that
runs the deploy itself fits the same interface.

`sync_review` requests a deployment of the merge commit; this node advances it.
Like every node here it never waits: it asks the backend where the commit has
got to, records the answer and hands back to the scheduler. A pending deploy is
asked again on the next tick (`has_work` keeps ticks coming while one is
pending), and only changes are reported — a build still running every five
minutes is not news. A commit that hasn't gone live within
`deploy.timeout_minutes` counts as failed, so a deploy workflow that never runs
can't keep ticks coming forever, and a failure wakes the planner
(`deploy_failed` in its coding_attention scan).

No LLM, no push: nothing this node does can change the code.
"""
import time
from typing import Any, Dict, Optional

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.deploy import (
    FAILED,
    LIVE,
    DeployTarget,
    UnknownDeployBackendError,
    deploy_configured,
    deploy_label,
    get_deployer,
)
from graphs.coding.utils.deploy.github_deployments import branch_head
from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor

# Long enough for a Pages build behind a busy Actions queue; short enough that a
# workflow which never runs is reported the same afternoon.
DEFAULT_TIMEOUT_MINUTES = 30
ROUTE_SCHEDULER = "scheduler"


async def deploy_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    report = list(state.get("tick_report") or [])
    # Set whatever happens below: the scheduler routes a pending deployment
    # here only while this is unset, so one tick asks the backend once.
    result: Dict[str, Any] = {
        "deploy_checked": True,
        "route": ROUTE_SCHEDULER,
        "tick_report": report,
        "error_message": "",
    }

    manifest = manifest_store.load_manifest(manifest_path)
    config = manifest.get("deploy") or {}
    deployment = dict(manifest.get("deployment") or {})
    if not deploy_configured(config) or deployment.get("status") != "pending":
        return result

    project = manifest.get("project_name") or "project"
    now = time.time()
    try:
        deployer = get_deployer(config)
    except UnknownDeployBackendError as e:
        _fail(manifest_path, report, project, None, "config", str(e), now)
        return result

    descriptor = get_repo_descriptor(manifest)
    slug = descriptor.get("slug") or ""
    branch = descriptor.get("default_branch") or "main"
    if not slug:
        _fail(manifest_path, report, project, None, "config",
              "the manifest names no `repo.slug`, so there is nothing to deploy from.", now)
        return result
    identity = get_push_identity(manifest)

    minutes = _timeout_minutes(config)
    timed_out = now - float(deployment.get("requested_at") or now) > minutes * 60
    checks = int(deployment.get("checks") or 0) + 1

    sha = deployment.get("target_sha") or ""
    if not sha:
        # No commit named (an operator's `redeploy`, or a merge whose commit
        # GitHub didn't report): deploy whatever the branch points at now.
        sha, error = await branch_head(slug, branch, identity)
        if not sha:
            if timed_out:
                _fail(manifest_path, report, project, None, "infra",
                      f"could not find what `{branch}` points at for {minutes} minutes ({error}).", now)
            else:
                manifest_store.persist_deployment(manifest_path, checks=checks, detail=error or None)
            return result

    status = await deployer.advance(DeployTarget(
        repo=slug, sha=sha, branch=branch, config=dict(config),
        handle=str(deployment.get("handle") or ""), push_identity=identity,
    ))
    sha7 = sha[:7]
    handle = status.handle or deployment.get("handle") or None

    if status.state == LIVE:
        url = status.url or deployment.get("url") or ""
        stored = manifest_store.persist_deployment(
            manifest_path, expect_target=sha,
            status="live", target_sha=sha, deployed_sha=sha, url=url or None,
            log_url=status.log_url or None, detail=status.detail or None, handle=handle,
            checks=checks, live_at=now, last_error=None,
        )
        if stored is not None:
            report.append(f"🌐 `{project}` is live at `{sha7}`" + (f": {url}" if url else "."))
        return result

    if status.state == FAILED:
        _fail(manifest_path, report, project, sha, "deploy",
              f"deploying `{sha7}` to {deploy_label(config)} failed: {status.detail or 'no reason given'}.",
              now, log_url=status.log_url or None, handle=handle, checks=checks)
        return result

    if timed_out:
        why = status.infra_error or status.detail or "no answer"
        _fail(manifest_path, report, project, sha, "infra" if status.infra_error else "deploy",
              f"`{sha7}` did not go live on {deploy_label(config)} within {minutes} minutes ({why}).",
              now, log_url=status.log_url or deployment.get("log_url"), handle=handle, checks=checks)
        return result

    stored = manifest_store.persist_deployment(
        manifest_path, expect_target=sha,
        target_sha=sha, checks=checks, handle=handle,
        detail=status.infra_error or status.detail or None,
        log_url=status.log_url or deployment.get("log_url"),
    )
    if stored is not None and checks == 1:
        report.append(f"🚚 `{project}`: deploying `{sha7}` to {deploy_label(config)}.")
    return result


def _fail(
    manifest_path: str,
    report: list,
    project: str,
    sha: Optional[str],
    kind: str,
    message: str,
    now: float,
    **fields: Any
) -> None:
    """Records a failed deployment; the planner's scan picks it up as `deploy_failed`.

    It stays failed until the next merge or an operator's `redeploy` asks again:
    retrying a deploy that failed would fail the same way.
    """
    stored = manifest_store.persist_deployment(
        manifest_path, expect_target=sha,
        status="failed",
        **({"target_sha": sha} if sha else {}),
        last_error={"stage": "deploy", "kind": kind, "message": message[:500], "at": now},
        **fields,
    )
    if stored is None:
        return
    log_url = fields.get("log_url")
    report.append(f"⚠️ `{project}`: {message}" + (f" {log_url}" if log_url else ""))


def _timeout_minutes(config: Dict[str, Any]) -> float:
    try:
        minutes = float(config.get("timeout_minutes") or DEFAULT_TIMEOUT_MINUTES)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_MINUTES
    return minutes if minutes > 0 else DEFAULT_TIMEOUT_MINUTES
