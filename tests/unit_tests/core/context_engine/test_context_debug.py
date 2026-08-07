# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.core.context_engine.processor.forked.support.context_debug import (
    CONTEXT_DEBUG_DIR_ENV,
    write_llm_request_record,
    write_debug_record,
)


def _context(workspace_dir=""):
    return SimpleNamespace(
        session_id=lambda: "sess-1",
        context_id=lambda: "ctx-1",
        workspace_dir=(lambda: workspace_dir) if workspace_dir else None,
    )


def test_write_debug_record_disabled_returns_none(tmp_path):
    log_path = write_debug_record(
        _context(),
        processor_type="TestProcessor",
        event="threshold_check",
        enabled=False,
        dump_dir=str(tmp_path),
        hit=False,
    )
    assert log_path is None
    assert not any(tmp_path.iterdir())


def test_write_debug_record_appends_jsonl(tmp_path):
    ctx = _context()
    write_debug_record(
        ctx,
        processor_type="RoundLevelCompressor",
        event="threshold_check",
        enabled=True,
        dump_dir=str(tmp_path),
        total_tokens=8500,
        threshold=51200,
        hit=False,
        reason="below_threshold",
    )
    write_debug_record(
        ctx,
        processor_type="RoundLevelCompressor",
        event="threshold_check",
        enabled=True,
        dump_dir=str(tmp_path),
        total_tokens=52000,
        threshold=51200,
        hit=True,
        reason="reached_threshold",
    )

    file_path = tmp_path / "RoundLevelCompressor.jsonl"
    records = [json.loads(line) for line in file_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["event"] == "threshold_check"
    assert records[0]["processor"] == "RoundLevelCompressor"
    assert records[0]["session_id"] == "sess-1"
    assert records[0]["hit"] is False
    assert records[1]["hit"] is True
    assert "timestamp" in records[0]


def test_write_debug_record_uses_env_when_no_dump_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(CONTEXT_DEBUG_DIR_ENV, str(tmp_path))
    log_path = write_debug_record(
        _context(),
        processor_type="Offloader",
        event="message_offloaded",
        enabled=True,
        dump_dir=None,
    )
    assert log_path is not None
    assert os.path.basename(log_path) == "Offloader.jsonl"


def test_write_debug_record_expands_session_template(tmp_path):
    template = str(tmp_path / "{session_id}" / "logs")
    log_path = write_debug_record(
        _context(),
        processor_type="DialogueCompressor",
        event="span_built",
        enabled=True,
        dump_dir=template,
    )
    assert log_path is not None
    assert "sess-1" in log_path


def test_write_debug_record_swallows_filesystem_errors():
    """Debug tracing must never break the compression pipeline."""
    with patch(
        "openjiuwen.core.context_engine.processor.forked.support.context_debug.open",
        side_effect=OSError("disk full"),
    ):
        log_path = write_debug_record(
            _context(),
            processor_type="TestProcessor",
            event="threshold_check",
            enabled=True,
            dump_dir="/nonexistent/path",
            hit=True,
        )
    assert log_path is None


def test_write_llm_request_record_persists_exact_payload(tmp_path):
    log_path = write_llm_request_record(
        _context(),
        enabled=True,
        dump_dir=str(tmp_path),
        model="qwen-max",
        provider="dashscope",
        request_id="request-1",
        sequence=3,
        messages=[
            {"role": "system", "content": "system instructions"},
            {"role": "user", "content": "the complete user request"},
        ],
        tools=[
            {
                "type": "function",
                "name": "read_file",
                "parameters": {"type": "object"},
            }
        ],
        context_window_tokens=131072,
        system_message_count=1,
        context_message_count=1,
        statistic={"total_tokens": 42},
        usage_report={"model": "qwen-max"},
    )

    assert log_path == str(tmp_path / "llm_request.jsonl")
    record = json.loads((tmp_path / "llm_request.jsonl").read_text(encoding="utf-8"))
    assert record["event"] == "pre_call"
    assert record["model"] == "qwen-max"
    assert record["message_count"] == 2
    assert record["tool_count"] == 1
    assert record["messages"][1]["content"] == "the complete user request"
    assert record["tools"][0]["name"] == "read_file"
    assert record["statistic"]["total_tokens"] == 42
