# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager
from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentResult


_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["continue", "edit_rerun"]},
        "edit_instructions": {"type": ["string", "null"]},
    },
    "required": ["intent"],
}


def _make_manager():
    return AvatarSessionManager(team_name="t", run_id="wf_1", model_resolver=lambda n: object())


@pytest.mark.asyncio
async def test_classify_edit_rerun_raises_early_return():
    mgr = _make_manager()
    with patch.object(mgr, "_classify_intent", new=AsyncMock(return_value={"intent": "edit_rerun", "edit_instructions": "改 X"})):
        with pytest.raises(WorkflowAborted) as ei:
            # _human_turn 会在分类前 await _await_human_reply；用 mock 绕过
            with patch.object(mgr, "_await_human_reply", new=AsyncMock(return_value="改脚本")):
                with patch.object(mgr, "_agent_turn", new=AsyncMock()):
                    await mgr._human_turn(state=None, prompt="问", opts={}, schema_json=None, correlation_id="c")
    assert ei.value.reason == "early_return"
    assert ei.value.reply == "改脚本"
    assert ei.value.edit_hints == "改 X"


@pytest.mark.asyncio
async def test_classify_continue_proceeds_to_format():
    mgr = _make_manager()
    called = {"agent_turn": False}
    async def fake_agent_turn(state, prompt, schema_json):
        called["agent_turn"] = True
        from openjiuwen.agent_teams.workflow.engine.backends.base import AgentResult
        return AgentResult(text="formatted")
    with patch.object(mgr, "_classify_intent", new=AsyncMock(return_value={"intent": "continue"})):
        with patch.object(mgr, "_await_human_reply", new=AsyncMock(return_value="正常回复")):
            with patch.object(mgr, "_agent_turn", new=fake_agent_turn):
                await mgr._human_turn(state=None, prompt="问", opts={}, schema_json=None, correlation_id="c")
    assert called["agent_turn"] is True


@pytest.mark.asyncio
async def test_classify_failure_degrades_to_continue():
    mgr = _make_manager()
    with patch.object(mgr, "_classify_intent", new=AsyncMock(side_effect=Exception("LLM 挂了"))):
        with patch.object(mgr, "_await_human_reply", new=AsyncMock(return_value="回复")):
            with patch.object(mgr, "_agent_turn", new=AsyncMock(return_value=AgentResult(text="x"))) as m:
                await mgr._human_turn(state=None, prompt="问", opts={}, schema_json=None, correlation_id="c")
    assert m.called  # degrade 到格式化路径
