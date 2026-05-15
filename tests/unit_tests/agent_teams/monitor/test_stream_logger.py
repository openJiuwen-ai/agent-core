# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``TeamStreamLogger`` chunk aggregation and logging."""

from unittest import mock

import pytest

from openjiuwen.agent_teams.monitor.stream_logger import TeamStreamLogger
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.stream import OutputSchema


@pytest.fixture
def log_mock():
    """Patch the module-level ``team_logger`` and yield the mock."""
    with mock.patch("openjiuwen.agent_teams.monitor.stream_logger.team_logger") as m:
        yield m


def _team_chunk(ctype, payload, *, member="leader", role=TeamRole.LEADER, index=0):
    """Build a tagged ``TeamOutputSchema`` chunk."""
    return TeamOutputSchema(
        type=ctype,
        index=index,
        payload=payload,
        source_member=member,
        role=role,
    )


def _plain_chunk(ctype, payload, *, index=0):
    """Build an untagged plain ``OutputSchema`` chunk (no member/role)."""
    return OutputSchema(type=ctype, index=index, payload=payload)


def _records(log_mock):
    """Collect ``(level, block)`` tuples emitted by ``_emit``.

    Filters to records whose template is the fixed ``"{}"`` placeholder,
    so the ``flush`` end-of-stream summary line is excluded.
    """
    out = []
    for level in ("debug", "info", "warning"):
        method = getattr(log_mock, level)
        for c in method.call_args_list:
            if len(c.args) == 2 and c.args[0] == "{}":
                out.append((level, c.args[1]))
    return out


@pytest.mark.level0
def test_accumulates_consecutive_llm_output(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "Hello "}))
    agg.feed(_team_chunk("llm_output", {"content": "world"}))
    agg.feed(_team_chunk("llm_output", {"content": "!"}))
    agg.feed(_team_chunk("tool_call", {"tool_name": "read", "tool_args": "{}"}))

    recs = _records(log_mock)
    assert len(recs) == 2
    text_recs = [r for r in recs if r[0] == "info"]
    tool_recs = [r for r in recs if r[0] == "debug"]
    assert len(text_recs) == 1
    assert "  | Hello world!" in text_recs[0][1]
    assert "category=text" in text_recs[0][1]
    assert len(tool_recs) == 1
    assert "category=tool_call" in tool_recs[0][1]


@pytest.mark.level0
def test_accumulates_consecutive_reasoning_at_debug(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_reasoning", {"content": "step one "}))
    agg.feed(_team_chunk("llm_reasoning", {"content": "step two"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert recs[0][0] == "debug"
    assert "  | step one step two" in recs[0][1]
    assert "category=reasoning" in recs[0][1]


@pytest.mark.level0
def test_member_change_breaks_accumulation(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "from leader"}, member="leader"))
    agg.feed(_team_chunk("llm_output", {"content": "from worker"}, member="researcher", role=TeamRole.TEAMMATE))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 2
    assert "member=leader" in recs[0][1] and "from leader" in recs[0][1]
    assert "member=researcher" in recs[1][1] and "from worker" in recs[1][1]


@pytest.mark.level0
def test_role_change_breaks_accumulation(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "a"}, member="alex", role=TeamRole.LEADER))
    agg.feed(_team_chunk("llm_output", {"content": "b"}, member="alex", role=TeamRole.TEAMMATE))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 2
    assert "role=leader" in recs[0][1]
    assert "role=teammate" in recs[1][1]


@pytest.mark.level0
def test_flush_emits_trailing_run(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "tail "}))
    agg.feed(_team_chunk("llm_output", {"content": "content"}))
    assert _records(log_mock) == []  # nothing emitted until a boundary

    agg.flush()
    recs = _records(log_mock)
    assert len(recs) == 1
    assert "  | tail content" in recs[0][1]


