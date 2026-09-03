# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strong-constraint contracts for the canonical-collection / adapter-export split.

Boundary enforced by this module:

1. Collection modules never reference ``LANGFUSE_`` or ``langfuse.*``.
2. The same canonical span exports to standard OTLP with zero Langfuse
   attributes, while the Langfuse adapter receives the projected ones.
3. Projection never mutates the original span.
4. LLM / tool / agent / team-root / task / reasoning mappings hold.
5. Modern (default) vs legacy prompt projection both verified.
6. A transform failure exports the original span instead of dropping it.
7. Token counts stay provider-raw through projection.
8. The file exporter (Langfuse file WAL) is wrapped in the same projection
   as the Langfuse OTLP exporter, and the deprecated ``backend`` selector
   is translated once with a warning.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.exporters.langfuse import (
    LANGFUSE_INGESTION_VERSION_HEADER,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
    LANGFUSE_TRACE_NAME,
    LANGFUSE_TRACE_TAGS,
    LangfuseExporterConfig,
    build_langfuse_auth_headers,
    project_langfuse_attributes,
    project_langfuse_span,
)
from openjiuwen.extensions.observability.exporters.transforming import (
    TransformingSpanExporter,
)
from openjiuwen.extensions.observability.runtime import (
    build_span_exporter,
    resolve_exporter_selection,
)
from openjiuwen.extensions.observability.semconv import (
    OJ_SPAN_INPUT,
    OJ_SPAN_OUTPUT,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    AT_TEAM_NAME,
    OJ_TRAJECTORY_RECORD_KIND,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Every module that belongs to the collection layer: it may only write
# standard GenAI/core attributes and openjiuwen.*/agentteam.* extensions.
_COLLECTION_MODULES = (
    "openjiuwen/extensions/observability/callback_handler.py",
    "openjiuwen/extensions/observability/span_context.py",
    "openjiuwen/extensions/observability/span_record_processor.py",
    "openjiuwen/extensions/observability/trajectory_events.py",
    "openjiuwen/extensions/observability/context_compression_handler.py",
    "openjiuwen/agent_teams/observability/monitor_handler.py",
    "openjiuwen/agent_teams/observability/span_context.py",
    "openjiuwen/agent_teams/observability/rail.py",
    "openjiuwen/agent_teams/observability/claude/bridge.py",
    "openjiuwen/agent_teams/observability/codex/bridge.py",
    "openjiuwen/harness/observability/rail.py",
    "openjiuwen/harness/observability/run_span.py",
    "openjiuwen/harness/observability/span_context.py",
)

_FORBIDDEN_PATTERN = re.compile(r"LANGFUSE_|langfuse\.")


def _make_llm_span(provider: TracerProvider) -> Any:
    """Return one ended canonical LLM (chat) span."""
    tracer = provider.get_tracer("langfuse-adapter-test")
    span = tracer.start_span(name="chat fake-model")
    span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
    span.set_attribute(OJ_TRAJECTORY_RECORD_KIND, "inference")
    span.set_attribute(GEN_AI_SYSTEM_INSTRUCTIONS, json.dumps([
        {"type": "text", "content": "Be precise"},
    ]))
    span.set_attribute(GEN_AI_INPUT_MESSAGES, json.dumps([
        {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
    ]))
    span.set_attribute(GEN_AI_OUTPUT_MESSAGES, json.dumps([
        {"role": "assistant", "parts": [{"type": "text", "content": "hello"}]},
    ]))
    span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, 12)
    span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, 7)
    span.set_attribute("gen_ai.conversation.id", "session-1")
    span.end()
    return span


def _ended_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return list(exporter.get_finished_spans())


# ---------------------------------------------------------------------------
# 1. Collection layer scan
# ---------------------------------------------------------------------------


