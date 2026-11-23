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
from openjiuwen.core.common.run_context import RunContext
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import InterruptionState
from openjiuwen.core.single_agent.interrupt.response import InterruptKind
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, pending_interrupts
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import DeepAgentParts
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from tests.unit_tests.agent_teams.harness.fixtures import wait_for_state
from tests.unit_tests.agent_teams.test_pending_approval_mailbox import mail, mailbox_handler
from tests.unit_tests.fixtures.deep_agent_interrupts import trip_workflow
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


class CountEdit(Tool):
    def __init__(self, name="edit_file"):
        super().__init__(
            ToolCard(
                id="approval-" + name,
                name=name,
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
async def runtime(tmp_path, monkeypatch, *, parallel=False, mixed=False, role=TeamRole.TEAMMATE):
    monkeypatch.setenv("OPENJIUWEN_HOME", str(tmp_path / "home"))
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="fake", api_base="http://unused.invalid"
        ),
        model_config=ModelRequestConfig(model="mock-model"),
    )
    llm = MockLLMModel()
    ids = ["call-a", "call-b"] if parallel or mixed else ["call-a"]
    llm.set_responses(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id=tool_id, type="function",
                             name="confirm_edit" if mixed and tool_id == "call-a" else "edit_file", arguments="{}")
                    for tool_id in ids
                ],
            ),
            create_text_response("done"),
            create_text_response("done"),
        ]
    )
    monkeypatch.setattr(Model, "stream", lambda self, *a, **kw: llm.stream(*a, **kw))
    monkeypatch.setattr(Model, "invoke", lambda self, *a, **kw: llm.invoke(*a, **kw))
    edit = CountEdit()
    other = CountEdit("confirm_edit")
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
    rails = [permission, ConfirmInterruptRail(tool_names=["confirm_edit"])] if mixed else [permission]
    tools = [edit, other] if mixed else [edit]
    spec = NS(
        resolve_parts=lambda context=None: DeepAgentParts(
            config=config, rails=rails, tool_cards=[tool.card for tool in tools], tool_instances=tools
        )
    )
    await Runner.start()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=role, member_name="worker")
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
        yield NS(agent=agent, native=native, harness=harness, handler=handler, mailbox=mailbox,
                 edit=edit, other=other, llm=llm)
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


def approval_answer(call_id, approved):
    answer = InteractiveInput()
    answer.update(call_id, {"approved": approved, "feedback": "", "auto_confirm": False})
    return answer


async def record_user_inputs(run):
    seen = []

    async def record(ctx):
        seen.append((ctx.inputs.source, list(ctx.inputs.parts), ctx.extra.get("run_context")))

    await run.native.react_agent.register_callback(AgentCallbackEvent.ON_USER_MESSAGE, record)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("immediate", [True, False])
@pytest.mark.parametrize("approved", [True, False])
async def test_direct_leader_input_waits_for_exact_permission_answer(tmp_path, monkeypatch, immediate, approved):
    async with runtime(tmp_path, monkeypatch, role=TeamRole.LEADER) as run:
        session = run.native.loop_session
        pending = pending_interrupts(session.get_state(INTERRUPTION_KEY))
        seen = await record_user_inputs(run)
        context = RunContext(extra={"request": "follow-up"})
        await asyncio.wait_for(run.agent.deliver_input("new task", use_steer=immediate, run_context=context), 1)

        assert run.native.state is HarnessState.IDLE
        assert run.native.active_round is None
        assert pending_interrupts(session.get_state(INTERRUPTION_KEY)) == pending
        queued, = run.native._st.pending_queue
        assert (queued.seq, queued.content, queued.immediate) == (2, "new task", immediate)
        assert queued.run_context is context
        assert run.edit.calls == 0 and run.llm.call_count == 1 and not seen

        await run.agent.resume_interrupt(approval_answer("call-a", approved))
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == int(approved)
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert seen == [("query", ["new task"], context)]
        assert run.llm.call_count == 3
        assert any(msg.role == "user" and msg.content == "new task" for msg in run.llm.call_history[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("first_call", ["call-a", "call-b"])
async def test_pending_inputs_keep_context_fifo_until_all_interrupts_resolve(tmp_path, monkeypatch, mixed, first_call):
    async with runtime(tmp_path, monkeypatch, parallel=True, mixed=mixed) as run:
        pending = pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))
        assert {item.kind for item in pending} == (
            {InterruptKind.PERMISSION, InterruptKind.CONFIRM} if mixed else {InterruptKind.PERMISSION}
        )
        seen = await record_user_inputs(run)
        run.llm.responses.extend([create_text_response("done"), create_text_response("done")])
        first, second = RunContext(extra={"request": 1}), RunContext(extra={"request": 2})
        inputs = [("a", first), ("b", first), ("c", second), ("d", first)]
        for text, context in inputs:
            await run.agent.deliver_input(text, run_context=context)
        queued = list(run.native._st.pending_queue)
        assert [(item.content, item.run_context) for item in queued] == inputs
        assert [item.seq for item in queued] == [2, 3, 4, 5]

        await run.agent.resume_interrupt(approval_answer(first_call, False))
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert len(pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))) == 1
        assert list(run.native._st.pending_queue) == queued and not seen

        await run.agent.resume_interrupt(approval_answer("call-b" if first_call == "call-a" else "call-a", False))
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert seen == [("query", ["a", "b"], first), ("query", ["c"], second), ("query", ["d"], first)]
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert run.edit.calls == run.other.calls == 0
        assert run.llm.call_count == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("after_clear", [False, True])
