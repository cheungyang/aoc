"""Picks the deployer a project's manifest asks for.

`deploy.backend` names it. A new destination is one class with an `advance()`
(utils/deploy/deploy_types.py) and one line in `BACKENDS`; the node, the
manifest state, the operator commands and the planner's scan don't change.

- `github_pages`: GitHub Pages, watched through GitHub's Deployments API.
- `github_deployment`: any deploy that reports to a GitHub environment
  (`deploy.environment`), e.g. Fly.io deployed by a GitHub Actions job.

An unknown name fails the deployment as a configuration error (and wakes the
planner) rather than doing nothing: a project that asked to be deployed and
quietly isn't would look exactly like one that is.
"""
from typing import Any, Dict, Optional

from graphs.coding.utils.deploy.deploy_types import Deployer
from graphs.coding.utils.deploy.github_deployments import (
    GitHubDeploymentWatcher,
    GitHubPagesWatcher,
)

BACKENDS = {
    GitHubPagesWatcher.name: GitHubPagesWatcher,
    GitHubDeploymentWatcher.name: GitHubDeploymentWatcher,
}
# A `deploy` block naming one of these (or none at all) means "don't deploy".
DISABLED = ("", "none")


class UnknownDeployBackendError(ValueError):
    pass


def deploy_backend(config: Optional[Dict[str, Any]]) -> str:
    """The backend a `deploy` block names, lower-cased; "" when there is none."""
    if not isinstance(config, dict):
        return ""
    return str(config.get("backend") or "").strip().lower()


def deploy_configured(config: Optional[Dict[str, Any]]) -> bool:
    """Whether the project asked to be deployed at all."""
    return deploy_backend(config) not in DISABLED


def get_deployer(config: Optional[Dict[str, Any]]) -> Deployer:
    backend = deploy_backend(config)
    cls = BACKENDS.get(backend)
    if cls is None:
        raise UnknownDeployBackendError(
            f"Unknown deploy backend `{backend}` (manifest `deploy.backend`). "
            f"Supported: {', '.join(sorted(BACKENDS))}."
        )
    return cls()


def deploy_label(config: Optional[Dict[str, Any]]) -> str:
    """Where a deploy goes, in words, for the tick report.

    A deployer may define `label(config)`; one that doesn't is named by its key.
    """
    backend = deploy_backend(config)
    cls = BACKENDS.get(backend)
    if cls is None:
        return f"`{backend}`"
    label = getattr(cls(), "label", None)
    return label(config) if callable(label) else backend
