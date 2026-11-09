"""显式终态仅增加诊断，不改变工具负载、回调和异常控制流。"""

import asyncio
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.events import EventStatus
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.foundation.tool import execution_logging
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
async def test_nested_forwarding_counts_one_public_call_and_keeps_payload():
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
    kinds = {record["metadata"]["invocation_kind"] for record in records}
    assert kinds == {"model", "internal_engine"}
    assert all(record["tool_call_id"] == "call-A" for record in records)
    assert sum(record["metadata"]["invocation_kind"] == "model" for record in records) == 1
    assert execution_logging._EXECUTION.get() is None


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


@pytest.mark.asyncio
async def test_unknown_publication_is_not_reported_as_zero_applied():
    async def failure(_inputs, **_kwargs):
        raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason="response lost",
                          details={"commit_state": "unknown", "reason": "publication_unknown"})

    tool = ExampleTool(failure)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        with pytest.raises(Exception, match="response lost"):
            await tool.invoke({}, tool_call_id="call-A")
    record = terminal_records(info, warning)[0]
    assert record["status"] == EventStatus.FAILURE
    assert record["metadata"]["outcome"] == "unknown"
    assert record["metadata"]["item_counts"]["applied_count"] is None
    assert record["metadata"]["root_error_code"] == 182501
    assert "response lost" in record["error_message"]


@pytest.mark.asyncio
async def test_cancelled_invocation_rethrows_and_cleans_context():
    async def cancelled(_inputs, **_kwargs):
        raise asyncio.CancelledError()

    tool = ExampleTool(cancelled)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        with pytest.raises(asyncio.CancelledError):
            await tool.invoke({}, tool_call_id="call-A")
    assert terminal_records(info, warning)[0]["status"] == EventStatus.CANCELLED
    assert execution_logging._EXECUTION.get() is None


@pytest.mark.asyncio
async def test_non_opt_in_tool_keeps_existing_boolean_log_and_plain_dict_behavior():
    async def result(inputs, **_kwargs):
        return ToolOutput(success=True) if inputs else {"message": "plain"}

    tool = ExampleTool(result, enabled=False)
    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        returned = await tool.invoke({})
        assert returned == {"message": "plain"}
        assert not info.called
        assert (await tool.invoke({"run": True})).success is True
    assert terminal_records(info, warning) == []
    assert info.call_count == 1


@pytest.mark.asyncio
async def test_logging_failure_does_not_change_confirmed_business_result():
    async def result(_inputs, **_kwargs):
        return TodoResult({"message": "done"}, commit_state="committed")

    tool = ExampleTool(result)
    with patch.object(execution_logging.logger, "info", side_effect=RuntimeError("logging unavailable")):
        returned = await tool.invoke({}, tool_call_id="call-A")
    assert returned.tool_commit_state == "committed"
    assert execution_logging._EXECUTION.get() is None


@pytest.mark.asyncio
async def test_missing_model_id_is_unattributed_direct_execution():
    async def result(_inputs, **_kwargs):
        return TodoResult({"message": "done"})

    with patch.object(execution_logging.logger, "info") as info, patch.object(execution_logging.logger, "warning") as warning:
        await ExampleTool(result).invoke({})
    record = terminal_records(info, warning)[0]
    assert record["metadata"]["invocation_kind"] == "direct"
    assert record["metadata"]["tool_call_id"] is None
