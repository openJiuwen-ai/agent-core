import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.context_engine.processor.compressor.reinjection import (
    ReinjectContext,
    build_todo_reinjected_content,
    build_single_reinjected_state_message,
)


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