def test_collection_modules_contain_no_langfuse_references() -> None:
    """`LANGFUSE_` / `langfuse.*` may not appear anywhere in the collection layer."""
    offenders: list[str] = []
    for relative in _COLLECTION_MODULES:
        source = (_PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            if _FORBIDDEN_PATTERN.search(line):
                offenders.append(f"{relative}:{lineno}: {line.strip()}")
    assert not offenders, "langfuse leaked into collection layer:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 2 + 3. Dual-exporter projection and immutability
# ---------------------------------------------------------------------------


def test_same_canonical_span_standard_otlp_zero_langfuse_and_adapter_projected() -> None:
    """One canonical span: raw exporter sees zero Langfuse attrs, adapter sees them."""
    provider = TracerProvider()
    raw_exporter = InMemorySpanExporter()
    adapter_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(raw_exporter))
    provider.add_span_processor(SimpleSpanProcessor(TransformingSpanExporter(
        adapter_exporter,
        transform=lambda span: project_langfuse_span(span, LangfuseExporterConfig()),
    )))

    span = _make_llm_span(provider)

    raw = _ended_spans(raw_exporter)
    projected = _ended_spans(adapter_exporter)
    assert [s.name for s in raw] == [span.name]
    assert [s.name for s in projected] == [span.name]

    for finished in raw:
        assert not [key for key in finished.attributes if key.startswith("langfuse.")], (
            "standard OTLP export must carry zero langfuse.* attributes"
        )
        assert "session.id" not in finished.attributes

    projected_attrs = projected[0].attributes
    assert projected_attrs[LANGFUSE_OBSERVATION_TYPE] == "generation"
    assert projected_attrs[LANGFUSE_SESSION_ID] == "session-1"
    assert "Be precise" in projected_attrs[LANGFUSE_OBSERVATION_INPUT]
    assert "hi" in projected_attrs[LANGFUSE_OBSERVATION_INPUT]
    assert "hello" in projected_attrs[LANGFUSE_OBSERVATION_OUTPUT]

    # 3. The raw exporter's span stays projection-free: the projection ran on a copy.
    assert LANGFUSE_OBSERVATION_TYPE not in raw[0].attributes


def test_projection_returns_a_copy_and_never_mutates_the_input() -> None:
    provider = TracerProvider()
    span = _make_llm_span(provider)
    before = dict(span.attributes)

    projected = project_langfuse_span(span, LangfuseExporterConfig())

    assert projected is not span
    assert projected.context.trace_id == span.context.trace_id
    assert projected.context.span_id == span.context.span_id
    assert projected.parent == span.parent
    assert projected.kind == span.kind
    assert projected.start_time == span.start_time
    assert projected.end_time == span.end_time
    assert projected.status == span.status
    assert projected.events == span.events
    assert projected.links == span.links
    assert projected.name == span.name
    # The input keeps exactly the attributes it had.
    assert dict(span.attributes) == before
    assert not [key for key in span.attributes if key.startswith("langfuse.")]
    # The copy carries the derived fields in addition.
    assert projected.attributes[LANGFUSE_OBSERVATION_TYPE] == "generation"


# ---------------------------------------------------------------------------
# 4. Per-record-kind mapping
# ---------------------------------------------------------------------------


def _finish_with(
    provider: TracerProvider,
    name: str,
    attributes: dict[str, Any],
    *,
    parent: Any = None,
) -> Any:
    tracer = provider.get_tracer("langfuse-adapter-test")
    if parent is not None:
        from opentelemetry import context as otel_context
        from opentelemetry.trace import set_span_in_context

        span = tracer.start_span(
            name=name,
            context=set_span_in_context(parent, otel_context.get_current()),
        )
    else:
        span = tracer.start_span(name=name)
    for key, value in attributes.items():
        span.set_attribute(key, value)
    span.end()
    return span


def test_tool_span_maps_arguments_and_result() -> None:
    provider = TracerProvider()
    span = _finish_with(provider, "execute_tool search", {
        GEN_AI_OPERATION_NAME: "execute_tool",
        OJ_TRAJECTORY_RECORD_KIND: "tool",
        GEN_AI_TOOL_CALL_ARGUMENTS: '{"query": "x"}',
        GEN_AI_TOOL_CALL_RESULT: '{"hits": 3}',
    })

    derived = project_langfuse_attributes(span)
    assert derived[LANGFUSE_OBSERVATION_TYPE] == "tool"
    assert derived[LANGFUSE_OBSERVATION_INPUT] == '{"query": "x"}'
    assert derived[LANGFUSE_OBSERVATION_OUTPUT] == '{"hits": 3}'


def test_agent_span_maps_span_io() -> None:
    """Agent spans carry their IO on the one backend-neutral span.input/output."""
    provider = TracerProvider()
    span = _finish_with(provider, "agent.leader.invoke", {
        GEN_AI_OPERATION_NAME: "invoke_agent",
        OJ_TRAJECTORY_RECORD_KIND: "agent",
        OJ_SPAN_INPUT: "plan this",
        OJ_SPAN_OUTPUT: "planned",
    })

    derived = project_langfuse_attributes(span)
    assert derived[LANGFUSE_OBSERVATION_TYPE] == "agent"
    assert derived[LANGFUSE_OBSERVATION_INPUT] == "plan this"
    assert derived[LANGFUSE_OBSERVATION_OUTPUT] == "planned"


