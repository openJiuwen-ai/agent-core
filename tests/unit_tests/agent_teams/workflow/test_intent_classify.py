# coding: utf-8
from __future__ import annotations
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


class _FakeTinyAgent:
    """Async-context-manager fake standing in for ``TinyAgent`` (used via ``async with``)."""

    def __init__(self, result):
        self._result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, content, *, schema=None):
        return self._result


def _make_base_spec_manager():
    """Manager built the way ``TeamWorkerBackend._sessions()`` builds it: a
    worker_base_spec with a resolved model — the only production path."""
    from openjiuwen.agent_teams.schema.deep_agent_spec import (
        DeepAgentSpec,
        TeamModelConfig,
    )
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

    model_config = TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test",
            verify_ssl=False,
        ),
        model_request_config=ModelRequestConfig(model_name="test-model"),
    )
    base_spec = DeepAgentSpec(model=model_config, tools=[])
    return AvatarSessionManager(worker_base_spec=base_spec, team_name="t", run_id="wf_1")


@pytest.mark.asyncio
async def test_classify_intent_internal_degrade_on_tiny_agent_failure():
    """TinyAgent construction raising must be caught inside _classify_intent -> None.

    Exercises the internal ``except Exception -> None`` degrade path directly
    (review gap: prior tests mocked ``_classify_intent`` itself, so this catch was
    never exercised).
    """
    mgr = _make_base_spec_manager()
    with patch(
        "openjiuwen.agent_teams.tiny_agent.TinyAgent",
        side_effect=Exception("LLM 挂了"),
    ):
        result = await mgr._classify_intent("改脚本", "问")
    assert result is None


@pytest.mark.asyncio
async def test_classify_intent_real_invocation_returns_structured_dict():
    """The real TinyAgent -> async-with -> run path returns the intent dict.

    Proves the actual invocation path works (not just the failure path), so a future
    behavior change inside the real method cannot slip past this suite.
    """
    mgr = _make_base_spec_manager()
    fake = _FakeTinyAgent({"intent": "edit_rerun", "edit_instructions": "改 X"})
    with patch(
        "openjiuwen.agent_teams.tiny_agent.TinyAgent",
        return_value=fake,
    ):
        result = await mgr._classify_intent("改脚本", "问")
    assert result == {"intent": "edit_rerun", "edit_instructions": "改 X"}


@pytest.mark.asyncio
async def test_classify_intent_via_worker_base_spec_model_not_none():
    """Regression: the real TeamWorkerBackend._sessions() construction (worker_base_spec
    with a resolved model) must actually run classification, not silently skip it.
    """
    mgr = _make_base_spec_manager()
    fake = _FakeTinyAgent({"intent": "continue"})
    with patch(
        "openjiuwen.agent_teams.tiny_agent.TinyAgent",
        return_value=fake,
    ):
        result = await mgr._classify_intent("正常回复", "问")
    assert result == {"intent": "continue"}
