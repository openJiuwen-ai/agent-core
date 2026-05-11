# coding: utf-8

"""Tests for openjiuwen.harness.tools.worktree.tools.

Mix of fast unit tests (tool guards, owner-id resolution, slug generation)
and one git-backed happy path that exercises EnterWorktreeTool +
ExitWorktreeTool against a real temporary repository.
"""

import os
import subprocess

import pytest

from tests.test_logger import logger
from openjiuwen.core.sys_operation.cwd import (
    CwdState,
    _cwd_state,
    get_cwd,
    init_cwd,
)
from openjiuwen.harness.tools.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorktreeConfig,
    WorktreeManager,
    WorktreeSession,
    get_current_session,
    set_current_session,
)
from openjiuwen.harness.tools.worktree.tools import (
    _generate_random_slug,
    _resolve_owner,
)


# --- Helpers -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_cwd_and_session(tmp_path):
    """Each test starts with a clean ContextVar + cwd."""
    set_current_session(None)
    init_cwd(str(tmp_path), workspace=str(tmp_path))
    yield
    set_current_session(None)
    _cwd_state.set(CwdState())


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_git_repo(path: str) -> None:
    """Initialize a minimal git repo with one commit and an origin/main."""
    os.makedirs(path, exist_ok=True)
    # ``--initial-branch`` requires git >= 2.28; use ``symbolic-ref`` so the
    # test works on any git version. HEAD must be repointed before the first
    # commit so the initial commit lands on ``main``.
    _git(path, "init", "--quiet")
    _git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "--quiet", "-m", "init")
    # ``GitBackend._resolve_base`` looks up ``origin/<default>``;
    # create the ref by pointing origin/main at HEAD locally.
    _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")


# --- Pure unit tests ---------------------------------------------------------


def test_generate_random_slug_format():
    slug = _generate_random_slug()
    parts = slug.split("-")
    assert len(parts) == 3
    assert all(parts), f"non-empty parts expected, got {parts}"
    assert len(parts[2]) == 4
    int(parts[2], 16)  # hex suffix
    logger.info("slug %s parses cleanly", slug)


def test_resolve_owner_prefers_generic_keys():
    owner_id, tag = _resolve_owner({"owner_id": "alice", "tag": "team-a"})
    assert owner_id == "alice"
    assert tag == "team-a"


def test_resolve_owner_falls_back_to_legacy_keys():
    owner_id, tag = _resolve_owner({"member_name": "bob", "team_name": "team-b"})
    assert owner_id == "bob"
    assert tag == "team-b"


def test_resolve_owner_returns_none_when_missing():
    owner_id, tag = _resolve_owner({})
    assert owner_id is None
    assert tag is None


@pytest.mark.asyncio
async def test_enter_rejects_invalid_slug():
    mgr = WorktreeManager(WorktreeConfig(enabled=True))
    tool = EnterWorktreeTool(mgr)
    result = await tool.invoke({"name": "../escape"})
    assert result.success is False
    assert "Invalid worktree name" in (result.error or "")


@pytest.mark.asyncio
async def test_enter_refuses_when_already_in_session():
    set_current_session(
        WorktreeSession(
            original_cwd="/tmp",
            worktree_path="/tmp/wt",
            worktree_name="existing",
        )
    )
    mgr = WorktreeManager(WorktreeConfig(enabled=True))
    tool = EnterWorktreeTool(mgr)
    result = await tool.invoke({"name": "another"})
    assert result.success is False
    assert "Already in worktree" in (result.error or "")
    assert "existing" in (result.error or "")


@pytest.mark.asyncio
async def test_exit_without_session_returns_error():
    mgr = WorktreeManager(WorktreeConfig(enabled=True))
    tool = ExitWorktreeTool(mgr)
    result = await tool.invoke({"action": "keep"})
    assert result.success is False
    assert "No active worktree session" in (result.error or "")


@pytest.mark.asyncio
async def test_exit_validates_action_value():
    set_current_session(
        WorktreeSession(
            original_cwd="/tmp",
            worktree_path="/tmp/wt",
            worktree_name="wt",
        )
    )
    mgr = WorktreeManager(WorktreeConfig(enabled=True))
    tool = ExitWorktreeTool(mgr)
    result = await tool.invoke({"action": "bogus"})
    assert result.success is False
    assert "'action' must be 'keep' or 'remove'" in (result.error or "")


