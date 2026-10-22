"""Direct host-driven actions, SDK image transport and lifecycle; no native/model calls."""

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.desktop.direct import DirectDesktopSession
from openjiuwen.harness.tools.desktop.direct_tool import DirectComputerUseTool
from openjiuwen.harness.tools.desktop.schema import DesktopSettings, Observation


class Lease:
    fd = 99

    def __init__(self):
        self.acquired = self.released = False

    def acquire(self):
        self.acquired = True

    def release(self):
        self.released = True


class Desktop:
    def __init__(self):
        self.actions = []
        self.generation = 0
        self.started = self.closed = False
        self.entered = asyncio.Event()
        self.block_action = self.block_start = self.fail_capture = False

    async def start(self):
        self.started = True
        if self.block_start:
            self.entered.set()
            await asyncio.Event().wait()

    async def observe(self):
        if self.fail_capture:
            raise RuntimeError("capture failed")
        self.generation += 1
        return Observation(
            "aW1hZ2U=",
            100,
            100,
            (0, 0, 100, 100),
            "notes",
            1,
            self.generation,
            elements=[{"id": str(self.generation), "role": "AXTextArea", "value": ""}],
        )

    async def act(self, action, observation):
        assert not self.closed and observation.generation == self.generation
        self.entered.set()
        if self.block_action:
            await asyncio.Event().wait()
        self.actions.append(action)

    async def close(self):
        self.closed = True


def setup(**limits):
    settings = DesktopSettings(
        enabled=True, execution_mode="direct", targets=[{"name": "notes", "pid": 99999}], budgets=limits
    )
    desktop, lease = Desktop(), Lease()
    state = DirectDesktopSession(settings, driver_factory=lambda _: desktop, lease_factory=lambda: lease)
    tool = DirectComputerUseTool(settings, session_factory=lambda: state)
    return tool, state, desktop, lease


async def observe(tool, sid="owner", **kwargs):
    return await tool.invoke({"operation": "observe"}, session=Session(sid), **kwargs)


async def act(tool, token, *, sid="owner", release=False):
    return await tool.invoke(
        {
            "operation": "act",
            "observation_id": token,
            "action": {"kind": "type", "text": "Two lines\nexact."},
            "release_after": release,
        },
        session=Session(sid),
    )


@pytest.mark.asyncio
async def test_one_action_returns_real_sdk_multimodal_without_inference():
    tool, state, desktop, lease = setup()
    first = await observe(tool)
    assert first.success and not desktop.actions and lease.acquired and not lease.released
    result = await act(tool, first.data["observation_id"], release=True)
    assert result.success and len(desktop.actions) == 1
    assert desktop.actions[0].text == "Two lines\nexact."
    assert desktop.closed and lease.released and state.closed
    assert result.data["model_calls"] == 0 and result.data["observation_id"] is None
    text = tool.render_for_llm(result)
    assert "base64" not in text and json.loads(text)["verified"] is False
    images = ReActAgent._build_multimodal_tool_result_messages(result)
    assert images[0].content[1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="


@pytest.mark.asyncio
async def test_done_awaits_worker_cleanup_before_reply_and_releases_only_once():
    tool, state, desktop, lease = setup()
    first = await observe(tool)
    assert '"operation":"done"' in json.loads(tool.render_for_llm(first))["completion_instruction"]
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def close_worker():
        calls.append("close")
        entered.set()
        await finish.wait()
        desktop.closed = True

    desktop.close = close_worker
    task = asyncio.create_task(tool.invoke({"operation": "done"}, session=Session("owner")))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not task.done() and not desktop.closed and not lease.released
        finish.set()
        result = await asyncio.wait_for(task, 1)
        assert result.success and result.data["status"] == "completed_unverified"
        assert result.data["closed"] is True and result.data["verified"] is False
        assert result.data["observation_id"] is None and result.data["model_calls"] == 0
        assert state.steps == 0 and desktop.generation == 1 and not desktop.actions
        assert desktop.closed and lease.released
        assert "has been released" in json.loads(tool.render_for_llm(result))["completion_instruction"]
        assert ReActAgent._build_multimodal_tool_result_messages(result) == []
        repeated = await tool.invoke({"operation": "done"}, session=Session("owner"))
        assert repeated.success and repeated.data["status"] == "completed_unverified"
        assert calls == ["close"]
        assert not (await act(tool, first.data["observation_id"])).success
        assert [event["event"] for event in state.trace][-3:] == ["done_requested", "stopped", "done_requested"]
    finally:
        finish.set()
        await task
        await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "contaminated", "expired", "cancelled"])
