"""Git operations for coding graph."""
from core.util.git_ops import *
from core.util.git_ops import (
    run_cmd_async,
    run_cmd_sync,
    resolve_base_ref,
    provision_worktree,
    get_git_diff,
    commit_and_push,
    discover_target_repo,
    create_pull_request,
    get_pull_request_status,
    merge_pull_request,
    teardown_worktree,
    comment_pull_request as _core_comment_pull_request,
)
from typing import Tuple, Optional

async def comment_pull_request(
    workspace_path: str,
    pr_url: str,
    body: str,
    target_repo: Optional[str] = None
) -> Tuple[bool, str]:
    """Posts a comment to a GitHub Pull Request using gh pr comment."""
    return await _core_comment_pull_request(
        workspace_path=workspace_path,
        pr_url=pr_url,
        body=body,
        target_repo=target_repo
    )
