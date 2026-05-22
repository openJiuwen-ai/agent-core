# coding: utf-8
"""Tests for format_previous_steps_for_branch."""

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    MULTIMODAL_SKILL_USER_MESSAGE_NAME,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.previous_steps import (
    format_previous_steps_for_branch,
)


def _assistant(
    content: str = "",
    tool_name: str = "",
    tool_args: str = "",
    tc_id: str = "",
) -> AssistantMessage:
    tool_calls = None
    if tool_name:
        tool_calls = [
            ToolCall(
                id=tc_id or "tc",
                type="function",
                name=tool_name,
                arguments=tool_args,
            )
        ]
    return AssistantMessage(content=content, tool_calls=tool_calls)


def test_format_previous_steps_omits_initial_user_and_screenshot_observations():
    """Task query and live VLM screenshot user turns are excluded from branch context."""
    messages = [
        UserMessage(content="Open GitHub and find the README."),
        UserMessage(
            content=[
                {"type": "text", "text": "Current foreground app: com.android.chrome"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
            ]
        ),
        AssistantMessage(content="I will tap the browser icon."),
    ]

    text = format_previous_steps_for_branch(messages)

    assert "User (task/query)" not in text
    assert "Open GitHub" not in text
    assert "Current foreground app" not in text
    assert "--- Step" in text
    assert "I will tap the browser icon" in text


def test_format_previous_steps_keeps_last_n_turns_with_omission_notice():
    """Only the most recent ``last_n_turns`` assistant/tool pairs are retained."""
    messages = [UserMessage(content="Do the task.")]
    for i in range(12):
        tc = f"t{i}"
        messages.append(
            _assistant(content=f"assistant-{i}", tool_name="wait", tool_args="{}", tc_id=tc)
        )
        messages.append(ToolMessage(tool_call_id=tc, name="wait", content=f"ok-{i}"))

    text = format_previous_steps_for_branch(messages, last_n_turns=10)

    assert "Do the task" not in text
    assert "earlier assistant turn(s) omitted" in text
    assert "\nassistant-0\n" not in text
    assert "\nassistant-1\n" not in text
    assert "\nassistant-2\n" in text
    assert "\nassistant-11\n" in text
    assert "ok-11" in text
    assert "Tool result (wait)" in text
    assert "--- Step 10 (assistant) ---" in text


def test_format_previous_steps_skips_multimodal_skill_user():
    """Skill reference image user messages must not pollute branch previous-step text."""
    messages = [
        UserMessage(
            content=[
                {"type": "text", "text": "[Skill reference image: foo]"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
            ],
            name=MULTIMODAL_SKILL_USER_MESSAGE_NAME,
        ),
        AssistantMessage(content="Next action."),
    ]

    text = format_previous_steps_for_branch(messages)

    assert "Skill reference image" not in text
    assert "base64" not in text
    assert "Next action" in text


def test_format_previous_steps_skips_in_flight_skill_tool_result():
    """The skill_tool turn being consulted is listed without its (not yet present) tool result."""
    messages = [
        UserMessage(content="Initial task query"),
        AssistantMessage(
            content="Loading skill.",
            tool_calls=[
                ToolCall(
                    id="tc2",
                    type="function",
                    name="skill_tool",
                    arguments='{"skill_name": "demo"}',
                )
            ],
        ),
    ]

    text = format_previous_steps_for_branch(messages, skip_tool_call_id="tc2")

    assert "Initial task query" not in text
    assert "Loading skill." in text
    assert "skill_tool" in text
    assert "Tool result (skill_tool)" not in text


def test_format_previous_steps_empty_messages():
    assert format_previous_steps_for_branch([]) == "(no previous steps)"


def test_format_previous_steps_only_user_messages_returns_no_steps():
    messages = [
        UserMessage(content="Task only."),
        UserMessage(content=[{"type": "text", "text": "screenshot"}]),
    ]
    assert format_previous_steps_for_branch(messages) == "(no previous steps)"


def test_format_previous_steps_includes_tool_call_and_result_lines():
    messages = [
        _assistant(content="Tap icon.", tool_name="click", tool_args='{"x":1}', tc_id="t0"),
        ToolMessage(tool_call_id="t0", name="click", content="clicked"),
    ]
    text = format_previous_steps_for_branch(messages)
    assert "Tap icon." in text
    assert "Tool call: click" in text
    assert "Tool result (click): clicked" in text


def test_format_previous_steps_assistant_with_only_tool_calls():
    messages = [
        _assistant(tool_name="wait", tool_args="{}", tc_id="t1"),
        ToolMessage(tool_call_id="t1", name="wait", content="done"),
    ]
    text = format_previous_steps_for_branch(messages)
    assert "Tool call: wait" in text
    assert "Tool result (wait): done" in text


def test_format_previous_steps_zero_last_n_turns_keeps_all_turns():
    messages = [UserMessage(content="task")]
    for i in range(3):
        tc = f"t{i}"
        messages.append(_assistant(content=f"a{i}", tool_name="w", tool_args="{}", tc_id=tc))
        messages.append(ToolMessage(tool_call_id=tc, name="w", content=f"r{i}"))

    text = format_previous_steps_for_branch(messages, last_n_turns=0)
    assert "earlier assistant turn(s) omitted" not in text
    assert "a0" in text
    assert "a2" in text
