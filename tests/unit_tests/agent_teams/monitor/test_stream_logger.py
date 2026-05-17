# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``TeamStreamLogger`` chunk aggregation and file output."""

from pathlib import Path

import pytest

from openjiuwen.agent_teams.monitor.stream_logger import TeamStreamLogger
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.stream import OutputSchema


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


def _headers(path):
    """Return ``(level, header_tail)`` for each emitted record in the file.

    ``header_tail`` is the ``member=... role=... category=...`` part. The
    ``stream end`` summary line and error markers have no ``member=`` and
    are skipped.
    """
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        for level in ("INFO", "DEBUG", "WARN"):
            tag = f"[{level}] member="
            idx = line.find(tag)
            if idx != -1:
                out.append((level, line[idx + len(f"[{level}] ") :]))
                break
    return out


def _text(path):
    """Return the full file content."""
    return Path(path).read_text(encoding="utf-8")


@pytest.mark.level0
def test_accumulates_consecutive_llm_output(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "Hello "}))
    agg.feed(_team_chunk("llm_output", {"content": "world"}))
    agg.feed(_team_chunk("llm_output", {"content": "!"}))
    agg.feed(_team_chunk("tool_call", {"tool_name": "read", "tool_args": "{}"}))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 2
    assert recs[0] == ("INFO", "member=leader role=leader category=text")
    assert recs[1][0] == "DEBUG"
    assert "category=tool_call" in recs[1][1]
    assert "  | Hello world!" in _text(log_path)


@pytest.mark.level0
def test_accumulates_consecutive_reasoning_at_debug(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_reasoning", {"content": "step one "}))
    agg.feed(_team_chunk("llm_reasoning", {"content": "step two"}))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "DEBUG"
    assert "category=reasoning" in recs[0][1]
    assert "  | step one step two" in _text(log_path)


@pytest.mark.level0
def test_interleaved_members_aggregate_per_source(tmp_path):
    """Chunks from two members interleaved on one stream still aggregate
    into one record per source -- the regression this design fixes."""
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    # leader text and teammate reasoning tokens arrive interleaved.
    agg.feed(_team_chunk("llm_output", {"content": "L1 "}, member="leader", role=TeamRole.LEADER))
    agg.feed(_team_chunk("llm_reasoning", {"content": "G1 "}, member="gamma", role=TeamRole.TEAMMATE))
    agg.feed(_team_chunk("llm_output", {"content": "L2 "}, member="leader", role=TeamRole.LEADER))
    agg.feed(_team_chunk("llm_reasoning", {"content": "G2"}, member="gamma", role=TeamRole.TEAMMATE))
    agg.feed(_team_chunk("llm_output", {"content": "L3"}, member="leader", role=TeamRole.LEADER))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 2
    by_member = {tail.split()[0]: (level, tail) for level, tail in recs}
    assert by_member["member=leader"][0] == "INFO"
    assert by_member["member=gamma"][0] == "DEBUG"
    text = _text(log_path)
    assert "  | L1 L2 L3" in text  # leader's run, uninterrupted by gamma
    assert "  | G1 G2" in text  # gamma's run, uninterrupted by leader


@pytest.mark.level1
def test_same_member_different_role_tracked_separately(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "a"}, member="alex", role=TeamRole.LEADER))
    agg.feed(_team_chunk("llm_output", {"content": "b"}, member="alex", role=TeamRole.TEAMMATE))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 2
    assert {tail for _, tail in recs} == {
        "member=alex role=leader category=text",
        "member=alex role=teammate category=text",
    }


@pytest.mark.level0
def test_flush_emits_trailing_runs(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "tail "}))
    agg.feed(_team_chunk("llm_output", {"content": "content"}))
    assert _headers(log_path) == []  # nothing emitted until a boundary

    agg.flush()
    recs = _headers(log_path)
    assert len(recs) == 1
    assert "  | tail content" in _text(log_path)


@pytest.mark.level1
def test_answer_deduped_after_llm_output(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "the answer"}))
    agg.feed(_team_chunk("answer", {"content": "the answer"}))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1


