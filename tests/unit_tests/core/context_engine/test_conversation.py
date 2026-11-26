# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest

from openjiuwen.core.context_engine import conversation_snapshot
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage


def test_offloaded_tool_result_keeps_pair_and_independent_payload():
    state = {"messages": [
        AssistantMessage(content="", tool_calls=[{"id": "tool-1", "type": "function",
                                                  "function": {"name": "read", "arguments": "{}"}}]),
        OffloadToolMessage(content="summary", tool_call_id="tool-1", offload_handle="h", offload_type="in_memory"),
    ], "offload_messages": {"h": [ToolMessage(content="full result", tool_call_id="tool-1")]}}
    snapshot = conversation_snapshot(state)
    state["offload_messages"]["h"][0].content = "changed"
    assert snapshot["offload_messages"]["h"][0].content == "full result"


@pytest.mark.parametrize("messages,error", [
    ([AssistantMessage(content="", tool_calls=[{"id": "unclosed", "type": "function",
                                                "function": {"name": "read", "arguments": "{}"}}])], "unclosed"),
    ([ToolMessage(content="orphan", tool_call_id="missing")], "unpaired"),
    ([OffloadToolMessage(content="file", tool_call_id="t", offload_handle="private-file",
                         offload_type="filesystem")], "not_portable"),
])
def test_incomplete_or_inaccessible_snapshots_fail_explicitly(messages, error):
    with pytest.raises(ValueError, match=error):
        conversation_snapshot({"messages": messages})
