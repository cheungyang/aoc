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
    "implemented",      # code written in the worktree
    "verified",         # verification_command passed
    "audited",          # advisory review done
    "published",        # committed, pushed, PR open
    "awaiting_review",  # waiting on a human decision on GitHub
    "merged",           # PR merged
    "done"              # merged, worktree torn down, manifest updated
]

STAGE_ORDER: List[str] = [
    "queued", "provisioned", "implemented", "verified", "audited",
    "published", "awaiting_review", "merged", "done"
]


class TaskError(TypedDict, total=False):
    """The last failure, classified so retries can target the right stage."""
    stage: str
    # "llm" | "verification" | "git" | "github" | "config" | "unknown".
    # Infrastructure failures must not consume the LLM budget, so the manifest
    # counts attempts per stage rather than once per task (B1).
    kind: str
    message: str
    at: float


class TaskEnvelope(TypedDict, total=False):
    task_id: str
    project_name: str
    feature_name: str
    spec_path: str
    dependencies: List[str]          # Prerequisite task_ids
    allowed_files: List[str]         # Strict filesystem whitelist
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
    # Digest of the worktree after `implement`. Unchanged digest means the code
    # is still there, so implement is skipped and verify can reuse its result.
    impl_digest: Optional[str]
    verified_digest: Optional[str]
    head_sha: Optional[str]
    # Highest review comment id already actioned, so a resume does not re-read
    # the whole thread (E5).
    review_cursor: Optional[str]
    # While now < poll_until the scheduled tick keeps checking GitHub for this
    # task; otherwise the tick exits without touching the network.
    poll_until: Optional[float]
    last_error: Optional[TaskError]
    updated_at: float


class RepoDescriptor(TypedDict, total=False):
    """The repository the graph works against, declared in the manifest."""
    mode: Literal["self", "existing", "create"]
    slug: Optional[str]              # owner/repo on GitHub
    default_branch: str
    visibility: Literal["private", "public"]
    push_identity: Optional[str]     # GitHub login of the machine user, if any
    push_identity_email: Optional[str]


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
    route: str                       # "implement"|"verify"|"audit"|"publish"|"sync_review"|"scheduler"|"done"
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
    poll_until: Optional[float]
    # Absolute path of the checkout the tick works against. Under repo mode
    # `self` this is the project root; under `existing`/`create` it is the
    # cached clone, so worktrees are provisioned from the right repository.
    repo_root: str
    # Accumulated across the tick: the lines the runner posts, and the tasks it
    # has already touched, so one tick cannot work the same task twice.
    tick_report: List[str]
    tick_handled: List[str]

