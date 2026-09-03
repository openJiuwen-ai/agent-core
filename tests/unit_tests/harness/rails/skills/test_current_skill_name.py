# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.skills.skill_use_rail import (
    clear_current_skill_name,
    get_current_skill_name,
    set_current_skill_name,
)


class _FakeSession:
    """Minimal session with get_state / update_state for skill-name persistence."""

    def __init__(self):
        self._state = {}

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict):
        self._state.update(data)


@pytest.fixture(autouse=True)
def _clear_skill_binding():
    clear_current_skill_name()
    yield
    clear_current_skill_name()


def test_set_get_clear_current_skill_name():
    assert get_current_skill_name() is None
    set_current_skill_name("alpha")
    assert get_current_skill_name() == "alpha"
    clear_current_skill_name()
    assert get_current_skill_name() is None


def test_session_binding_survives_contextvar_loss():
    """Session is source of truth when ContextVar does not propagate."""
    session = _FakeSession()
    set_current_skill_name("hello-skill", session=session)
    assert get_current_skill_name(session) == "hello-skill"

    # Simulate a different tool-execution context where ContextVar is empty
    # (clear without session leaves session state intact).
    clear_current_skill_name()
    assert get_current_skill_name() is None
    assert get_current_skill_name(session) == "hello-skill"


def test_clear_with_session_clears_both():
    session = _FakeSession()
    set_current_skill_name("writer", session=session)
    clear_current_skill_name(session=session)
    assert get_current_skill_name() is None
    assert get_current_skill_name(session) is None
    assert session.get_state("current_skill_name") is None


@pytest.mark.asyncio
async def test_skill_use_rail_clears_on_after_invoke():
    from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

    session = _FakeSession()
    set_current_skill_name("writer", session=session)
    rail = SkillUseRail(skills_dir="/tmp/unused-skills", include_tools=False)
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=ToolCallInputs(tool_name="bash", tool_args={}),
        session=session,
    )

    await rail.after_invoke(ctx)
    assert get_current_skill_name() is None
    assert get_current_skill_name(session) is None


@pytest.mark.asyncio
async def test_skill_tool_success_sets_name(tmp_path, monkeypatch):
    from openjiuwen.core.single_agent.skills.skill_manager import Skill
    from openjiuwen.harness.tools.skills.skill_tool import SkillTool

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: d\n---\n\n# demo\n", encoding="utf-8")
    skill = Skill(name="demo", description="d", directory=skill_dir)
    session = _FakeSession()

    op = SimpleNamespace(
        fs=lambda: SimpleNamespace(
            read_file=AsyncMock(
                return_value=SimpleNamespace(
                    code=0,
                    message="",
                    data=SimpleNamespace(content="# demo\n"),
                )
            )
        )
    )
    tool = SkillTool(op, lambda: [skill])
    monkeypatch.setattr(
        "openjiuwen.harness.tools.skills.skill_tool._skill_layout_metadata",
        lambda _path: {
            "directory_tree": "demo/",
            "discovered_skill_names": [],
            "tree_truncated": False,
            "nested_skills_truncated": False,
        },
    )

    result = await tool.invoke({"skill_name": "demo"}, session=session)
    assert result.success is True
    assert get_current_skill_name() == "demo"
    assert get_current_skill_name(session) == "demo"

    # Cross-context: ContextVar lost, session still has the name.
    clear_current_skill_name()
    assert get_current_skill_name(session) == "demo"


@pytest.mark.asyncio
async def test_skill_tool_failure_does_not_set_name():
    from openjiuwen.harness.tools.skills.skill_tool import SkillTool

    session = _FakeSession()
    op = SimpleNamespace(fs=lambda: SimpleNamespace(read_file=AsyncMock()))
    tool = SkillTool(op, lambda: [])
    set_current_skill_name("previous", session=session)

    result = await tool.invoke({"skill_name": "missing"}, session=session)
    assert result.success is False
    assert get_current_skill_name() == "previous"
    assert get_current_skill_name(session) == "previous"
