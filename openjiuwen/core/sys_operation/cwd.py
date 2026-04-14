# coding: utf-8

"""Per-agent CWD state management via contextvars.

Each asyncio Task (agent / team member / subagent) gets isolated
CWD state through Python's native ContextVar + Task inheritance.
All tools and operations read CWD exclusively through get_cwd().

Three-layer CWD model inspired by Claude Code:

  Layer 1 -- project_root: project identity anchor (set once)
  Layer 2 -- original_cwd: session start point (worktree lifecycle)
  Layer 3 -- cwd: current working directory (updated after shell cmds)

Reading priority: cwd -> original_cwd -> os.getcwd()

Auxiliary workspace locations (not part of the cwd fallback chain):

  - workspace: agent workspace root (DeepAgent per-agent artifact dir)
  - team_workspace: shared team workspace root (mounted via .team/)

Both are optional and return ``None`` when unset -- they record
related paths used by tools, not where shell commands run.

Implementation note -- mutable container pattern:

  The ContextVar holds a reference to a mutable ``CwdState`` object,
  NOT three separate ContextVar[str] values.  This is critical because
  ``asyncio.gather`` creates child Tasks that copy ContextVar bindings
  (copying the *reference*, not the object).  Tool calls executed via
  gather share the same CwdState instance, so mutations in one tool
  (e.g. enter_worktree calling set_cwd) are visible to subsequent
  tools in the same agent.

  Inter-agent isolation is achieved by ``init_cwd()``, which creates
  a *new* CwdState object and sets it via ``_cwd_state.set()``.
  This only affects the current context -- the parent agent keeps its
  old CwdState reference.
"""

import os
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CwdState:
    """Mutable CWD state container.

    Shared by reference within one agent's tool calls (asyncio.gather
    copies the reference, not the object).  A new instance is created
    per agent via init_cwd() to provide inter-agent isolation.
    """
    cwd: str | None = None
    original_cwd: str | None = None
    project_root: str | None = None
    workspace: str | None = None
    team_workspace: str | None = None


_cwd_state: ContextVar[CwdState | None] = ContextVar("cwd_state", default=None)


def _resolve(path: str) -> str:
    return str(Path(path).resolve())


def _state() -> CwdState:
    """Get or create the CwdState for the current context."""
    s = _cwd_state.get()
    if s is None:
        s = CwdState()
        _cwd_state.set(s)
    return s


# ---- Layer 3: CWD (high-frequency updates) ---------------------------------

def get_cwd() -> str:
    """Single read entry point for current working directory.

    All tools and operations MUST use this instead of os.getcwd()
    or reading work_dir from config.
    """
    s = _state()
    return s.cwd or s.original_cwd or os.getcwd()


def set_cwd(cwd: str) -> None:
    """Update CWD in current agent context.

    Typically called after shell command execution when the shell's
    working directory changed (e.g. ``cd`` command), or on worktree
    enter.  Visible to all subsequent tool calls in the same agent.
    """
    _state().cwd = _resolve(cwd)


# ---- Layer 2: Original CWD (worktree lifecycle) ----------------------------

def get_original_cwd() -> str:
    """Session start point.  Used as restore target on worktree exit."""
    s = _state()
    return s.original_cwd or os.getcwd()


def set_original_cwd(cwd: str) -> None:
    """Update session start point.  Called on worktree enter/exit."""
    _state().original_cwd = _resolve(cwd)


# ---- Layer 1: Project Root (set once) --------------------------------------

def get_project_root() -> str:
    """Project identity anchor.  Used for skill discovery, session
    history paths, etc.  Never changes mid-session (except --worktree
    startup).
    """
    s = _state()
    return s.project_root or get_original_cwd()


def set_project_root(root: str) -> None:
    """Set project root.  Should be called once at agent startup."""
    _state().project_root = _resolve(root)


# ---- Workspace: per-agent artifact root ------------------------------------

def get_workspace() -> str | None:
    """Get the agent workspace root for the current agent context.

    Returns the DeepAgent per-agent workspace directory (where
    ``.workspace``, memory files, and artifacts live).  Unlike
    ``get_cwd()``, this has no fallback -- returns ``None`` when
    the agent has no workspace configured.
    """
    return _state().workspace


def set_workspace(path: str) -> None:
    """Set the agent workspace root.  Typically called once at agent startup."""
    _state().workspace = _resolve(path)


# ---- Team workspace: shared across team members ---------------------------

def get_team_workspace() -> str | None:
    """Get the team shared workspace root for the current agent context.

    Returns the path owned by ``TeamWorkspaceManager`` and mounted
    into agent workspaces as ``.team/{team_id}/``.  Returns ``None``
    when the agent is not part of a team or the team has no
    shared workspace.
    """
    return _state().team_workspace


def set_team_workspace(path: str) -> None:
    """Set the team shared workspace root.  Called when joining a team."""
    _state().team_workspace = _resolve(path)


# ---- Bulk initialization ---------------------------------------------------

def init_cwd(
    cwd: str,
    project_root: str | None = None,
    *,
    workspace: str | None = None,
    team_workspace: str | None = None,
) -> None:
    """Initialize all CWD layers with a NEW CwdState instance.

    Called once per agent startup, inside the asyncio Task context
    (typically in DeepAgent._ensure_initialized).  Creates a fresh
    CwdState object -- this provides inter-agent isolation because
    ``_cwd_state.set()`` only affects the current context, while
    the parent agent keeps its old reference.

    Args:
        cwd: Initial working directory (becomes cwd + original_cwd).
        project_root: Project identity root.  Defaults to cwd.
        workspace: Agent workspace root.  Optional -- leave unset for
            agents without a workspace directory.
        team_workspace: Team shared workspace root.  Optional -- only
            set when the agent belongs to a team with a shared workspace.
    """
    resolved = _resolve(cwd)
    state = CwdState(
        cwd=resolved,
        original_cwd=resolved,
        project_root=_resolve(project_root) if project_root else resolved,
        workspace=_resolve(workspace) if workspace else None,
        team_workspace=_resolve(team_workspace) if team_workspace else None,
    )
    _cwd_state.set(state)