async def test_done_does_not_turn_failed_session_into_completion(status):
    tool, state, desktop, lease = setup()
    await observe(tool)
    await state.close(status, "original failure")
    result = await tool.invoke({"operation": "done"}, session=Session("owner"))
    assert not result.success and result.data["status"] == status
    assert result.data["reason"] == "original failure" and result.data["verified"] is False
    assert "completion_instruction" not in json.loads(tool.render_for_llm(result))
    assert desktop.closed and lease.released and not desktop.actions


@pytest.mark.asyncio
async def test_done_requires_owner_and_does_not_start_worker_or_accept_input():
    tool, state, desktop, lease = setup()
    assert not (await tool.invoke({"operation": "done"}, session=Session("owner"))).success
    assert not desktop.started and not lease.acquired
    try:
        await observe(tool)
        assert not (await tool.invoke({"operation": "done"}, session=Session("foreign"))).success
        assert not (
            await tool.invoke(
                {"operation": "done", "action": {"kind": "type", "text": "unexpected"}}, session=Session("owner")
            )
        ).success
        assert not state.closed and not lease.released and not desktop.actions
    finally:
        await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("ending", ["done", "refuses", "iteration_limit", "cancel", "error"])
async def test_real_react_loop_uses_normal_completion_and_always_reaps_worker(monkeypatch, ending, streaming):
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    tool, state, desktop, lease = setup()
    agent = ReActAgent(card=AgentCard(name="desktop_completion_test"))
    agent.configure(
        ReActAgentConfig().configure_model("unused").configure_max_iterations(2 if ending == "iteration_limit" else 6)
    )
    agent.context_engine = ContextEngine(
        ContextEngineConfig(enable_tiktoken_counter=False, enable_openrouter_model_context_window_tokens=False)
    )
    agent.ability_manager.add(tool.card)
    calls = []

    async def model_response(**kwargs):
        calls.append(kwargs)
        count = len(calls)
        assert "tool_choice" not in kwargs
        if count == 1 or count == 2 and ending == "done":
            return AssistantMessage(
                tool_calls=[
                    {
                        "id": f"call-{count}",
                        "type": "function",
                        "name": "computer_use",
                        "arguments": json.dumps({"operation": "observe" if count == 1 else "done"}),
                    }
                ]
            )
        if count == 2 and ending == "cancel":
            raise asyncio.CancelledError()
        if count == 2 and ending == "error":
            raise RuntimeError("synthetic model error")
        if count == 3 and ending == "done":
            assert desktop.closed and lease.released
            return AssistantMessage(content="Desktop control is released.")
        if ending == "iteration_limit":
            return AssistantMessage(
                tool_calls=[
                    {
                        "id": f"call-{count}",
                        "type": "function",
                        "name": "computer_use",
                        "arguments": json.dumps({"operation": "observe"}),
                    }
                ]
            )
        return AssistantMessage(content="I will close the desktop now.")

    async def stream_response(**kwargs):
        yield await model_response(**kwargs)

    monkeypatch.setattr(agent, "_get_llm", lambda: NS(invoke=model_response, stream=stream_response))
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_: tool)
    try:
        if ending in {"cancel", "error"}:
            with pytest.raises(asyncio.CancelledError if ending == "cancel" else RuntimeError):
                await agent.invoke({"query": "Observe, then finish."}, session=Session("owner"), _streaming=streaming)
        else:
            result = await agent.invoke(
                {"query": "Observe, then finish."}, session=Session("owner"), _streaming=streaming
            )
            assert result["result_type"] == ("error" if ending == "iteration_limit" else "answer")
        assert state.closed and desktop.closed and lease.released and not desktop.actions
        assert state.status == ("completed_unverified" if ending == "done" else "cancelled")
        assert not agent.agent_callback_manager.has_hooks(AgentCallbackEvent.BEFORE_MODEL_CALL)
        assert not agent.agent_callback_manager.has_hooks(AgentCallbackEvent.AFTER_MODEL_CALL)
        assert not agent.agent_callback_manager.has_hooks(AgentCallbackEvent.AFTER_INVOKE)
        assert len(calls) == (3 if ending == "done" else 2)
        assert not any(event["event"] == "completion_reminder" for event in state.trace)
    finally:
        await tool.close()
        await agent.agent_callback_manager.clear()


