from typing import TypedDict, List, Dict, Any, Optional, Literal
from typing_extensions import TypedDict as ExtTypedDict
from langchain_core.messages import AnyMessage

# Manifest v3 vocabulary. The legacy names are still accepted on read and mapped
# by utils/manifest.migrate_manifest — nothing writes them any more, but a
# manifest on disk may predate the migration.
TaskStatusV3 = Literal["queued", "active", "awaiting_review", "done", "failed", "halted", "blocked"]
TaskStatusLegacy = Literal["pending", "in_progress", "in_review", "completed", "rejected"]
TaskStatus = Literal[
    "queued", "active", "awaiting_review", "done", "failed", "halted", "blocked",
    "pending", "in_progress", "in_review", "completed", "rejected"
]

# Where a task got to. Every stage is the output of exactly one node, so a tick can
# resume at the recorded stage instead of restarting the task — this is what makes
# "never redo the LLM part" true after an infrastructure failure.
TaskStage = Literal[
    "queued",           # nothing done yet
    "provisioned",      # worktree exists on the right branch
    "implemented",      # code written in the worktree (uncommitted, local only)
    "pushed",           # committed and pushed to the task branch; head_sha recorded
    "verified",         # verification_command passed on head_sha
    "audited",          # advisory review done
    "published",        # committed, pushed, PR open
    "awaiting_review",  # waiting on a human decision on GitHub
    "merged",           # PR merged
    "done"              # merged, worktree torn down, manifest updated
]

STAGE_ORDER: List[str] = [
    "queued", "provisioned", "implemented", "pushed", "verified", "audited",
    "published", "awaiting_review", "merged", "done"
]


class TaskError(TypedDict, total=False):
    """The last failure, classified so retries can target the right stage."""
    stage: str
    # "llm" | "verification" | "git" | "github" | "config" | "privacy" | "unknown".
    # Infrastructure failures must not consume the LLM budget, so the manifest
    # counts attempts per stage rather than once per task. "privacy" is a
    # push the privacy guard stopped: nothing was pushed.
    kind: str
    message: str
    at: float


class TickError(TypedDict, total=False):
    """The manifest's top-level `tick_error`: why ticks stop before claiming any task.

    Written by the scheduler (utils/manifest.record_tick_error) when preflight
    or the repository fails, and removed once a tick gets past both. Only a
    change of stage or message rewrites it, so `at` is when the failure started.
    """
    stage: str                       # "preflight" | "repo"
    message: str
    at: float


class PlannerFixes(TypedDict, total=False):
    """A task's `planner_fixes`: software-planner's record of re-queuing it after a halt.

    Written by software-planner, never by the graph, in the same edit that
    re-queues the task. Its coding_attention scan counts it against
    `max_fixes_per_task`, so a task can't bounce between planner and tick forever.
    """
    count: int
    log: List[str]                   # one dated line per fix


class TaskEnvelope(TypedDict, total=False):
    task_id: str
    project_name: str
    feature_name: str
    spec_path: str
    dependencies: List[str]          # Prerequisite task_ids
    allowed_files: List[str]         # Strict filesystem whitelist
    # Run once per worktree, before any code is written: scaffolding and
    # dependency install. Keeping it out of `verification_command` means an
    # install failure is an environment fault, not a failed test, and is not
    # re-run on every verify. Falls back to the manifest-level `setup_command`.
    setup_command: Optional[str]
    setup_done: bool                 # Reset whenever the worktree is re-created
    # Overrides the manifest's `environment` block (the verify backend) for this
    # task, e.g. {"backend": "local"} for a bootstrap task that scaffolds.
    environment: Optional[Dict[str, Any]]
    verification_command: str        # CLI test command
    acceptance_criteria: str         # Given-When-Then criteria
    status: TaskStatus
    run_id: Optional[str]
    branch_name: Optional[str]
    target_repo: Optional[str]
    pr_url: Optional[str]
    commit_url: Optional[str]
    error_message: Optional[str]

    # --- v3 durable model -------------------------------------------------
    stage: TaskStage
    # Per stage, e.g. {"implement": 2, "publish": 1}: a flaky `gh` no longer
    # burns the LLM budget, and a genuinely failing test still stops at 3.
    attempts: Dict[str, int]
    lease_owner: Optional[str]
    lease_expires_at: Optional[float]
    # Digest of the *uncommitted* worktree after `implement`. Only meaningful
    # between implement and push, on the machine that ran implement: once the
    # work is committed the diff is empty, so every committed tree would share
    # one digest. Past `pushed`, identity is the commit SHA instead.
    impl_digest: Optional[str]
    verified_digest: Optional[str]   # legacy (pre-SHA); read by nothing new
    # The commit on the task branch that the later stages vouch for. A SHA is
    # unique per attempt, identical on every host and on GitHub, and is exactly
    # what a remote runner fetches, so "already verified" means
    # verified_sha == head_sha, not "the local worktree looks the same".
    head_sha: Optional[str]
    verified_sha: Optional[str]
    audited_sha: Optional[str]
    # Highest review comment id already actioned, so a resume does not re-read
    # the whole thread.
    review_cursor: Optional[str]
    # Reviewer comments harvested but not yet acted on. Durable so feedback
    # survives a hand-off through the scheduler (e.g. re-provisioning the
    # worktree on another host); cleared once implement consumes it.
    review_feedback: Optional[List[str]]
    # While now < poll_until the scheduled tick keeps checking GitHub for this
    # task; otherwise the tick exits without touching the network.
    poll_until: Optional[float]
    last_error: Optional[TaskError]
    # software-planner's fixes after halts. The graph neither writes nor clears it.
    planner_fixes: Optional[PlannerFixes]
    updated_at: float