@pytest.mark.level1
def test_answer_dedup_is_per_source(tmp_path):
    """A teammate's ``answer`` is not dropped just because the leader
    produced ``llm_output`` -- dedup is keyed per source."""
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "leader text"}, member="leader", role=TeamRole.LEADER))
    agg.feed(_team_chunk("answer", {"content": "gamma answer"}, member="gamma", role=TeamRole.TEAMMATE))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 2
    assert "  | gamma answer" in _text(log_path)


@pytest.mark.level1
def test_answer_fallback_when_no_llm_output(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("answer", {"content": "fallback answer"}))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "INFO"
    assert "  | fallback answer" in _text(log_path)


@pytest.mark.level0
@pytest.mark.parametrize(
    "ctype,payload,expected_level",
    [
        ("llm_output", {"content": "hi"}, "INFO"),
        ("answer", {"content": "hi"}, "INFO"),
        ("llm_reasoning", {"content": "thinking"}, "DEBUG"),
        ("tool_call", {"tool_name": "read", "tool_args": "{}"}, "DEBUG"),
        ("tool_result", {"tool_name": "read", "tool_result": "ok"}, "DEBUG"),
        ("__interaction__", {"interaction_id": "c1"}, "WARN"),
        ("controller_output", "task failed", "WARN"),
        ("message", {"content": "sys note"}, "INFO"),
        ("todo.updated", {"items": "[]"}, "INFO"),
        ("mystery_type", {"content": "x"}, "INFO"),
    ],
)
def test_level_routing(tmp_path, ctype, payload, expected_level):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk(ctype, payload))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == expected_level


@pytest.mark.level1
def test_runtime_ready_special_cased(tmp_path):
    log_path = tmp_path / "stream.log"
    payload = {
        "event_type": "team.runtime_ready",
        "team_name": "spec_team",
        "session_id": "sess_1",
        "activation_kind": "create",
    }
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("message", payload))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "INFO"
    assert "category=runtime_ready" in recs[0][1]
    text = _text(log_path)
    assert "team=spec_team" in text
    assert "session=sess_1" in text
    assert "activation=create" in text


@pytest.mark.level1
def test_plain_message_vs_runtime_ready(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("message", {"content": "just a status line"}))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert "category=message" in recs[0][1]
    assert "  | just a status line" in _text(log_path)


@pytest.mark.level1
def test_tool_result_truncated(tmp_path):
    log_path = tmp_path / "stream.log"
    big = "x" * 5000
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("tool_result", {"tool_name": "read", "tool_result": big}))
    agg.flush()

    text = _text(log_path)
    assert "… (truncated)" in text
    assert text.count("x") < 5000


@pytest.mark.level1
def test_llm_output_never_truncated(tmp_path):
    log_path = tmp_path / "stream.log"
    big = "x" * 10000
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": big}))
    agg.flush()

    text = _text(log_path)
    assert big in text
    assert "(truncated)" not in text


@pytest.mark.level0
def test_multiline_markdown_preserved(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "# Title\n\n- a\n- b"}))
    agg.flush()

    text = _text(log_path)
    assert "  | # Title\n  | \n  | - a\n  | - b" in text
    assert "\\n" not in text  # real newlines, not escaped


@pytest.mark.level1
def test_plain_outputschema_chunk_is_skipped(tmp_path):
    """Plain (untagged) ``OutputSchema`` chunks come from infrastructure
    layers (tracer, workflow normalisation) -- not team flow -- and are
    dropped so they don't drown the diagnostic file."""
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_plain_chunk("llm_output", {"content": "untagged text"}))
    agg.feed(_plain_chunk("message", {"traceId": "abc", "invokeId": "def"}))
    agg.flush()

    assert _headers(log_path) == []
    # Both chunks were counted (the "stream end" summary reports total fed).
    assert "stream end, 2 chunks" in _text(log_path)


@pytest.mark.level1
def test_tool_call_empty_fields_falls_back_to_payload(tmp_path):
    """Non-``tool_tracker`` paths emit ``tool_call`` chunks without the
    canonical ``tool_name`` / ``tool_args`` keys. Fall back to a capped
    payload dump so the record carries actual info."""
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    payload = {"tool_update": {"name": "send_message", "status": "in_progress"}}
    agg.feed(_team_chunk("tool_call", payload))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "DEBUG"
    assert "tool_name=" not in _text(log_path)
    assert "tool_update" in _text(log_path)


