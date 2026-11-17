# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Durable approvals through the real NativeHarness and ReAct interrupt loop."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.agent_lifecycle import AgentLifecycleHandler
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness, TeamHarness
from openjiuwen.agent_teams.rails.team_permission_rail import TeamPermissionRail
from openjiuwen.agent_teams.schema.events import EventMessage, ToolApprovalResultEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import DeepAgentParts
from openjiuwen.harness.schema.config import DeepAgentConfig
from tests.unit_tests.agent_teams.harness.fixtures import wait_for_state
from tests.unit_tests.agent_teams.test_pending_approval_mailbox import mail, mailbox_handler
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


class CountEdit(Tool):
    def __init__(self):
        super().__init__(
            ToolCard(
                id="approval-edit",
                name="edit_file",
                description="Count a fake edit",
                input_params={"type": "object", "properties": {}},
                idempotent=False,
            )
        )
        self.calls = 0

    async def invoke(self, inputs, **kwargs):
        self.calls += 1
        return "edited"

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


@asynccontextmanager
async def runtime(tmp_path, monkeypatch, *, parallel=False):
    monkeypatch.setenv("OPENJIUWEN_HOME", str(tmp_path / "home"))
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="fake", api_base="http://unused.invalid"
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )
    llm = MockLLMModel()
    ids = ["call-a", "call-b"] if parallel else ["call-a"]
    llm.set_responses(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id=tool_id, type="function", name="edit_file", arguments="{}") for tool_id in ids],
            ),
            create_text_response("done"),
            create_text_response("done"),
        ]
    )
    monkeypatch.setattr(Model, "stream", lambda self, *a, **kw: llm.stream(*a, **kw))
    monkeypatch.setattr(Model, "invoke", lambda self, *a, **kw: llm.invoke(*a, **kw))
    edit = CountEdit()
    permission = TeamPermissionRail(
        config={"enabled": True, "defaults": {"*": "allow"}, "tools": {"edit_file": {"*": "ask"}}}
    )
    card = AgentCard(name="approval-native")
    config = DeepAgentConfig(
        card=card,
        model=model,
        enable_task_loop=True,
        max_iterations=4,
        cwd=str(tmp_path),
        enable_read_image_multimodal=False,
    )
    spec = NS(
        resolve_parts=lambda context=None: DeepAgentParts(
            config=config, rails=[permission], tool_cards=[edit.card], tool_instances=[edit]
        )
    )
    await Runner.start()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=TeamRole.TEAMMATE, member_name="worker")
    agent = TeamAgent(card)
    agent._configurator.harness = harness
    handler, mailbox, _ = mailbox_handler([], agent)
    handler._blueprint.ctx = NS(member_name="worker")
    agent._configurator._blueprint = handler._blueprint
    try:
        await harness.start(team_session=None)
        await harness.send("edit")
        assert await wait_for_state(harness, HarnessState.IDLE)
        assert agent.has_pending_interrupt(), "the real permission rail must suspend the fake edit"
        assert edit.calls == 0
        yield NS(agent=agent, native=native, harness=harness, handler=handler, mailbox=mailbox, edit=edit, llm=llm)
    finally:
        await harness.stop()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_durable_reply_reaches_real_permission_gate(tmp_path, monkeypatch, approved):
    async with runtime(tmp_path, monkeypatch) as run:
        run.mailbox.messages = [mail("3"), mail("2", approval=True, approved=approved), mail("1")]
        await run.handler._process_unread_messages("worker")
        assert run.mailbox.read == ["2"]
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert not run.agent.has_pending_interrupt()
        assert run.edit.calls == int(approved)
        await run.handler._process_unread_messages("worker")
        assert run.mailbox.read == ["2", "1", "3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_first", [True, False])
async def test_event_and_durable_copy_do_not_repeat_the_edit(tmp_path, monkeypatch, event_first):
    async with runtime(tmp_path, monkeypatch) as run:
        run.mailbox.messages = [mail("1", approval=True)]
        lifecycle = AgentLifecycleHandler(run.agent, run.handler._blueprint, run.handler._infra, None)
        event = EventMessage.from_event(
            ToolApprovalResultEvent(team_name="team", member_name="worker", tool_call_id="call-a", approved=True)
        )
        actions = [
            lambda: lifecycle.on_tool_approval_result(event),
            lambda: run.handler._process_unread_messages("worker"),
        ]
        if not event_first:
            actions.reverse()
        for action in actions:
            await action()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        await run.handler._process_unread_messages("worker")
        assert run.edit.calls == 1
        assert run.mailbox.read == ["1"]
        assert not run.agent.has_pending_interrupt()
        assert run.llm.call_count == 2, "an approval copy must not start a new model query"


@pytest.mark.asyncio
async def test_duplicate_event_before_react_clears_state_is_not_a_new_query(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        react = run.native.react_agent
        invoke = react.invoke

        async def pause_before_clear(*args, **kwargs):
            entered.set()
            await release.wait()
            return await invoke(*args, **kwargs)

        monkeypatch.setattr(react, "invoke", pause_before_clear)
        run.mailbox.messages = [mail("1", approval=True)]
        lifecycle = AgentLifecycleHandler(run.agent, run.handler._blueprint, run.handler._infra, None)
        event = EventMessage.from_event(
            ToolApprovalResultEvent(team_name="team", member_name="worker", tool_call_id="call-a", approved=True)
        )
        try:
            await run.handler._process_unread_messages("worker")
            await asyncio.wait_for(entered.wait(), 2)
            assert run.agent.has_in_flight_round()
            assert run.agent.has_pending_interrupt()
            await lifecycle.on_tool_approval_result(event)
        finally:
            release.set()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 1
        assert run.llm.call_count == 2
        assert not run.agent.has_pending_interrupt()


@pytest.mark.asyncio
async def test_reply_for_another_tool_cannot_release_the_current_interrupt(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        run.mailbox.messages = [mail("1", approval=True, tool_call_id="expired-call")]
        await run.handler._process_unread_messages("worker")
        assert run.mailbox.read == ["1"]
        assert run.agent.has_pending_interrupt()
        assert run.edit.calls == 0


@pytest.mark.asyncio
async def test_parallel_reply_survives_the_state_clear_window(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        react = run.native.react_agent
        original = react._init_context

        async def pause_after_clear(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(react, "_init_context", pause_after_clear)
        run.mailbox.messages = [mail("1", approval=True), mail("2", approval=True, tool_call_id="call-b")]
        try:
            await run.handler._process_unread_messages("worker")
            await asyncio.wait_for(entered.wait(), 2)
            assert run.agent.has_in_flight_round()
            assert not run.agent.has_pending_interrupt()
            await run.handler._process_unread_messages("worker")
            assert run.mailbox.read == ["1"]
        finally:
            release.set()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.agent.has_pending_interrupt()
        assert run.edit.calls == 1
        monkeypatch.setattr(react, "_init_context", original)
        await run.handler._process_unread_messages("worker")
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 2
        assert run.mailbox.read == ["1", "2"]
        assert not run.agent.has_pending_interrupt()