@pytest.mark.asyncio
async def test_completion_guard_is_context_owned_and_does_not_block_other_tools():
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, ModelCallInputs
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    tool, state, desktop, _ = setup()
    engine, context = await context_fixture()
    agent = ReActAgent(card=AgentCard(name="desktop_completion_isolation"))
    ctx = AgentCallbackContext(agent=agent, context=context, session=Session("owner"))
    try:
        await observe(tool, _tool_callback_context=ctx)
        guard = tool._completion_guard
        await observe(tool, _tool_callback_context=ctx)
        assert tool._completion_guard is guard
        sibling = await engine.create_context(session=Session("other-context"))
        assert sibling is not context
        for sid, other_context in (("foreign", context), ("owner", sibling)):
            foreign = AgentCallbackContext(
                agent=agent,
                context=other_context,
                session=Session(sid),
                inputs=ModelCallInputs(response=AssistantMessage(content="Done.")),
            )
            await foreign.fire(AgentCallbackEvent.BEFORE_MODEL_CALL)
            assert not hasattr(foreign.inputs, "tool_choice")
            await foreign.fire(AgentCallbackEvent.AFTER_MODEL_CALL)
            await foreign.fire(AgentCallbackEvent.AFTER_INVOKE)
            assert not foreign.consume_model_continue_request() and not state.closed
        ctx.inputs = ModelCallInputs(
            response=AssistantMessage(
                tool_calls=[
                    {"id": "search", "type": "function", "name": "tool_search", "arguments": '{"query":"document"}'}
                ]
            )
        )
        await ctx.fire(AgentCallbackEvent.BEFORE_MODEL_CALL)
        assert not hasattr(ctx.inputs, "tool_choice")
        await ctx.fire(AgentCallbackEvent.AFTER_MODEL_CALL)
        assert not ctx.consume_model_continue_request() and not desktop.closed
        await tool.invoke({"operation": "done"}, _tool_callback_context=ctx)
        assert not guard.registrations
        ctx.inputs = ModelCallInputs()
        await ctx.fire(AgentCallbackEvent.BEFORE_MODEL_CALL)
        assert not hasattr(ctx.inputs, "tool_choice")
    finally:
        await tool.close()
        await agent.agent_callback_manager.clear()


@pytest.mark.asyncio
async def test_stale_tokens_and_foreign_sessions_never_post_input():
    tool, _, desktop, _ = setup()
    try:
        first = await observe(tool)
        assert not (await act(tool, first.data["observation_id"], sid="foreign")).success
        assert not (await observe(tool, "foreign")).success
        assert not (await tool.invoke({"operation": "close"}, session=Session("foreign"))).success
        second = await act(tool, first.data["observation_id"])
        assert second.success
        assert not (await act(tool, first.data["observation_id"])).success
        assert len(desktop.actions) == 1
    finally:
        await tool.close()


