# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""One bounded request-entry judgment, without live models or storage."""

import asyncio
import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.persistent_agent.execution.control import ExecutionControl
from openjiuwen.harness.persistent_agent.schema.integration import EXECUTION_HEADER, HANDOFF_TEXT, WINDOW_PART_KEY
from openjiuwen.harness.persistent_agent.schema.window import WindowSeed

pytestmark = pytest.mark.level0


def api():
    return import_module("openjiuwen.harness.persistent_agent.execution.window_decision")


def setup_case(content):
    response = content if isinstance(content, AssistantMessage) else AssistantMessage(content=content)
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))
    spec = SimpleNamespace(model=SimpleNamespace(build=lambda: model))
    execution = SimpleNamespace(execution_id="e-1", config_version="v1")
    budget = SimpleNamespace(admit=AsyncMock(), report=AsyncMock())
    context = SimpleNamespace(
        execution=execution, ports=SimpleNamespace(budget=budget), binding=SimpleNamespace(control=ExecutionControl())
    )
    emitter = SimpleNamespace(emit=AsyncMock())
    return spec, context, model, emitter


def switch_response(arguments=None, name="new_context", call_id="entry-call-1"):
    arguments = arguments if arguments is not None else {"objective": "new topic"}
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                type="function",
                name=name,
                arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
            )
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["continue", "switch"])
async def test_one_model_call_returns_validated_decision_and_reports_usage(monkeypatch, decision):
    mod = api()
    response = switch_response() if decision == "switch" else '{"decision":"continue","reason":"same topic"}'
    spec, context, model, emitter = setup_case(response)
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )

    result = await mod.judge_window(spec, context, "new topic", [UserMessage(content="old topic")], emitter)

    assert result.decision == decision
    pending = context.binding.control.pending_window_switch
    if decision == "switch":
        assert pending.trigger_kind == "request_entry"
        assert isinstance(pending.seed, WindowSeed)
        assert pending.seed.objective == "new topic"
        assert context.binding.control.rollover_committed is False
    else:
        assert pending is None
    model.invoke.assert_awaited_once()
    tools = model.invoke.await_args.kwargs["tools"]
    assert len(tools) == 1 and tools[0].name == "new_context"
    assert "trigger_kind" not in tools[0].parameters["properties"]
    context.ports.budget.report.assert_awaited_once()
    assert [str(item.args[0]) for item in emitter.emit.await_args_list] == (
        ["model.completed", "tool.completed"] if decision == "switch" else ["model.completed"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown", "multiple", "bad_json", "bad_seed", "forged_boundary", "missing_id"])
async def test_bad_tool_calls_never_partially_queue_a_window(monkeypatch, case):
    mod = api()
    response = switch_response()
    if case == "unknown":
        response.tool_calls[0].name = "bash"
    elif case == "multiple":
        response.tool_calls.append(ToolCall(id="second", type="function", name="bash", arguments="{}"))
    elif case == "bad_json":
        response.tool_calls[0].arguments = "not json"
    elif case == "bad_seed":
        response.tool_calls[0].arguments = '{"objective":" "}'
    elif case == "forged_boundary":
        response.tool_calls[0].arguments = '{"objective":"task","trigger_kind":"agent"}'
    else:
        response.tool_calls[0].id = None
    spec, context, model, emitter = setup_case(response)
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.degraded and result.decision == "continue"
    assert context.binding.control.pending_window_switch is None
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"decision":"switch","reason":"x"}',
        '{"decision":"switch","reason":"x","seed":{"objective":"valid but not a tool call"}}',
        '{"decision":"continue","reason":"x","seed":null}',
        '{"decision":"switch","reason":"x","seed":{"objective":" "}}',
    ],
)
async def test_invalid_result_degrades_without_retry(monkeypatch, bad):
    mod = api()
    spec, context, model, emitter = setup_case(bad)
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="history")], emitter)
    assert result.decision == "continue"
    assert result.degraded is True
    model.invoke.assert_awaited_once()


