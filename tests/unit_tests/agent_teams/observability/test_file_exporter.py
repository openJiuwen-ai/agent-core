# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the file-based SpanExporter (per-day OTLP JSON lines, no collector).

The exporter appends every ended span (one OTLP JSON line each) straight
to a per-day ``traces-<YYYY-MM-DD>.jsonl`` file on ``export()`` — no
in-memory buffer, nothing deferred to flush. Each line is a standalone
``ExportTraceServiceRequest`` with hex traceId/spanId, ingestible by
Collector/Langfuse via POST /v1/traces. Spans from different traces
share the file; the collector splits traces by traceId on ingest.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

from openjiuwen.agent_teams.observability import ObservabilityConfig
from openjiuwen.agent_teams.observability.file_exporter import TraceFileExporter


def _make_span(
    name: str = "test.span",
    *,
    session_id: str | None = "sess-1",
) -> ReadableSpan:
    """Build a finished ReadableSpan with the given attributes."""
    tracer = TracerProvider().get_tracer("ut")
    span = tracer.start_span(name, kind=SpanKind.INTERNAL)
    if session_id is not None:
        span.set_attribute("session.id", session_id)
    span.set_status(Status(StatusCode.OK))
    span.end()
    return span


def _day_file(tmp_path: Path) -> Path:
    """The per-day file path the exporter writes to today."""
    return tmp_path / f"traces-{time.strftime('%Y-%m-%d')}.jsonl"


def _read_spans(path: Path) -> list[dict]:
    """Read a ``.jsonl`` file and return the union of spans across all lines.

    Each line is its own ``ExportTraceServiceRequest``; spans from every
    line are concatenated in file order.
    """
    spans: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        for rs in data.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                spans.extend(ss.get("spans", []))
    return spans


# ---------------------------------------------------------------------------
# Exporter in isolation
# ---------------------------------------------------------------------------


