"""Host-owned git checkpoint on ``experiments/<run_id>/generated_code/``.

The coding agent edits ``agent_workspace/output/``. After a passing smoke
test the host copies that tree into ``generated_code/`` in place (``.git``
never moves) and commits. Restore is a host ``git checkout`` plus a
reseeding of ``output/`` with no agent ``.git``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

_HOST_NAME = "rsi-host"
_HOST_EMAIL = "rsi-host@localhost"
_SKIP_NAMES = {".git", "__pycache__", "logs"}
_KEEP_IN_DEST = {".git", ".gitignore"}
_HOST_GITIGNORE = (
    "# Host-owned: do not commit execution or bytecode noise.\n"
    "logs/\n"
    "__pycache__/\n"
    "*.pyc\n"
    "*.pyo\n"
    ".pytest_cache/\n"
)


class CheckpointError(RuntimeError):
    """Host git checkpoint/restore failed."""


def force_rmtree(path: Path) -> None:
    """Delete a tree that may contain read-only Git objects (Windows)."""

    def _unlock_and_retry(func, target, exc):
        error = exc if isinstance(exc, BaseException) else exc[1]
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError as retry_exc:
            raise error from retry_exc

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_unlock_and_retry)
    else:
        shutil.rmtree(
            path,
            onerror=lambda func, target, exc_info: _unlock_and_retry(func, target, exc_info),
        )


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = _HOST_NAME
    env["GIT_AUTHOR_EMAIL"] = _HOST_EMAIL
    env["GIT_COMMITTER_NAME"] = _HOST_NAME
    env["GIT_COMMITTER_EMAIL"] = _HOST_EMAIL
    return env


def _run_git(code_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=code_dir,
        capture_output=True,
        text=True,
        check=check,
        env=_git_env(),
    )


def is_repo(code_dir: Path) -> bool:
    return (code_dir / ".git").exists()


def recover_host_git(code_dir: Path) -> bool:
    """Restore ``.git`` from a leftover sibling stash before considering init."""
    if is_repo(code_dir):
        return True
    parent = code_dir.parent
    if not parent.is_dir():
        return False
    name = code_dir.name
    stash_dirs = sorted(
        (path for path in parent.glob(f".{name}.git-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stash in stash_dirs:
        try:
            unstash_host_git(stash, code_dir)
        except OSError:
            continue
        if is_repo(code_dir):
            return True
    previous_dirs = sorted(
        (path for path in parent.glob(f".{name}.previous-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in previous_dirs:
        git_dir = backup / ".git"
        if not git_dir.exists():
            continue
        try:
            unstash_host_git(git_dir, code_dir)
        except OSError:
            continue
        if is_repo(code_dir):
            return True
    return False


def ensure_gitignore(code_dir: Path) -> None:
    """Keep host ignore rules even if a candidate brought its own file."""
    code_dir.mkdir(parents=True, exist_ok=True)
    path = code_dir / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing = [
        line
        for line in ("logs/", "__pycache__/", "*.pyc", "*.pyo", ".pytest_cache/")
        if line not in existing.splitlines()
    ]
    if not existing:
        path.write_text(_HOST_GITIGNORE, encoding="utf-8")
        return
    if not missing:
        return
    suffix = "" if existing.endswith("\n") else "\n"
    path.write_text(existing + suffix + "\n".join(missing) + "\n", encoding="utf-8")


def ensure_repo(code_dir: Path) -> None:
    code_dir.mkdir(parents=True, exist_ok=True)
    if not is_repo(code_dir):
        recover_host_git(code_dir)
    if is_repo(code_dir):
        _run_git(code_dir, "config", "user.email", _HOST_EMAIL, check=False)
        _run_git(code_dir, "config", "user.name", _HOST_NAME, check=False)
        ensure_gitignore(code_dir)
        return
    proc = _run_git(code_dir, "init", "-b", "main", check=False)
    if proc.returncode != 0:
        _run_git(code_dir, "init")
        _run_git(code_dir, "checkout", "-b", "main", check=False)
    _run_git(code_dir, "config", "user.email", _HOST_EMAIL)
    _run_git(code_dir, "config", "user.name", _HOST_NAME)
    ensure_gitignore(code_dir)


def current_commit(code_dir: Path) -> str:
    if not code_dir.exists() or not is_repo(code_dir):
        return ""
    proc = _run_git(code_dir, "rev-parse", "HEAD", check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def commit_exists(code_dir: Path, sha: str) -> bool:
    cleaned = sha.strip()
    if not cleaned or not is_repo(code_dir):
        return False
    proc = _run_git(code_dir, "cat-file", "-t", cleaned, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "commit"


def host_commit(code_dir: Path, message: str) -> str:
    """``git add -A && git commit``; skip empty commits. Returns HEAD."""
    ensure_repo(code_dir)
    _run_git(code_dir, "add", "-A")
    status = _run_git(code_dir, "status", "--porcelain")
    if not status.stdout.strip():
        return current_commit(code_dir)
    proc = _run_git(code_dir, "commit", "-m", message, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git commit failed").strip()
        raise CheckpointError(detail)
    return current_commit(code_dir)


def restore_commit(code_dir: Path, sha: str) -> str:
    """Move ``generated_code`` HEAD to ``sha`` without leaving a detached HEAD."""
    cleaned = sha.strip()
    if not commit_exists(code_dir, cleaned):
        raise CheckpointError(f"unknown code commit: {cleaned}")
    proc = _run_git(code_dir, "checkout", "-B", "main", cleaned, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git checkout failed").strip()
        raise CheckpointError(detail)
    return current_commit(code_dir)


def _copy_ignore(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name in _SKIP_NAMES or name.endswith(".pyc")]


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        force_rmtree(path)
        return
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    path.unlink()


def _copy_tree_excluding_git(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    for item in source.iterdir():
        if item.name in _SKIP_NAMES or item.suffix == ".pyc":
            continue
        destination = dest / item.name
        if item.is_dir():
            shutil.copytree(item, destination, ignore=_copy_ignore)
        else:
            shutil.copy2(item, destination)


def seed_output_from_head(code_dir: Path, output_dir: Path) -> None:
    """Replace ``output/`` with ``generated_code/`` files and drop ``output/.git``."""
    if output_dir.exists():
        force_rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not code_dir.exists():
        return
    _copy_tree_excluding_git(code_dir, output_dir)
    leftover_git = output_dir / ".git"
    if leftover_git.exists():
        force_rmtree(leftover_git)


def sync_tree_into_repo(source: Path, dest: Path) -> list[str]:
    """Copy ``source`` onto ``dest`` without moving ``dest/.git``.

    Files in ``dest`` that are not in ``source`` are removed, except ``.git``
    and ``.gitignore``. Execution ``logs/`` is never copied and is deleted
    from the destination before commit.
    """
    skipped: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    recover_host_git(dest)
    ensure_repo(dest)
    for item in source.iterdir():
        if item.name in _SKIP_NAMES or item.suffix == ".pyc":
            skipped.append(item.name)
            continue
        destination = dest / item.name
        _remove_path(destination)
        if item.is_dir():
            shutil.copytree(item, destination, ignore=_copy_ignore)
        else:
            shutil.copy2(item, destination)
    source_names = {
        item.name
        for item in source.iterdir()
        if item.name not in _SKIP_NAMES and item.suffix != ".pyc"
    }
    for item in list(dest.iterdir()):
        if item.name in _KEEP_IN_DEST:
            continue
        if item.name in _SKIP_NAMES or item.suffix == ".pyc" or item.name not in source_names:
            _remove_path(item)
    ensure_gitignore(dest)
    return skipped


def replace_directory(src: Path, dest: Path, *, attempts: int = 8) -> None:
    """Rename a directory, retrying Windows sharing/access denials."""
    delay = 0.05
    last_exc: BaseException | None = None
    for _ in range(max(1, attempts)):
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay)
            delay = min(delay * 2, 0.5)
    if last_exc is None:
        raise RuntimeError("replace_directory: exhausted retries without recording a PermissionError")
    raise last_exc


def stash_host_git(code_dir: Path, stash_dir: Path) -> bool:
    """Move ``generated_code/.git`` to a sibling path.

    Recovery-only helper for leftover stashes from older promote swaps.
    """
    git_dir = code_dir / ".git"
    if not git_dir.exists():
        return False
    if stash_dir.exists():
        force_rmtree(stash_dir)
    replace_directory(git_dir, stash_dir)
    return True


def unstash_host_git(stash_dir: Path, code_dir: Path) -> None:
    """Put a stashed ``.git`` back onto ``code_dir``."""
    if not stash_dir.exists():
        return
    code_dir.mkdir(parents=True, exist_ok=True)
    destination = code_dir / ".git"
    if destination.exists():
        force_rmtree(destination)
    replace_directory(stash_dir, destination)
