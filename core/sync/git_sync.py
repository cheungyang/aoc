"""Git sync core module for synchronizing PKM Obsidian Vault and Main Codebase.

Provides robust synchronization logic:
1. PKM Obsidian Vault: Staged add, commit, pull with remote conflict resolution (-X theirs), and push.
2. Main Codebase: Pull updates only, resolving conflicts with remote content.
3. Formatted error messages for scheduling agents if manual resolution is required.
"""

import os
import sys
import datetime
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

from core.config import Config


class GitSyncError(Exception):
    """Base exception for Git sync errors."""
    pass


class GitSyncConflictError(GitSyncError):
    """Exception raised when merge conflicts cannot be resolved automatically."""
    def __init__(self, target: str, path: str, conflicted_files: List[str], details: str = ""):
        self.target = target
        self.path = path
        self.conflicted_files = conflicted_files
        self.details = details
        file_list_str = "\n".join(f"  - {f}" for f in conflicted_files)
        msg = (
            f"Git Sync Conflict in {target} ({path}):\n"
            f"Failed to automatically resolve conflicts with remote.\n"
            f"The following files require manual resolution:\n{file_list_str}"
        )
        if details:
            msg += f"\nDetails: {details}"
        super().__init__(msg)


@dataclass
class GitSyncResult:
    """Result of a sync operation for a repository."""
    target: str
    path: str
    branch: str = ""
    status: str = "unknown"  # success, up_to_date, merged_with_remote, conflict, error, skipped
    committed_files: List[str] = field(default_factory=list)
    commit_hash: Optional[str] = None
    pulled: bool = False
    pull_details: str = ""
    pushed: bool = False
    push_details: str = ""
    conflicted_files: List[str] = field(default_factory=list)
    message: str = ""
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status in ("success", "up_to_date", "merged_with_remote", "skipped", "dry_run")


def run_git_cmd(
    args: List[str],
    cwd: str,
    check: bool = False,
    env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess:
    """Runs a git command in the specified directory."""
    cmd = ["git"] + args
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        env=full_env
    )