@pytest.mark.asyncio
async def test_stop_between_calls_closes_idle_worker_and_does_not_reopen_for_act():
    tool, state, desktop, lease = setup()
    result = await observe(tool)
    await tool.cancel_session("foreign")
    assert not desktop.closed
    await tool.cancel_session("owner")
    assert desktop.closed and lease.released and state.closed
    assert not (await act(tool, result.data["observation_id"])).success
    assert not desktop.actions


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "act"])
async def test_cancel_reaps_before_releasing_lease(phase):
    tool, state, desktop, lease = setup()
    if phase == "start":
        desktop.block_start = True
        task = asyncio.create_task(observe(tool))
    else:
        result = await observe(tool)
        desktop.block_action = True
        task = asyncio.create_task(act(tool, result.data["observation_id"]))
    await desktop.entered.wait()
    await tool.cancel_session("owner")
    assert task.cancelled() and desktop.closed and lease.released and state.closed
    assert not desktop.actions


@pytest.mark.asyncio
async def test_absolute_deadline_cleans_up_between_model_calls():
    tool, state, desktop, lease = setup(wall_seconds=0.05)
    result = await observe(tool)
    await asyncio.wait_for(asyncio.shield(state.watchdog), 1)
    assert desktop.closed and lease.released and state.status == "expired"
    assert not (await act(tool, result.data["observation_id"])).success


@pytest.mark.asyncio
async def test_failure_after_action_does_not_retry_or_claim_verified_success():
    tool, state, desktop, lease = setup()
    first = await observe(tool)
    desktop.fail_capture = True
    result = await act(tool, first.data["observation_id"])
    assert not result.success and result.data["verified"] is False
    assert len(desktop.actions) == 1 and desktop.closed and lease.released
    assert state.steps == 1 and result.data["observation_id"] is None


@pytest.mark.asyncio
async def test_step_limit_stops_additional_input():
    tool, _, desktop, lease = setup(max_steps=1)
    first = await observe(tool)
    second = await act(tool, first.data["observation_id"])
    assert not (await act(tool, second.data["observation_id"])).success
    assert len(desktop.actions) == 1 and lease.released


@pytest.mark.asyncio
async def test_sdk_context_owns_all_desktop_images_and_unrelated_user_images():
    engine = ContextEngine(ContextEngineConfig(enable_tiktoken_counter=False))
    context = await engine.create_context()
    user = UserMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,dXNlcg=="}}])
    await context.add_messages(user)
    tool, _, _, _ = setup()
    callback = NS(context=context)
    try:
        first = await observe(tool, _tool_callback_context=callback)
        await context.add_messages(ReActAgent._build_multimodal_tool_result_messages(first))
        second = await observe(tool, _tool_callback_context=callback)
        await context.add_messages(ReActAgent._build_multimodal_tool_result_messages(second))
        window = await context.get_context_window()
        urls = [
            part["image_url"]["url"]
            for m in window.get_messages()
            if isinstance(m.content, list)
            for part in m.content
            if part.get("type") == "image_url"
        ]
        assert urls == ["data:image/png;base64,dXNlcg==", *["data:image/png;base64,aW1hZ2U="] * 2]
    finally:
        await tool.close()


