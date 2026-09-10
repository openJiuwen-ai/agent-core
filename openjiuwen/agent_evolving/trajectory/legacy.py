# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Read-only conversion of historical trajectory mappings.

Legacy step/detail dictionaries are parsed only in this module. Callers always
receive the canonical ``Trajectory`` value object; no reverse conversion or
legacy runtime type remains.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from openjiuwen.extensions.observability import semconv
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    MEMBER_ID,
    CASE_ID,
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
    RL_REWARD,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    attributes_to_map,
    write_llm_exchange,
)


_LEGACY_TRAJECTORY_ID = "openjiuwen.trajectory.id"
_LEGACY_SESSION_ID = "openjiuwen.session.id"
_LEGACY_TEAM_ID = "openjiuwen.team.id"
_LEGACY_MEMBER_ID = "openjiuwen.member.id"

_RESOURCE_ALIASES = {
    _LEGACY_TRAJECTORY_ID: TRAJECTORY_ID,
    "openjiuwen.session_id": SESSION_ID,
    _LEGACY_SESSION_ID: SESSION_ID,
    _LEGACY_TEAM_ID: TEAM_ID,
    _LEGACY_MEMBER_ID: MEMBER_ID,
    "session.id": SESSION_ID,
    "session_id": SESSION_ID,
    "team_id": TEAM_ID,
    "member_id": MEMBER_ID,
    "source": TRAJECTORY_SOURCE,
}


