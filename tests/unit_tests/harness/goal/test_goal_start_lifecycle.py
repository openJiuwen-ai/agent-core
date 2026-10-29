# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal execution through the factory, interaction supervisor, and real tools."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.goal.schema import GoalRecord, GoalStatus
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
from openjiuwen.harness.tools.goal import SubmitGoalReportTool
from tests.unit_tests.fixtures.mock_llm import create_text_response, create_tool_call_response


def _report(status: str) -> AssistantMessage:
    return create_tool_call_response(
        "submit_goal_report",
        json.dumps({"status": status, "evidence": f"Verified {status} progress"}),
        tool_call_id=f"report-{uuid.uuid4().hex}",
    )


class _ScriptModel:
    """Separate main/assessor scripts, optionally sharing one response queue."""

    def __init__(self, statuses: list[str], *, shared: bool = False) -> None:
        self.responses: list[AssistantMessage] = []
        for status in statuses:
            self.responses.extend([_report(status), create_text_response("Attempt finished.")])
        self.assessment = create_text_response(
            json.dumps({"status": "complete", "evidence": "Independent verification passed"})
        )
        if shared:
            self.responses.append(self.assessment)
        self.shared = shared
        self.main_calls = 0
        self.assessor_calls = 0

    def _next(self) -> AssistantMessage:
        if not self.responses:
            raise AssertionError("Unexpected extra model call after the scripted goal")
        return self.responses.pop(0)

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.main_calls += 1
        response = self._next()
        yield AssistantMessageChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
        )

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        self.assessor_calls += 1
        return self._next() if self.shared else self.assessment


class _InitCaptureRail(AgentRail):
    def __init__(self) -> None:
        super().__init__()
        self.init_count = 0

    def init(self, agent: Any) -> None:
        self.init_count += 1


def _make_agent(
    monkeypatch: pytest.MonkeyPatch,
    script: _ScriptModel,
    *,
    rails: list[AgentRail] | None = None,
) -> DeepAgent:
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://unused.invalid",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="scripted-goal-model"),
    )
    monkeypatch.setattr(Model, "invoke", script.invoke)
    monkeypatch.setattr(Model, "stream", script.stream)
    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", lambda model: None)
    return create_deep_agent(
        model=model,
        auto_create_workspace=False,
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        enable_task_loop=True,
        rails=rails,
    )


async def _run_goal(
    agent: DeepAgent,
    *,
    max_attempts: int = 10,
    session: Session | None = None,
) -> tuple[GoalRecord, list[Any]]:
    if session is None:
        session = create_agent_session(session_id=f"goal-start-{uuid.uuid4().hex}", card=agent.card)
        await session.pre_run(inputs={})
    await agent.start(session=session)
    await agent.goal_manager.set("Finish the three verified steps", max_attempts=max_attempts)
    stream = await agent.attach_output()
    assert stream is not None

    async def collect() -> list[Any]:
        return [item async for item in stream]

    outputs = await asyncio.wait_for(collect(), timeout=10)
    # A terminal goal.updated event can precede round cleanup and stream forwarding.
    async def wait_for_idle() -> None:
        while agent.active_round is not None or agent._interaction_round_task is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_idle(), timeout=5)
    if agent._interaction_emit_tasks:
        await asyncio.gather(*agent._interaction_emit_tasks)
    await asyncio.sleep(0.02)
    return await agent.goal_manager.get(), outputs


@pytest.mark.parametrize("shared", [True, False], ids=["shared-script", "independent-assessor"])
@pytest.mark.asyncio
async def test_cold_start_hybrid_consumes_each_report_once(monkeypatch: pytest.MonkeyPatch, shared: bool) -> None:
    """CONTINUE cannot invoke an assessor whose independent answer is COMPLETE."""
    script = _ScriptModel(["continue", "continue", "complete"], shared=shared)
    agent = _make_agent(monkeypatch, script)
    await Runner.start()
    try:
        record, outputs = await _run_goal(agent)

        assert record.status is GoalStatus.COMPLETED
        assert record.attempt_count == 3
        assert (script.main_calls, script.assessor_calls) == (6, 1)
        assert not agent.event_manager.has_pending_work()
        assert all(getattr(item, "type", None) != "execution.error" for item in outputs)
    finally:
        await agent.stop()
        agent.ability_manager.teardown_tools()
        await Runner.stop()


