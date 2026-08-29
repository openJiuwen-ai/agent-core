# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for DeepAgent AgentTemplate expert_role attachments."""

# pylint: disable=protected-access
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import (
    DeepAgent,
    _EXPERT_ROLE_SECTION,
    _expert_role_load_content,
    _expert_role_unload_content,
)
from openjiuwen.harness.resources import LoadRecord
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PluginSpec
from openjiuwen.harness.schema.interaction import RoundWorkItem
from tests.unit_tests.harness.test_deep_agent import FakeReactAgent

_SESSION_ID = "sess-a"


def _template(name: str) -> AgentTemplateSpec:
    return AgentTemplateSpec(agent_card=AgentCard(name=name, description=f"{name} expert"))


def _session(session_id: str = _SESSION_ID) -> Session:
    return Session(session_id=session_id)


def _configured_agent(
    *,
    enable_task_loop: bool = False,
    language: str | None = "cn",
) -> DeepAgent:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
        DeepAgentConfig(enable_task_loop=enable_task_loop, language=language)
    )
    agent.set_react_agent(FakeReactAgent(), initialized=True)
    return agent


@pytest.fixture
def stub_extension_hot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def apply_hot(_agent: Any, _parts: Any) -> list:
        return []

    async def unapply_hot(_agent: Any, _refs: Any) -> list[str]:
        return ["unapplied"]

    monkeypatch.setattr("openjiuwen.harness.extension_binder.apply_extension_hot", apply_hot)
    monkeypatch.setattr("openjiuwen.harness.extension_binder.unapply_extension_hot", unapply_hot)


async def _expert_role(agent: DeepAgent, session_id: str):
    items = await agent.prompt_attachment_manager.collect_for_session(session_id)
    return next((item for item in items if item.section == _EXPERT_ROLE_SECTION), None)


def _empty_context(session_id: str) -> SessionModelContext:
    return SessionModelContext(
        f"ctx-{session_id}",
        session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )


@pytest.mark.asyncio
async def test_load_a_invoke_writes_snapshot_before_user_message(
    stub_extension_hot: None,
) -> None:
    """T-01: load A then invoke with a session materializes expert_role before UserMessage."""
    agent = _configured_agent()
    await agent.load_agent_template_spec(_template("A"))

    await agent.invoke({"query": "hello"}, session=_session())

    session_id = _SESSION_ID
    attachment = await _expert_role(agent, session_id)
    assert attachment is not None
    assert attachment.content == _expert_role_load_content("A")
    assert attachment.metadata.get("role_name") == "A"

    context = _empty_context(session_id)
    snapshot = await agent.prompt_attachment_manager.sync_to_context(context, session_id)
    user_message = UserMessage(content="hello")
    await context.add_messages(user_message)

    assert isinstance(snapshot, SystemMessage)
    assert context.get_messages() == [snapshot, user_message]
    assert "用户选择了A专家" in snapshot.content


@pytest.mark.asyncio
async def test_same_session_repeat_invoke_does_not_append_delta(
    stub_extension_hot: None,
) -> None:
    """T-02: unchanged role on the same session does not append an attachment delta."""
    agent = _configured_agent()
    await agent.load_agent_template_spec(_template("A"))
    await agent.invoke({"query": "first"}, session=_session())

    context = _empty_context(_SESSION_ID)
    snapshot = await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    )
    assert snapshot is not None

    await agent.invoke({"query": "second"}, session=_session())
    assert await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    ) is None


@pytest.mark.asyncio
async def test_switch_a_to_b_writes_self_contained_b_load_delta(
    stub_extension_hot: None,
) -> None:
    """T-03: A then unload A, load B yields one B load delta that cancels prior roles."""
    agent = _configured_agent()
    record_a = await agent.load_agent_template_spec(_template("A"))
    await agent.invoke({"query": "with-a"}, session=_session())

    context = _empty_context(_SESSION_ID)
    assert await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    ) is not None

    await agent.unload_extension(record_a)
    await agent.load_agent_template_spec(_template("B"))
    await agent.invoke({"query": "with-b"}, session=_session())

    delta = await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    )
    assert delta is not None
    assert "用户选择了B专家" in delta.content
    assert "此前专家角色已取消" in delta.content
    assert "用户选择了A专家" not in delta.content


@pytest.mark.asyncio
async def test_unload_a_then_invoke_writes_cancel_delta(
    stub_extension_hot: None,
) -> None:
    """T-04: session that saw A receives an unload notice after A is removed."""
    agent = _configured_agent()
    record_a = await agent.load_agent_template_spec(_template("A"))
    await agent.invoke({"query": "with-a"}, session=_session())

    context = _empty_context(_SESSION_ID)
    assert await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    ) is not None

    await agent.unload_extension(record_a)
    await agent.invoke({"query": "after-unload"}, session=_session())

    delta = await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    )
    assert delta is not None
    assert "用户取消了A专家选择" in delta.content
    assert "回退到你的默认角色和能力" in delta.content
    attachment = await _expert_role(agent, _SESSION_ID)
    assert attachment is not None
    assert attachment.content == _expert_role_unload_content("A")