class RepoDescriptor(TypedDict, total=False):
    """The repository the graph works against, declared in the manifest."""
    mode: Literal["existing", "create"]   # an older manifest's "self" reads as "existing" (utils/repo.py)
    slug: Optional[str]              # owner/repo on GitHub
    default_branch: str
    visibility: Literal["private", "public"]
    push_identity: Optional[str]     # GitHub login of the machine user, if any
    push_identity_email: Optional[str]


class DeployConfig(TypedDict, total=False):
    """The manifest's `deploy` block: where the project's default branch goes live.

    Written by the planner or by hand; read by the deploy node through
    utils/deploy (deploy_factory.BACKENDS).
    """
    backend: str                     # "github_pages" | "github_deployment" | "none"
    environment: str                 # GitHub environment to watch (github_pages: "github-pages")
    url: str                         # shown when the deployment reports no URL of its own
    timeout_minutes: int             # how long a commit may take to go live before it counts as failed


class DeploymentState(TypedDict, total=False):
    """The manifest's top-level `deployment`: where the default branch has got to.

    Written only by the graph (utils/manifest.request_deploy and
    persist_deployment). One per project: deployment follows the branch, not a
    task, so a merged task is `done` whatever its deploy does.
    """
    status: Literal["pending", "live", "failed"]
    backend: str
    target_sha: Optional[str]        # the commit that should go live; None: the branch's head
    requested_at: float              # when it was asked for; the timeout counts from here
    checks: int                      # how many ticks have asked the backend about it
    handle: Optional[str]            # a driver's id for the deploy it started (watchers: none)
    detail: Optional[str]            # the backend's latest word on it
    deployed_sha: Optional[str]      # the last commit confirmed live
    url: Optional[str]               # where that commit can be opened
    log_url: Optional[str]           # where to look: the workflow run, the deployment log
    live_at: Optional[float]
    last_error: Optional[TaskError]  # stage "deploy"; kind "deploy" | "infra" | "config"
    updated_at: float


class CodingState(TypedDict, total=False):
    # 1. Queue & Manifest Context
    build_request_path: str
    project_name: str
    target_repo: str
    repo: RepoDescriptor
    max_concurrency: int
    queue: List[TaskEnvelope]
    completed_tasks: List[str]
    failed_tasks: List[str]

    # 2. Active Run Context
    run_id: str
    thread_id: str
    session_id: str
    channel: str
    current_task: TaskEnvelope
    project_path: str                # Spec directory: pkm/wiki/software/<project>
    workspace_path: str              # Coding workspace directory: workspaces/runs/<run_id>/
    branch_name: str
    base_branch: str
    base_ref: str
    spec_path: str

    # 3. Implement & verify
    implementation_summary: str
    test_run_passed: bool
    test_stdout: str
    test_stderr: str
    modified_files: List[str]
    diff_summary: str
    # What the privacy check found in a push it stopped, for the worker: where
    # and what kind, never the words, since the worker's summary is public.
    privacy_findings: str

    # 4. Review
    pr_url: str
    pr_number: Optional[int]
    latest_human_feedback: str
    github_pr_comments: List[str]

    # 5. Finalization & System Delivery
    commit_url: str
    error_message: str
    messages: List[AnyMessage]

    # 6. Tick reconciler
    # These are channels, not decoration: LangGraph carries only what the schema
    # declares, so an undeclared `route` would be dropped between nodes and every
    # conditional edge would fall through to END.
    route: str                       # "implement"|"push"|"verify"|"audit"|"publish"|"sync_review"|"deploy"|"scheduler"|"done"
    stage: TaskStage
    graph_id: str
    required_tools: List[str]        # derived from graph.json's `tools` grant
    audit_passed: bool
    audit_feedback: str
    reviewers: List[str]
    approval_signals: Optional[List[str]]
    rejection_signals: Optional[List[str]]
    lease_owner: str
    impl_digest: Optional[str]
    head_sha: str
    verified_sha: str
    poll_until: Optional[float]
    # Absolute path of the checkout the tick works against: the managed clone of
    # repo.slug (this repository's too), so worktrees are provisioned from the
    # right repository.
    repo_root: str
    # Accumulated across the tick: the lines the runner posts, and the tasks it
    # has already touched, so one tick cannot work the same task twice.
    tick_report: List[str]
    tick_handled: List[str]
    # The deploy node ran this tick. The scheduler routes a pending deployment
    # to it only while this is unset, so deploy → scheduler cannot loop.
    deploy_checked: bool