@pytest.mark.asyncio
async def test_exit_remove_translates_validation_error_to_tool_output(monkeypatch):
    """ValidationError from manager.exit must surface as ToolOutput.error.

    The tool contract is to return ToolOutput, never to raise BaseError up
    to the caller. Verify the rendered ValidationError message reaches
    ToolOutput.error verbatim so the model sees the two-phase confirmation
    instructions.
    """
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import raise_error

    set_current_session(
        WorktreeSession(
            original_cwd="/tmp",
            worktree_path="/tmp/wt",
            worktree_name="wt",
        )
    )
    mgr = WorktreeManager(WorktreeConfig(enabled=True))

    async def fake_exit(action, *, discard_changes=False):
        raise_error(
            StatusCode.TOOL_WORKTREE_EXIT_INVALID,
            reason="Worktree has 2 uncommitted files. Set discard_changes=True to proceed.",
        )

    monkeypatch.setattr(mgr, "exit", fake_exit)
    tool = ExitWorktreeTool(mgr)

    result = await tool.invoke({"action": "remove"})
    assert result.success is False
    assert "Worktree has 2 uncommitted files" in (result.error or "")
    assert "discard_changes=True" in (result.error or "")


@pytest.mark.asyncio
async def test_event_handler_receives_generic_events(tmp_path, monkeypatch):
    """Sanity check that manager dispatches generic events to the handler.

    Uses real git. The handler captures both events without coupling to
    any team-specific event class.
    """
    repo = str(tmp_path / "repo")
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    _init_git_repo(repo)

    # Operate from inside the repo so find_canonical_git_root succeeds.
    monkeypatch.chdir(repo)
    init_cwd(repo, workspace=workspace)

    events: list = []

    async def handler(event):
        events.append(event)

    mgr = WorktreeManager(
        WorktreeConfig(enabled=True),
        event_handler=handler,
    )
    enter_tool = EnterWorktreeTool(mgr)
    exit_tool = ExitWorktreeTool(mgr)

    enter_result = await enter_tool.invoke(
        {"name": "wt-happy"},
        owner_id="alice",
        tag="team-a",
    )
    assert enter_result.success, enter_result.error
    assert os.path.isdir(enter_result.data["worktree_path"])
    assert get_current_session() is not None
    # CWD should now be inside the worktree.
    assert get_cwd().startswith(enter_result.data["worktree_path"])

    exit_result = await exit_tool.invoke({"action": "remove", "discard_changes": True})
    assert exit_result.success, exit_result.error
    assert get_current_session() is None
    assert not os.path.isdir(enter_result.data["worktree_path"])

    # Both events fired with generic owner/tag fields populated.
    assert len(events) == 2
    created, removed = events
    assert created.worktree_name == "wt-happy"
    assert created.owner_id == "alice"
    assert created.tag == "team-a"
    assert removed.worktree_name == "wt-happy"
    assert removed.owner_id == "alice"
    assert removed.tag == "team-a"
    logger.info("worktree happy path: %s -> removed cleanly", enter_result.data["worktree_path"])


@pytest.mark.asyncio
async def test_legacy_team_kwargs_propagate_to_session():
    """``member_name``/``team_name`` kwargs still populate session fields."""
    mgr_calls: list = []

    class _RecordingManager:
        async def enter(self, slug, *, member_name=None, team_name=None):
            mgr_calls.append((slug, member_name, team_name))
            return WorktreeSession(
                original_cwd="/tmp",
                worktree_path="/tmp/wt",
                worktree_name=slug,
                worktree_branch="worktree-x",
                member_name=member_name,
                team_name=team_name,
            )

    tool = EnterWorktreeTool(_RecordingManager())  # type: ignore[arg-type]
    # The cwd helpers are imported lazily inside invoke; provide a
    # workspace so set_cwd doesn't blow up on the in-memory cwd state.
    init_cwd("/tmp", workspace="/tmp")

    out = await tool.invoke(
        {"name": "legacy-wt"},
        member_name="member-1",
        team_name="team-x",
    )
    assert out.success, out.error
    # Manager saw the legacy keys re-routed through the generic args.
    assert mgr_calls == [("legacy-wt", "member-1", "team-x")]
