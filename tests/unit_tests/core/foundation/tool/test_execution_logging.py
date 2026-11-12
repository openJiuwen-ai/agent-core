"""显式终态仅增加诊断，不改变工具负载、回调和异常控制流。"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.events import EventStatus
from openjiuwen.core.foundation.tool import execution_logging
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import ToolCallEvents
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.todo_store import TodoResult


class ExampleTool(Tool):
    def __init__(self, operation, *, enabled=True, name="example"):
        super().__init__(ToolCard(id=name, name=name))
        self._operation = operation
        self._log_execution_status = enabled

    async def invoke(self, inputs, **kwargs):
        return await self._operation(inputs, **kwargs)

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def terminal_records(info, warning):
    return [call.kwargs for mock in (info, warning) for call in mock.call_args_list
            if call.kwargs.get("metadata", {}).get("event_role") == "terminal"]


@pytest.mark.asyncio
async def test_forwarding_preserves_payload_and_existing_call_id():
    async def result(_inputs, **_kwargs):
        return TodoResult({"message": "done"}, outcome="success", counts={"applied_count": 1})

    inner = ExampleTool(result, name="todo_modify")

    async def forward(inputs, **kwargs):
        return await inner.invoke(inputs, **kwargs)

    outer = ExampleTool(forward, name="todo")
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        returned = await outer.invoke({"action": "modify", "todos": [{"id": "A"}]}, tool_call_id="call-A")
    assert dict(returned) == {"message": "done"}
    records = terminal_records(info, warning)
    assert len(records) == 2
    assert {record["tool_name"] for record in records} == {"todo", "todo_modify"}
    assert all(record["tool_call_id"] == "call-A" for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,status", [
    ("success", EventStatus.SUCCESS), ("noop", EventStatus.SUCCESS),
    ("partial", EventStatus.FAILURE), ("failure", EventStatus.FAILURE),
])
async def test_explicit_outcome_does_not_change_dict_return(outcome, status):
    async def result(_inputs, **_kwargs):
        return TodoResult({"message": "unchanged shape"}, outcome=outcome)

    tool = ExampleTool(result)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        returned = await tool.invoke({}, tool_call_id="call-A")
    assert dict(returned) == {"message": "unchanged shape"}
    records = terminal_records(info, warning)
    assert len(records) == 1 and records[0]["status"] == status
    assert records[0]["metadata"]["outcome"] == outcome


@pytest.mark.asyncio
async def test_unknown_publication_is_not_reported_as_zero_applied():
    async def failure(_inputs, **_kwargs):
        raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason="response lost",
                          details={"commit_state": "unknown", "reason": "publication_unknown"})

    tool = ExampleTool(failure)
    with (
        patch.object(execution_logging.logger, "info") as info,
        patch.object(execution_logging.logger, "warning") as warning,
        pytest.raises(Exception, match="response lost"),
    ):
        await tool.invoke({}, tool_call_id="call-A")
    record = terminal_records(info, warning)[0]
    assert record["status"] == EventStatus.FAILURE
    assert record["metadata"]["outcome"] == "unknown"
    assert record["metadata"]["item_counts"]["applied_count"] is None
    assert record["metadata"]["outer_error_code"] == 182501
    assert "response lost" in record["error_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [
    (asyncio.CancelledError(), EventStatus.CANCELLED),
    (TimeoutError("timed out"), EventStatus.TIMEOUT),
    (RuntimeError("failed"), EventStatus.FAILURE),
])
async def test_invocation_error_is_logged_and_rethrown(error, status):
    async def failure(_inputs, **_kwargs):
        raise error

    tool = ExampleTool(failure)
    with (
        patch.object(execution_logging.logger, "info") as info,
        patch.object(execution_logging.logger, "warning") as warning,
        pytest.raises(type(error)) as caught,
    ):
        await tool.invoke({}, tool_call_id="call-A")
    assert caught.value is error
    assert terminal_records(info, warning)[0]["status"] == status


@pytest.mark.asyncio
async def test_non_opt_in_tool_keeps_existing_boolean_log_and_plain_dict_behavior():
    async def result(inputs, **_kwargs):
        return ToolOutput(success=inputs["success"]) if inputs else {"message": "plain"}

    tool = ExampleTool(result, enabled=False)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        returned = await tool.invoke({})
        assert returned == {"message": "plain"}
        assert not info.called
        assert (await tool.invoke({"success": True})).success is True
        assert (await tool.invoke({"success": False})).success is False
    assert terminal_records(info, warning) == []
    assert info.call_count == 1
    assert warning.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("result,outcome", [
    (ToolOutput(success=True), "success"),
    (ToolOutput(success=False), "failure"),
    ({"message": "no explicit status"}, "unknown"),
])
async def test_explicit_logging_uses_boolean_fallback_without_assuming_dict_success(result, outcome):
    async def operation(_inputs, **_kwargs):
        return result

    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        assert await ExampleTool(operation).invoke({}) is result
    assert terminal_records(info, warning)[0]["metadata"]["outcome"] == outcome


@pytest.mark.asyncio
async def test_logging_failure_does_not_change_confirmed_business_result():
    async def result(_inputs, **_kwargs):
        return TodoResult({"message": "done"}, commit_state="committed")

    tool = ExampleTool(result)
    with patch.object(execution_logging.logger, "info", side_effect=RuntimeError("logging unavailable")):
        returned = await tool.invoke({}, tool_call_id="call-A")
    assert returned.tool_commit_state == "committed"


@pytest.mark.asyncio
async def test_logging_failure_does_not_replace_original_error():
    error = build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason="response lost")

    async def failure(_inputs, **_kwargs):
        raise error

    with (
        patch.object(execution_logging.logger, "warning", side_effect=RuntimeError("logging unavailable")),
        pytest.raises(type(error)) as caught,
    ):
        await ExampleTool(failure).invoke({})
    assert caught.value is error


@pytest.mark.asyncio
async def test_overlapping_calls_keep_their_own_log_records():
    release = asyncio.Event()

    async def operation(inputs, **_kwargs):
        if inputs["wait"]:
            await release.wait()
        else:
            release.set()
        return TodoResult({"message": "done"}, counts={"applied_count": inputs["count"]})

    tool = ExampleTool(operation)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        await asyncio.gather(
            tool.invoke({"wait": True, "count": 1}, tool_call_id="slow"),
            tool.invoke({"wait": False, "count": 2}, tool_call_id="fast"),
        )
    records = terminal_records(info, warning)
    assert len(records) == 2
    assert {row["tool_call_id"]: row["metadata"]["item_counts"]["applied_count"] for row in records} == {
        "slow": 1, "fast": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failing_event", [ToolCallEvents.TOOL_CALL_STARTED, ToolCallEvents.TOOL_CALL_FINISHED])
async def test_callback_error_boundaries_match_existing_wrapper(enabled, failing_event):
    error = RuntimeError("callback failed")
    seen = []
    operation = AsyncMock(return_value=TodoResult(
        {"message": "done"}, commit_state="committed", counts={"applied_count": 1},
    ))
    tool = ExampleTool(operation, enabled=enabled)

    async def trigger(event, *_args, **_kwargs):
        if event in (ToolCallEvents.TOOL_CALL_STARTED, ToolCallEvents.TOOL_CALL_FINISHED, ToolCallEvents.TOOL_CALL_ERROR):
            seen.append(event)
        if event == failing_event:
            raise error

    with (
        patch.object(Runner.callback_framework, "trigger", side_effect=trigger),
        patch.object(execution_logging.logger, "info") as info,
        patch.object(execution_logging.logger, "warning") as warning,
        pytest.raises(RuntimeError) as caught,
    ):
        await tool.invoke({})
    assert caught.value is error
    expected = [ToolCallEvents.TOOL_CALL_STARTED]
    if failing_event == ToolCallEvents.TOOL_CALL_FINISHED:
        operation.assert_awaited_once()
        expected.append(ToolCallEvents.TOOL_CALL_FINISHED)
    else:
        operation.assert_not_awaited()
    if enabled or failing_event == ToolCallEvents.TOOL_CALL_FINISHED:
        expected.append(ToolCallEvents.TOOL_CALL_ERROR)
    assert seen == expected
    records = terminal_records(info, warning)
    if enabled:
        assert len(records) == 1
        assert records[0]["metadata"]["outcome"] == "failure"
        if failing_event == ToolCallEvents.TOOL_CALL_FINISHED:
            assert records[0]["metadata"]["commit_state"] == "committed"
            assert records[0]["metadata"]["item_counts"]["applied_count"] == 1
    else:
        assert records == []
