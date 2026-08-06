import json
from pathlib import Path
from typing import Any

from openjiuwen.symphony.experience.trace.parser import parse_session


def _write_session(tmp_path: Path, history: list[dict[str, Any]]) -> Path:
    sessions_dir = tmp_path / "sessions"
    session_dir = sessions_dir / "session-1"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (session_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    return sessions_dir


def _user_record() -> dict[str, Any]:
    return {
        "request_id": "request-1",
        "role": "user",
        "content": "Extract and summarize the document",
    }


def test_parse_session_extracts_candidates_from_compose_graph_call(tmp_path: Path) -> None:
    sessions_dir = _write_session(
        tmp_path,
        [
            _user_record(),
            {
                "request_id": "request-1",
                "role": "assistant",
                "event_type": "chat.tool_call",
                "tool_call": {
                    "name": "symphony_compose_graph",
                    "arguments": json.dumps({"candidate_skill_ids": ["extract", "summarize"]}),
                },
            },
            {
                "request_id": "request-1",
                "role": "assistant",
                "event_type": "chat.final",
                "content": "Done",
            },
        ],
    )

    records = parse_session("session-1", sessions_dir)

    assert len(records) == 1
    assert records[0].skills == ["extract", "summarize"]


def test_parse_session_extracts_selected_skills_from_compose_graph_result(tmp_path: Path) -> None:
    sessions_dir = _write_session(
        tmp_path,
        [
            _user_record(),
            {
                "request_id": "request-1",
                "role": "assistant",
                "event_type": "chat.tool_result",
                "tool_name": "symphony_compose_graph",
                "result": "graph composed",
                "raw_output": {
                    "plan": {
                        "steps": [
                            {"skill_id": "extract"},
                            {"name": "summarize"},
                            {"skill_id": "extract"},
                        ]
                    }
                },
            },
        ],
    )

    records = parse_session("session-1", sessions_dir)

    assert len(records) == 1
    assert records[0].skills == ["extract", "summarize"]


def test_parse_session_does_not_recognize_legacy_compose_score(tmp_path: Path) -> None:
    sessions_dir = _write_session(
        tmp_path,
        [
            _user_record(),
            {
                "request_id": "request-1",
                "role": "assistant",
                "event_type": "chat.tool_call",
                "tool_call": {
                    "name": "symphony_compose_score",
                    "arguments": json.dumps({"candidate_skill_ids": ["legacy-skill"]}),
                },
            },
        ],
    )

    records = parse_session("session-1", sessions_dir)

    assert len(records) == 1
    assert records[0].skills == []
