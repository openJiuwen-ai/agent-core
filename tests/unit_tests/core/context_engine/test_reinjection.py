import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.context_engine.processor.compressor.reinjection import (
    build_file_reinjected_content,
    build_plan_reinjected_content,
    build_plan_mode_reinjected_content,
    build_skill_reinjected_content,
    ReinjectContext,
    build_todo_reinjected_content,
    build_single_reinjected_state_message,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _ctx(session_state, *, workspace_root=None, context=None):
    return ReinjectContext(
        session_state=session_state,
        source_messages=[],
        messages_to_keep=[],
        workspace_root=workspace_root,
        config=SimpleNamespace(),
        state_marker="[STATE]",
        truncate=lambda text: text,
        context=context,
    )


def _ctx_with_messages(session_state, source_messages, *, messages_to_keep=None, workspace_root=None):
    return ReinjectContext(
        session_state=session_state,
        source_messages=source_messages,
        messages_to_keep=messages_to_keep or [],
        workspace_root=workspace_root,
        config=SimpleNamespace(reinject_recent_skills=3),
        state_marker="[STATE]",
        truncate=lambda text: text,
        context=None,
    )


def _tool_call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, type="function", arguments=arguments)


def test_build_plan_mode_reinjected_content_skips_default_normal_mode():
    content = build_plan_mode_reinjected_content(
        _ctx({"plan_mode": {"mode": "normal", "pre_plan_mode": "normal", "plan_slug": None}})
    )

    assert content == ""


def test_build_plan_reinjected_content_reads_enter_plan_mode_path_when_workspace_differs(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    swarm_workspace = tmp_path / "jiuwenswarm-workspace"
    plan_dir = swarm_workspace / ".plans"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "active-plan.md"
    plan_path.write_text("# Active Plan\n\n- Keep this plan visible.\n", encoding="utf-8")

    content = build_plan_reinjected_content(
        _ctx_with_messages(
            {"plan_mode": {"mode": "normal", "pre_plan_mode": "plan", "plan_slug": "active-plan"}},
            [
                AssistantMessage(
                    content="",
                    tool_calls=[_tool_call("tc-plan", "enter_plan_mode", "{}")],
                ),
                ToolMessage(
                    content=f"Plan file created at: {plan_path}\nContinue the workflow.",
                    tool_call_id="tc-plan",
                ),
            ],
            workspace_root=str(project_root),
        )
    )

    assert "Current plan file:" in content
    assert str(plan_path) in content
    assert "Keep this plan visible." in content


def test_build_file_reinjected_content_reads_plain_read_file_tool_result():
    content = build_file_reinjected_content(
        _ctx_with_messages(
            {},
            [
                UserMessage(content="read file"),
                AssistantMessage(
                    content="",
                    tool_calls=[_tool_call("tc-file", "read_file", '{"file_path": "/repo/src/app.py"}')],
                ),
                ToolMessage(content="def main():\n    return 1\n", tool_call_id="tc-file"),
            ],
        )
    )

    assert "Recently read file: /repo/src/app.py" in content
    assert "Lines returned: 2" in content
    assert "def main():" in content


def test_build_skill_reinjected_content_reads_tool_output_repr():
    messages = [
        UserMessage(content="load skill"),
        AssistantMessage(
            content="",
            tool_calls=[_tool_call("tc-skill", "skill_tool", '{"skill_name": "debugging"}')],
        ),
        ToolMessage(
            content=(
                "success=True data={'skill_directory': '/skills/debugging', "
                "'skill_content': '# Debugging\\nUse evidence first.'} error=None"
            ),
            tool_call_id="tc-skill",
        ),
    ]

    reinjected = build_skill_reinjected_content(_ctx_with_messages({}, messages))

    assert len(reinjected) == 1
    assert "Skill: debugging" in reinjected[0].content
    assert "# Debugging" in reinjected[0].content


def test_build_todo_reinjected_content_reads_active_todos_from_file(tmp_path):
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / "todo.json").write_text(
        json.dumps(
            [
                {"id": "inspect", "content": "Inspect compressors", "status": "completed"},
                {"id": "wire", "content": "Wire reinjection", "status": "in_progress"},
                {"id": "verify", "content": "Run tests", "status": "pending"},
                {"id": "drop", "content": "Discarded task", "status": "cancelled"},
            ]
        ),
        encoding="utf-8",
    )
    context = MagicMock()
    context.session_id.return_value = "session-1"

    content = build_todo_reinjected_content(
        _ctx(
            {"todos": [{"id": "ignored", "content": "Ignore session state", "status": "pending"}]},
            workspace_root=str(tmp_path),
            context=context,
        )
    )

    assert "Active todos:" in content
    assert "[in_progress] wire: Wire reinjection" in content
    assert "[pending] verify: Run tests" in content
    assert "completed: 1" in content
    assert "Discarded task" not in content
    assert "Ignore session state" not in content


def test_build_todo_reinjected_content_reads_jiuwenswarm_todo_directory(tmp_path):
    session_dir = tmp_path / "todo" / "session-1"
    session_dir.mkdir(parents=True)
    (session_dir / "todo.json").write_text(
        json.dumps([{"id": "todo-1", "content": "Keep active", "status": "pending"}]),
        encoding="utf-8",
    )
    context = MagicMock()
    context.session_id.return_value = "session-1"

    content = build_todo_reinjected_content(
        _ctx({}, workspace_root=str(tmp_path), context=context)
    )

    assert "Active todos:" in content
    assert "[pending] todo-1: Keep active" in content


def test_build_todo_reinjected_content_ignores_session_state_without_file():
    content = build_todo_reinjected_content(
        _ctx({"todos": [{"id": "verify", "content": "Run tests", "status": "pending"}]})
    )

    assert content == ""


def test_build_single_reinjected_state_message_combines_selected_builders():
    context = MagicMock()
    context.session_id.return_value = "session-1"
    message = build_single_reinjected_state_message(
        _ctx({"todos": [{"id": "verify", "content": "Run tests", "status": "pending"}]}, context=context),
        builder_names=["todo"],
    )

    assert message is None
