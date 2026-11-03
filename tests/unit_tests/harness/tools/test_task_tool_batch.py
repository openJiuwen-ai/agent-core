"""Task groups resolve tool calls while retaining unfinished child executions."""

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.runner import Runner  # noqa: F401 -- use the SDK bootstrap import order
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


class Child:
    def __init__(self, label, gates, started, calls):
        self.card = SimpleNamespace(id=label)
        self.label, self.gates, self.started, self.calls = label, gates, started, calls
        self.cleaned = 0

    async def invoke(self, inputs):
        self.calls.append((self.label, inputs))
        self.started[self.label].set()
        await self.gates[self.label].wait()
        return {"result_type": "answer", "output": self.label + "-done"}

    async def cleanup_task_resources(self):
        self.cleaned += 1


def make_tool(labels=("A", "B", "C")):
    gates = {name: asyncio.Event() for name in labels}
    started = {name: asyncio.Event() for name in labels}
    calls, children = [], []

    def create(_kind, child_session_id, **_kwargs):
        label = labels[len(children)]
        child = Child(label, gates, started, calls)
        children.append(child)
        return child

    parent = SimpleNamespace(
        card=SimpleNamespace(id="parent"),
        deep_config=SimpleNamespace(subagents=[]),
        create_subagent=create,
        resolve_subagent_type=lambda name: "general-purpose",
    )
    tool = TaskTool(
        ToolCard(id="group", name="task_tool", description="test", properties={"resilience": {"timeout_s": 1800}}),
        parent,

    )
    return tool, gates, started, calls, children


def dispatch(*descriptions, existing=(), timeout=None):
    result = {
        "action": "dispatch",
        "tasks": [{"subagent_type": "general-purpose", "task_description": name} for name in descriptions],
    }
    if existing:
        result["task_ids"] = list(existing)
    if timeout is not None:
        result["timeout_s"] = timeout
    return result


@pytest.mark.asyncio
async def test_a_returns_then_c_starts_while_b_is_still_running():
    tool, gates, started, calls, children = make_tool()
    session = Session(session_id="parent-session")
    first = asyncio.create_task(tool.invoke(dispatch("A", "B"), session=session, tool_call_id="first"))
    try:
        await asyncio.wait_for(asyncio.gather(started["A"].wait(), started["B"].wait()), 2)
        assert not first.done()
        gates["A"].set()
        result = await asyncio.wait_for(first, 2)
        assert [item["output"] for item in result.data["ready"]] == ["A-done"]
        (b_id,) = result.data["pending_ids"]
        second = asyncio.create_task(
            tool.invoke(dispatch("C", existing=[b_id]), session=session, tool_call_id="second")
        )
        await asyncio.wait_for(started["C"].wait(), 2)
        assert not gates["B"].is_set() and not second.done()
        gates["B"].set()
        result = await asyncio.wait_for(second, 2)
        assert result.data["ready"][0]["output"] == "B-done"
        assert result.data["ready"][0]["dispatch_tool_call_id"] == "first"
        assert result.data["ready"][0]["dispatch_index"] == 1
        (c_id,) = result.data["pending_ids"]
        gates["C"].set()
        result = await tool.invoke({"action": "wait", "task_ids": [c_id]}, session=session, tool_call_id="third")
        assert result.data["ready"][0]["output"] == "C-done" and not result.data["pending_ids"]
        assert len({entry[1]["conversation_id"] for entry in calls}) == 3
        assert [child.cleaned for child in children] == [1, 1, 1]
    finally:
        for gate in gates.values():
            gate.set()
        await tool.close_task_groups("parent-session", reason="test")
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_wait_timeout_keeps_worker_and_repeat_read_does_not_execute_twice():
    tool, gates, started, calls, _ = make_tool(("A",))
    session = Session(session_id="parent-session")
    try:
        result = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="one")
        (task_id,) = result.data["pending_ids"]
        await asyncio.wait_for(started["A"].wait(), 2)
        result = await tool.invoke(
            {"action": "wait", "task_ids": [task_id], "timeout_s": 0.01}, session=session, tool_call_id="poll"
        )
        assert result.data["timed_out"] and not result.data["ready"]
        gates["A"].set()
        for index in range(2):
            result = await tool.invoke(
                {"action": "wait", "task_ids": [task_id]}, session=session, tool_call_id="read" + str(index)
            )
            assert result.data["ready"][0]["output"] == "A-done"
        assert len(calls) == 1
    finally:
        gates["A"].set()
        await tool.close_task_groups("parent-session", reason="test")


