# coding: utf-8

"""Git CLI wrapper for worktree operations.

Pure async subprocess wrapper with zero business logic. All functions are
stateless — context is passed via arguments. Fails fast on errors by default.

Security: interactive credential prompts are suppressed via GIT_TERMINAL_PROMPT=0.
"""

import asyncio
import os
from dataclasses import dataclass

from openjiuwen.core.common.logging import team_logger


class GitError(Exception):
    """Git command execution failed."""

    def __init__(self, command: list[str], returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {command[0]} failed (rc={returncode}): {stderr}")


@dataclass(frozen=True, slots=True)
class GitResult:
    """Result of a git command execution."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited with code 0."""
        return self.returncode == 0


# -- Safe environment ---------------------------------------------------------


def _git_env() -> dict[str, str]:
    """Build environment dict that suppresses interactive prompts.

    Returns:
        Environment variables dict with GIT_TERMINAL_PROMPT=0 and
        GIT_ASKPASS unset to prevent credential dialogs.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    return env


# -- Low-level execution ------------------------------------------------------


async def _run_git(
    args: list[str],
    *,
    cwd: str | None = None,
    check: bool = False,
) -> GitResult:
    """Run a git command asynchronously.

    Args:
        args: Git subcommand and arguments (without leading "git").
        cwd: Working directory for the command.
        check: If True, raise GitError on non-zero exit.

    Returns:
        GitResult with captured stdout/stderr.

    Raises:
        GitError: If check is True and the command exits non-zero.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_git_env(),
        stdin=asyncio.subprocess.DEVNULL,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    result = GitResult(
        returncode=proc.returncode or 0,
        stdout=stdout_bytes.decode().strip(),
        stderr=stderr_bytes.decode().strip(),
    )
    if check and not result.ok:
        raise GitError(args, result.returncode, result.stderr)
    return result


# -- Query operations ---------------------------------------------------------


async def find_git_root(cwd: str) -> str | None:
    """Find the git repository root from cwd.

    Args:
        cwd: Directory to start searching from.

    Returns:
        Absolute path to the repository root, or None if not in a repo.
    """
    r = await _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return r.stdout if r.ok else None


async def get_current_branch(cwd: str) -> str | None:
    """Get the current branch name.

    Args:
        cwd: Working directory inside the repository.

    Returns:
        Branch name, or None if in detached HEAD state or not in a repo.
    """
    r = await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return r.stdout if r.ok and r.stdout != "HEAD" else None


async def get_default_branch(cwd: str) -> str:
    """Detect the default branch (main/master).

    Tries symbolic-ref first, then falls back to probing common names.

    Args:
        cwd: Working directory inside the repository.

    Returns:
        Default branch name (never None — falls back to "main").
    """
    r = await _run_git(
        ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        cwd=cwd,
    )
    if r.ok:
        # "origin/main" -> "main"
        return r.stdout.split("/", 1)[-1]
    # Fallback: try common names
    for name in ("main", "master"):
        check = await _run_git(
            ["rev-parse", "--verify", f"origin/{name}"],
            cwd=cwd,
        )
        if check.ok:
            return name
    return "main"


async def rev_parse(ref: str, cwd: str) -> str | None:
    """Resolve a ref to its SHA.

    Args:
        ref: Git ref to resolve (branch, tag, HEAD, etc.).
        cwd: Working directory inside the repository.

    Returns:
        Full SHA string, or None if the ref cannot be resolved.
    """
    r = await _run_git(["rev-parse", ref], cwd=cwd)
    return r.stdout if r.ok else None


async def resolve_git_dir(cwd: str) -> str | None:
    """Get the .git directory path (works for worktrees too).

    Args:
        cwd: Working directory inside the repository or worktree.

    Returns:
        Absolute path to the .git directory, or None.
    """
    r = await _run_git(["rev-parse", "--git-dir"], cwd=cwd)
    if not r.ok:
        return None
    git_dir = r.stdout
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(cwd, git_dir)
    return os.path.normpath(git_dir)


async def find_canonical_git_root(cwd: str) -> str | None:
    """Find the main repository root, even from within a worktree.

    If cwd is inside a worktree, returns the parent repo root.
    This ensures new worktrees are always created under the main repo.

    Args:
        cwd: Working directory to start from.

    Returns:
        Absolute path to the canonical repository root, or None.
    """
    git_dir = await resolve_git_dir(cwd)
    if not git_dir:
        return None
    # Check if this is a worktree (has commondir file)
    commondir_path = os.path.join(git_dir, "commondir")
    if os.path.isfile(commondir_path):
        with open(commondir_path) as f:
            common = f.read().strip()
        common_abs = os.path.normpath(os.path.join(git_dir, common))
        # commondir points to the shared .git dir
        if os.path.basename(common_abs) == ".git":
            return os.path.dirname(common_abs)
        return common_abs
    # Regular repo: git_dir is <root>/.git
    root = await find_git_root(cwd)
    return root


# -- Worktree operations ------------------------------------------------------


async def worktree_add(
    repo_root: str,
    worktree_path: str,
    branch_name: str,
    base_ref: str,
    *,
    no_checkout: bool = False,
) -> None:
    """Create a new git worktree.

    Uses -B (force-create branch) to handle orphaned branch refs
    without requiring a separate ``git branch -D`` call.

    Args:
        repo_root: Repository root directory.
        worktree_path: Target directory for the new worktree.
        branch_name: Branch to create/reset for this worktree.
        base_ref: Starting point ref (branch, tag, or SHA).
        no_checkout: If True, skip file checkout (for sparse checkout flow).

    Raises:
        GitError: If worktree creation fails.
    """
    args = ["worktree", "add"]
    if no_checkout:
        args.append("--no-checkout")
    args.extend(["-B", branch_name, worktree_path, base_ref])
    await _run_git(args, cwd=repo_root, check=True)


async def worktree_remove(
    worktree_path: str,
    *,
    repo_root: str,
    force: bool = False,
) -> bool:
    """Remove a git worktree directory.

    Args:
        worktree_path: Absolute path to the worktree to remove.
        repo_root: Repository root directory.
        force: If True, remove even with uncommitted changes.

    Returns:
        True if removal succeeded, False otherwise.
    """
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(worktree_path)
    r = await _run_git(args, cwd=repo_root)
    return r.ok


async def worktree_prune(repo_root: str) -> None:
    """Prune stale worktree references.

    Args:
        repo_root: Repository root directory.
    """
    await _run_git(["worktree", "prune"], cwd=repo_root)


async def branch_delete(branch: str, repo_root: str) -> bool:
    """Delete a local git branch.

    Args:
        branch: Branch name to delete.
        repo_root: Repository root directory.

    Returns:
        True if deletion succeeded, False otherwise.
    """
    r = await _run_git(["branch", "-D", branch], cwd=repo_root)
    return r.ok


async def fetch_ref(
    repo_root: str,
    ref: str,
    *,
    remote: str = "origin",
) -> bool:
    """Fetch a specific ref from remote.

    Args:
        repo_root: Repository root directory.
        ref: Git ref to fetch.
        remote: Remote name (default "origin").

    Returns:
        True if fetch succeeded, False otherwise.
    """
    r = await _run_git(["fetch", remote, ref], cwd=repo_root)
    return r.ok


async def sparse_checkout_set(
    worktree_path: str,
    paths: list[str],
) -> None:
    """Configure sparse checkout in cone mode.

    Args:
        worktree_path: Worktree directory to configure.
        paths: List of paths to include in sparse checkout.

    Raises:
        GitError: If sparse checkout configuration fails.
    """
    await _run_git(
        ["sparse-checkout", "set", "--cone", "--", *paths],
        cwd=worktree_path,
        check=True,
    )
    await _run_git(["checkout", "HEAD"], cwd=worktree_path, check=True)


# -- Status queries ------------------------------------------------------------


async def status_porcelain(cwd: str) -> list[str]:
    """Get file changes as porcelain lines.

    Args:
        cwd: Working directory inside the repository.

    Returns:
        List of porcelain status lines, empty list on failure.
    """
    r = await _run_git(["status", "--porcelain"], cwd=cwd)
    if not r.ok:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


async def count_commits_since(
    base_commit: str,
    cwd: str,
) -> int | None:
    """Count commits between base_commit and HEAD.

    Args:
        base_commit: Base commit SHA to count from.
        cwd: Working directory inside the repository.

    Returns:
        Number of commits, or None if the count cannot be determined
        (fail-closed).
    """
    r = await _run_git(
        ["rev-list", "--count", f"{base_commit}..HEAD"],
        cwd=cwd,
    )
    if not r.ok:
        return None
    try:
        return int(r.stdout)
    except ValueError:
        return None


async def has_unpushed_commits(cwd: str) -> bool | None:
    """Check if there are commits not pushed to any remote.

    Args:
        cwd: Working directory inside the repository.

    Returns:
        True if unpushed commits exist, False if none, None if check
        fails (fail-closed: caller should treat as having changes).
    """
    r = await _run_git(
        ["rev-list", "--max-count=1", "HEAD", "--not", "--remotes"],
        cwd=cwd,
    )
    if not r.ok:
        return None
    return len(r.stdout) > 0


async def read_worktree_head_sha(worktree_path: str) -> str | None:
    """Fast path: read HEAD SHA without spawning git subprocess.

    Reads .git file -> gitdir -> HEAD -> resolve ref.
    ~0.5ms vs ~15ms for ``git rev-parse HEAD``.

    Args:
        worktree_path: Absolute path to the worktree directory.

    Returns:
        HEAD SHA string, or None if worktree doesn't exist or is corrupted.
    """
    git_file = os.path.join(worktree_path, ".git")
    try:
        with open(git_file) as f:
            content = f.read().strip()
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None

    if not content.startswith("gitdir:"):
        return None

    git_dir = os.path.normpath(
        os.path.join(worktree_path, content[len("gitdir:"):].strip())
    )
    head_file = os.path.join(git_dir, "HEAD")
    try:
        with open(head_file) as f:
            head = f.read().strip()
    except (FileNotFoundError, PermissionError):
        return None

    # Detached HEAD: raw SHA
    if not head.startswith("ref:"):
        return head if len(head) == 40 else None

    # Branch ref: resolve to SHA
    ref_path = head[len("ref:"):].strip()
    # Try worktree-local refs first
    for base in (git_dir,):
        full_ref = os.path.join(base, ref_path)
        try:
            with open(full_ref) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue

    # Fallback: read from commondir
    commondir_file = os.path.join(git_dir, "commondir")
    try:
        with open(commondir_file) as f:
            common = f.read().strip()
        common_abs = os.path.normpath(os.path.join(git_dir, common))
        full_ref = os.path.join(common_abs, ref_path)
        with open(full_ref) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        return None