def test_team_root_span_gets_trace_name_tags_and_session() -> None:
    provider = TracerProvider()
    span = _finish_with(provider, "team.alpha", {
        AT_TEAM_NAME: "alpha",
        "gen_ai.conversation.id": "session-9",
    })

    derived = project_langfuse_attributes(span)
    assert derived[LANGFUSE_TRACE_NAME] == "team.alpha"
    assert derived[LANGFUSE_TRACE_TAGS] == ["alpha"]
    assert derived[LANGFUSE_SESSION_ID] == "session-9"


def test_task_and_event_spans_map_span_io() -> None:
    provider = TracerProvider()
    root = _finish_with(provider, "team.alpha", {AT_TEAM_NAME: "alpha"})
    task = _finish_with(provider, "task.t1", {
        OJ_SPAN_INPUT: '{"task_id": "t1"}',
        OJ_SPAN_OUTPUT: "completed",
    }, parent=root)

    derived = project_langfuse_attributes(task)
    assert derived[LANGFUSE_OBSERVATION_TYPE] == "span"
    assert derived[LANGFUSE_OBSERVATION_INPUT] == '{"task_id": "t1"}'
    assert derived[LANGFUSE_OBSERVATION_OUTPUT] == "completed"
    # Only the trace root carries trace-level attributes.
    assert LANGFUSE_TRACE_NAME not in derived


def test_reasoning_span_has_no_input_placeholder() -> None:
    provider = TracerProvider()
    span = _finish_with(provider, "llm.reasoning", {
        OJ_TRAJECTORY_RECORD_KIND: "reasoning",
        GEN_AI_OUTPUT_MESSAGES: json.dumps([
            {"role": "assistant", "parts": [{"type": "reasoning", "content": "thinking"}]},
        ]),
    })

    derived = project_langfuse_attributes(span)
    assert derived[LANGFUSE_OBSERVATION_TYPE] == "span"
    assert LANGFUSE_OBSERVATION_INPUT not in derived, (
        "the adapter decides the input placeholder for reasoning spans, not the collection layer"
    )
    assert "thinking" in derived[LANGFUSE_OBSERVATION_OUTPUT]


def test_workflow_and_retrieval_operation_types() -> None:
    provider = TracerProvider()
    workflow = _finish_with(provider, "workflow.run", {GEN_AI_OPERATION_NAME: "workflow"})
    retrieval = _finish_with(provider, "retrieve", {GEN_AI_OPERATION_NAME: "retrieval"})
    embeddings = _finish_with(provider, "embed", {GEN_AI_OPERATION_NAME: "embeddings"})

    assert project_langfuse_attributes(workflow)[LANGFUSE_OBSERVATION_TYPE] == "chain"
    assert project_langfuse_attributes(retrieval)[LANGFUSE_OBSERVATION_TYPE] == "retriever"
    assert project_langfuse_attributes(embeddings)[LANGFUSE_OBSERVATION_TYPE] == "embedding"


# ---------------------------------------------------------------------------
# 5. Legacy prompt projection
# ---------------------------------------------------------------------------


def test_legacy_prompt_projection_off_by_default_and_on_demand() -> None:
    provider = TracerProvider()
    span = _make_llm_span(provider)

    modern = project_langfuse_attributes(span, LangfuseExporterConfig())
    assert not [
        key for key in modern
        if key.startswith(("gen_ai.prompt.", "gen_ai.completion."))
    ], "indexed prompt/completion projection must stay off by default"

    legacy = project_langfuse_attributes(
        span,
        LangfuseExporterConfig(legacy_prompt_projection=True),
    )
    assert legacy["gen_ai.prompt.0.role"] == "user"
    assert legacy["gen_ai.prompt.0.content"] == "hi"
    assert legacy["gen_ai.completion.0.role"] == "assistant"
    assert legacy["gen_ai.completion.0.content"] == "hello"


# ---------------------------------------------------------------------------
# 6. Transform failure falls back to the original span
# ---------------------------------------------------------------------------


def test_transform_failure_exports_the_original_span() -> None:
    exporter = InMemorySpanExporter()

    def _boom(span: Any) -> Any:
        raise RuntimeError("projection exploded")

    wrapped = TransformingSpanExporter(exporter, transform=_boom)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(wrapped))
    span = _make_llm_span(provider)

    finished = _ended_spans(exporter)
    assert [s.name for s in finished] == [span.name]
    assert finished[0].context.span_id == span.context.span_id
    assert GEN_AI_OPERATION_NAME in finished[0].attributes


# ---------------------------------------------------------------------------
# 7. Token counts stay provider-raw
# ---------------------------------------------------------------------------


