"""Watchers over GitHub's Deployments API.

GitHub Pages records a deployment in the `github-pages` environment for the
commit it published, whether Actions deployed it (`actions/deploy-pages`) or
Pages built it from a branch. So does any workflow job that names an
`environment:`, which is how a Fly.io, Cloud Run or Render deploy run from
GitHub Actions shows up. One watcher therefore covers all of them: find the
deployment of the commit in the environment, and read its latest status.

The deploy itself runs in the target repository's CI, so the graph triggers
nothing and never holds a hosting credential: the only token it uses here is
the one it already reads PRs with.
"""
import json
from typing import Any, Optional, Tuple
from urllib.parse import quote

from core.git import ops as git_ops
from core.git.identity import subprocess_env
from graphs.coding.utils.deploy.deploy_types import (
    FAILED,
    LIVE,
    PENDING,
    DeployStatus,
    DeployTarget,
)

# GitHub's deployment status states, mapped to ours. Anything else (`queued`,
# `in_progress`, `pending`, …) is still on its way.
_LIVE_STATES = ("success",)
_FAILED_STATES = ("failure", "error")
# GitHub retires a successful deployment once a newer one in the same
# environment succeeds: the commit was live, and something newer is now.
_SUPERSEDED_STATES = ("inactive",)


async def gh_api(path: str, push_identity: Any = None) -> Tuple[Optional[Any], str]:
    """GETs `path` from the GitHub API through `gh`. Returns (parsed JSON, error)."""
    code, out, err = await git_ops.run_cmd_async(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        cwd=".", timeout=20.0, env=subprocess_env(push_identity) or None,
    )
    if code != 0:
        return None, (err or out or f"gh api exited {code}").strip()[:300]
    try:
        return json.loads(out or "null"), ""
    except ValueError as e:
        return None, f"unreadable answer from the GitHub API ({e})"


async def branch_head(repo: str, branch: str, push_identity: Any = None) -> Tuple[str, str]:
    """The commit `branch` points at on GitHub. Returns (sha, error)."""
    data, error = await gh_api(f"repos/{repo}/commits/{quote(branch, safe='')}", push_identity)
    if error:
        return "", error
    sha = data.get("sha") if isinstance(data, dict) else None
    return (str(sha), "") if sha else ("", f"GitHub named no commit for `{branch}`")


class GitHubDeploymentWatcher:
    """Any deploy that reports to a GitHub deployment environment.

    `deploy.environment` names the environment the deploy job reports to — for
    a Fly.io deploy from Actions, the job's `environment:` (e.g. `production`).
    """
    name = "github_deployment"
    default_environment = ""

    def environment(self, config) -> str:
        return str((config or {}).get("environment") or self.default_environment or "").strip()

    def fallback_url(self, target: DeployTarget) -> str:
        return str(target.config.get("url") or "")

    def label(self, config) -> str:
        return f"the `{self.environment(config) or '?'}` environment"

    async def advance(self, target: DeployTarget) -> DeployStatus:
        environment = self.environment(target.config)
        if not environment:
            return DeployStatus(
                FAILED,
                detail="`deploy.environment` is not set: name the GitHub environment "
                       "the deploy job reports to (e.g. `production`).",
            )
        sha7 = target.sha[:7]

        deployments, error = await gh_api(
            f"repos/{target.repo}/deployments?sha={target.sha}"
            f"&environment={quote(environment, safe='')}&per_page=5",
            target.push_identity,
        )
        if error:
            return DeployStatus(PENDING, infra_error=f"could not list `{environment}` deployments: {error}")
        if not isinstance(deployments, list) or not deployments:
            return DeployStatus(PENDING, detail=f"no `{environment}` deployment of `{sha7}` yet")

        # Newest first: a re-run of the deploy is a new deployment of the same commit.
        deployment = deployments[0]
        statuses, error = await gh_api(
            f"repos/{target.repo}/deployments/{deployment.get('id')}/statuses?per_page=1",
            target.push_identity,
        )
        if error:
            return DeployStatus(PENDING, infra_error=f"could not read the deployment's status: {error}")
        if not isinstance(statuses, list) or not statuses:
            return DeployStatus(PENDING, detail=f"the `{environment}` deployment of `{sha7}` hasn't reported yet")

        status = statuses[0]
        state = str(status.get("state") or "").lower()
        url = str(status.get("environment_url") or "") or self.fallback_url(target)
        log_url = str(status.get("log_url") or status.get("target_url") or "")
        if state in _LIVE_STATES:
            return DeployStatus(LIVE, url=url, log_url=log_url)
        if state in _SUPERSEDED_STATES:
            return DeployStatus(LIVE, url=url, log_url=log_url,
                                detail="since superseded by a newer deployment")
        if state in _FAILED_STATES:
            return DeployStatus(FAILED, url=url, log_url=log_url,
                                detail=str(status.get("description") or f"the deployment reported `{state}`"))
        return DeployStatus(PENDING, url=url, log_url=log_url,
                            detail=f"the `{environment}` deployment of `{sha7}` is {state or 'starting'}")


class GitHubPagesWatcher(GitHubDeploymentWatcher):
    """GitHub Pages: deployed by Actions or built from a branch, both report to `github-pages`.

    Pages only deploys what the repository tells it to — a workflow on pushes
    to the default branch, with Pages' source set to "GitHub Actions" (or a
    branch source). This watcher only reports what happened.
    """
    name = "github_pages"
    default_environment = "github-pages"

    def label(self, config) -> str:
        return "GitHub Pages"

    def fallback_url(self, target: DeployTarget) -> str:
        configured = target.config.get("url")
        if configured:
            return str(configured)
        owner, _, name = target.repo.partition("/")
        site = f"{owner.lower()}.github.io"
        return f"https://{site}/" if name.lower() == site else f"https://{site}/{name}/"