@pytest.mark.asyncio
async def test_invalid_request_and_missing_session_do_not_start_driver():
    tool, _, desktop, _ = setup()
    assert not (await tool.invoke({"operation": "observe"})).success
    assert not (await tool.invoke({"task": "do everything"}, session=Session("owner"))).success
    assert not (await tool.invoke({"operation": "act", "action": {"kind": "done"}}, session=Session("owner"))).success
    assert not desktop.started


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,fields",
    [
        ("focus", {"target": "notes"}),
        ("focus", {"element_id": "observed-editor"}),
        ("click", {"element_id": "1"}),
        ("click", {"x": 21, "y": 37}),
        ("type", {"text": "Résumé\n费用: ₹125;  punctuation!?"}),
        ("type", {"text": "alpha\tbeta\nγ"}),
        ("shortcut", {"keys": ["cmd", "a"]}),
        ("scroll", {"direction": "left", "amount": 4}),
        ("wait", {"seconds": 0.25}),
    ],
)
async def test_flat_actions_preserve_model_selected_operation_and_values(operation, fields):
    import jsonschema

    tool, _, desktop, _ = setup()
    try:
        first = await observe(tool)
        request = {"operation": operation, "observation_id": first.data["observation_id"], **fields}
        jsonschema.validate(request, tool.card.input_params)
        result = await tool.invoke(request, session=Session("owner"))
        assert result.success and len(desktop.actions) == 1
        assert desktop.actions[0].kind == operation
        for name, value in fields.items():
            assert getattr(desktop.actions[0], name) == value
        assert result.data["observation_id"] != first.data["observation_id"]
        assert result.data["model_calls"] == 0
    finally:
        await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_call",
    [
        {"kind": "type", "text": "different task"},
        {"operation": "act", "kind": "type", "text": "different task"},
        {"operation": "type", "text": "value"},
        {"operation": "type", "observation_id": "current", "text": "value", "element_id": "1"},
        {"operation": "type", "observation_id": "current"},
        {"operation": "type", "observation_id": "current", "text": 123},
        {"operation": "type", "observation_id": "current", "text": "value", "action": {"kind": "click"}},
        {"operation": "click", "observation_id": "current", "element_id": "1", "x": 5, "y": 5},
        {"operation": "shortcut", "observation_id": "current", "keys": []},
        {"operation": "type", "observation_id": "stale", "text": "value"},
    ],
)
async def test_protocol_errors_are_actionable_and_never_silently_repaired(invalid_call):
    tool, state, desktop, _ = setup()
    try:
        first = await observe(tool)
        request = dict(invalid_call)
        if request.get("observation_id") == "current":
            request["observation_id"] = first.data["observation_id"]
        result = await tool.invoke(request, session=Session("owner"))
        rendered = json.loads(tool.render_for_llm(result))
        assert not result.success and not desktop.actions
        assert rendered["executed"] is False and rendered["retryable"] is True
        assert "flat computer_use" in rendered["correction"]
        assert rendered["observation_id"] == first.data["observation_id"] == state.token
        assert desktop.generation == 1
    finally:
        await tool.close()


@pytest.mark.asyncio
async def test_valid_call_resets_rejections_and_foreign_errors_do_not_close_owner():
    tool, state, desktop, lease = setup()
    bad = {"kind": "type", "text": "arbitrary value"}
    try:
        await observe(tool)
        for _ in range(4):
            result = await tool.invoke(bad, session=Session("foreign"))
            assert not result.success and "observation_id" not in tool.render_for_llm(result)
        for _ in range(2):
            assert not (await tool.invoke(bad, session=Session("owner"))).success
        assert not state.closed and not lease.released
        corrected = await tool.invoke(
            {"operation": "type", "observation_id": state.token, "text": "other content"}, session=Session("owner")
        )
        assert corrected.success and len(desktop.actions) == 1
        for count in (1, 2):
            result = await tool.invoke(bad, session=Session("owner"))
            assert result.data["invalid_call_count"] == count and not state.closed
        assert (await tool.invoke({"operation": "done"}, session=Session("owner"))).success
    finally:
        await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("start_session", [False, True])
async def test_real_react_stops_invalid_call_loop_without_another_model_request(monkeypatch, streaming, start_session):
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    tool, state, desktop, lease = setup()
    agent = ReActAgent(card=AgentCard(name="desktop_protocol_test"))
    agent.configure(ReActAgentConfig().configure_model("unused").configure_max_iterations(12))
    agent.context_engine = ContextEngine(
        ContextEngineConfig(enable_tiktoken_counter=False, enable_openrouter_model_context_window_tokens=False)
    )
    agent.ability_manager.add(tool.card)
    calls = []

    async def response(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 3 + int(start_session), "SDK did not honor bounded protocol failure"
        args = (
            {"operation": "observe"}
            if start_session and len(calls) == 1
            else {"operation": "act", "kind": "type", "text": "unrelated sample"}
        )
        return AssistantMessage(
            tool_calls=[
                {
                    "id": f"protocol-{len(calls)}",
                    "type": "function",
                    "name": "computer_use",
                    "arguments": json.dumps(args),
                }
            ]
        )

    async def stream_response(**kwargs):
        yield await response(**kwargs)

    monkeypatch.setattr(agent, "_get_llm", lambda: NS(invoke=response, stream=stream_response))
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_: tool)
    try:
        result = await agent.invoke({"query": "Use the desktop."}, session=Session("owner"), _streaming=streaming)
        assert result["result_type"] == "error" and "three consecutive invalid" in result["output"]
        assert len(calls) == 3 + int(start_session) and not desktop.actions
        if start_session:
            assert state.closed and desktop.closed and lease.released and state.status == "failed"
            assert not tool._completion_guard.registrations
        else:
            assert not desktop.started and not lease.acquired
    finally:
        await tool.close()
        await agent.agent_callback_manager.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "action"])