def test_projection_keeps_provider_raw_token_values() -> None:
    provider = TracerProvider()
    span = _make_llm_span(provider)

    projected = project_langfuse_span(span)
    assert projected.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert projected.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    # The projection adds no usage attributes of its own.
    assert not [
        key for key in projected.attributes
        if key not in span.attributes and key.startswith("gen_ai.usage.")
    ]


# ---------------------------------------------------------------------------
# 8. Exporter wiring, auth headers, deprecated backend
# ---------------------------------------------------------------------------


def test_langfuse_auth_and_ingestion_headers() -> None:
    v4 = build_langfuse_auth_headers("pk", "sk", ingestion_version=4)
    assert v4[LANGFUSE_INGESTION_VERSION_HEADER] == "4"
    assert v4["authorization"].startswith("Basic ")

    v3 = build_langfuse_auth_headers("pk", "sk", ingestion_version=3)
    assert LANGFUSE_INGESTION_VERSION_HEADER not in v3
    assert v3["authorization"] == v4["authorization"]

    empty = build_langfuse_auth_headers("", "", ingestion_version=4)
    assert "authorization" not in empty


def test_langfuse_exporter_uses_otlp_http_behind_projection() -> None:
    config = ObservabilityConfig(
        enabled=True,
        exporter="langfuse",
        endpoint="https://langfuse.example/api/public/otel/v1/traces",
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
    )
    exporter = build_span_exporter(config)
    assert isinstance(exporter, TransformingSpanExporter)

    inner = exporter.exporter
    assert type(inner).__name__ == "OTLPSpanExporter"
    assert inner._endpoint == config.endpoint


def test_file_exporter_is_wrapped_in_the_langfuse_projection(tmp_path: Any) -> None:
    """The file exporter is the file WAL for Langfuse: same projection applied."""
    config = ObservabilityConfig(
        enabled=True,
        exporter="file",
        traces_dir=str(tmp_path / "traces"),
    )
    exporter = build_span_exporter(config)
    assert isinstance(exporter, TransformingSpanExporter)
    assert type(exporter.exporter).__name__ == "TraceFileExporter"

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _make_llm_span(provider)

    files = sorted((tmp_path / "traces").glob("traces-*.jsonl"))
    assert files, "file exporter wrote no trace file"
    lines = files[-1].read_text(encoding="utf-8").splitlines()
    assert lines
    record = json.loads(lines[-1])
    attrs = record["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {item["key"] for item in attrs}
    assert "langfuse.observation.type" in keys
    assert "langfuse.observation.input" in keys


def test_file_and_langfuse_exporters_share_identical_projection(tmp_path: Any) -> None:
    """File WAL lines and the Langfuse exporter payload carry the same fields."""
    file_config = ObservabilityConfig(
        enabled=True,
        exporter="file",
        traces_dir=str(tmp_path / "wal"),
    )
    file_exporter = build_span_exporter(file_config)

    provider = TracerProvider()
    collector = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(file_exporter))
    provider.add_span_processor(SimpleSpanProcessor(TransformingSpanExporter(
        collector,
        transform=lambda span: project_langfuse_span(
            span,
            LangfuseExporterConfig.from_observability_config(file_config),
        ),
    )))
    _make_llm_span(provider)

    wal_line = json.loads(
        sorted((tmp_path / "wal").glob("traces-*.jsonl"))[-1]
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    wal_attrs = {
        item["key"]: item["value"].get("stringValue")
        for item in wal_line["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    }
    otel_attrs = collector.get_finished_spans()[0].attributes
    for key in (
        LANGFUSE_OBSERVATION_TYPE,
        LANGFUSE_OBSERVATION_INPUT,
        LANGFUSE_OBSERVATION_OUTPUT,
        LANGFUSE_SESSION_ID,
    ):
        assert wal_attrs[key] == otel_attrs[key]


def test_deprecated_backend_is_translated_once_with_warning() -> None:
    config = ObservabilityConfig(enabled=True, backend="langfuse")
    with pytest.warns(DeprecationWarning, match="backend is deprecated"):
        assert resolve_exporter_selection(config) == "langfuse"

    modern = ObservabilityConfig(enabled=True, exporter="otlp_http")
    with _no_warnings():
        assert resolve_exporter_selection(modern) == "otlp_http"


class _no_warnings:
    """Context manager asserting that no warning is emitted."""

    def __init__(self) -> None:
        self._ctx: Any = None

    def __enter__(self) -> "_no_warnings":
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("error")
        return self

    def __exit__(self, *exc: Any) -> None:
        self._ctx.__exit__(*exc)