@pytest.mark.parametrize("immediate", [True, False])
async def test_input_during_typed_resume_keeps_running_semantics(tmp_path, monkeypatch, after_clear, immediate):
    async with runtime(tmp_path, monkeypatch) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        react = run.native.react_agent
        method = "_init_context" if after_clear else "invoke"
        original = getattr(react, method)
        resumes = []
        handle_resume = react._handle_resume
        seen = await record_user_inputs(run)

        async def hold(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        async def record_resume(state, user_input, *args, **kwargs):
            resumes.append(user_input)
            return await handle_resume(state, user_input, *args, **kwargs)

        monkeypatch.setattr(react, method, hold)
        monkeypatch.setattr(react, "_handle_resume", record_resume)
        answer = approval_answer("call-a", False)
        context = RunContext(extra={"request": "during-resume"})
        try:
            await run.agent.resume_interrupt(answer)
            await asyncio.wait_for(entered.wait(), 2)
            assert run.native.state is HarnessState.RUNNING
            assert run.agent.has_pending_interrupt() is not after_clear
            await run.agent.deliver_input("during resume", use_steer=immediate, run_context=context)
            assert len(run.native._st.pending_queue) == int(not immediate)
        finally:
            release.set()
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert resumes == [answer]
        assert seen == [("steering" if immediate else "query", ["during resume"], context)]
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert run.edit.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("after_settle", [False, True])
async def test_input_waiting_at_interrupt_settlement_is_kept(tmp_path, monkeypatch, after_settle):
    async with runtime(tmp_path, monkeypatch, parallel=True) as run:
        entered, release = asyncio.Event(), asyncio.Event()
        original = run.native._on_round_done
        seen = await record_user_inputs(run)

        async def hold(cmd):
            if after_settle:
                await original(cmd)
            entered.set()
            await release.wait()
            if not after_settle:
                await original(cmd)

        monkeypatch.setattr(run.native, "_on_round_done", hold)
        delivery = None
        try:
            await run.agent.resume_interrupt(approval_answer("call-a", False))
            await asyncio.wait_for(entered.wait(), 2)
            delivery = asyncio.create_task(run.agent.deliver_input("at settlement"))
            await asyncio.sleep(0)
            assert not delivery.done()
        finally:
            release.set()
            if delivery is not None:
                await delivery
        monkeypatch.setattr(run.native, "_on_round_done", original)
        assert run.native.state is HarnessState.IDLE
        assert run.native._st.pending_queue[0].content == "at settlement"
        assert run.llm.call_count == 1 and not seen
        await run.agent.resume_interrupt(approval_answer("call-b", False))
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert [(source, parts) for source, parts, _ in seen] == [("query", ["at settlement"])]
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert run.edit.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", InterruptKind.CONFIRM, InterruptKind.ASK_USER, InterruptKind.TEAM_TOOL_APPROVAL, None])
async def test_idle_permission_guard_uses_existing_kind_compatibility(tmp_path, monkeypatch, kind):
    async with runtime(tmp_path, monkeypatch) as run:
        state = run.native.loop_session.get_state(INTERRUPTION_KEY)
        request = state.interrupted_tools["call-a"].interrupt_requests["call-a"]
        request.interrupt_kind = kind or ""
        request.metadata.pop("interrupt_kind", None)
        if kind is None:
            request.metadata["interrupt_kind"] = InterruptKind.PERMISSION
        run.native.loop_session.update_state({INTERRUPTION_KEY: state})
        await run.agent.deliver_input("ordinary text")
        if kind is None:
            assert run.native.state is HarnessState.IDLE
            assert run.native._st.pending_queue[0].content == "ordinary text"
            assert run.native._st.round_id_counter == 1
        else:
            assert run.native._st.round_id_counter == 2
            assert not run.native._st.pending_queue
            assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert run.edit.calls == 0


@pytest.mark.asyncio
async def test_legacy_workflow_still_accepts_plain_text_feedback(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        await run.agent.resume_interrupt(approval_answer("call-a", False))
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        workflow = trip_workflow()
        run.native.react_agent.ability_manager.add(workflow)
        run.llm.set_responses([
            AssistantMessage(content="", tool_calls=[ToolCall(id="trip", type="function", name="plan_trip", arguments="{}")]),
            create_text_response("trip complete"),
        ])
        await run.agent.deliver_input("plan a trip")
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert isinstance(run.native.loop_session.get_state(INTERRUPTION_KEY), InterruptionState)
        assert not pending_interrupts(run.native.loop_session.get_state(INTERRUPTION_KEY))

        await run.agent.deliver_input("Paris")
        assert await wait_for_state(run.harness, HarnessState.IDLE)
        assert not run.agent.has_pending_interrupt() and not run.native._st.pending_queue
        assert any(msg.role == "tool" and "Paris" in str(msg.content) for msg in run.llm.call_history[-1])
        assert run.llm.call_count == 2
