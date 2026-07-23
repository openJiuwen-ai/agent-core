# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo


def _context() -> SessionModelContext:
    return SessionModelContext(
        context_id="ctx_diff",
        session_id="sess_diff",
        config=ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )


def _window(
        message_contents: list[str],
        *,
        tools: list[ToolInfo] | None = None,
) -> ContextWindow:
    return ContextWindow(
        system_messages=[SystemMessage(content="system")],
        context_messages=[UserMessage(content=content) for content in message_contents],
        tools=tools or [],
    )


def _tool(name: str) -> ToolInfo:
    return ToolInfo(name=name, description=f"{name} description", parameters={})


def test_detection_is_explicit_and_saves_snapshot():
    context = _context()

    change = context.detect_context_window_change(_window(["a"]))

    assert change is None
    assert context._last_llm_bound_context_window is not None


def test_first_detection_only_saves_snapshot():
    context = _context()
    window = _window(["a"])

    change = context.detect_context_window_change(window)

    assert change is None
    assert context._last_llm_bound_context_window is not window
    assert context._last_llm_bound_context_window.get_messages() == window.get_messages()


def test_same_window_returns_none():
    context = _context()
    context.detect_context_window_change(_window(["a", "b"]))

    change = context.detect_context_window_change(_window(["a", "b"]))

    assert change is None


def test_messages_append_only_returns_none():
    context = _context()
    context.detect_context_window_change(_window(["a", "b"]))

    change = context.detect_context_window_change(_window(["a", "b", "c"]))

    assert change is None


def test_messages_truncated_returns_msg_start():
    context = _context()
    context.detect_context_window_change(_window(["a", "b", "c"]))

    change = context.detect_context_window_change(_window(["a", "b"]))

    assert change is not None
    assert change.has_change is True
    assert change.msg_start == 3
    assert change.msg_end == 4
    assert change.tools_start is None
    assert [message.content for message in change.old_messages] == ["system", "a", "b", "c"]


def test_messages_replaced_in_middle_returns_msg_start():
    context = _context()
    context.detect_context_window_change(_window(["a", "b", "c"]))

    change = context.detect_context_window_change(_window(["a", "x", "c"]))

    assert change is not None
    assert change.msg_start == 2
    assert change.msg_end == 4
    assert change.tools_start is None


def test_tools_append_invalidates_messages_after_system_prompt():
    context = _context()
    context.detect_context_window_change(_window(["a"], tools=[_tool("t1")]))

    change = context.detect_context_window_change(_window(["a"], tools=[_tool("t1"), _tool("t2")]))

    assert change is not None
    assert change.msg_start == 1
    assert change.msg_end == 2
    assert change.tools_start is None
    assert change.tools_end is None


def test_tools_append_with_only_system_message_needs_no_old_suffix_evict():
    context = _context()
    context.detect_context_window_change(_window([], tools=[]))

    change = context.detect_context_window_change(_window([], tools=[_tool("t1")]))

    assert change is None


def test_tools_replaced_returns_tools_start():
    context = _context()
    context.detect_context_window_change(_window(["a"], tools=[_tool("t1"), _tool("t2")]))

    change = context.detect_context_window_change(_window(["a"], tools=[_tool("t1"), _tool("tx")]))

    assert change is not None
    assert change.msg_start is None
    assert change.tools_start == 1
    assert change.tools_end == 2
    assert [tool.name for tool in change.old_tools] == ["t1", "t2"]


def test_messages_and_tools_change_together():
    context = _context()
    context.detect_context_window_change(_window(["a", "b"], tools=[_tool("t1"), _tool("t2")]))

    change = context.detect_context_window_change(_window(["a", "x"], tools=[_tool("t1"), _tool("tx")]))

    assert change is not None
    assert change.msg_start == 2
    assert change.msg_end == 3
    assert change.tools_start == 1
    assert change.tools_end == 2


def test_snapshot_updates_after_each_detection():
    context = _context()
    context.detect_context_window_change(_window(["a", "b", "c"]))
    change = context.detect_context_window_change(_window(["x", "b", "c"]))
    assert change is not None
    assert change.msg_start == 1

    change = context.detect_context_window_change(_window(["x", "b", "c", "d"]))

    assert change is None


def test_snapshot_is_deep_copied_and_not_polluted_by_later_mutation():
    context = _context()
    first_window = _window(["a"])
    context.detect_context_window_change(first_window)
    first_window.context_messages[0].content = "mutated"

    change = context.detect_context_window_change(_window(["a", "b"]))

    assert change is None


def test_context_window_diff_can_be_reused_by_legacy_release_path():
    context = _context()

    first_change = context.detect_context_window_change(_window(["a", "b"]))
    second_change = context.detect_context_window_change(_window(["a", "x"]))

    assert first_change is None
    assert second_change is not None
    assert second_change.msg_start == 2
