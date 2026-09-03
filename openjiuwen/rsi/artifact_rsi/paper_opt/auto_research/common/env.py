"""Load repository ``.env`` into process environment."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import project_root

_LOADED = False


def _repo_checkout_root() -> Path | None:
    """Walk up from this file to the actual git checkout root (marked by
    ``.git`` + ``pyproject.toml``).

    ``project_root()`` intentionally gets redirected to a per-task run_dir
    once an orchestrator run starts (see
    ``common/workspace.py::set_project_root``), and even its own default
    (no override) resolves to ``paper_opt/`` rather than the repo checkout
    -- neither location is where a developer's ``.env`` normally lives.
    This is a separate, stable anchor for finding that file.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def load_project_dotenv(*, force: bool = False) -> Path | None:
    """Load ``.env`` once (does not override existing env vars).

    Tries ``<project_root()>/.env`` first -- this lets a caller that has
    overridden ``project_root()`` (e.g. a per-task run_dir) supply its own
    ``.env`` -- then falls back to the repo checkout root, which is where a
    developer's ``.env`` normally lives (see
    ``configs/pipeline.default.yaml``'s "see repo-root .env" comment) and
    which neither ``project_root()`` nor its own default resolves to.

    Returns the path that was loaded, or ``None`` if none of the
    candidates exist.
    """
    global _LOADED
    if _LOADED and not force:
        return None
    _LOADED = True

    candidates = [project_root() / ".env"]
    repo_root = _repo_checkout_root()
    if repo_root is not None:
        candidates.append(repo_root / ".env")

    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return path
    return None