@pytest.mark.asyncio
async def test_unload_plugin_or_unknown_load_id_keeps_current_role(
    stub_extension_hot: None,
) -> None:
    """T-05: unloading a plugin record or unknown load_id does not clear the role."""
    agent = _configured_agent()
    template_record = await agent.load_agent_template_spec(_template("A"))
    plugin_record = await agent.load_plugin_spec(PluginSpec(id="plugin-1", name="helper"))

    assert agent._active_agent_template == (template_record.load_id, "A")

    await agent.unload_extension(plugin_record)
    assert agent._active_agent_template == (template_record.load_id, "A")

    unknown = LoadRecord(load_id="missing")
    assert await agent.unload_extension(unknown) == []
    assert agent._active_agent_template == (template_record.load_id, "A")


@pytest.mark.asyncio
async def test_failed_template_load_keeps_role_and_writes_no_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-06: bind failure leaves the current role unchanged and writes no attachment."""
    agent = _configured_agent()
    agent._active_agent_template = ("old-id", "OldRole")

    async def boom(_agent: Any, _parts: Any) -> list:
        raise RuntimeError("bind failed")

    monkeypatch.setattr("openjiuwen.harness.extension_binder.apply_extension_hot", boom)

    with pytest.raises(Exception, match="bind failed"):
        await agent.load_agent_template_spec(_template("B"))

    assert agent._active_agent_template == ("old-id", "OldRole")
    assert await _expert_role(agent, _SESSION_ID) is None


@pytest.mark.asyncio
async def test_two_sessions_each_get_independent_snapshot(
    stub_extension_hot: None,
) -> None:
    """T-07: two sessions each receive their own expert_role snapshot while A is active."""
    agent = _configured_agent()
    await agent.load_agent_template_spec(_template("A"))

    session_one = Session(session_id="sess-one")
    session_two = Session(session_id="sess-two")
    await agent.invoke({"query": "one"}, session=session_one)
    await agent.invoke({"query": "two"}, session=session_two)

    first = await _expert_role(agent, "sess-one")
    second = await _expert_role(agent, "sess-two")
    assert first is not None and second is not None
    assert first.session_id == "sess-one"
    assert second.session_id == "sess-two"
    assert first.content == second.content == _expert_role_load_content("A")


@pytest.mark.asyncio
async def test_stream_syncs_role_change_before_inner_call(
    stub_extension_hot: None,
) -> None:
    """T-08: stream materializes role changes before the inner agent is called."""
    agent = _configured_agent()
    fake = agent.react_agent
    record = await agent.load_agent_template_spec(_template("A"))

    chunks = [chunk async for chunk in agent.stream("first", session=_session())]
    assert chunks
    assert await _expert_role(agent, _SESSION_ID) is not None

    await agent.unload_extension(record)
    [chunk async for chunk in agent.stream("continuation", session=_session())]

    attachment = await _expert_role(agent, _SESSION_ID)
    assert attachment is not None
    assert attachment.content == _expert_role_unload_content("A")
    assert len(fake.stream_calls) == 2


@pytest.mark.asyncio
async def test_same_role_name_different_load_id_is_idempotent(
    stub_extension_hot: None,
) -> None:
    """T-09: reloading the same role_name with a new load_id does not emit a delta."""
    agent = _configured_agent()
    first = await agent.load_agent_template_spec(_template("A"))
    await agent.invoke({"query": "first"}, session=_session())

    context = _empty_context(_SESSION_ID)
    assert await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    ) is not None

    await agent.unload_extension(first)
    second = await agent.load_agent_template_spec(_template("A"))
    assert second.load_id != first.load_id
    await agent.invoke({"query": "reload"}, session=_session())

    assert await agent.prompt_attachment_manager.sync_to_context(
        context, _SESSION_ID
    ) is None
    attachment = await _expert_role(agent, _SESSION_ID)
    assert attachment is not None
    assert attachment.content == _expert_role_load_content("A")


@pytest.mark.asyncio
async def test_task_loop_invoke_still_writes_expert_role(
    stub_extension_hot: None,
) -> None:
    """T-10: enable_task_loop=True still materializes expert_role on invoke."""
    agent = _configured_agent(enable_task_loop=True)
    await agent.load_agent_template_spec(_template("A"))
    session = Session(session_id="loop-session")

    result = await agent.invoke("loop_input", session=session)

    assert result["output"] == "echo:loop_input"
    attachment = await _expert_role(agent, "loop-session")
    assert attachment is not None
    assert attachment.content == _expert_role_load_content("A")


@pytest.mark.asyncio
async def test_run_one_round_writes_attachment_for_bound_session(
    stub_extension_hot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-11: the interaction path materializes expert_role onto the start()-bound session."""
    agent = _configured_agent()
    await agent.load_agent_template_spec(_template("A"))
    session = Session(session_id="interact-session")
    coordinator = MagicMock()
    controller = MagicMock()
    controller.submit_round = AsyncMock()
    controller.wait_round_completion = AsyncMock(
        return_value={"output": "ok", "result_type": "answer"}
    )

    monkeypatch.setattr(
        agent,
        "prepare_interaction_task_loop",
        AsyncMock(return_value=(coordinator, controller)),
    )
    monkeypatch.setattr(agent, "_write_round_result_to_stream", AsyncMock())
    monkeypatch.setattr(agent, "_build_interaction_next_work", MagicMock(return_value=None))
    monkeypatch.setattr(agent, "save_state", MagicMock())
    monkeypatch.setattr(agent, "clear_state", MagicMock())

    work = RoundWorkItem.user(
        request_id="r1",
        inputs={"query": "hello"},
        reset_loop=False,
    )
    await agent.run_one_round(work, "task-1", session)

    attachment = await _expert_role(agent, session.get_session_id())
    assert attachment is not None
    assert attachment.content == _expert_role_load_content("A")
    assert attachment.session_id == "interact-session"


