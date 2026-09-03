# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Langfuse OTLP exporter adapter: projection, authentication and transport.

This is the only module in the collection pipeline that knows about
Langfuse. It derives ``langfuse.*`` attributes from the canonical span
(standard GenAI + ``openjiuwen.*`` / ``agentteam.*`` extensions) at export
time, wraps an OTLP HTTP exporter behind :class:`TransformingSpanExporter`,
and attaches the auth / ingestion-version headers Langfuse expects.

Mapping summary (canonical → Langfuse):

- ``gen_ai.input.messages`` + ``gen_ai.system_instructions`` → observation input
- ``gen_ai.output.messages`` → observation output (generations and reasoning)
- ``gen_ai.tool.call.arguments`` / ``gen_ai.tool.call.result`` → tool I/O
- ``agentteam.agent.input/output`` → agent observation I/O
- ``openjiuwen.span.input/output`` → observation I/O for other records
- ``gen_ai.conversation.id`` / ``openjiuwen.session.id`` → ``session.id``
- team name (root span) → ``langfuse.trace.name`` / ``langfuse.trace.tags``
- operation / record kind → ``langfuse.observation.type``

The projection never mutates the input span; it always returns a full copy.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter

from openjiuwen.extensions.observability.exporters.transforming import (
    TransformingSpanExporter,
    clone_readable_span,
)
from openjiuwen.extensions.observability.semconv import (
    AT_TEAM_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    OJ_SPAN_INPUT,
    OJ_SPAN_OUTPUT,
    OJ_TRAJECTORY_RECORD_KIND,
)

# ---------------------------------------------------------------------------
# Langfuse OTel ingestion attributes — private to this adapter
# ---------------------------------------------------------------------------
# Key names follow the Langfuse SDK's ``LangfuseOtelSpanAttributes``:
# "session.id" is NOT namespaced with "langfuse.".

LANGFUSE_TRACE_NAME = "langfuse.trace.name"
LANGFUSE_TRACE_TAGS = "langfuse.trace.tags"
LANGFUSE_SESSION_ID = "session.id"
LANGFUSE_OBSERVATION_INPUT = "langfuse.observation.input"
LANGFUSE_OBSERVATION_OUTPUT = "langfuse.observation.output"
LANGFUSE_OBSERVATION_TYPE = "langfuse.observation.type"

# Header selecting Langfuse v4 OTLP ingestion.
LANGFUSE_INGESTION_VERSION_HEADER = "x-langfuse-ingestion-version"

_GENERATION_OPERATIONS = frozenset({"chat", "generate_content", "text_completion"})


@dataclass(frozen=True)
class LangfuseExporterConfig:
    """Adapter knobs isolated from the shared observability config.

    Attributes:
        ingestion_version: Langfuse ingestion protocol major version. v4 is
            selected through the ``x-langfuse-ingestion-version: 4`` header.
        legacy_prompt_projection: Additionally emit the deprecated indexed
            ``gen_ai.prompt.{i}.*`` / ``gen_ai.completion.{i}.*`` attributes
            for old self-hosted Langfuse versions. Default off.
    """

    ingestion_version: Literal[3, 4] = 4
    legacy_prompt_projection: bool = False

    @classmethod
    def from_observability_config(cls, config: Any) -> "LangfuseExporterConfig":
        """Build the adapter config from the shared observability config."""
        return cls(
            ingestion_version=config.langfuse_ingestion_version,
            legacy_prompt_projection=config.langfuse_legacy_prompt_projection,
        )


def _json_list(value: Any) -> list[Any] | None:
    """Return the JSON list behind an attribute value, or None."""
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _text(value: Any) -> str:
    """Render one attribute value as Langfuse-compatible text."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _parts_text(message: Mapping[str, Any]) -> str:
    """Join the text content of one structured message's parts."""
    parts = message.get("parts")
    if not isinstance(parts, list):
        content = message.get("content")
        return _text(content) if content not in (None, "") else ""
    fragments: list[str] = []
    for part in parts:
        if isinstance(part, Mapping) and part.get("content") not in (None, ""):
            fragments.append(_text(part["content"]))
        elif not isinstance(part, Mapping):
            fragments.append(_text(part))
    return "".join(fragments)


