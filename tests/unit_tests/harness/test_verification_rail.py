# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for VerificationRail."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.harness.rails.verification_rail import (
    VERIFICATION_ALLOWED_TOOLS,
    VerificationRail,
)
from openjiuwen.harness.workspace.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(tool_name: str, tool_args: dict | None = None, session=None, json_args: bool = False) -> Mock:
    """Build a mock context.

    Args:
        json_args: When True, serialise tool_args to a JSON string to match the
                   real runtime where ToolCall.arguments arrives as a raw JSON string.
    """
    import json as _json
    ctx = Mock()
    ctx.extra = {}
    ctx.inputs = Mock()
    ctx.inputs.tool_name = tool_name
    raw = tool_args or {}
    ctx.inputs.tool_args = _json.dumps(raw) if json_args else raw
    ctx.inputs.tool_call = Mock()
    ctx.inputs.tool_call.id = "call-123"
    ctx.session = session
    return ctx


def _make_rail(workspace=None) -> VerificationRail:
    rail = VerificationRail()
    rail._agent = Mock()
    rail._agent._deep_config = Mock()
    rail._agent._deep_config.enable_task_loop = True
    rail.system_prompt_builder = Mock()
    rail.system_prompt_builder.language = "en"
    if workspace is not None:
        rail.workspace = workspace
    return rail


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Workspace scope guard
# ---------------------------------------------------------------------------

class TestWorkspaceScopeGuard:
    """Tests run with both dict args (pre-parsed) and JSON-string args (real runtime format)."""

    def test_allowed_path_within_workspace_passes(self, tmp_path):
        """A path inside the workspace root is not blocked."""
        rail = _make_rail(workspace=Workspace(root_path=str(tmp_path)))
        ctx = _make_ctx("list_files", {"path": str(tmp_path / "subdir")})
        _run(rail.before_tool_call(ctx))
        assert not ctx.extra.get("_skip_tool"), "In-scope path should not be blocked"

    def test_allowed_path_within_workspace_passes_json_args(self, tmp_path):
        """Same as above with JSON-string args (matches real ToolCall.arguments format)."""
        rail = _make_rail(workspace=Workspace(root_path=str(tmp_path)))
        ctx = _make_ctx("list_files", {"path": str(tmp_path / "subdir")}, json_args=True)
        _run(rail.before_tool_call(ctx))
        assert not ctx.extra.get("_skip_tool")

    def test_allowed_path_at_workspace_root_passes(self, tmp_path):
        """A path equal to the workspace root itself is not blocked."""
        rail = _make_rail(workspace=Workspace(root_path=str(tmp_path)))
        ctx = _make_ctx("list_files", {"path": str(tmp_path)})
        _run(rail.before_tool_call(ctx))
        assert not ctx.extra.get("_skip_tool")

    def test_out_of_scope_path_is_blocked(self, tmp_path):
        """A path outside the workspace root is rejected with a clear message."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        rail = _make_rail(workspace=Workspace(root_path=str(workspace_dir)))
        outside = tmp_path  # parent of the workspace — out of scope
        ctx = _make_ctx("list_files", {"path": str(outside)})
        _run(rail.before_tool_call(ctx))
        assert ctx.extra.get("_skip_tool"), "Out-of-scope path should be blocked"
        error = ctx.inputs.tool_result["error"]
        assert "outside the workspace scope" in error
        assert str(workspace_dir.resolve()) in error

    def test_out_of_scope_path_is_blocked_json_args(self, tmp_path):
        """Same block check with JSON-string args (real runtime format)."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        rail = _make_rail(workspace=Workspace(root_path=str(workspace_dir)))
        outside = tmp_path
        ctx = _make_ctx("list_files", {"path": str(outside)}, json_args=True)
        _run(rail.before_tool_call(ctx))
        assert ctx.extra.get("_skip_tool"), "Out-of-scope path should be blocked"
        assert "outside the workspace scope" in ctx.inputs.tool_result["error"]

    def test_out_of_scope_read_file_is_blocked(self, tmp_path):
        """read_file uses 'file_path' arg — ensure the mapping is respected."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        rail = _make_rail(workspace=Workspace(root_path=str(workspace_dir)))
        ctx = _make_ctx("read_file", {"file_path": str(tmp_path / "secret.txt")})
        _run(rail.before_tool_call(ctx))
        assert ctx.extra.get("_skip_tool")
        assert "outside the workspace scope" in ctx.inputs.tool_result["error"]

    def test_out_of_scope_read_file_is_blocked_json_args(self, tmp_path):
        """Same read_file block check with JSON-string args."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        rail = _make_rail(workspace=Workspace(root_path=str(workspace_dir)))
        ctx = _make_ctx("read_file", {"file_path": str(tmp_path / "secret.txt")}, json_args=True)
        _run(rail.before_tool_call(ctx))
        assert ctx.extra.get("_skip_tool")
        assert "outside the workspace scope" in ctx.inputs.tool_result["error"]

    def test_no_workspace_configured_passes_through(self, tmp_path):
        """When no workspace is set the guard is a no-op."""
        rail = _make_rail(workspace=None)
        rail.workspace = None
        ctx = _make_ctx("list_files", {"path": "/etc/passwd"})
        _run(rail.before_tool_call(ctx))
        assert not ctx.extra.get("_skip_tool")

    def test_workspace_as_string_path(self, tmp_path):
        """Workspace can be provided as a plain string path."""
        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        rail = _make_rail()
        rail.workspace = str(workspace_dir)  # string, not Workspace object
        ctx = _make_ctx("list_files", {"path": str(tmp_path)}, json_args=True)  # outside, JSON args
        _run(rail.before_tool_call(ctx))
        assert ctx.extra.get("_skip_tool")

    def test_non_path_tool_not_affected(self):
        """Tools not in _PATH_TOOL_ARG are not subject to the scope check."""
        rail = _make_rail(workspace="/some/workspace")
        ctx = _make_ctx("bash", {"command": "ls /"})

        _run(rail.before_tool_call(ctx))

        assert not ctx.extra.get("_skip_tool")

    def test_disallowed_tool_blocked_before_scope_check(self, tmp_path):
        """Disallowed tools are rejected by the allowlist, not the scope guard."""
        rail = _make_rail(workspace=Workspace(root_path=str(tmp_path)))
        ctx = _make_ctx("write_file", {"file_path": str(tmp_path / "x.txt"), "content": "hi"})

        _run(rail.before_tool_call(ctx))

        assert ctx.extra.get("_skip_tool")
        assert "write_file" in ctx.inputs.tool_result["error"]
        assert "outside the workspace scope" not in ctx.inputs.tool_result["error"]