@pytest.mark.level1
def test_answer_deduped_after_llm_output(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "the answer"}))
    agg.feed(_team_chunk("answer", {"content": "the answer"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert "  | the answer" in recs[0][1]


@pytest.mark.level1
def test_answer_fallback_when_no_llm_output(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("answer", {"content": "fallback answer"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert recs[0][0] == "info"
    assert "  | fallback answer" in recs[0][1]


@pytest.mark.level0
@pytest.mark.parametrize(
    "ctype,payload,expected_level",
    [
        ("llm_output", {"content": "hi"}, "info"),
        ("answer", {"content": "hi"}, "info"),
        ("llm_reasoning", {"content": "thinking"}, "debug"),
        ("tool_call", {"tool_name": "read", "tool_args": "{}"}, "debug"),
        ("tool_result", {"tool_name": "read", "tool_result": "ok"}, "debug"),
        ("__interaction__", {"interaction_id": "c1"}, "warning"),
        ("controller_output", "task failed", "warning"),
        ("message", {"content": "sys note"}, "info"),
        ("todo.updated", {"items": "[]"}, "info"),
        ("mystery_type", {"content": "x"}, "info"),
    ],
)
def test_level_routing(log_mock, ctype, payload, expected_level):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk(ctype, payload))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert recs[0][0] == expected_level


@pytest.mark.level1
def test_runtime_ready_special_cased(log_mock):
    payload = {
        "event_type": "team.runtime_ready",
        "team_name": "spec_team",
        "session_id": "sess_1",
        "activation_kind": "create",
    }
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("message", payload))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert recs[0][0] == "info"
    block = recs[0][1]
    assert "category=runtime_ready" in block
    assert "team=spec_team" in block
    assert "session=sess_1" in block
    assert "activation=create" in block


@pytest.mark.level1
def test_plain_message_vs_runtime_ready(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("message", {"content": "just a status line"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert "category=message" in recs[0][1]
    assert "  | just a status line" in recs[0][1]


@pytest.mark.level1
def test_tool_result_truncated(log_mock):
    big = "x" * 5000
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("tool_result", {"tool_name": "read", "tool_result": big}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    block = recs[0][1]
    assert "… (truncated)" in block
    assert block.count("x") < 5000


@pytest.mark.level1
def test_llm_output_never_truncated(log_mock):
    big = "x" * 10000
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": big}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    assert big in recs[0][1]
    assert "(truncated)" not in recs[0][1]


@pytest.mark.level0
def test_multiline_markdown_preserved(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("llm_output", {"content": "# Title\n\n- a\n- b"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    block = recs[0][1]
    assert "  | # Title\n  | \n  | - a\n  | - b" in block
    assert "\\n" not in block  # real newlines, not escaped


@pytest.mark.level1
def test_plain_outputschema_chunk_no_attrs(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_plain_chunk("llm_output", {"content": "untagged text"}))
    agg.flush()

    recs = _records(log_mock)
    assert len(recs) == 1
    block = recs[0][1]
    assert "member=<unknown>" in block
    assert "role=<unknown>" in block
    assert "  | untagged text" in block


@pytest.mark.level1
def test_feed_never_raises(log_mock):
    class _Exploding:
        @property
        def type(self):
            raise RuntimeError("boom")

        payload = None

    agg = TeamStreamLogger()
    agg.feed(_Exploding())  # must not raise
    agg.feed(None)  # must not raise
    assert log_mock.exception.called

    # The aggregator stays usable after a bad chunk.
    agg.feed(_team_chunk("llm_output", {"content": "still works"}))
    agg.flush()
    recs = _records(log_mock)
    assert len(recs) == 1
    assert "  | still works" in recs[0][1]


@pytest.mark.level1
def test_flush_never_raises(log_mock):
    agg = TeamStreamLogger()
    # Corrupt internal state so the join in _flush_accumulated raises.
    agg._buf = [123]  # type: ignore[list-item]
    agg._cat = "text"
    agg.flush()  # must not raise
    assert log_mock.exception.called


@pytest.mark.level0
def test_header_format_contract(log_mock):
    agg = TeamStreamLogger()
    agg.feed(_team_chunk("tool_call", {"tool_name": "read_file", "tool_args": "path"}))

    recs = _records(log_mock)
    assert len(recs) == 1
    block = recs[0][1]
    assert block.startswith("[team.stream] member=leader role=leader category=tool_call\n  | ")