@pytest.mark.asyncio
async def test_invalid_existing_id_does_not_start_new_children():
    tool, _, _, _, children = make_tool(("A",))
    session = Session(session_id="parent-session")
    try:
        result = await tool.invoke(dispatch("A", existing=["foreign"], timeout=0), session=session, tool_call_id="bad")
        assert not result.success and "task_not_found" in str(result.error)
        assert not children
    finally:
        await tool.close_task_groups("parent-session", reason="test")


@pytest.mark.asyncio
async def test_repeated_dispatch_id_reuses_original_batch():
    tool, gates, started, calls, children = make_tool(("A",))
    session = Session(session_id="parent-session")
    try:
        first = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="same")
        second = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="same")
        assert first.data["submitted"] == second.data["submitted"]
        await asyncio.wait_for(started["A"].wait(), 2)
        assert len(children) == len(calls) == 1
        conflict = await tool.invoke(dispatch("changed", timeout=0), session=session, tool_call_id="same")
        assert not conflict.success and "dispatch_conflict" in str(conflict.error)
    finally:
        gates["A"].set()
        await tool.close_task_groups("parent-session", reason="test")


@pytest.mark.asyncio
async def test_closing_other_session_does_not_cancel_current_run():
    tool, gates, started, _, children = make_tool(("A",))
    run_id, token = tool.task_manager.enter_run("parent-session")
    session = Session(session_id="parent-session")
    try:
        result = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="one")
        (task_id,) = result.data["pending_ids"]
        await asyncio.wait_for(started["A"].wait(), 2)
        assert await tool.close_task_groups("other-session", reason="foreign") is None
        gates["A"].set()
        result = await tool.invoke({"action": "wait", "task_ids": [task_id]}, session=session, tool_call_id="read")
        assert result.data["ready"][0]["status"] == "completed"
        assert children[0].cleaned == 1
    finally:
        gates["A"].set()
        await tool.close_task_groups("parent-session", reason="test")
        tool.task_manager.leave_run(run_id, token)


@pytest.mark.asyncio
async def test_close_releases_direct_task_group_records():
    tool, gates, started, _, _ = make_tool(("A",))
    session = Session(session_id="parent-session")
    await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="one")
    await asyncio.wait_for(started["A"].wait(), 2)
    await tool.close_task_groups("parent-session", reason="test")
    assert not tool.task_manager._runs
    gates["A"].set()