def _normalise_otlp_aliases(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical OTLP copy with historical resource aliases removed."""

    normalized = deepcopy(dict(payload))
    resource_spans = normalized.get("resourceSpans")
    if not isinstance(resource_spans, list):
        return normalized

    for resource_span in resource_spans:
        if not isinstance(resource_span, dict):
            continue
        resource = resource_span.get("resource")
        if not isinstance(resource, dict):
            continue
        attributes = resource.get("attributes")
        if isinstance(attributes, Mapping):
            attributes = [{"key": str(key), "value": deepcopy(value)} for key, value in attributes.items()]
            resource["attributes"] = attributes
        if not isinstance(attributes, list):
            continue

        by_key = {
            item.get("key"): item for item in attributes if isinstance(item, dict) and item.get("key") is not None
        }
        for old_key, new_key in _RESOURCE_ALIASES.items():
            old_item = by_key.get(old_key)
            if old_item is None:
                continue
            if new_key in by_key:
                attributes.remove(old_item)
                by_key.pop(old_key, None)
                continue
            old_item["key"] = new_key
            by_key[new_key] = old_item
            by_key.pop(old_key, None)

    return normalized


def _has_legacy_resource_alias(record: Mapping[str, Any]) -> bool:
    resource_spans = record.get("resourceSpans")
    if not isinstance(resource_spans, list):
        return False
    aliases = set(_RESOURCE_ALIASES)
    for resource_span in resource_spans:
        if not isinstance(resource_span, Mapping):
            continue
        resource = resource_span.get("resource")
        if not isinstance(resource, Mapping):
            continue
        attributes = resource.get("attributes")
        if isinstance(attributes, Mapping):
            if aliases.intersection(str(key) for key in attributes):
                return True
        elif isinstance(attributes, list):
            if any(isinstance(item, Mapping) and item.get("key") in aliases for item in attributes):
                return True
    return False


def _trace_id(value: Any) -> str:
    text = str(value)
    if len(text) == 32 and all(char in "0123456789abcdefABCDEF" for char in text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _span_id(value: Any, index: int) -> str:
    text = str(value or f"{index + 1:016x}")
    if len(text) == 16 and all(char in "0123456789abcdefABCDEF" for char in text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _as_message_list(value: Any) -> list[dict[str, Any]]:
    """Normalize a legacy messages field onto a list of flat messages."""

    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [
        dict(item) if isinstance(item, Mapping) else {"content": str(item)}
        for item in values
    ]


def _llm_exchange_attributes(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one legacy LLM step into the standard GenAI attributes.

    The tool calls of the reply keep their own top-level attribute, which is
    where every reader looks for them, in addition to riding along inside the
    structured output message.
    """

    prompts = _as_message_list(detail.get("messages"))
    completions = _as_message_list(detail.get("response"))
    attributes = write_llm_exchange(prompts, completions)
    for message in completions:
        if message.get("tool_calls") is not None:
            attributes[semconv.GEN_AI_TOOL_CALLS] = deepcopy(message["tool_calls"])
            break
    return attributes


def _legacy_step_span(value: Any, index: int, execution_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("legacy step must be a mapping")
    step = dict(value)
    kind = str(step.get("kind") or "")
    if kind not in {"llm", "tool"}:
        raise ValueError("legacy step kind must be llm or tool")
    detail = dict(step.get("detail") or {}) if isinstance(step.get("detail"), Mapping) else {}
    meta = dict(step.get("meta") or {}) if isinstance(step.get("meta"), Mapping) else {}
    attributes: dict[str, Any] = {}
    if step.get("reward") is not None:
        attributes[RL_REWARD] = deepcopy(step["reward"])
    for source_key, target_key in (
        ("prompt_token_ids", RL_PROMPT_TOKEN_IDS),
        ("completion_token_ids", RL_COMPLETION_TOKEN_IDS),
        ("logprobs", RL_LOGPROBS),
    ):
        if step.get(source_key) is not None:
            attributes[target_key] = deepcopy(step[source_key])

    detail_meta = detail.get("meta") if isinstance(detail.get("meta"), Mapping) else {}
    for key in ("provider_response_json", "response_mask", "routed_experts", "render_fingerprint"):
        value = meta.get(key, detail_meta.get(key))
        if value is not None:
            attributes[key] = deepcopy(value)

    if kind == "llm":
        name = str(meta.get("span_name") or "llm.call")
        attributes[semconv.GEN_AI_OPERATION_NAME] = "chat"
        if detail.get("model") is not None:
            attributes[semconv.GEN_AI_REQUEST_MODEL] = deepcopy(detail["model"])
        attributes.update(_llm_exchange_attributes(detail))
        if detail.get("tools") is not None:
            attributes[semconv.GEN_AI_TOOL_DEFINITIONS] = deepcopy(detail["tools"])
        usage = detail.get("usage") if isinstance(detail.get("usage"), Mapping) else {}
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if prompt_tokens is not None:
            attributes[semconv.GEN_AI_USAGE_PROMPT_TOKENS] = prompt_tokens
        if completion_tokens is not None:
            attributes[semconv.GEN_AI_USAGE_COMPLETION_TOKENS] = completion_tokens
    else:
        tool_name = str(detail.get("tool_name") or "")
        name = str(meta.get("span_name") or f"tool.{tool_name or index + 1}")
        attributes[semconv.GEN_AI_OPERATION_NAME] = "execute_tool"
        attributes[semconv.GEN_AI_TOOL_NAME] = tool_name
        if detail.get("tool_call_id") is not None:
            attributes[semconv.GEN_AI_TOOL_ID] = deepcopy(detail["tool_call_id"])
        if detail.get("call_args") is not None:
            attributes[semconv.GEN_AI_TOOL_INPUT] = deepcopy(detail["call_args"])
        if detail.get("call_result") is not None:
            attributes[semconv.GEN_AI_TOOL_OUTPUT] = deepcopy(detail["call_result"])

    span: dict[str, Any] = {
        "traceId": _trace_id(execution_id),
        "spanId": _span_id(meta.get("span_id"), index),
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "attributes": attributes_from_map(attributes),
        "status": {
            "code": "STATUS_CODE_ERROR" if step.get("error") else "STATUS_CODE_OK",
            **({"message": str(step["error"])} if step.get("error") else {}),
        },
    }
    if meta.get("parent_span_id"):
        span["parentSpanId"] = _span_id(meta["parent_span_id"], index)
    if step.get("start_time_ms") is not None:
        span["startTimeUnixNano"] = str(int(step["start_time_ms"]) * 1_000_000)
    if step.get("end_time_ms") is not None:
        span["endTimeUnixNano"] = str(int(step["end_time_ms"]) * 1_000_000)
    return span


def _legacy_to_otlp(record: Mapping[str, Any]) -> dict[str, Any]:
    execution_id = record.get("execution_id") or record.get("trajectory_id")
    if execution_id is None:
        raise ValueError("legacy trajectory is missing execution_id")
    steps_value = record.get("steps", [])
    if not isinstance(steps_value, list):
        raise ValueError("legacy trajectory steps must be a list")
    meta = deepcopy(record.get("meta") or {})
    if not isinstance(meta, dict):
        raise ValueError("legacy trajectory meta must be a mapping")
    source = record.get("source") or meta.pop("source", None) or "offline"
    nested_otlp = record.get("otlp_trace")
    payload = deepcopy(dict(nested_otlp)) if isinstance(nested_otlp, Mapping) else {"resourceSpans": []}
    resource_spans = payload.setdefault("resourceSpans", [])
    if not resource_spans:
        resource_spans.append({"resource": {}, "scopeSpans": []})
    resource_span = resource_spans[0]
    resource = resource_span.setdefault("resource", {})
    attributes = attributes_to_map(resource.get("attributes"))
    attributes.update(meta)
    attributes.update(
        {
            TRAJECTORY_ID: str(execution_id),
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            TRAJECTORY_SOURCE: str(source),
        }
    )
    if record.get("session_id") is not None:
        attributes[SESSION_ID] = deepcopy(record["session_id"])
    if record.get("case_id") is not None:
        attributes[CASE_ID] = deepcopy(record["case_id"])
    if record.get("cost") is not None:
        attributes["cost"] = deepcopy(record["cost"])
    resource["attributes"] = attributes_from_map(attributes)
    scope_spans = resource_span.setdefault("scopeSpans", [])
    if not scope_spans:
        scope_spans.append(
            {
                "scope": {
                    "name": "openjiuwen.agent_evolving.trajectory",
                    "version": TRAJECTORY_SCHEMA_VERSION,
                },
                "spans": [],
            }
        )
    if not any(scope.get("spans") for scope in scope_spans if isinstance(scope, Mapping)):
        scope_spans[0]["spans"] = [
            _legacy_step_span(step, index, str(execution_id)) for index, step in enumerate(steps_value)
        ]
    return payload


def is_legacy_record(record: Any) -> bool:
    """Return whether ``record`` requires the historical read path."""

    return isinstance(record, Mapping) and (
        _has_legacy_resource_alias(record)
        or (
            (isinstance(record.get("steps"), list) or "execution_id" in record or "otlp_trace" in record)
            and not isinstance(record.get("resourceSpans"), list)
        )
    )


def upgrade_legacy_record(record: Mapping[str, Any]) -> Trajectory:
    """Upgrade one old step/alias record to canonical ``Trajectory``.

    The input is never mutated.  Canonical OTLP records are accepted as a
    no-op read boundary so Store loading can use this function uniformly.
    """

    if not isinstance(record, Mapping):
        raise TypeError("trajectory record must be a mapping")
    elif isinstance(record.get("resourceSpans"), list):
        payload = _normalise_otlp_aliases(record)
    else:
        payload = _normalise_otlp_aliases(_legacy_to_otlp(record))
    if not payload.get("resourceSpans"):
        raise ValueError("legacy trajectory did not produce resourceSpans")

    # ``trajectory_from_legacy`` emits the schema version, but old OTLP
    # records may omit it.  Reading must not invent a second schema version;
    # adding the current value only fills the canonical envelope field.
    resource_span = payload["resourceSpans"][0]
    if isinstance(resource_span, dict):
        resource = resource_span.setdefault("resource", {})
        if isinstance(resource, dict):
            attributes = resource.setdefault("attributes", [])
            if isinstance(attributes, list):
                keys = {item.get("key") for item in attributes if isinstance(item, Mapping)}
                if TRAJECTORY_SCHEMA_VERSION_ATTR not in keys:
                    attributes.append(
                        {
                            "key": TRAJECTORY_SCHEMA_VERSION_ATTR,
                            "value": {"stringValue": TRAJECTORY_SCHEMA_VERSION},
                        }
                    )
    return Trajectory.from_historical_otlp(payload)


# Descriptive aliases keep the read boundary discoverable without exposing a
# reverse conversion or a legacy object type.
from_legacy = upgrade_legacy_record
upgrade_record = upgrade_legacy_record


__all__ = [
    "from_legacy",
    "is_legacy_record",
    "upgrade_legacy_record",
    "upgrade_record",
]