def _observation_type(attributes: Mapping[str, Any]) -> str:
    """Map canonical operation/record kind onto a Langfuse observation type."""
    operation = str(attributes.get(GEN_AI_OPERATION_NAME) or "")
    kind = str(attributes.get(OJ_TRAJECTORY_RECORD_KIND) or "")
    if operation in _GENERATION_OPERATIONS:
        return "generation"
    if operation == "embeddings":
        return "embedding"
    if operation == "execute_tool" or kind == "tool":
        return "tool"
    if operation == "invoke_agent" or kind == "agent":
        return "agent"
    if operation == "retrieval" or kind == "retrieval":
        return "retriever"
    if operation == "workflow" or kind == "workflow":
        return "chain"
    return "span"


def _generation_input(attributes: Mapping[str, Any]) -> str | None:
    """Combine system instructions and input messages into the observation input."""
    system_parts = _json_list(attributes.get(GEN_AI_SYSTEM_INSTRUCTIONS))
    input_messages = _json_list(attributes.get(GEN_AI_INPUT_MESSAGES))
    combined: list[Any] = []
    if system_parts:
        combined.append({"role": "system", "parts": system_parts})
    if input_messages:
        combined.extend(input_messages)
    if not combined:
        return None
    return json.dumps(combined, ensure_ascii=False, default=str)


def _observation_input(
    attributes: Mapping[str, Any],
    observation_type: str,
    kind: str,
) -> str | None:
    """Resolve the Langfuse observation input from canonical attributes."""
    if observation_type in ("generation", "embedding"):
        return _generation_input(attributes)
    if observation_type == "tool":
        return _text(attributes[GEN_AI_TOOL_CALL_ARGUMENTS]) if attributes.get(
            GEN_AI_TOOL_CALL_ARGUMENTS
        ) not in (None, "") else None
    if observation_type == "agent":
        value = attributes.get(OJ_SPAN_INPUT)
        return _text(value) if value not in (None, "") else None
    if kind == "reasoning":
        # No input placeholder for reasoning spans; the adapter decides this,
        # not the collection layer.
        return None
    value = attributes.get(OJ_SPAN_INPUT)
    return _text(value) if value not in (None, "") else None


def _observation_output(
    attributes: Mapping[str, Any],
    observation_type: str,
    kind: str,
) -> str | None:
    """Resolve the Langfuse observation output from canonical attributes."""
    if observation_type in ("generation", "embedding") or kind == "reasoning":
        value = attributes.get(GEN_AI_OUTPUT_MESSAGES)
        return _text(value) if value not in (None, "") else None
    if observation_type == "tool":
        value = attributes.get(GEN_AI_TOOL_CALL_RESULT)
        return _text(value) if value not in (None, "") else None
    value = attributes.get(OJ_SPAN_OUTPUT)
    return _text(value) if value not in (None, "") else None


