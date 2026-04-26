# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill body lifecycle tests for SkillUseRail.

Covers the load -> stub flow on success path, the unload flow on
``skill_complete``, and child-session hint consumption.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.context_engine.active_skill_bodies import (
    ACTIVE_SKILL_BODIES_STATE_KEY,
    ACTIVE_SKILL_HINTS_STATE_KEY,
    stage_active_skill_hints_for_session,
)
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail
from openjiuwen.harness.tools.base_tool import ToolOutput


class _FakeSession:
    def __init__(self, sid: str = "sess-1"):
        self._sid = sid
        self._state: Dict[str, Any] = {}

    def get_session_id(self) -> str:
        return self._sid

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, data: Dict[str, Any]) -> None:
        self._state.update(data)


class _FakeContext:
    def __init__(self, messages: Optional[List[ToolMessage]] = None,
                 session: Optional[_FakeSession] = None):
        self._messages = list(messages or [])
        self._session = session

    def get_messages(self) -> List[Any]:
        return list(self._messages)

    def set_messages(self, msgs: List[Any]) -> None:
        self._messages = list(msgs)

    def get_session_ref(self) -> Optional[_FakeSession]:
        return self._session


def _make_rail(max_bodies: int = 1) -> SkillUseRail:
    rail = SkillUseRail.__new__(SkillUseRail)
    # Bypass __init__ entirely; only set the few attributes used by hooks.
    rail.max_active_skill_bodies = max_bodies
    rail.system_prompt_builder = None
    rail.skills = []
    return rail


def _build_ctx(*, session, context, inputs):
    ctx = AgentCallbackContext(agent=MagicMock())
    ctx.session = session
    ctx.context = context
    ctx.inputs = inputs
    return ctx


@pytest.mark.unit
class TestSkillToolLoadFlow:
    @pytest.mark.asyncio
    async def test_record_then_stub_on_success(self):
        rail = _make_rail(max_bodies=1)
        session = _FakeSession()
        full_msg = ToolMessage(
            content="full body content",
            tool_call_id="tc-1",
            metadata={
                "is_skill_body": True,
                "skill_name": "alpha",
                "relative_file_path": "SKILL.md",
            },
        )
        ctx = _FakeContext(messages=[full_msg], session=session)
        inputs = ToolCallInputs(
            tool_name="skill_tool",
            tool_args={},
            tool_msg=full_msg,
            tool_result=ToolOutput(
                success=True,
                data={"skill_content": "full body content"},
                extra_metadata={
                    "is_skill_body": True,
                    "skill_name": "alpha",
                    "relative_file_path": "SKILL.md",
                },
            ),
        )
        await rail.after_tool_call(_build_ctx(session=session, context=ctx, inputs=inputs))

        # Session state recorded.
        active = session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY) or {}
        assert any(v["skill_name"] == "alpha" for v in active.values())

        # Original tool message should now be stubbed and tool_call_id preserved.
        assert full_msg.tool_call_id == "tc-1"
        assert full_msg.metadata.get("skill_body_stub") is True
        assert full_msg.metadata.get("is_skill_body") is False
        assert "[SKILL LOADED]" in full_msg.content

    @pytest.mark.asyncio
    async def test_no_stub_when_record_fails(self):
        # max_bodies=0 disables recording -> caller must keep full body intact.
        rail = _make_rail(max_bodies=0)
        session = _FakeSession()
        full_msg = ToolMessage(
            content="full body content",
            tool_call_id="tc-1",
            metadata={
                "is_skill_body": True,
                "skill_name": "alpha",
                "relative_file_path": "SKILL.md",
            },
        )
        ctx = _FakeContext(messages=[full_msg], session=session)
        inputs = ToolCallInputs(
            tool_name="skill_tool",
            tool_args={},
            tool_msg=full_msg,
            tool_result=ToolOutput(
                success=True,
                data={"skill_content": "full body content"},
                extra_metadata={
                    "is_skill_body": True,
                    "skill_name": "alpha",
                    "relative_file_path": "SKILL.md",
                },
            ),
        )
        await rail.after_tool_call(_build_ctx(session=session, context=ctx, inputs=inputs))

        assert full_msg.content == "full body content"
        assert full_msg.metadata.get("is_skill_body") is True
        assert full_msg.metadata.get("skill_body_stub") is not True


@pytest.mark.unit
class TestSkillCompleteFlow:
    @pytest.mark.asyncio
    async def test_unload_clears_session_and_marks_stub(self):
        rail = _make_rail(max_bodies=1)
        session = _FakeSession()
        # Pre-populate session with an active body.
        session.update_state({
            ACTIVE_SKILL_BODIES_STATE_KEY: {
                "alpha\x00SKILL.md": {
                    "skill_name": "alpha",
                    "relative_file_path": "SKILL.md",
                    "body": "...",
                    "tool_call_id": "tc-1",
                    "invoked_at": 1.0,
                },
            }
        })

        # Existing load-stub message left over from a successful skill_tool call.
        load_stub = ToolMessage(
            content="[SKILL LOADED] ...",
            tool_call_id="tc-1",
            metadata={
                "skill_name": "alpha",
                "relative_file_path": "SKILL.md",
                "skill_body_stub": True,
                "skill_body_active": True,
            },
        )
        ctx = _FakeContext(messages=[load_stub], session=session)
        inputs = ToolCallInputs(
            tool_name="skill_complete",
            tool_args={"skill_name": "alpha"},
            tool_msg=ToolMessage(content="ok", tool_call_id="tc-2",
                                 metadata={"unload_skill_name": "alpha"}),
            tool_result=ToolOutput(
                success=True,
                data="ok",
                extra_metadata={"unload_skill_name": "alpha"},
            ),
        )
        await rail.after_tool_call(_build_ctx(session=session, context=ctx, inputs=inputs))

        # Session active state should be empty.
        assert not session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)
        # Original load stub keeps tool_call_id but is now marked unloaded.
        assert load_stub.tool_call_id == "tc-1"
        assert load_stub.metadata.get("skill_unloaded") is True
        assert load_stub.metadata.get("skill_body_active") is False


@pytest.mark.unit
class TestActiveSkillHintConsumption:
    @pytest.mark.asyncio
    async def test_consume_pending_hints_writes_to_session(self):
        rail = _make_rail(max_bodies=1)
        session = _FakeSession(sid="child-1")
        stage_active_skill_hints_for_session(
            "child-1",
            [{"skill_name": "alpha", "relative_file_path": "SKILL.md"}],
        )
        ctx = AgentCallbackContext(agent=MagicMock())
        ctx.session = session
        rail._consume_pending_active_skill_hints(ctx)

        hints = session.get_state(ACTIVE_SKILL_HINTS_STATE_KEY)
        assert hints == [{"skill_name": "alpha", "relative_file_path": "SKILL.md"}]