def is_git_repo(path: str) -> bool:
    """Checks if a directory is a valid git repository."""
    if not os.path.isdir(path):
        return False
    git_dir = os.path.join(path, ".git")
    if os.path.exists(git_dir):
        return True
    res = run_git_cmd(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return res.returncode == 0 and res.stdout.strip() == "true"


def get_current_branch(path: str) -> str:
    """Gets the name of the current active branch in the repository."""
    res = run_git_cmd(["branch", "--show-current"], cwd=path)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    res = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return "main"


def get_default_remote(path: str) -> str:
    """Gets the default remote name (origin if available, otherwise first remote)."""
    res = run_git_cmd(["remote"], cwd=path)
    if res.returncode == 0:
        remotes = res.stdout.strip().split()
        if "origin" in remotes:
            return "origin"
        if remotes:
            return remotes[0]
    return "origin"


def get_uncommitted_files(path: str) -> List[str]:
    """Gets a list of modified, staged, or untracked files."""
    res = run_git_cmd(["status", "--porcelain"], cwd=path)
    files = []
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                parts = line.split(maxsplit=1)
                if len(parts) > 1:
                    files.append(parts[1])
    return files


def get_unmerged_files(path: str) -> List[str]:
    """Gets a list of currently conflicted / unmerged files."""
    # Method 1: diff filter=U
    res = run_git_cmd(["diff", "--name-only", "--diff-filter=U"], cwd=path)
    conflicts = set()
    if res.returncode == 0 and res.stdout.strip():
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                conflicts.add(line)

    # Method 2: status porcelain checks for conflict codes
    res_status = run_git_cmd(["status", "--porcelain"], cwd=path)
    if res_status.returncode == 0:
        for line in res_status.stdout.splitlines():
            if len(line) >= 3:
                code = line[:2]
                if code in ("UU", "AA", "UD", "DU", "DD", "AU", "UA"):
                    filename = line[3:].strip()
                    if filename:
                        conflicts.add(filename)

    return sorted(list(conflicts))


def resolve_conflicts_with_theirs(path: str, remote_ref: str = "origin/main") -> Tuple[bool, List[str]]:
    """
    Attempts to automatically resolve merge conflicts favoring remote content.
    - If file exists on remote: checkout remote version and add.
    - If file deleted on remote: rm file.
    Returns (success_boolean, remaining_conflicted_files).
    """
    unmerged = get_unmerged_files(path)
    if not unmerged:
        return True, []

    for file_path in unmerged:
        # Check if file exists on remote ref
        cat_res = run_git_cmd(["cat-file", "-e", f"{remote_ref}:{file_path}"], cwd=path)
        if cat_res.returncode == 0:
            # Remote has the file -> take remote version
            checkout_res = run_git_cmd(["checkout", "--theirs", "--", file_path], cwd=path)
            if checkout_res.returncode != 0:
                # Fallback to checkout explicitly from remote_ref
                run_git_cmd(["checkout", remote_ref, "--", file_path], cwd=path)
            run_git_cmd(["add", file_path], cwd=path)
        else:
            # File was deleted on remote -> remove locally
            run_git_cmd(["rm", "-f", "--", file_path], cwd=path)

    remaining = get_unmerged_files(path)
    return (len(remaining) == 0), remaining


def sync_pkm_vault(
    pkm_dir: Optional[str] = None,
    remote_name: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> GitSyncResult:
    """
    Synchronizes the PKM Obsidian Vault:
    1. git add -A
    2. git commit all changes
    3. git pull (resolving conflicts with remote content automatically)
    4. git push
    """
    target = "PKM Obsidian Vault"
    pkm_path = os.path.abspath(os.path.expanduser(pkm_dir)) if pkm_dir else Config().pkm_dir
    pkm_path = os.path.abspath(os.path.expanduser(pkm_path))

    result = GitSyncResult(target=target, path=pkm_path)

    if not is_git_repo(pkm_path):
        result.status = "error"
        result.error = f"PKM directory '{pkm_path}' is not a valid Git repository."
        result.message = result.error
        return result

    branch = get_current_branch(pkm_path)
    result.branch = branch
    remote = remote_name or get_default_remote(pkm_path)
    remote_ref = f"{remote}/{branch}"

    if dry_run:
        uncommitted = get_uncommitted_files(pkm_path)
        result.status = "dry_run"
        result.committed_files = uncommitted
        result.message = f"[DRY-RUN] Would add and commit {len(uncommitted)} files, pull from {remote_ref}, and push."
        return result

    try:
        # Step 1: Add all new and modified files
        run_git_cmd(["add", "-A"], cwd=pkm_path, check=True)

        # Step 2: Commit if there are staged changes
        staged_check = run_git_cmd(["diff", "--cached", "--quiet"], cwd=pkm_path)
        if staged_check.returncode != 0:
            # There are staged changes to commit
            uncommitted = get_uncommitted_files(pkm_path)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_msg = f"Auto-sync PKM vault: {now_str}"
            commit_res = run_git_cmd(["commit", "-m", commit_msg], cwd=pkm_path)
            if commit_res.returncode == 0:
                result.committed_files = uncommitted
                # Get commit hash
                hash_res = run_git_cmd(["rev-parse", "--short", "HEAD"], cwd=pkm_path)
                if hash_res.returncode == 0:
                    result.commit_hash = hash_res.stdout.strip()
            else:
                result.status = "error"
                result.error = f"Failed to commit changes in PKM: {commit_res.stderr.strip()}"
                result.message = result.error
                return result

        # Check if remote is configured
        remotes_check = run_git_cmd(["remote"], cwd=pkm_path)
        if not remotes_check.stdout.strip():
            result.status = "success"
            result.message = f"Committed {len(result.committed_files)} files locally. No remote configured for PKM."
            return result

        # Step 3: Fetch and pull remote updates
        fetch_res = run_git_cmd(["fetch", remote, branch], cwd=pkm_path)
        if fetch_res.returncode != 0:
            # Fetch failed (e.g. network/auth error)
            err_msg = fetch_res.stderr.strip() or fetch_res.stdout.strip()
            result.status = "error"
            result.error = f"Failed to fetch from remote '{remote}' for PKM: {err_msg}"
            result.message = result.error
            return result

        # Attempt pull / merge favoring remote changes on conflict
        merge_res = run_git_cmd(
            ["merge", remote_ref, "-X", "theirs", "-m", "Merge remote changes into PKM vault (favor remote content)"],
            cwd=pkm_path
        )

        if merge_res.returncode != 0:
            # Merge encountered conflicts or failed
            conflicts = get_unmerged_files(pkm_path)
            if conflicts:
                # Attempt manual conflict resolution with --theirs
                resolved_ok, remaining_conflicts = resolve_conflicts_with_theirs(pkm_path, remote_ref)
                if resolved_ok:
                    # Complete merge commit
                    fin_commit = run_git_cmd(["commit", "--no-edit", "-m", "Auto-resolved conflicts favoring remote content"], cwd=pkm_path)
                    if fin_commit.returncode != 0:
                        # Fallback commit
                        run_git_cmd(["commit", "-m", "Auto-resolved conflicts favoring remote content"], cwd=pkm_path)
                    result.pulled = True
                    result.pull_details = f"Merged updates from {remote_ref} (conflicts in {len(conflicts)} files auto-resolved with remote)."
                else:
                    # Could not resolve automatically!
                    run_git_cmd(["merge", "--abort"], cwd=pkm_path)
                    result.status = "conflict"
                    result.conflicted_files = remaining_conflicts
                    result.error = (
                        f"Failed to automatically resolve conflicts with remote in PKM Obsidian Vault.\n"
                        f"Files requiring manual resolution:\n" + "\n".join(f"  - {f}" for f in remaining_conflicts)
                    )
                    result.message = result.error
                    return result
            else:
                # Other merge failure (e.g. local untracked file conflict)
                err_msg = merge_res.stderr.strip() or merge_res.stdout.strip()
                result.status = "error"
                result.error = f"Failed to merge {remote_ref} into PKM: {err_msg}"
                result.message = result.error
                return result
        else:
            result.pulled = True
            result.pull_details = merge_res.stdout.strip() or "Up to date / Merged successfully"

        # Step 4: Push local commits to remote
        ahead_check = run_git_cmd(["rev-list", f"{remote_ref}..HEAD", "--count"], cwd=pkm_path)
        ahead_count = int(ahead_check.stdout.strip()) if ahead_check.returncode == 0 and ahead_check.stdout.strip().isdigit() else 0

        if ahead_count > 0:
            push_res = run_git_cmd(["push", remote, branch], cwd=pkm_path)
            if push_res.returncode == 0:
                result.pushed = True
                result.push_details = f"Successfully pushed {ahead_count} commit(s) to {remote_ref}"
            else:
                err_msg = push_res.stderr.strip() or push_res.stdout.strip()
                result.status = "error"
                result.error = f"Failed to push to {remote_ref} for PKM: {err_msg}"
                result.message = result.error
                return result
        else:
            result.pushed = False
            result.push_details = "Remote is up to date (no unpushed commits)"

        result.status = "success"
        committed_summary = f"committed {len(result.committed_files)} file(s)" if result.committed_files else "clean working tree"
        result.message = (
            f"PKM Vault sync complete on branch '{branch}': {committed_summary}, "
            f"pulled from {remote_ref}, {result.push_details}."
        )
        return result

    except Exception as e:
        result.status = "error"
        result.error = f"Unexpected error during PKM vault sync: {str(e)}"
        result.message = result.error
        return result


def sync_main_codebase(
    codebase_dir: Optional[str] = None,
    remote_name: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> GitSyncResult:
    """
    Synchronizes the Main Codebase:
    1. Fetches remote updates.
    2. Only git pulls for new updates, resolving conflicts with remote content automatically.
    3. Does NOT commit local changes or git push.
    """
    target = "Main Codebase"
    default_codebase = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    codebase_path = os.path.abspath(os.path.expanduser(codebase_dir)) if codebase_dir else default_codebase

    result = GitSyncResult(target=target, path=codebase_path)

    if not is_git_repo(codebase_path):
        result.status = "error"
        result.error = f"Codebase directory '{codebase_path}' is not a valid Git repository."
        result.message = result.error
        return result

    branch = get_current_branch(codebase_path)
    result.branch = branch
    remote = remote_name or get_default_remote(codebase_path)
    remote_ref = f"{remote}/{branch}"

    if dry_run:
        result.status = "dry_run"
        result.message = f"[DRY-RUN] Would fetch from {remote} and pull updates for branch '{branch}' resolving with remote."
        return result

    try:
        # Check if remote exists
        remotes_check = run_git_cmd(["remote"], cwd=codebase_path)
        if not remotes_check.stdout.strip():
            result.status = "up_to_date"
            result.message = f"No remote configured for Main Codebase on branch '{branch}'."
            return result

        # Step 1: Fetch remote
        fetch_res = run_git_cmd(["fetch", remote, branch], cwd=codebase_path)
        if fetch_res.returncode != 0:
            err_msg = fetch_res.stderr.strip() or fetch_res.stdout.strip()
            result.status = "error"
            result.error = f"Failed to fetch from remote '{remote}' for Main Codebase: {err_msg}"
            result.message = result.error
            return result

        # Check how many commits behind remote
        behind_check = run_git_cmd(["rev-list", f"HEAD..{remote_ref}", "--count"], cwd=codebase_path)
        behind_count = int(behind_check.stdout.strip()) if behind_check.returncode == 0 and behind_check.stdout.strip().isdigit() else 0

        if behind_count == 0:
            result.status = "up_to_date"
            result.pulled = False
            result.pull_details = "Already up to date with remote"
            result.message = f"Main Codebase is up to date on branch '{branch}' (0 new commits on remote)."
            return result

        # Check if working tree has uncommitted modifications
        uncommitted = get_uncommitted_files(codebase_path)
        stashed = False

        if uncommitted:
            # Stash temporary changes to allow pulling cleanly
            stash_res = run_git_cmd(["stash", "push", "-u", "-m", "Auto-sync temporary stash before pull"], cwd=codebase_path)
            stashed = (stash_res.returncode == 0 and "No local changes to save" not in stash_res.stdout)

        # Step 2: Merge / pull remote updates with -X theirs
        merge_res = run_git_cmd(
            ["merge", remote_ref, "-X", "theirs", "-m", "Merge remote updates into codebase (favor remote content)"],
            cwd=codebase_path
        )

        if merge_res.returncode != 0:
            # Conflict occurred during merge
            conflicts = get_unmerged_files(codebase_path)
            if conflicts:
                resolved_ok, remaining = resolve_conflicts_with_theirs(codebase_path, remote_ref)
                if resolved_ok:
                    run_git_cmd(["commit", "--no-edit", "-m", "Auto-resolved conflicts favoring remote content"], cwd=codebase_path)
                    result.pulled = True
                    result.pull_details = f"Merged {behind_count} commit(s) from {remote_ref} (conflicts resolved with remote)."
                else:
                    run_git_cmd(["merge", "--abort"], cwd=codebase_path)
                    if stashed:
                        run_git_cmd(["stash", "pop"], cwd=codebase_path)
                    result.status = "conflict"
                    result.conflicted_files = remaining
                    result.error = (
                        f"Failed to automatically resolve conflicts with remote in Main Codebase.\n"
                        f"Files requiring manual resolution:\n" + "\n".join(f"  - {f}" for f in remaining)
                    )
                    result.message = result.error
                    return result
            else:
                err_msg = merge_res.stderr.strip() or merge_res.stdout.strip()
                if stashed:
                    run_git_cmd(["stash", "pop"], cwd=codebase_path)
                result.status = "error"
                result.error = f"Failed to pull updates from {remote_ref} for Main Codebase: {err_msg}"
                result.message = result.error
                return result
        else:
            result.pulled = True
            result.pull_details = f"Updated {behind_count} commit(s) from {remote_ref}"

        # If we stashed, pop and resolve any pop conflicts with remote
        if stashed:
            pop_res = run_git_cmd(["stash", "pop"], cwd=codebase_path)
            if pop_res.returncode != 0:
                pop_conflicts = get_unmerged_files(codebase_path)
                if pop_conflicts:
                    pop_resolved, pop_remaining = resolve_conflicts_with_theirs(codebase_path, remote_ref)
                    if not pop_resolved:
                        result.status = "conflict"
                        result.conflicted_files = pop_remaining
                        result.error = (
                            f"Conflicts occurred restoring local changes after pull in Main Codebase.\n"
                            f"Files requiring manual resolution:\n" + "\n".join(f"  - {f}" for f in pop_remaining)
                        )
                        result.message = result.error
                        return result
                    else:
                        # Drop the stash if conflict was resolved
                        run_git_cmd(["stash", "drop"], cwd=codebase_path)

        result.status = "success"
        result.message = f"Main Codebase successfully pulled {behind_count} commit(s) from {remote_ref} on branch '{branch}'."
        return result

    except Exception as e:
        result.status = "error"
        result.error = f"Unexpected error during Main Codebase sync: {str(e)}"
        result.message = result.error
        return result


def sync_all(
    pkm_dir: Optional[str] = None,
    codebase_dir: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
    skip_pkm: bool = False,
    skip_codebase: bool = False,
) -> Dict[str, Any]:
    """
    Executes full synchronization for both PKM Obsidian Vault and Main Codebase.
    Returns a dictionary with status, results, and formatted summary or error string.
    """
    results: Dict[str, GitSyncResult] = {}
    errors: List[str] = []
    conflict_files: List[str] = []

    # 1. Sync PKM Obsidian Vault
    if not skip_pkm:
        pkm_res = sync_pkm_vault(pkm_dir=pkm_dir, dry_run=dry_run, verbose=verbose)
        results["pkm_vault"] = pkm_res
        if not pkm_res.is_success:
            errors.append(f"[{pkm_res.target}] {pkm_res.error or pkm_res.message}")
            if pkm_res.conflicted_files:
                conflict_files.extend(pkm_res.conflicted_files)
    else:
        results["pkm_vault"] = GitSyncResult(target="PKM Obsidian Vault", path="", status="skipped", message="Skipped by option")

    # 2. Sync Main Codebase
    if not skip_codebase:
        cb_res = sync_main_codebase(codebase_dir=codebase_dir, dry_run=dry_run, verbose=verbose)
        results["main_codebase"] = cb_res
        if not cb_res.is_success:
            errors.append(f"[{cb_res.target}] {cb_res.error or cb_res.message}")
            if cb_res.conflicted_files:
                conflict_files.extend(cb_res.conflicted_files)
    else:
        results["main_codebase"] = GitSyncResult(target="Main Codebase", path="", status="skipped", message="Skipped by option")

    success = len(errors) == 0

    # Build human-readable output summary
    lines = ["=== Git Sync Summary ==="]
    for key, res in results.items():
        lines.append(f"[{res.target}] ({res.path})")
        lines.append(f"  - Status: {res.status.upper()}")
        if res.branch:
            lines.append(f"  - Branch: {res.branch}")
        if res.committed_files:
            lines.append(f"  - Committed: {len(res.committed_files)} file(s) ({res.commit_hash or 'new commit'})")
        if res.pulled:
            lines.append(f"  - Pull: {res.pull_details}")
        if res.pushed:
            lines.append(f"  - Push: {res.push_details}")
        if res.error:
            lines.append(f"  - Error: {res.error}")

    if not success:
        lines.append("\n=== SYNC FAILED ===")
        if conflict_files:
            lines.append("The following files require manual conflict resolution:")
            for cf in conflict_files:
                lines.append(f"  - {cf}")
    else:
        lines.append("=== Sync Complete ===")

    summary_text = "\n".join(lines)

    return {
        "success": success,
        "results": results,
        "errors": errors,
        "conflict_files": conflict_files,
        "summary": summary_text
    }