@pytest.mark.asyncio
async def test_same_inner_approval_id_resumes_only_addressed_original_child():
    from openjiuwen.core.session import InteractiveInput
    from openjiuwen.core.session.stream import OutputSchema
    from openjiuwen.core.session.interaction.interaction import InteractionOutput
    from openjiuwen.core.foundation.llm import ToolCall
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest, ToolCallInterruptRequest

    children = []
    all_asked = asyncio.Event()
    asked_count = 0

    class AskingChild:
        def __init__(self):
            self.card = SimpleNamespace(id=str(len(children)))
            self.responses, self.cleaned = [], 0
            self.resumed = asyncio.Event()

        async def invoke(self, inputs):
            nonlocal asked_count
            if isinstance(inputs["query"], InteractiveInput):
                value = inputs["query"].user_inputs["same-inner"]
                self.responses.append(value)
                self.resumed.set()
                return {"result_type": "answer", "output": "allowed" if value["approved"] else "rejected"}
            asked_count += 1
            if asked_count == 2:
                all_asked.set()
            call = ToolCall(id="same-inner", name="write_file", type="function", arguments="{}")
            request = ToolCallInterruptRequest.from_tool_call(InterruptRequest(message="Approve?"), call)
            output = OutputSchema(
                type="interaction", index=0, payload=InteractionOutput(id="same-inner", value=request)
            )
            return {"result_type": "interrupt", "interrupt_ids": ["same-inner"], "state": [output]}

        async def cleanup_task_resources(self):
            self.cleaned += 1

    def create(*_args, **_kwargs):
        child = AskingChild()
        children.append(child)
        return child

    parent = SimpleNamespace(
        card=SimpleNamespace(id="parent"),
        deep_config=SimpleNamespace(subagents=[]),
        create_subagent=create,
        resolve_subagent_type=lambda _: "general-purpose",
    )
    tool = TaskTool(ToolCard(id="group", name="task_tool", description="test"), parent)
    session = Session(session_id="approval-session")
    original = dispatch("A", "B")
    try:
        result = await tool.invoke(original, session=session, tool_call_id="dispatch")
        # Wait for both child barriers, then collect their stable approval IDs.
        await asyncio.wait_for(all_asked.wait(), 2)
        result = await tool.invoke(original, session=session, tool_call_id="dispatch")
        assert result["result_type"] == "interrupt" and len(set(result["interrupt_ids"])) == 2
        assert all(child.cleaned == 0 for child in children)
        first_id, second_id = result["interrupt_ids"]
        reply = InteractiveInput()
        reply.update(first_id, {"approved": True})
        result = await tool.invoke({**original, "query": reply}, session=session, tool_call_id="dispatch")
        assert result["result_type"] == "interrupt" and result["interrupt_ids"] == [second_id]
        await asyncio.wait_for(children[0].resumed.wait(), 2)
        assert len(children) == 2 and children[0].responses == [{"approved": True}]
        assert not children[1].responses
        reply = InteractiveInput()
        reply.update(second_id, {"approved": False})
        result = await tool.invoke({**original, "query": reply}, session=session, tool_call_id="dispatch")
        outputs = {item["output"] for item in result.data["ready"]}
        while result.data["pending_ids"]:
            result = await asyncio.wait_for(
                tool.invoke(
                    {"action": "wait", "task_ids": result.data["pending_ids"]}, session=session, tool_call_id="collect"
                ),
                2,
            )
            outputs.update(item["output"] for item in result.data["ready"])
        assert outputs == {"allowed", "rejected"}
        assert len(children) == 2 and all(child.cleaned == 1 for child in children)
    finally:
        await tool.close_task_groups("approval-session", reason="test")


@pytest.mark.asyncio
async def test_cancel_one_child_keeps_sibling_running():
    tool, gates, started, _, children = make_tool(("A", "B"))
    session = Session(session_id="parent-session")
    try:
        result = await tool.invoke(dispatch("A", "B", timeout=0), session=session, tool_call_id="two")
        a_id, b_id = result.data["pending_ids"]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 2)
        result = await tool.invoke({"action": "cancel", "task_ids": [a_id]}, session=session, tool_call_id="cancel")
        assert result.data["tasks"][0]["status"] == "cancelled"
        assert children[0].cleaned == 1 and children[1].cleaned == 0
        gates["B"].set()
        result = await tool.invoke({"action": "wait", "task_ids": [b_id]}, session=session, tool_call_id="wait")
        assert result.data["ready"][0]["output"] == "B-done"
    finally:
        for gate in gates.values():
            gate.set()
        await tool.close_task_groups("parent-session", reason="test")


@pytest.mark.asyncio
async def test_task_budget_is_enforced_after_dispatch_call_has_returned():
    tool, gates, started, _, children = make_tool(("A",))
    tool.task_manager.task_timeout_s = 0.02
    session = Session(session_id="parent-session")
    try:
        result = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="timed")
        (task_id,) = result.data["pending_ids"]
        await asyncio.wait_for(started["A"].wait(), 2)
        result = await asyncio.wait_for(
            tool.invoke({"action": "wait", "task_ids": [task_id]}, session=session, tool_call_id="collect"), 2
        )
        assert result.data["ready"][0]["status"] == "timed_out"
        assert result.data["ready"][0]["error"]["code"] == "task_timeout"
        assert children[0].cleaned == 1
    finally:
        gates["A"].set()
        await tool.close_task_groups("parent-session", reason="test")