def test_preview_is_bounded_visible_text_and_retains_previous_goal():
    mod = api()
    messages = [
        UserMessage(content="request ids", metadata={WINDOW_PART_KEY: EXECUTION_HEADER}),
        UserMessage(content="previous goal"),
        AssistantMessage(content="visible", reasoning_content="HIDDEN"),
        ToolMessage(content="SECRET TOOL PAYLOAD", tool_call_id="t1"),
    ]
    messages += [AssistantMessage(content="x" * 30000) for _ in range(30)]
    preview = mod.history_preview(messages)
    encoded = json.dumps(preview)
    assert "previous goal" in encoded
    assert "HIDDEN" not in encoded
    assert "SECRET TOOL PAYLOAD" not in encoded
    assert len(encoded) < 20000


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_continue(monkeypatch):
    mod = api()
    spec, context, model, emitter = setup_case("")
    context.ports.budget.admit.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    model.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private upstream error")])
async def test_model_error_reports_unknown_usage_and_continues_without_retry(monkeypatch, failure):
    mod = api()
    spec, context, model, emitter = setup_case("")
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )
    model.invoke.side_effect = failure
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.decision == "continue" and result.degraded
    assert "private upstream error" not in result.reason
    model.invoke.assert_awaited_once()
    assert context.ports.budget.report.await_args.args[2].status == "unknown"


@pytest.mark.asyncio
async def test_actual_timeout_is_bounded_and_does_not_retry(monkeypatch):
    mod = api()
    spec, context, model, emitter = setup_case("")
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )
    monkeypatch.setattr(mod, "_TIMEOUT_SECONDS", 0.01)

    async def never_finishes(*args, **kwargs):
        await asyncio.Event().wait()

    model.invoke.side_effect = never_finishes
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.degraded and result.reason == "TimeoutError"
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_budget_does_not_call_model_or_report_unused_usage(monkeypatch):
    mod = api()
    spec, context, model, emitter = setup_case("")
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="denied", assert_call=lambda *a: None)),
    )
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.degraded and result.decision == "continue"
    model.invoke.assert_not_awaited()
    context.ports.budget.report.assert_not_awaited()


def test_history_does_not_treat_execution_metadata_as_previous_user_goal():
    preview = api().history_preview(
        [
            UserMessage(content="real goal"),
            AssistantMessage(content="conclusion"),
            UserMessage(content="execution ids only", metadata={WINDOW_PART_KEY: EXECUTION_HEADER}),
        ]
    )
    assert preview["previous_goal"] == "real goal"
    assert "execution ids only" not in json.dumps(preview)


def test_delivered_answer_is_visible_but_does_not_replace_previous_goal():
    preview = api().history_preview(
        [
            UserMessage(content="real goal"),
            UserMessage(content="rewritten final answer", metadata={WINDOW_PART_KEY: HANDOFF_TEXT}),
        ]
    )
    assert preview["previous_goal"] == "real goal"
    assert "rewritten final answer" in json.dumps(preview)


@pytest.mark.asyncio
async def test_completion_recording_failure_does_not_undo_an_accepted_request(monkeypatch):
    mod = api()
    spec, context, _, emitter = setup_case(switch_response())
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )

    async def ordinary_failure(kind, payload):
        if str(kind) == "tool.completed":
            raise RuntimeError("ordinary recording unavailable")

    emitter.emit = ordinary_failure
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.decision == "switch" and not result.degraded
    assert context.binding.control.pending_window_switch.seed.objective == "new topic"
    assert context.binding.control.rollover_committed is False


@pytest.mark.asyncio
async def test_entry_model_cannot_overwrite_an_already_queued_agent_request(monkeypatch):
    mod = api()
    spec, context, _, emitter = setup_case(switch_response())
    tool = mod.PersistentNewContextTool(context.binding.control)
    assert (await tool.invoke({"objective": "existing task"})).success
    monkeypatch.setattr(
        mod,
        "BudgetAdmission",
        SimpleNamespace(model_validate=lambda x: SimpleNamespace(status="admitted", assert_call=lambda *a: None)),
    )
    monkeypatch.setattr(
        mod, "BudgetReport", SimpleNamespace(model_validate=lambda x: SimpleNamespace(assert_call=lambda *a: None))
    )
    result = await mod.judge_window(spec, context, "task", [UserMessage(content="old")], emitter)
    assert result.degraded and result.decision == "continue"
    queued = context.binding.control.pending_window_switch
    assert queued.trigger_kind == "agent" and queued.seed.objective == "existing task"
    completed = emitter.emit.await_args_list[-1].args[1]
    assert completed.status == "failed"