@pytest.mark.asyncio
async def test_direct_call_without_session_skips_expert_role_attachment(
    stub_extension_hot: None,
) -> None:
    """T-12: Core invoke/stream without a session does not invent default_session."""
    agent = _configured_agent()
    fake = agent.react_agent
    await agent.load_agent_template_spec(_template("A"))

    await agent.invoke("hello")

    assert fake.invoke_calls[0]["inputs"] == {"query": "hello"}
    assert fake.invoke_calls[0]["session"] is None
    assert await _expert_role(agent, "default_session") is None

    chunks = [chunk async for chunk in agent.stream("streamed")]
    assert chunks
    assert fake.stream_calls[0]["inputs"] == {"query": "streamed"}
    assert fake.stream_calls[0]["session"] is None
    assert await _expert_role(agent, "default_session") is None


@pytest.mark.asyncio
async def test_subagent_invoke_does_not_inherit_parent_role(
    stub_extension_hot: None,
) -> None:
    """T-13: a child DeepAgent session does not receive the parent's expert_role notice."""
    parent = _configured_agent()
    await parent.load_agent_template_spec(_template("A"))
    await parent.invoke({"query": "parent", "conversation_id": "parent-session"})

    child = _configured_agent()
    await child.invoke({"query": "child", "conversation_id": "child-session"})

    assert parent._active_agent_template is not None
    assert child._active_agent_template is None
    assert await _expert_role(parent, "parent-session") is not None
    assert await _expert_role(child, "child-session") is None
    assert await _expert_role(child, "parent-session") is None


def test_expert_role_notices_follow_prompt_language() -> None:
    """Load/unload notices must render in the same cn/en pair as identity prompts."""
    cn_load = _expert_role_load_content("A", "cn")
    en_load = _expert_role_load_content("A", "en")
    cn_unload = _expert_role_unload_content("A", "cn")
    en_unload = _expert_role_unload_content("A", "en")

    assert "用户选择了A专家" in cn_load
    assert "The user selected the A expert" in en_load
    assert "用户取消了A专家选择" in cn_unload
    assert "The user cancelled the A expert selection" in en_unload
    assert cn_load != en_load
    assert cn_unload != en_unload


@pytest.mark.asyncio
async def test_english_locale_invoke_writes_english_load_and_unload(
    stub_extension_hot: None,
) -> None:
    """An English-locale agent must receive English runtime role notices."""
    agent = _configured_agent(language="en")
    record = await agent.load_agent_template_spec(_template("A"))

    await agent.invoke({"query": "hello"}, session=_session())

    load_attachment = await _expert_role(agent, _SESSION_ID)
    assert load_attachment is not None
    assert load_attachment.content == _expert_role_load_content("A", "en")
    assert "The user selected the A expert" in load_attachment.content
    assert "用户选择了" not in load_attachment.content

    await agent.unload_extension(record)
    await agent.invoke({"query": "after-unload"}, session=_session())

    unload_attachment = await _expert_role(agent, _SESSION_ID)
    assert unload_attachment is not None
    assert unload_attachment.content == _expert_role_unload_content("A", "en")
    assert "The user cancelled the A expert selection" in unload_attachment.content
    assert "用户取消了" not in unload_attachment.content