def test_group_factory_exposes_one_tool_with_nested_live_type_enum():
    from openjiuwen.harness.tools.subagent.task_tool import create_task_tool

    spec = SimpleNamespace(agent_card=SimpleNamespace(name="general-purpose"))
    parent = SimpleNamespace(card=SimpleNamespace(id="owner"), deep_config=SimpleNamespace(subagents=[spec]))
    tools = create_task_tool(parent, "general-purpose")
    assert len(tools) == 1 and tools[0].card.name == "task_tool"
    params = tools[0].card.input_params
    assert params["properties"]["action"]["enum"] == ["dispatch", "wait", "cancel"]
    assert params["properties"]["tasks"]["items"]["properties"]["subagent_type"]["enum"] == ["general-purpose"]
    assert set(params["properties"]) == {"action", "tasks", "task_ids", "timeout_s"}
    assert params["required"] == ["action"]
    assert "dispatch" in tools[0].card.description


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True, "round", "loop"])
async def test_root_finalization_cancels_unfinished_group_before_final_output(tmp_path, monkeypatch, streaming):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.session.stream import OutputSchema
    from openjiuwen.harness import create_deep_agent
    from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail

    gate, started = asyncio.Event(), asyncio.Event()
    children = []

    class PendingChild:
        card = SimpleNamespace(id="pending-child")
        cleaned = 0

        async def invoke(self, inputs):
            started.set()
            await gate.wait()
            return {"result_type": "answer", "output": "late"}

        async def cleanup_task_resources(self):
            self.cleaned += 1

    rail = SubagentRail()
    parent = create_deep_agent(
        Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="http://test.invalid/v1"
            ),
            model_config=ModelRequestConfig(model="test"),
        ),
        rails=[rail],
        workspace=str(tmp_path),
        auto_create_workspace=False,
        add_general_purpose_agent=True,
        enable_task_loop=False,
        enable_read_image_multimodal=False,
    )

    def create(*_args, **_kwargs):
        child = PendingChild()
        children.append(child)
        return child

    monkeypatch.setattr(parent, "create_subagent", create)

    async def start_group(ctx, session):
        result = await rail.tools[0].invoke(dispatch("A", timeout=0), session=session, tool_call_id="child-call")
        assert result.success
        await asyncio.wait_for(started.wait(), 2)
        return {"result_type": "answer", "output": "premature"}

    async def stream_group(ctx, session, stream_modes):
        yield OutputSchema(type="answer", index=0, payload=await start_group(ctx, session))

    monkeypatch.setattr(parent, "_run_single_round_invoke", start_group)
    monkeypatch.setattr(parent, "_run_single_round_stream", stream_group)
    session = Session(session_id="root-final", card=parent.card)
    try:
        if streaming == "round":
            await parent._ensure_initialized()
            from unittest.mock import AsyncMock
            from openjiuwen.harness.schema.interaction import RoundWorkItem

            async def finish(**kwargs):
                return await start_group(None, session)

            controller = SimpleNamespace(submit_round=AsyncMock(), wait_round_completion=finish)
            monkeypatch.setattr(parent, "prepare_interaction_task_loop", AsyncMock(return_value=(None, controller)))
            monkeypatch.setattr(parent, "_build_interaction_next_work", lambda **kwargs: None)
            saved = []

            async def write(result, session):
                assert children[0].cleaned == 1
                saved.append(result)

            monkeypatch.setattr(parent, "_write_round_result_to_stream", write)
            outcome = await parent.run_one_round(
                RoundWorkItem.user(
                    request_id=None, inputs={"query": "run", "conversation_id": "root-final"}, reset_loop=False
                ),
                "task",
                session,
            )
            assert outcome.error_code is None
            (result,) = saved
        elif streaming == "loop":
            queue = asyncio.Queue()
            parent.deep_config.enable_task_loop = True

            async def rounds(ctx, session):
                await start_group(ctx, session)
                yield {"result_type": "answer", "output": "intermediate"}
                assert children[0].cleaned == 0, "intermediate loop result must retain workers"
                yield {"result_type": "answer", "output": "final"}

            async def write(result, session):
                await queue.put(OutputSchema(type="answer", index=0, payload=result))

            async def iterator():
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    yield chunk

            async def close_stream():
                await queue.put(None)

            monkeypatch.setattr(parent, "_run_task_loop", rounds)
            monkeypatch.setattr(parent, "_write_round_result_to_stream", write)
            monkeypatch.setattr(session, "stream_iterator", iterator)
            monkeypatch.setattr(session, "close_stream", close_stream)
            async for chunk in parent.stream({"query": "run", "conversation_id": "root-final"}, session):
                if chunk.type == "answer":
                    result = chunk.payload
            assert result["output"].startswith("final")
        elif streaming:
            async for chunk in parent.stream({"query": "run", "conversation_id": "root-final"}, session):
                if chunk.type == "answer":
                    assert children[0].cleaned == 1
                    result = chunk.payload
        else:
            result = await parent.invoke({"query": "run", "conversation_id": "root-final"}, session)
        assert result["task_group_summary"]["tasks"][0]["status"] == "cancelled"
        assert result["task_group_summary"]["unfinished"]
        assert "未完成" in result["output"]
        assert children[0].cleaned == 1
        assert not rail.task_manager._runs
    finally:
        gate.set()
        await parent.cancel_background_subtasks("root-final", reason="test")
        parent.ability_manager.teardown_tools()


