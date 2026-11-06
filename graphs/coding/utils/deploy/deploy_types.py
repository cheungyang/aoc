"""What a deployer is, independent of where it deploys.

Deployment belongs to a project, not to a task: it follows the default branch,
and the coding graph's `deploy` node asks a deployer, once per tick, where the
commit that should be live has got to. A deployer never waits — a pending
answer is asked again on the next tick — so a slow build cannot hold a tick.

Two kinds of deployer fit this one interface:

- **Watchers** observe a deploy that something else runs. GitHub Pages, and any
  GitHub Actions job that names an `environment:` (which is how a Fly.io,
  Cloud Run or Render deploy driven by CI shows up), report to GitHub's
  Deployments API. The graph triggers nothing and holds no hosting credentials.
- **Drivers** run the deploy themselves (a future `flyctl deploy`). The first
  call starts it and returns a `handle`; the node stores the handle and passes
  it back on every later call, which is what makes starting idempotent per
  commit.

Outcomes are kept apart the way verification keeps them apart: a deploy that
failed is `failed`; not being able to find out (GitHub unreachable) is
`infra_error`, which says nothing about the deploy and is simply asked again.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

PENDING = "pending"
LIVE = "live"
FAILED = "failed"
STATES = (PENDING, LIVE, FAILED)


@dataclass(frozen=True)
class DeployTarget:
    """The commit that should be live, and what a deployer needs to find it."""
    repo: str                                              # owner/repo on GitHub
    sha: str                                               # the commit that should go live
    branch: str                                            # the default branch it is on
    config: Dict[str, Any] = field(default_factory=dict)   # the manifest's `deploy` block
    handle: str = ""                                       # what a driver returned when it started this deploy
    push_identity: Any = None                              # credentials for GitHub API calls


@dataclass
class DeployStatus:
    state: str                       # pending | live | failed
    url: str = ""                    # where it can be opened, once known
    detail: str = ""                 # why it is pending or failed, in words
    log_url: str = ""                # where to look: the workflow run, the deployment log
    handle: str = ""                 # a driver's id for the deploy it started
    # Set when the deployer could not find out. Says nothing about the deploy.
    infra_error: Optional[str] = None


@runtime_checkable
class Deployer(Protocol):
    """Moves `target.sha` towards live, if it drives the deploy, and says where it is.

    Must be idempotent per commit and must never raise for an ordinary failure:
    a deploy that failed is a `failed` status, and a deployer that couldn't tell
    sets `infra_error`. A traceback would abort the tick instead of recording
    one failed deployment.
    """
    name: str

    async def advance(self, target: DeployTarget) -> DeployStatus:
        ...