async def test_absolute_deadline_cancels_inflight_native_work(phase):
    tool, state, desktop, lease = setup(wall_seconds=0.05)
    if phase == "start":
        desktop.block_start = True
        task = asyncio.create_task(observe(tool))
    else:
        first = await observe(tool)
        desktop.block_action = True
        task = asyncio.create_task(act(tool, first.data["observation_id"]))
    await desktop.entered.wait()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(asyncio.gather(state.watchdog, return_exceptions=True), 1)
    assert state.closed and desktop.closed and lease.released and not desktop.actions
    assert state.status == "expired"


@pytest.mark.asyncio
async def test_simultaneous_tool_call_is_rejected_without_second_input():
    tool, _, desktop, _ = setup()
    first = await observe(tool)
    desktop.block_action = True
    task = asyncio.create_task(act(tool, first.data["observation_id"]))
    await desktop.entered.wait()
    second = await act(tool, first.data["observation_id"])
    assert not second.success and "busy" in second.error
    await tool.cancel_session("owner")
    assert task.cancelled() and not desktop.actions


@pytest.mark.asyncio
async def test_ineffective_clicks_stop_without_a_model_retry():
    tool, state, desktop, lease = setup()
    result = await observe(tool)
    for _ in range(tool.settings.stall_click_limit):
        result = await tool.invoke(
            {
                "operation": "act",
                "observation_id": result.data["observation_id"],
                "action": {"kind": "click", "x": 20, "y": 20},
            },
            session=Session("owner"),
        )
    assert not result.success and result.data["status"] == "stalled"
    assert state.steps == tool.settings.stall_click_limit and desktop.closed and lease.released
    assert result.data["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_real_react_finishes_stall_without_reopening_desktop(monkeypatch, streaming):
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    tool, state, desktop, lease = setup()
    agent = ReActAgent(card=AgentCard(name="desktop_stall_test"))
    agent.configure(ReActAgentConfig().configure_model("unused").configure_max_iterations(12))
    agent.context_engine = ContextEngine(
        ContextEngineConfig(enable_tiktoken_counter=False, enable_openrouter_model_context_window_tokens=False)
    )
    agent.ability_manager.add(tool.card)
    calls = []

    async def response(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 1 + tool.settings.stall_click_limit, "host continued inference after native stall"
        args = (
            {"operation": "observe"}
            if len(calls) == 1
            else {
                "operation": "click",
                "observation_id": state.token,
                "x": 20,
                "y": 20,
            }
        )
        return AssistantMessage(
            tool_calls=[
                {
                    "id": f"stall-{len(calls)}",
                    "type": "function",
                    "name": "computer_use",
                    "arguments": json.dumps(args),
                }
            ]
        )

    async def stream_response(**kwargs):
        yield await response(**kwargs)

    monkeypatch.setattr(agent, "_get_llm", lambda: NS(invoke=response, stream=stream_response))
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_: tool)
    try:
        result = await agent.invoke({"query": "Use the desktop."}, session=Session("owner"), _streaming=streaming)
        assert result["result_type"] == "error" and "repeated ineffective clicks" in result["output"]
        assert "not confirmed" in result["output"]
        assert len(calls) == 1 + tool.settings.stall_click_limit
        assert state.closed and state.status == "stalled" and desktop.closed and lease.released
        assert not tool._completion_guard.registrations
    finally:
        await tool.close()
        await agent.agent_callback_manager.clear()


@pytest.mark.asyncio
async def test_two_direct_tools_respect_shared_lease(tmp_path):
    from openjiuwen.harness.tools.desktop.ownership import DesktopLease

    tool1, _, _, _ = setup()
    tool2, _, _, _ = setup()
    drivers = [Desktop(), Desktop()]
    for tool, driver in zip([tool1, tool2], drivers):
        tool.session_factory = lambda t=tool, d=driver: DirectDesktopSession(
            t.settings, driver_factory=lambda _: d, lease_factory=lambda: DesktopLease(tmp_path / "lease")
        )
    try:
        assert (await observe(tool1)).success
        result = await observe(tool2, sid="other")
        assert not result.success and not drivers[1].started
        assert not drivers[0].closed
    finally:
        await tool1.close()
        await tool2.close()


async def context_fixture():
    engine = ContextEngine(ContextEngineConfig(enable_tiktoken_counter=False, default_window_round_num=10))
    context = await engine.create_context()
    await context.add_messages(
        UserMessage(content="Copy the original value exactly, including spaces and punctuation.")
    )
    return engine, context


async def invoke_with_context(tool, context, args, call_id):
    await context.add_messages(
        AssistantMessage(
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "name": "computer_use",
                    "arguments": json.dumps(args),
                }
            ]
        )
    )
    result = await tool.invoke(args, _tool_callback_context=NS(context=context, session=Session("owner")))
    await context.add_messages(ToolMessage(content=tool.render_for_llm(result), tool_call_id=call_id))
    await context.add_messages(ReActAgent._build_multimodal_tool_result_messages(result))
    return result