@pytest.mark.asyncio
async def test_continue_reports_reach_max_attempts_without_assessor(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _ScriptModel(["continue", "continue"])
    agent = _make_agent(monkeypatch, script)
    await Runner.start()
    try:
        record, outputs = await _run_goal(agent, max_attempts=2)

        assert record.status is GoalStatus.BLOCKED
        assert record.attempt_count == 2
        assert (script.main_calls, script.assessor_calls) == (4, 0)
        assert not agent.event_manager.has_pending_work()
        assert all(getattr(item, "type", None) != "execution.error" for item in outputs)
    finally:
        await agent.stop()
        agent.ability_manager.teardown_tools()
        await Runner.stop()


def _assert_completion_callbacks(agent: DeepAgent, rail: TaskCompletionRail) -> None:
    for event in rail.get_callbacks():
        owners = []
        for target in (agent, agent.react_agent):
            key = f"{target._instance_id}_{event}"
            owners.extend(
                callback.callback.__self__
                for callback in Runner.callback_framework._callbacks.get(key, [])
                if isinstance(getattr(callback.callback, "__self__", None), TaskCompletionRail)
            )
        assert owners == [rail], f"Expected exactly one TaskCompletionRail callback for {event}"


@pytest.mark.parametrize("setup", ["cold", "preinitialized", "factory", "add", "fallback"])
@pytest.mark.asyncio
async def test_start_preserves_completion_rail_identity_and_lazy_initialization(
    monkeypatch: pytest.MonkeyPatch,
    setup: str,
) -> None:
    script = _ScriptModel(["complete"])
    custom = TaskCompletionRail(
        task_instruction="Keep configuration: {query}",
        completion_promise="DONE",
        required_confirmations=2,
        allow_promise_details=True,
        max_rounds=7,
        timeout_seconds=12,
        goal_language="en",
    )
    agent = _make_agent(monkeypatch, script, rails=[custom] if setup == "factory" else None)
    if setup == "add":
        agent.add_rail(custom)
    if setup == "fallback":
        agent.strip_rails_by_type((TaskCompletionRail,))
    pending = agent.find_pending_rails_by_type(TaskCompletionRail)
    expected = pending[0] if pending else None
    probe = _InitCaptureRail()
    agent.add_rail(probe)
    executed_sinks = []
    original_invoke = SubmitGoalReportTool.invoke

    async def capture_report(tool: SubmitGoalReportTool, inputs: Any, **kwargs: Any) -> Any:
        executed_sinks.append(tool._sink)
        return await original_invoke(tool, inputs, **kwargs)

    monkeypatch.setattr(SubmitGoalReportTool, "invoke", capture_report)
    await Runner.start()
    try:
        if setup == "preinitialized":
            await agent._ensure_initialized()
        session = create_agent_session(session_id=f"goal-identity-{uuid.uuid4().hex}", card=agent.card)
        await session.pre_run(inputs={})
        await agent.start(session=session)
        rail = expected or agent.find_pending_rails_by_type(TaskCompletionRail)[0]
        assert isinstance(rail, TaskCompletionRail)
        if expected is not None:
            assert rail is expected
        assert agent.find_pending_rails_by_type(TaskCompletionRail) == ([] if setup == "preinitialized" else [rail])
        assert probe.init_count == (1 if setup == "preinitialized" else 0)
        if setup == "preinitialized":
            _assert_completion_callbacks(agent, rail)
        assert agent.goal_manager is rail._goal_manager
        for tool in rail._goal_tools:
            assert agent.ability_manager.get(tool.card.name) is tool.card
        current_card = agent.ability_manager.get("get_current_goal")
        current_tool = Runner.resource_mgr.get_tool(tool_id=current_card.id, session=session)
        assert (await current_tool.invoke({}))["has_goal"] is False
        report_card = agent.ability_manager.get("submit_goal_report")
        report_tool = Runner.resource_mgr.get_tool(tool_id=report_card.id, session=session)
        assert (await report_tool.invoke({"status": "continue"}))["result"] == "report_accepted"
        assert rail._goal_report_sink.consume() is not None
        await agent.start(session=session)
        assert agent.ability_manager.get("submit_goal_report") is report_card
        if setup == "preinitialized":
            _assert_completion_callbacks(agent, rail)

        record, outputs = await _run_goal(agent, session=session)

        assert record.status is GoalStatus.COMPLETED
        assert agent.find_rails_by_type((TaskCompletionRail,)) == [rail]
        assert probe.init_count == 1
        _assert_completion_callbacks(agent, rail)
        assert executed_sinks == [rail._goal_report_sink, rail._goal_report_sink]
        card = agent.ability_manager.get("submit_goal_report")
        tool = Runner.resource_mgr.get_tool(tool_id=card.id, session=session)
        assert tool._sink is rail._goal_report_sink
        if setup in ("factory", "add"):
            assert rail is custom
            assert (rail.task_instruction, rail.completion_promise) == ("Keep configuration: {query}", "DONE")
            assert (rail.required_confirmations, rail.allow_promise_details) == (2, True)
            assert (rail.max_rounds, rail.timeout_seconds) == (7, 12)
        assert (script.main_calls, script.assessor_calls) == (2, 1)
        await agent.start(session=session)
        _assert_completion_callbacks(agent, rail)
        assert (script.main_calls, script.assessor_calls) == (2, 1)
        assert not agent.event_manager.has_pending_work()
        assert all(getattr(item, "type", None) != "execution.error" for item in outputs)
    finally:
        await agent.stop()
        agent.ability_manager.teardown_tools()
        await Runner.stop()


class _DependencyRail(_InitCaptureRail):
    priority = 95


class _CustomCompletionRail(TaskCompletionRail):
    priority = 90

    def __init__(self, dependency: _DependencyRail | None = None) -> None:
        super().__init__(completion_promise="CUSTOM")
        self.dependency = dependency
        self.init_count = 0
        self.uninit_count = 0

    def init(self, agent: Any) -> None:
        if self.dependency is not None:
            assert self.dependency.init_count > 0
        self.init_count += 1
        super().init(agent)

    def uninit(self, agent: Any) -> None:
        self.uninit_count += 1
        super().uninit(agent)


class _OtherCompletionRail(_CustomCompletionRail):
    pass


def _configure_rails(agent: DeepAgent, rails: list[AgentRail] | None) -> None:
    from dataclasses import replace

    agent.configure(replace(agent.deep_config, rails=rails))


async def _start_agent(agent: DeepAgent) -> Session:
    session = create_agent_session(session_id=f"goal-compat-{uuid.uuid4().hex}", card=agent.card)
    await session.pre_run(inputs={})
    await agent.start(session=session)
    return session


async def _close_agent(agent: DeepAgent) -> None:
    await agent.stop()
    agent.ability_manager.teardown_tools()
    await Runner.stop()


@pytest.mark.asyncio
async def test_start_keeps_custom_init_in_priority_order(monkeypatch: pytest.MonkeyPatch) -> None:
    dependency = _DependencyRail()
    custom = _CustomCompletionRail(dependency)
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom, dependency])
    await Runner.start()
    try:
        await _start_agent(agent)
        assert (dependency.init_count, custom.init_count) == (0, 0)
        assert agent.find_pending_rails_by_type(TaskCompletionRail) == [custom]
        assert not agent.is_registered_rail(custom)
        card = agent.ability_manager.get("submit_goal_report")
        sink = custom._goal_report_sink
        await agent._ensure_initialized()
        assert (dependency.init_count, custom.init_count) == (1, 1)
        assert agent.ability_manager.get("submit_goal_report") is card
        assert custom._goal_report_sink is sink
        _assert_completion_callbacks(agent, custom)
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.asyncio
async def test_partial_config_preserves_custom_rail_and_sink(
    monkeypatch: pytest.MonkeyPatch, started: bool,
) -> None:
    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom])
    await Runner.start()
    try:
        if started:
            await _start_agent(agent)
            await agent._ensure_initialized()
        sink = custom._goal_report_sink
        for rails in ([], [_InitCaptureRail()], [], [_InitCaptureRail()]):
            _configure_rails(agent, rails)
            assert agent.find_rails_by_type((TaskCompletionRail,)) == [custom]
        if not started:
            await _start_agent(agent)
        await agent._ensure_initialized()
        if started:
            assert custom._goal_report_sink is sink
        _assert_completion_callbacks(agent, custom)
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.asyncio
async def test_explicit_completion_family_uses_last_instance(
    monkeypatch: pytest.MonkeyPatch, started: bool,
) -> None:
    first, last = _CustomCompletionRail(), _OtherCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[first, last])
    await Runner.start()
    try:
        assert agent.find_pending_rails_by_type(TaskCompletionRail) == [last]
        if started:
            await _start_agent(agent)
        for _ in range(2):
            _configure_rails(agent, [first, last])
            assert agent.find_pending_rails_by_type(TaskCompletionRail) == [last]
        if not started:
            await _start_agent(agent)
        await agent._ensure_initialized()
        assert first.init_count == 0
        assert agent._task_completion_rail is last
        _assert_completion_callbacks(agent, last)
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("restore_original", [False, True])
@pytest.mark.asyncio
async def test_registered_replacement_switches_tools_only_after_retirement(
    monkeypatch: pytest.MonkeyPatch, restore_original: bool,
) -> None:
    old, new = _CustomCompletionRail(), _OtherCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[old])
    await Runner.start()
    try:
        await _start_agent(agent)
        await agent._ensure_initialized()
        card = agent.ability_manager.get("submit_goal_report")
        sink = old._goal_report_sink
        agent.add_rail(new)
        assert not agent._initialized
        assert agent._task_completion_rail is None
        assert agent.ability_manager.get("submit_goal_report") is card
        assert new._goal_tools == []
        _assert_completion_callbacks(agent, old)
        if restore_original:
            agent.add_rail(old)
            assert agent._task_completion_rail is old
        selected = old if restore_original else new
        await agent._ensure_initialized()
        assert agent._task_completion_rail is selected
        assert agent.find_rails_by_type((TaskCompletionRail,)) == [selected]
        _assert_completion_callbacks(agent, selected)
        if restore_original:
            assert old.uninit_count == 0
            assert old._goal_report_sink is sink
            assert agent.ability_manager.get("submit_goal_report") is card
        else:
            assert old.uninit_count == 1
            assert selected._goal_tools[0].card is agent.ability_manager.get("submit_goal_report")
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("removal", ["remove", "strip", "unregister", "full-config", "replace"])
@pytest.mark.asyncio
async def test_pending_goal_tools_are_cleaned_without_custom_uninit(
    monkeypatch: pytest.MonkeyPatch, removal: str,
) -> None:
    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom])
    await Runner.start()
    try:
        session = await _start_agent(agent)
        old_tools = list(custom._goal_tools)
        assert len(old_tools) == 2
        if removal == "remove":
            agent.remove_pending_rail(custom)
        elif removal == "strip":
            agent.strip_rails_by_type((TaskCompletionRail,))
        elif removal == "unregister":
            await agent.unregister_rail(custom)
        elif removal == "full-config":
            _configure_rails(agent, None)
        else:
            agent.add_rail(_OtherCompletionRail())
        assert custom.init_count == custom.uninit_count == 0
        assert custom._goal_tools == []
        for tool in old_tools:
            assert agent.ability_manager.get(tool.card.name) is not tool.card
        pending = agent.find_pending_rails_by_type(TaskCompletionRail)
        if pending:
            current = pending[0]
            card = agent.ability_manager.get("submit_goal_report")
            assert card is current._goal_tools[0].card
            resource = Runner.resource_mgr.get_tool(tool_id=card.id, session=session)
            assert resource._sink is current._goal_report_sink
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_old_goal_cleanup_preserves_replacement_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = _CustomCompletionRail(), _OtherCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[old])
    await Runner.start()
    try:
        session = await _start_agent(agent)
        new.set_goal_manager(agent.goal_manager)
        new.init(agent)
        cards = [tool.card for tool in new._goal_tools]
        old.uninit(agent)
        for card in cards:
            assert agent.ability_manager.get(card.name) is card
            resource = Runner.resource_mgr.get_tool(tool_id=card.id, session=session)
            assert resource.card is card
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_completion_callbacks_stay_single_after_later_init_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingRail(AgentRail):
        priority = 0

        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def init(self, agent: Any) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("retry initialization")

    custom = _CustomCompletionRail()
    failing = FailingRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom, failing])
    await Runner.start()
    try:
        await _start_agent(agent)
        with pytest.raises(RuntimeError, match="retry initialization"):
            await agent._ensure_initialized()
        _assert_completion_callbacks(agent, custom)
        card = agent.ability_manager.get("submit_goal_report")
        _configure_rails(agent, [custom])
        await agent._ensure_initialized()
        assert custom.init_count == 2
        assert agent.ability_manager.get("submit_goal_report") is card
        assert agent.find_rails_by_type((TaskCompletionRail,)) == [custom]
        _assert_completion_callbacks(agent, custom)
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.asyncio
async def test_repeated_partial_config_consumes_goal_reports_once(
    monkeypatch: pytest.MonkeyPatch, started: bool,
) -> None:
    script = _ScriptModel(["continue", "continue", "complete"])
    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, script, rails=[custom])
    await Runner.start()
    try:
        session = None
        if started:
            session = await _start_agent(agent)
            await agent._ensure_initialized()
        for _ in range(2):
            _configure_rails(agent, [_InitCaptureRail()])
        record, outputs = await _run_goal(agent, session=session)
        assert record.status is GoalStatus.COMPLETED
        assert record.attempt_count == 3
        assert (script.main_calls, script.assessor_calls) == (6, 1)
        assert agent.find_rails_by_type((TaskCompletionRail,)) == [custom]
        _assert_completion_callbacks(agent, custom)
        card = agent.ability_manager.get("submit_goal_report")
        tool = Runner.resource_mgr.get_tool(tool_id=card.id)
        assert tool._sink is custom._goal_report_sink
        assert not agent.event_manager.has_pending_work()
        assert all(getattr(item, "type", None) != "execution.error" for item in outputs)
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_registered_same_instance_reconfiguration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom])
    await Runner.start()
    try:
        await _start_agent(agent)
        await agent._ensure_initialized()
        card = agent.ability_manager.get("submit_goal_report")
        sink = custom._goal_report_sink
        for _ in range(2):
            _configure_rails(agent, [custom])
            await agent._ensure_initialized()
            assert agent.find_rails_by_type((TaskCompletionRail,)) == [custom]
            assert agent.ability_manager.get("submit_goal_report") is card
            assert custom._goal_report_sink is sink
            _assert_completion_callbacks(agent, custom)
        assert custom.init_count == 3
        assert custom.uninit_count == 0
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.asyncio
async def test_partial_config_refreshes_goal_tool_language_without_custom_init(
    monkeypatch: pytest.MonkeyPatch, initialized: bool,
) -> None:
    from dataclasses import replace

    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[custom])
    await Runner.start()
    try:
        await _start_agent(agent)
        if initialized:
            await agent._ensure_initialized()
        sink = custom._goal_report_sink
        old_cards = [tool.card for tool in custom._goal_tools]
        agent.configure(replace(agent.deep_config, rails=[], language="en", system_prompt="English identity"))
        assert custom.init_count == int(initialized)
        assert custom._goal_tools[-1]._language == "en"
        assert custom._goal_report_sink is sink
        assert agent.goal_manager.language == "en"
        for old, tool in zip(old_cards, custom._goal_tools):
            assert tool.card.description != old.description
            assert agent.ability_manager.get(tool.card.name) is tool.card
        await agent._ensure_initialized()
        _assert_completion_callbacks(agent, custom)
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("streaming", [False, True], ids=["invoke", "stream"])
@pytest.mark.asyncio
async def test_direct_execution_initializes_custom_completion_rail(
    monkeypatch: pytest.MonkeyPatch, streaming: bool,
) -> None:
    script = _ScriptModel([])
    custom = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, script, rails=[custom])
    script.responses.append(create_text_response("Direct response"))

    async def main_invoke(*args: Any, **kwargs: Any) -> AssistantMessage:
        script.main_calls += 1
        return script._next()

    monkeypatch.setattr(Model, "invoke", main_invoke)
    await Runner.start()
    try:
        session = create_agent_session(session_id=f"goal-direct-{uuid.uuid4().hex}", card=agent.card)
        await session.pre_run(inputs={})
        if streaming:
            outputs = [item async for item in agent.stream("Say hello", session=session)]
            assert outputs
        else:
            await agent.invoke("Say hello", session=session)
        assert custom.init_count == 1
        assert agent._task_completion_rail is custom
        _assert_completion_callbacks(agent, custom)
        assert script.main_calls == 1
    finally:
        await _close_agent(agent)


