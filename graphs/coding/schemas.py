from typing import TypedDict, List, Dict, Any, Optional, Literal
from typing_extensions import TypedDict as ExtTypedDict
from langchain_core.messages import AnyMessage

TaskStatus = Literal["pending", "in_progress", "in_review", "completed", "failed", "blocked"]

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

class CodingState(TypedDict, total=False):
    # 1. Queue & Manifest Context
    build_request_path: str
    project_name: str
    target_repo: str
    max_concurrency: int
    queue: List[TaskEnvelope]
    active_runs: Dict[str, TaskEnvelope]
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

    # 3. Spec Validation (Goldfish 0)
    spec_validation_passed: bool
    spec_validation_feedback: str

    # 4. Execution & QA Flags (Goldfish 1 & 2 + Subprocess Tester)
    implementation_summary: str
    test_run_passed: bool
    test_stdout: str
    test_stderr: str
    attempt_count: int
    max_retries: int
    critic_passed: bool
    critic_feedback: str
    modified_files: List[str]
    diff_summary: str

    # 5. HITL Review Gate (Interruption)
    pr_url: str
    pr_number: Optional[int]
    hitl_decision: str  # "approved" | "revise" | "abort"
    latest_human_feedback: str
    github_pr_comments: List[str]

    # 6. Finalization & System Delivery
    commit_url: str
    error_message: str
    messages: List[AnyMessage]