async def model_window(context):
    return await context.get_context_window(
        system_messages=[SystemMessage(content="Keep these system rules intact.")],
        tools=[ToolInfo(name="computer_use"), ToolInfo(name="read_file"), ToolInfo(name="ask_user")],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["done", "close", "failure", "cancel"])
async def test_tool_leaves_history_processors_and_catalog_to_host(ending):
    engine, context = await context_fixture()
    tool, state, desktop, _ = setup()
    schemas = [ToolInfo(name=name) for name in ("computer_use", "tool_search", "tool_call", "ask_user", "read_file")]
    calls = []

    async def existing_host_mutator(_context, window):
        calls.append(True)
        return window

    context._window_mutators.append(existing_host_mutator)
    original_mutators = context._window_mutators
    try:
        for number in range(3):
            before = [m.model_dump() for m in context.get_messages()]
            await invoke_with_context(tool, context, {"operation": "observe"}, str(number))
            assert [m.model_dump() for m in context.get_messages()][: len(before)] == before
            assert context._window_mutators is original_mutators
            assert context._window_mutators == [existing_host_mutator]
            window = await context.get_context_window(tools=schemas)
            assert window.tools == schemas
            receipts = [json.loads(m.content) for m in window.context_messages if m.role == "tool"]
            assert len(receipts) == number + 1
            assert all("observation" in receipt for receipt in receipts)
        if ending in {"done", "close"}:
            await invoke_with_context(tool, context, {"operation": ending}, "release")
        elif ending == "failure":
            desktop.fail_capture = True
            await invoke_with_context(tool, context, {"operation": "observe"}, "failed")
        else:
            await tool.cancel_session("owner")
        assert state.closed
        before = [m.model_dump() for m in context.get_messages()]
        window = await context.get_context_window(tools=schemas)
        assert window.tools == schemas
        assert [m.model_dump() for m in context.get_messages()] == before
        assert context._window_mutators is original_mutators
        assert len(calls) == 4
        sibling = await engine.create_context(session=Session("sibling"))
        assert (await sibling.get_context_window(tools=schemas)).tools == schemas
    finally:
        await tool.close()