def test_export_writes_immediately(tmp_path: Path) -> None:
    """export() appends straight to today's file — no buffering, no flush needed."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("llm.call", session_id="abc")

    result = exporter.export([span])
    assert result == SpanExportResult.SUCCESS
    # file exists right after export() — no force_flush required
    f = _day_file(tmp_path)
    assert f.exists()
    spans = _read_spans(f)
    assert len(spans) == 1
    assert spans[0]["name"] == "llm.call"


def test_each_line_is_valid_otlp_json_with_hex_ids(tmp_path: Path) -> None:
    """Each appended line is OTLP JSON with hex traceId/spanId (not base64)."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("llm.call", session_id="abc")
    exporter.export([span])

    lines = [ln for ln in _day_file(tmp_path).read_text("utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert "resourceSpans" in data
    sp = data["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert sp["name"] == "llm.call"
    assert len(sp["traceId"]) == 32
    assert all(c in "0123456789abcdef" for c in sp["traceId"]), "traceId must be hex"
    assert len(sp["spanId"]) == 16
    assert all(c in "0123456789abcdef" for c in sp["spanId"]), "spanId must be hex"


def test_spans_of_same_trace_interleaved_in_one_file(tmp_path: Path) -> None:
    """Two spans of the same trace land as two lines in today's file;
    parentSpanId still links child to parent."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    provider = TracerProvider()
    tracer = provider.get_tracer("ut")
    from opentelemetry.trace import set_span_in_context

    parent = tracer.start_span("parent", kind=SpanKind.INTERNAL)
    parent.set_attribute("session.id", "abc")
    parent.set_status(Status(StatusCode.OK))
    child = tracer.start_span("child", kind=SpanKind.INTERNAL, context=set_span_in_context(parent))
    child.set_attribute("session.id", "abc")
    child.set_status(Status(StatusCode.OK))
    parent.end()
    child.end()

    exporter.export([parent])
    exporter.export([child])

    spans = _read_spans(_day_file(tmp_path))
    assert len(spans) == 2
    names = {s["name"] for s in spans}
    assert names == {"parent", "child"}
    parent_sp = next(s for s in spans if s["name"] == "parent")
    child_sp = next(s for s in spans if s["name"] == "child")
    assert child_sp.get("parentSpanId") == parent_sp["spanId"]


def test_spans_of_different_traces_share_one_file(tmp_path: Path) -> None:
    """Spans from different traces are interleaved in the same per-day file;
    each carries its own traceId so the collector can split them."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    s1 = _make_span("a.span", session_id="s1")
    s2 = _make_span("b.span", session_id="s2")
    exporter.export([s1])
    exporter.export([s2])

    spans = _read_spans(_day_file(tmp_path))
    assert len(spans) == 2
    trace_ids = {s["traceId"] for s in spans}
    assert len(trace_ids) == 2, "two distinct traces in one file"


def test_no_session_attribute_still_written(tmp_path: Path) -> None:
    """A span without session.id is appended like any other — no fallback needed."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("orphan.span", session_id=None)
    exporter.export([span])
    assert _read_spans(_day_file(tmp_path))[0]["name"] == "orphan.span"


def test_repeated_export_appends_no_duplication(tmp_path: Path) -> None:
    """Two exports of the same span produce two distinct lines (no dedup needed
    — append semantics). Two exports of different spans just accumulate."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("x.span", session_id="abc")
    exporter.export([span])
    exporter.force_flush()  # no-op; must not drop or duplicate the line
    spans = _read_spans(_day_file(tmp_path))
    assert len(spans) == 1


def test_shutdown_is_noop_does_not_lose_data(tmp_path: Path) -> None:
    """shutdown is a no-op (nothing buffered); data already on disk stays."""
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("late.span", session_id="abc")
    exporter.export([span])
    exporter.shutdown()
    assert len(_read_spans(_day_file(tmp_path))) == 1


def test_cleanup_deletes_old_trace_files(tmp_path: Path) -> None:
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=1)
    span = _make_span("old.span", session_id="old")
    exporter.export([span])

    old_file = _day_file(tmp_path)
    assert old_file.is_file()
    old_time = time.time() - 2 * 86400
    os.utime(old_file, (old_time, old_time))

    exporter._cleanup_old_files()
    assert not old_file.exists()


def test_cleanup_keeps_recent_trace_files(tmp_path: Path) -> None:
    exporter = TraceFileExporter(root_dir=str(tmp_path), retention_days=7)
    span = _make_span("fresh.span", session_id="fresh")
    exporter.export([span])
    exporter._cleanup_old_files()
    assert _day_file(tmp_path).is_file()


# ---------------------------------------------------------------------------
# End-to-end through init_observability / _build_exporter
# ---------------------------------------------------------------------------


@pytest.fixture
def file_config(tmp_path: Path) -> ObservabilityConfig:
    return ObservabilityConfig(
        enabled=True,
        exporter="file",
        traces_dir=str(tmp_path / "traces"),
        file_retention_days=7,
        sample_rate=1.0,
    )


def test_build_exporter_returns_trace_file_exporter(file_config: ObservabilityConfig) -> None:
    from openjiuwen.agent_teams.observability.setup import _build_exporter

    exporter = _build_exporter(file_config)
    assert isinstance(exporter, TraceFileExporter)
    assert exporter.root_dir == file_config.traces_dir
    assert exporter.retention_days == 7


def test_init_observability_writes_per_day_jsonl(file_config: ObservabilityConfig) -> None:
    """init_observability with exporter=file lands a per-day ``.jsonl`` on
    disk after shutdown; each line is a hex-id OTLP JSON request. Pair it
    with BatchSpanProcessor (setup.py default) — spans flush to disk on
    provider shutdown."""
    import asyncio

    asyncio.run(_e2e_async(file_config))


async def _e2e_async(file_config: ObservabilityConfig) -> None:
    from openjiuwen.agent_teams.observability import init_observability, shutdown_observability
    from openjiuwen.agent_teams.observability.setup import get_tracer
    from openjiuwen.agent_teams.observability.span_context import get_or_create_team_span, remove_team_span
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.callback.events import LLMCallEvents

    class _FakeUsage:
        input_tokens = 12
        output_tokens = 7
        total_tokens = 19
        model_name = "fake-llm-1"

    class _FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = "hello"
            self.reasoning_content = ""
            self.finish_reason = "stop"
            self.tool_calls = None
            self.usage_metadata = _FakeUsage()

    init_observability(file_config)
    try:
        get_or_create_team_span("e2e_team", get_tracer("openjiuwen.agent_teams.observability"))
        fw = Runner.callback_framework
        messages = [{"role": "user", "content": "hi"}]
        await fw.trigger(LLMCallEvents.LLM_INVOKE_INPUT, messages=messages, model="fake-llm-1")
        await fw.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            messages=messages,
            result=_FakeAssistantMessage(),
        )
        remove_team_span("e2e_team")
    finally:
        shutdown_observability()

    traces_root = Path(file_config.traces_dir)
    jsonl_files = list(traces_root.glob("*.jsonl"))
    assert jsonl_files, "no .jsonl trace file written"
    for jf in jsonl_files:
        for line in jf.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            assert "resourceSpans" in data
            for rs in data["resourceSpans"]:
                for ss in rs["scopeSpans"]:
                    for sp in ss["spans"]:
                        tid = sp.get("traceId", "")
                        assert len(tid) == 32 and all(c in "0123456789abcdef" for c in tid)