@pytest.mark.level1
def test_tool_update_extracts_nested_fields(tmp_path):
    """``tool_update`` chunks wrap tool fields under an inner key (emitted
    by third-party rails such as jiuwenclaw's stream_event_rail).
    Extract them directly so the record is informative, not a payload dump."""
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    payload = {
        "tool_update": {
            "tool_name": "send_message",
            "tool_call_id": "tool-abc",
            "arguments": '{"content": "hello"}',
            "status": "in_progress",
        }
    }
    agg.feed(_team_chunk("tool_update", payload))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "DEBUG"
    assert "category=tool_update" in recs[0][1]
    text = _text(log_path)
    assert "tool_name=send_message" in text
    assert "status=in_progress" in text
    assert "tool_call_id=tool-abc" in text


@pytest.mark.level1
def test_tool_result_empty_fields_falls_back_to_payload(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    payload = {"tool_update": {"name": "send_message", "status": "finish"}}
    agg.feed(_team_chunk("tool_result", payload))
    agg.flush()

    recs = _headers(log_path)
    assert len(recs) == 1
    assert recs[0][0] == "DEBUG"
    assert "tool_update" in _text(log_path)


@pytest.mark.level1
def test_feed_never_raises(tmp_path):
    """Bad / non-OutputSchema chunks are skipped without raising."""

    class _Exploding:
        @property
        def type(self):
            raise RuntimeError("boom")

        payload = None

    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_Exploding())  # not TeamOutputSchema -> skipped, no raise
    agg.feed(None)  # not TeamOutputSchema -> skipped, no raise

    # The aggregator stays usable after the bad chunks.
    agg.feed(_team_chunk("llm_output", {"content": "still works"}))
    agg.flush()
    assert "  | still works" in _text(log_path)


@pytest.mark.level1
def test_feed_swallows_internal_exception(tmp_path):
    """If processing a tagged chunk raises mid-pipeline, the error
    marker is written to the file and the aggregator stays usable."""
    import openjiuwen.agent_teams.monitor.stream_logger as sl_mod

    def _raise_boom(*_args, **_kwargs):
        raise RuntimeError("classifier boom")

    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    original = sl_mod._classify
    try:
        sl_mod._classify = _raise_boom
        agg.feed(_team_chunk("llm_output", {"content": "triggers boom"}))
    finally:
        sl_mod._classify = original

    agg.feed(_team_chunk("llm_output", {"content": "still works"}))
    agg.flush()

    text = _text(log_path)
    assert "feed error" in text
    assert "  | still works" in text


@pytest.mark.level1
def test_flush_never_raises(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    # Corrupt internal state so the join in _flush_key raises.
    from openjiuwen.agent_teams.monitor.stream_logger import _Run

    agg._runs[("m", "r")] = _Run(category="text", buf=[123])  # type: ignore[list-item]
    agg.flush()  # must not raise

    assert "flush error" in _text(log_path)
    assert agg._file is None  # file still closed despite the error


@pytest.mark.level0
def test_header_format_contract(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("tool_call", {"tool_name": "read_file", "tool_args": "path"}))
    agg.flush()

    # First line is "<date> <time> [DEBUG] member=leader role=leader category=tool_call".
    first_line = _text(log_path).splitlines()[0]
    assert first_line.endswith("[DEBUG] member=leader role=leader category=tool_call")
    assert "\n  | " in _text(log_path)


@pytest.mark.level1
def test_creates_parent_dirs(tmp_path):
    log_path = tmp_path / "nested" / "dir" / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "x"}))
    agg.flush()

    assert log_path.exists()
    assert "  | x" in _text(log_path)


@pytest.mark.level1
def test_flush_closes_file(tmp_path):
    log_path = tmp_path / "stream.log"
    agg = TeamStreamLogger(log_path)
    agg.feed(_team_chunk("llm_output", {"content": "x"}))
    agg.flush()

    assert agg._file is None