def test_group_schema_refresh_does_not_nest_control_arguments():
    from openjiuwen.harness.tools.subagent.task_tool import create_task_tool

    parent = SimpleNamespace(card=SimpleNamespace(id="owner"), deep_config=SimpleNamespace(subagents=[]))
    card = create_task_tool(parent, "general-purpose")[0].card
    from openjiuwen.harness.prompts.tools import get_tool_input_params
    card.input_params = get_tool_input_params("task_tool")
    nested = card.input_params["properties"]["tasks"]["items"]["properties"]
    assert set(nested) == {"subagent_type", "task_description", "browser_capabilities"}


def test_subagent_rail_clone_does_not_share_task_manager():
    from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail

    rail = SubagentRail()
    rail.tools = [make_tool()[0]]
    cloned = rail.clone_for_subagent()
    assert cloned.tools is None and cloned.task_manager is None


@pytest.mark.asyncio
async def test_unloading_rail_closes_pending_children():
    from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail

    tool, gates, started, calls, children = make_tool(("A",))
    rail = SubagentRail()
    rail.tools = [tool]
    session = Session(session_id="unload")
    try:
        await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="unload")
        await asyncio.wait_for(started["A"].wait(), 2)
        cleaned = asyncio.Event()
        original = children[0].cleanup_task_resources

        async def cleanup():
            await original()
            cleaned.set()

        children[0].cleanup_task_resources = cleanup
        rail.uninit(SimpleNamespace())
        await asyncio.wait_for(cleaned.wait(), 2)
        assert children[0].cleaned == 1
    finally:
        gates["A"].set()
        await tool.close_task_groups("unload")


@pytest.mark.asyncio
async def test_interaction_internal_followup_reuses_scope_until_logical_task_finishes(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.harness import create_deep_agent
    from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
    from openjiuwen.harness.schema.interaction import RoundWorkItem

    rail = SubagentRail()
    parent = create_deep_agent(
        Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="http://test.invalid/v1"
            ),
            model_config=ModelRequestConfig(model="test"),
        ),
        rails=[rail],
        workspace=str(tmp_path),
        auto_create_workspace=False,
        enable_read_image_multimodal=False,
        add_general_purpose_agent=True,
    )
    await parent._ensure_initialized()
    tool, gates, started, calls, children = make_tool(("A",))
    rail.tools = [tool]
    session = Session(session_id="continuation", card=parent.card)
    next_work = RoundWorkItem.user(request_id="same-user", inputs={"query": "continue"}, reset_loop=False)
    first_work = RoundWorkItem.user(request_id="same-user", inputs={"query": "start"}, reset_loop=False)
    snapshots = []

    async def result(**kwargs):
        if not snapshots:
            await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="pending")
            await asyncio.wait_for(started["A"].wait(), 2)
        snapshots.append(tool.task_manager.scope_if_active("continuation").run_id)
        return {"result_type": "answer", "output": "round"}

    controller = SimpleNamespace(submit_round=AsyncMock(), wait_round_completion=result)
    monkeypatch.setattr(parent, "prepare_interaction_task_loop", AsyncMock(return_value=(None, controller)))
    monkeypatch.setattr(
        parent, "_build_interaction_next_work", lambda **kwargs: next_work if len(snapshots) == 1 else None
    )
    writes = []

    async def write(result, session):
        writes.append(result)

    monkeypatch.setattr(parent, "_write_round_result_to_stream", write)
    try:
        first = await parent.run_one_round(first_work, "first", session)
        assert first.error_code is None and first.next_work is next_work
        assert children[0].cleaned == 0
        second = await parent.run_one_round(next_work, "second", session)
        assert second.error_code is None and second.next_work is None
        assert snapshots[0] == snapshots[1] and children[0].cleaned == 1
        assert "task_group_summary" not in writes[0]
        assert writes[1]["task_group_summary"]["unfinished"]
    finally:
        gates["A"].set()
        await tool.close_task_groups("continuation")
        parent.ability_manager.teardown_tools()