@pytest.mark.parametrize("full_replacement", [False, True])
@pytest.mark.asyncio
async def test_start_uses_pending_replacement_after_preinitialization(
    monkeypatch: pytest.MonkeyPatch, full_replacement: bool,
) -> None:
    old = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[old])
    await Runner.start()
    try:
        await agent._ensure_initialized()
        evaluator_calls = []
        original_build = old.build_evaluators

        def capture_evaluators() -> list[Any]:
            evaluator_calls.append(old)
            return original_build()

        monkeypatch.setattr(old, "build_evaluators", capture_evaluators)
        _configure_rails(agent, None if full_replacement else [_OtherCompletionRail()])
        new = agent.find_pending_rails_by_type(TaskCompletionRail)[0]
        session = await _start_agent(agent)
        assert evaluator_calls == []
        assert old.init_count == 1
        if isinstance(new, _CustomCompletionRail):
            assert new.init_count == 0
        assert agent.is_pending_rail(new)
        card = agent.ability_manager.get("submit_goal_report")
        tool = Runner.resource_mgr.get_tool(tool_id=card.id, session=session)
        assert tool._sink is new._goal_report_sink
        await agent._ensure_initialized()
        assert old.uninit_count == 1
        assert agent._task_completion_rail is new
        assert agent.ability_manager.get("submit_goal_report") is card
        _assert_completion_callbacks(agent, new)
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_full_config_keeps_goal_tools_available_after_prestart_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _CustomCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[old])
    await Runner.start()
    try:
        await agent._ensure_initialized()
        _configure_rails(agent, [_OtherCompletionRail()])
        session = await _start_agent(agent)
        displaced = agent.find_pending_rails_by_type(TaskCompletionRail)[0]
        _configure_rails(agent, None)
        selected = agent.find_pending_rails_by_type(TaskCompletionRail)[0]
        assert selected is not displaced
        assert displaced.init_count == displaced.uninit_count == 0
        assert displaced._goal_tools == []
        current_card = agent.ability_manager.get("get_current_goal")
        current_tool = Runner.resource_mgr.get_tool(tool_id=current_card.id, session=session)
        assert (await current_tool.invoke({}))["has_goal"] is False
        report_card = agent.ability_manager.get("submit_goal_report")
        report_tool = Runner.resource_mgr.get_tool(tool_id=report_card.id, session=session)
        assert report_tool._sink is selected._goal_report_sink
        assert (await report_tool.invoke({"status": "continue"}))["result"] == "report_accepted"
        assert selected._goal_report_sink.consume() is not None
        await agent._ensure_initialized()
        assert agent.ability_manager.get("submit_goal_report") is report_card
        assert agent._task_completion_rail is selected
        _assert_completion_callbacks(agent, selected)
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_completion_replacement_uses_identity_when_instances_compare_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EqualCompletionRail(_CustomCompletionRail):
        def __eq__(self, other: Any) -> bool:
            return isinstance(other, TaskCompletionRail)

    old, new = EqualCompletionRail(), EqualCompletionRail()
    agent = _make_agent(monkeypatch, _ScriptModel(["complete"]), rails=[old])
    await Runner.start()
    try:
        await _start_agent(agent)
        await agent._ensure_initialized()
        agent.add_rail(new)
        assert [id(rail) for rail in agent._stale_rails if isinstance(rail, TaskCompletionRail)] == [id(old)]
        assert all(rail is not old for rail in agent._registered_rails)
        await agent._ensure_initialized()
        assert old.uninit_count == 1
        assert new.init_count == 1
        assert agent._task_completion_rail is new
        assert [id(rail) for rail in agent._registered_rails if isinstance(rail, TaskCompletionRail)] == [id(new)]
        _assert_completion_callbacks(agent, new)
    finally:
        await _close_agent(agent)


@pytest.mark.asyncio
async def test_start_after_stripping_initialized_completion_rail_runs_goal_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _CustomCompletionRail()
    script = _ScriptModel(["complete"])
    agent = _make_agent(monkeypatch, script, rails=[old])
    await Runner.start()
    try:
        await agent._ensure_initialized()
        agent.strip_rails_by_type((TaskCompletionRail,))
        assert agent._task_completion_rail is None
        session = await _start_agent(agent)
        selected = agent.find_pending_rails_by_type(TaskCompletionRail)[0]
        assert selected is not old
        assert old.init_count == 1
        record, outputs = await _run_goal(agent, session=session)
        assert record.status is GoalStatus.COMPLETED
        assert record.attempt_count == 1
        assert (script.main_calls, script.assessor_calls) == (2, 1)
        assert agent._task_completion_rail is selected
        assert old.uninit_count == 1
        _assert_completion_callbacks(agent, selected)
        assert not agent.event_manager.has_pending_work()
        assert all(getattr(item, "type", None) != "execution.error" for item in outputs)
    finally:
        await _close_agent(agent)