# ---------------------------------------------------------------------------
# Conditional reminder injection
# ---------------------------------------------------------------------------

class TestConditionalReminderInjection:

    def test_injects_when_task_loop_active(self):
        """Reminder is injected when enable_task_loop is True and not in plan mode."""
        rail = _make_rail()
        rail._agent._deep_config.enable_task_loop = True

        session = Mock()
        state = Mock()
        state.plan_mode.mode = "auto"
        rail._agent.load_state.return_value = state
        ctx = _make_ctx("", session=session)

        _run(rail.before_model_call(ctx))

        rail.system_prompt_builder.add_section.assert_called_once()

    def test_skips_when_task_loop_disabled(self):
        """Reminder is NOT injected when enable_task_loop is False."""
        rail = _make_rail()
        rail._agent._deep_config.enable_task_loop = False
        ctx = _make_ctx("")

        _run(rail.before_model_call(ctx))

        rail.system_prompt_builder.add_section.assert_not_called()

    def test_skips_when_in_plan_mode(self):
        """Reminder is NOT injected while the agent is in plan mode."""
        rail = _make_rail()
        rail._agent._deep_config.enable_task_loop = True

        session = Mock()
        state = Mock()
        state.plan_mode.mode = "plan"
        rail._agent.load_state.return_value = state
        ctx = _make_ctx("", session=session)

        _run(rail.before_model_call(ctx))

        rail.system_prompt_builder.add_section.assert_not_called()

    def test_skips_when_no_builder(self):
        """Reminder injection is a no-op when system_prompt_builder is not set."""
        rail = _make_rail()
        rail.system_prompt_builder = None
        ctx = _make_ctx("")

        _run(rail.before_model_call(ctx))  # should not raise

    def test_load_state_exception_is_swallowed(self):
        """An error reading plan mode state should not crash the hook."""
        rail = _make_rail()
        rail._agent._deep_config.enable_task_loop = True
        rail._agent.load_state.side_effect = RuntimeError("state unavailable")

        session = Mock()
        ctx = _make_ctx("", session=session)

        _run(rail.before_model_call(ctx))

        # Falls through to injection despite the error.
        rail.system_prompt_builder.add_section.assert_called_once()