@pytest.mark.asyncio
async def test_task_listener_reports_uncollected_completion_without_consuming_result():
    tool, gates, started, _, _ = make_tool(("A",))
    session = Session(session_id="parent-session")
    notifications, completed = [], asyncio.Event()

    def observe(scope, snapshot):
        notifications.append((scope, snapshot))
        if snapshot["status"] == "completed":
            completed.set()

    def broken(_scope, _snapshot):
        raise RuntimeError("UI unavailable")

    unsubscribe = tool.task_manager.add_task_listener(observe)
    remove_broken = tool.task_manager.add_task_listener(broken)
    try:
        result = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="one")
        await asyncio.wait_for(started["A"].wait(), 2)
        gates["A"].set()
        await asyncio.wait_for(completed.wait(), 2)
        scope, snapshot = notifications[-1]
        assert scope.parent_session_id == "parent-session"
        assert snapshot["task_id"] == result.data["pending_ids"][0]
        assert snapshot["dispatch_tool_call_id"] == "one" and snapshot["dispatch_index"] == 0
        assert snapshot["output"] == "A-done"
        report = await tool.close_task_groups("parent-session")
        assert report["uncollected"] == [snapshot["task_id"]]
        assert [s["status"] for _, s in notifications] == ["running", "completed"]
    finally:
        unsubscribe()
        unsubscribe()
        remove_broken()
        gates["A"].set()
        await tool.close_task_groups("parent-session")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "timed_out", "cancelled", "approval"])
async def test_task_listener_observes_failure_timeout_cancel_and_approval_resume(outcome):
    tool, gates, started, _, _ = make_tool(("A",))
    session = Session(session_id="parent-session")
    observed, changed = [], asyncio.Event()
    def listener(scope, snapshot):
        if snapshot["status"] != "running":
            observed.append(snapshot)
            changed.set()
    unsubscribe = tool.task_manager.add_task_listener(listener)
    original = tool.parent_agent.create_subagent
    def create(*args, **kwargs):
        child = original(*args, **kwargs)
        calls = 0
        async def execute(inputs):
            nonlocal calls
            calls += 1
            started["A"].set()
            if outcome == "failed":
                raise RuntimeError("child failed")
            if outcome == "approval":
                if calls == 1:
                    return {"result_type": "interrupt", "interrupt_ids": ["approval"]}
                return {"result_type": "answer", "output": "approved"}
            await gates["A"].wait()
            return {"result_type": "answer", "output": "done"}
        child.invoke = execute
        return child
    tool.parent_agent.create_subagent = create
    if outcome == "timed_out":
        tool.task_manager.task_timeout_s = 0.01
    try:
        receipt = await tool.invoke(dispatch("A", timeout=0), session=session, tool_call_id="one")
        await asyncio.wait_for(started["A"].wait(), 2)
        scope = tool.task_manager.scope_if_active("parent-session")
        if outcome == "cancelled":
            await tool.task_manager.cancel(scope, receipt.data["pending_ids"])
        await asyncio.wait_for(changed.wait(), 2)
        expected = "waiting_approval" if outcome == "approval" else outcome
        assert observed[0]["status"] == expected
        if outcome == "approval":
            changed.clear()
            await tool.task_manager.resume(scope, receipt.data["pending_ids"][0], "approved")
            await asyncio.wait_for(changed.wait(), 2)
            assert [r["status"] for r in observed] == ["waiting_approval", "completed"]
        unsubscribe()
        count = len(observed)
        await tool.close_task_groups("parent-session")
        assert len(observed) == count
    finally:
        gates["A"].set()
        unsubscribe()
        await tool.close_task_groups("parent-session")