def _legacy_prompt_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Project deprecated indexed prompt/completion attributes (opt-in)."""
    derived: dict[str, Any] = {}
    input_messages = _json_list(attributes.get(GEN_AI_INPUT_MESSAGES)) or []
    for index, message in enumerate(input_messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role is not None:
            derived[f"gen_ai.prompt.{index}.role"] = str(role)
        content = _parts_text(message)
        if content:
            derived[f"gen_ai.prompt.{index}.content"] = content
    output_messages = _json_list(attributes.get(GEN_AI_OUTPUT_MESSAGES)) or []
    for index, message in enumerate(output_messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role is not None:
            derived[f"gen_ai.completion.{index}.role"] = str(role)
        content = _parts_text(message)
        if content:
            derived[f"gen_ai.completion.{index}.content"] = content
    return derived


def project_langfuse_attributes(
    span: ReadableSpan,
    config: LangfuseExporterConfig | None = None,
) -> dict[str, Any]:
    """Derive the Langfuse projection attributes for one canonical span.

    The returned mapping only contains ``langfuse.*``, ``session.id`` and —
    when legacy projection is enabled — deprecated ``gen_ai.prompt.*`` /
    ``gen_ai.completion.*`` keys.
    """
    config = config or LangfuseExporterConfig()
    attributes = span.attributes or {}
    derived: dict[str, Any] = {}

    session_id = attributes.get(GEN_AI_CONVERSATION_ID)
    if session_id not in (None, ""):
        derived[LANGFUSE_SESSION_ID] = str(session_id)

    observation_type = _observation_type(attributes)
    kind = str(attributes.get(OJ_TRAJECTORY_RECORD_KIND) or "")
    derived[LANGFUSE_OBSERVATION_TYPE] = observation_type

    input_value = _observation_input(attributes, observation_type, kind)
    if input_value is not None:
        derived[LANGFUSE_OBSERVATION_INPUT] = input_value
    output_value = _observation_output(attributes, observation_type, kind)
    if output_value is not None:
        derived[LANGFUSE_OBSERVATION_OUTPUT] = output_value

    # Trace-level attributes are attached to the trace root only.
    if span.parent is None:
        derived[LANGFUSE_TRACE_NAME] = span.name
        team_name = attributes.get(AT_TEAM_NAME)
        if team_name not in (None, ""):
            derived[LANGFUSE_TRACE_TAGS] = [str(team_name)]

    if config.legacy_prompt_projection:
        derived.update(_legacy_prompt_attributes(attributes))
    return derived


def project_langfuse_span(
    span: ReadableSpan,
    config: LangfuseExporterConfig | None = None,
) -> ReadableSpan:
    """Return a Langfuse-projected copy of ``span``; the input stays untouched."""
    attributes = dict(span.attributes or {})
    attributes.update(project_langfuse_attributes(span, config))
    return clone_readable_span(span, attributes)


def build_langfuse_auth_headers(
    public_key: str,
    secret_key: str,
    ingestion_version: Literal[3, 4] = 4,
) -> dict[str, str]:
    """Build the Basic-auth and ingestion-version headers for Langfuse."""
    headers: dict[str, str] = {}
    if public_key and secret_key:
        credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        headers["authorization"] = f"Basic {credentials}"
    if ingestion_version == 4:
        headers[LANGFUSE_INGESTION_VERSION_HEADER] = "4"
    return headers


def build_langfuse_span_exporter(config: Any) -> SpanExporter:
    """Build the OTLP HTTP exporter for Langfuse behind the projection adapter.

    Args:
        config: The shared ``ObservabilityConfig``. ``endpoint`` must point at
            the Langfuse OTLP HTTP route (``.../api/public/otel/v1/traces``).
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpExporter,
    )

    adapter_config = LangfuseExporterConfig.from_observability_config(config)
    inner = HttpExporter(
        endpoint=config.endpoint,
        headers=build_langfuse_auth_headers(
            config.langfuse_public_key,
            config.langfuse_secret_key,
            ingestion_version=adapter_config.ingestion_version,
        ),
    )
    return TransformingSpanExporter(
        inner,
        transform=lambda span: project_langfuse_span(span, adapter_config),
    )


__all__ = [
    "LANGFUSE_INGESTION_VERSION_HEADER",
    "LANGFUSE_OBSERVATION_INPUT",
    "LANGFUSE_OBSERVATION_OUTPUT",
    "LANGFUSE_OBSERVATION_TYPE",
    "LANGFUSE_SESSION_ID",
    "LANGFUSE_TRACE_NAME",
    "LANGFUSE_TRACE_TAGS",
    "LangfuseExporterConfig",
    "build_langfuse_auth_headers",
    "build_langfuse_span_exporter",
    "project_langfuse_attributes",
    "project_langfuse_span",
]
