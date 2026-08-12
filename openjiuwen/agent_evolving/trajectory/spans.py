# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stateless accessors and transformations for canonical OTLP trajectories.

The online trajectory path receives ordinary OTLP JSON dictionaries from the
observability processor.  This module deliberately does not own a window,
registry, or subscription.  Functions return detached dictionaries/lists and
transformations create a new :class:`Trajectory` value through
``Trajectory.from_otlp``.

Current attributes follow the observability conventions; migration-only
fallbacks remain explicitly owned by the trajectory package.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Iterator, TypeAlias

from openjiuwen.agent_evolving.trajectory import legacy_semconv
from openjiuwen.extensions.observability import semconv


JSONValue: TypeAlias = Any
Span: TypeAlias = dict[str, Any]
SpanIdentity: TypeAlias = tuple[str, str]


# ---------------------------------------------------------------------------
# Small OTLP codec helpers
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> JSONValue:
    """Return a detached JSON-compatible value without leaking input objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    # OTel SDK values occasionally expose model_dump()/to_dict().  Falling
    # back to a string is safer than retaining a live SDK object in a value.
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return _json_safe(dumped)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return _json_safe(dumped)
    return str(value)


def encode_otlp_value(value: Any) -> dict[str, Any]:
    """Encode a Python value in the OTLP JSON ``AnyValue`` representation."""

    value = _json_safe(value)
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_otlp_value(item) for item in value]}}
    if isinstance(value, Mapping):
        return {
            "kvlistValue": {
                "values": [{"key": str(key), "value": encode_otlp_value(item)} for key, item in value.items()]
            }
        }
    return {"stringValue": str(value)}


def decode_otlp_value(value: Any) -> Any:
    """Decode an OTLP ``AnyValue`` (or return a detached plain value).

    A few exporters hand us already-decoded values, so accepting plain scalar
    values here keeps the accessor tolerant without changing canonical output.
    """

    if not isinstance(value, Mapping):
        return _json_safe(value)
    if "stringValue" in value:
        return _json_safe(value["stringValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return _json_safe(value["intValue"])
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return _json_safe(value["doubleValue"])
    if "arrayValue" in value:
        array = value.get("arrayValue") or {}
        values = array.get("values") if isinstance(array, Mapping) else []
        return [decode_otlp_value(item) for item in values or []]
    if "kvlistValue" in value:
        kvlist = value.get("kvlistValue") or {}
        values = kvlist.get("values") if isinstance(kvlist, Mapping) else []
        return {
            str(item.get("key")): decode_otlp_value(item.get("value"))
            for item in values or []
            if isinstance(item, Mapping) and item.get("key") is not None
        }
    # Attribute mappings used by tests and some exporters are already plain
    # dictionaries; recursively detach them rather than returning the input.
    return {str(key): decode_otlp_value(item) for key, item in value.items()}


def attributes_to_map(attributes: Any) -> dict[str, Any]:
    """Decode OTLP attributes into a detached ``{name: value}`` mapping."""

    if attributes is None:
        return {}
    if isinstance(attributes, Mapping):
        # A canonical mapping may still use ``{"value": AnyValue}`` entries.
        result: dict[str, Any] = {}
        for key, value in attributes.items():
            if isinstance(value, Mapping) and set(value) == {"value"}:
                value = value.get("value")
            result[str(key)] = decode_otlp_value(value)
        return result
    result = {}
    if not isinstance(attributes, Iterable) or isinstance(attributes, (str, bytes)):
        return result
    for item in attributes:
        if not isinstance(item, Mapping) or item.get("key") is None:
            continue
        result[str(item["key"])] = decode_otlp_value(item.get("value"))
    return result


def attributes_from_map(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Encode and deterministically order a Python attribute mapping."""

    return [
        {"key": str(key), "value": encode_otlp_value(attributes[key])}
        for key in sorted(attributes)
        if attributes[key] is not None
    ]


def _normalize_attributes(attributes: Any) -> list[dict[str, Any]]:
    return attributes_from_map(attributes_to_map(attributes))


def normalize_span(span: Mapping[str, Any]) -> Span:
    """Return a detached, canonical-looking OTLP span.

    Unknown span fields are retained.  Native trace/span IDs are intentionally
    not re-hashed or replaced: they are routing and identity facts from the
    observability producer.
    """

    if not isinstance(span, Mapping):
        raise TypeError("span must be a mapping")
    normalized = _json_safe(span)
    if not isinstance(normalized, dict):  # pragma: no cover - _json_safe contract
        raise TypeError("span must be a mapping")
    if "attributes" in normalized:
        normalized["attributes"] = _normalize_attributes(normalized.get("attributes"))
    if "events" in normalized:
        events = normalized.get("events") or []
        normalized_events: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, Mapping):
                continue
            item = _json_safe(event)
            if "attributes" in item:
                item["attributes"] = _normalize_attributes(item.get("attributes"))
            normalized_events.append(item)
        normalized["events"] = normalized_events
    if "links" in normalized:
        links = normalized.get("links") or []
        normalized["links"] = [_json_safe(link) for link in links if isinstance(link, Mapping)]
    return normalized


def normalize_otlp(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an OTLP TraceData mapping without changing its semantics."""

    if not isinstance(payload, Mapping):
        raise TypeError("OTLP payload must be a mapping")
    result = _json_safe(payload)
    if not isinstance(result, dict):  # pragma: no cover - _json_safe contract
        raise TypeError("OTLP payload must be a mapping")
    resource_spans = result.get("resourceSpans") or []
    if not isinstance(resource_spans, list):
        resource_spans = []
    normalized_resources: list[dict[str, Any]] = []
    for resource_span in resource_spans:
        if not isinstance(resource_span, Mapping):
            continue
        item = _json_safe(resource_span)
        resource = item.get("resource")
        if not isinstance(resource, Mapping):
            resource = {}
        resource = dict(resource)
        if "attributes" in resource:
            resource["attributes"] = _normalize_attributes(resource.get("attributes"))
        item["resource"] = resource
        scopes = item.get("scopeSpans") or []
        normalized_scopes: list[dict[str, Any]] = []
        for scope in scopes:
            if not isinstance(scope, Mapping):
                continue
            scope_item = _json_safe(scope)
            scope_item["scope"] = dict(scope_item.get("scope") or {})
            spans = scope_item.get("spans") or []
            scope_item["spans"] = [normalize_span(span) for span in spans if isinstance(span, Mapping)]
            normalized_scopes.append(scope_item)
        item["scopeSpans"] = normalized_scopes
        normalized_resources.append(item)
    result["resourceSpans"] = normalized_resources
    return result


def _payload_for(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_otlp = getattr(value, "to_otlp", None)
    if callable(to_otlp):
        payload = to_otlp()
        if isinstance(payload, Mapping):
            return payload
    payload = getattr(value, "otlp_trace", None)
    if isinstance(payload, Mapping):
        return payload
    raise TypeError("expected a Trajectory or OTLP mapping")


def _trajectory_from_payload(payload: Mapping[str, Any]) -> Any:
    from openjiuwen.agent_evolving.trajectory.model import Trajectory

    normalized = normalize_otlp(payload)
    return Trajectory.from_otlp(normalized)


def _payload_spans(payload: Mapping[str, Any]) -> Iterator[tuple[int, int, int, Span]]:
    for resource_index, resource_span in enumerate(payload.get("resourceSpans") or []):
        if not isinstance(resource_span, Mapping):
            continue
        for scope_index, scope_span in enumerate(resource_span.get("scopeSpans") or []):
            if not isinstance(scope_span, Mapping):
                continue
            for span_index, span in enumerate(scope_span.get("spans") or []):
                if isinstance(span, Mapping):
                    yield resource_index, scope_index, span_index, normalize_span(span)


def iter_spans(value: Any) -> Iterator[Span]:
    """Yield detached spans from every OTLP resource/scope group."""

    payload = _payload_for(value)
    for _, _, _, span in _payload_spans(payload):
        yield span


def span_attributes(span: Mapping[str, Any]) -> dict[str, Any]:
    """Return decoded span attributes as an independent mapping."""

    if not isinstance(span, Mapping):
        return {}
    return attributes_to_map(span.get("attributes"))


def span_status(span: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached status mapping (or an empty mapping)."""

    status = span.get("status") if isinstance(span, Mapping) else None
    return dict(_json_safe(status)) if isinstance(status, Mapping) else {}


def span_events(span: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return detached span events with decoded attributes."""

    events = span.get("events") if isinstance(span, Mapping) else None
    result: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        item = _json_safe(event)
        if isinstance(item, dict) and "attributes" in item:
            item["attributes"] = _normalize_attributes(item.get("attributes"))
        if isinstance(item, dict):
            result.append(item)
    return result


def span_identity(span: Mapping[str, Any]) -> SpanIdentity | None:
    """Return the native ``(traceId, spanId)`` identity, if complete."""

    if not isinstance(span, Mapping):
        return None
    trace_id = span.get("traceId")
    span_id = span.get("spanId")
    if trace_id is None or span_id is None:
        return None
    trace_text = _id_text(trace_id)
    span_text = _id_text(span_id)
    if not trace_text or not span_text:
        return None
    return trace_text, span_text


def _id_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value).strip()


def _time_value(span: Mapping[str, Any], field: str) -> int:
    value = span.get(field)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def span_sort_key(span: Mapping[str, Any]) -> tuple[int, int, str, str]:
    """Stable chronological ordering used by merge/crop and Team forests."""

    identity = span_identity(span) or ("", "")
    return (
        _time_value(span, "startTimeUnixNano"),
        _time_value(span, "endTimeUnixNano"),
        identity[0],
        identity[1],
    )


def _scope_bucket_key(scope_span: Mapping[str, Any]) -> tuple[Any, ...]:
    scope = scope_span.get("scope") or {}
    return tuple(sorted((str(k), repr(v)) for k, v in scope.items()))


def _sort_payload_spans(payload: dict[str, Any]) -> None:
    for resource_span in payload.get("resourceSpans") or []:
        for scope_span in resource_span.get("scopeSpans") or []:
            spans = scope_span.get("spans") or []
            scope_span["spans"] = sorted(spans, key=span_sort_key)


def merge_payloads(*values: Any) -> dict[str, Any]:
    """Merge OTLP payloads into one logical resource, de-duplicating identities.

    The first payload owns resource metadata (including trajectory/session
    identity).  Native span trace/span IDs from all inputs are retained; later
    resource-level identity attributes are not copied into a second resource
    record, which would otherwise make a merged window appear to have multiple
    trajectories.
    """

    payloads = [normalize_otlp(_payload_for(value)) for value in values if value is not None]
    if not payloads:
        return {"resourceSpans": []}
    first = deepcopy(payloads[0])
    first_resources = first.get("resourceSpans") or []
    first_resource = first_resources[0] if first_resources else {}
    logical_resource: dict[str, Any] = {
        "resource": deepcopy(first_resource.get("resource") or {}) if isinstance(first_resource, Mapping) else {},
        "scopeSpans": [],
    }
    result: dict[str, Any] = {"resourceSpans": [logical_resource]}
    seen: set[SpanIdentity] = set()
    for payload in payloads:
        for incoming_resource in payload.get("resourceSpans") or []:
            if not isinstance(incoming_resource, Mapping):
                continue
            for incoming_scope in incoming_resource.get("scopeSpans") or []:
                if not isinstance(incoming_scope, Mapping):
                    continue
                matching_scope = None
                scope_key = _scope_bucket_key(incoming_scope)
                for existing_scope in logical_resource["scopeSpans"]:
                    if _scope_bucket_key(existing_scope) == scope_key:
                        matching_scope = existing_scope
                        break
                if matching_scope is None:
                    matching_scope = {
                        "scope": deepcopy(incoming_scope.get("scope") or {}),
                        "spans": [],
                    }
                    logical_resource["scopeSpans"].append(matching_scope)
                existing_spans = matching_scope.setdefault("spans", [])
                for incoming_span in incoming_scope.get("spans") or []:
                    normalized = normalize_span(incoming_span)
                    identity = span_identity(normalized)
                    if identity is not None and identity in seen:
                        continue
                    existing_spans.append(normalized)
                    if identity is not None:
                        seen.add(identity)
    _sort_payload_spans(result)
    return normalize_otlp(result)


def merge_trajectories(*values: Any) -> Any:
    """Return a new canonical trajectory containing unique spans from inputs."""

    return _trajectory_from_payload(merge_payloads(*values))


def merge_spans(base: Any, additions: Iterable[Mapping[str, Any]]) -> Any:
    """Merge detached span dictionaries into a trajectory value."""

    payload = normalize_otlp(_payload_for(base))
    extra = {"resourceSpans": [{"resource": {}, "scopeSpans": [{"spans": list(additions)}]}]}
    return _trajectory_from_payload(merge_payloads(payload, extra))


def trim_spans(
    spans: Iterable[Mapping[str, Any]],
    max_spans: int | None = None,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[Span]:
    """Return a detached, chronologically trimmed span list.

    ``max_spans`` keeps the newest spans after time filtering.  A non-positive
    limit yields an empty list, which is useful for a bounded clean window.
    """

    selected = [normalize_span(span) for span in spans if isinstance(span, Mapping)]
    if start_time is not None:
        selected = [span for span in selected if _time_value(span, "endTimeUnixNano") >= start_time]
    if end_time is not None:
        selected = [span for span in selected if _time_value(span, "startTimeUnixNano") <= end_time]
    selected.sort(key=span_sort_key)
    if max_spans is not None:
        if max_spans <= 0:
            return []
        selected = selected[-max_spans:]
    return deepcopy(selected)


def trim_trajectory(
    value: Any,
    max_spans: int | None = None,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
) -> Any:
    """Return a new trajectory retaining only the selected span window."""

    payload = normalize_otlp(_payload_for(value))
    if max_spans is None and start_time is None and end_time is None:
        return _trajectory_from_payload(payload)
    selected = trim_spans(
        list(iter_spans(payload)),
        max_spans,
        start_time=start_time,
        end_time=end_time,
    )
    # Keep the first resource/scope metadata while replacing spans with the
    # selected forest.  Empty spans are valid for a snapshot; resourceSpans is
    # retained so Trajectory validation still sees a canonical envelope.
    result = deepcopy(payload)
    locations: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for resource_span in result.get("resourceSpans") or []:
        for scope_span in resource_span.get("scopeSpans") or []:
            scope_span["spans"] = []
            locations.append((resource_span, scope_span))
    if not locations:
        result.setdefault("resourceSpans", []).append({"resource": {}, "scopeSpans": [{"scope": {}, "spans": []}]})
        locations.append((result["resourceSpans"][0], result["resourceSpans"][0]["scopeSpans"][0]))
    # Preserve each selected span's original resource/scope when possible.
    for span in selected:
        # The detached span does not carry its source location; using the first
        # scope is deterministic and preserves the canonical envelope.
        locations[0][1].setdefault("spans", []).append(span)
    _sort_payload_spans(result)
    return _trajectory_from_payload(result)


def crop_trajectory(value: Any, *args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`trim_trajectory` used by window callers."""

    return trim_trajectory(value, *args, **kwargs)


# ---------------------------------------------------------------------------
# Semantic accessors
# ---------------------------------------------------------------------------


def decode_json_attribute(value: Any) -> Any:
    """Decode a JSON-encoded attribute while preserving non-JSON values."""

    if not isinstance(value, str):
        return deepcopy(value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _decode_structured_attribute(value: Any) -> Any:
    """Decode object/array attributes without coercing scalar strings."""

    if not isinstance(value, str):
        return deepcopy(value)
    stripped = value.strip()
    if not stripped or stripped[0] not in '[{"':
        return value
    return decode_json_attribute(value)


_INDEXED_ATTRIBUTE_RE = re.compile(r"^(?P<base>.+)\.(?P<index>\d+)\.(?P<field>[^.]+)$")


def _indexed_messages(attributes: Mapping[str, Any], base: str) -> list[dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    prefix = f"{base}."
    for key, value in attributes.items():
        if not key.startswith(prefix):
            continue
        match = _INDEXED_ATTRIBUTE_RE.match(key)
        if not match or match.group("base") != base:
            continue
        index = int(match.group("index"))
        indexed.setdefault(index, {})[match.group("field")] = deepcopy(value)
    return [indexed[index] for index in sorted(indexed)]


def _message_list(value: Any) -> list[dict[str, Any]]:
    decoded = _decode_structured_attribute(value)
    if isinstance(decoded, Mapping):
        decoded = [decoded]
    if not isinstance(decoded, list):
        return []
    messages: list[dict[str, Any]] = []
    for message in decoded:
        if isinstance(message, Mapping):
            messages.append(deepcopy(dict(message)))
        elif message is not None:
            messages.append({"role": "unknown", "content": str(message)})
    return messages


def read_llm_exchange(span: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read detached prompt and completion messages from an ``llm.call`` span."""

    attrs = span_attributes(span)
    prompts = _indexed_messages(attrs, semconv.GEN_AI_PROMPT)
    completions = _indexed_messages(attrs, semconv.GEN_AI_COMPLETION)
    if not prompts:
        prompts = _message_list(attrs.get(legacy_semconv.LEGACY_GEN_AI_INPUT_MESSAGES))
    if not completions:
        completions = _message_list(attrs.get(legacy_semconv.LEGACY_GEN_AI_OUTPUT_MESSAGES))
    tool_calls = _decode_structured_attribute(attrs.get(semconv.GEN_AI_TOOL_CALLS))
    if tool_calls not in (None, ""):
        if completions:
            completions[0].setdefault("tool_calls", tool_calls)
        else:
            completions.append({"role": "assistant", "tool_calls": tool_calls})
    return deepcopy(prompts), deepcopy(completions)


def read_llm_messages(span: Mapping[str, Any], *, include_empty: bool = False) -> list[dict[str, Any]]:
    """Read indexed prompt/completion messages from an ``llm.call`` span."""

    prompts, completions = read_llm_exchange(span)
    messages = prompts + completions
    if include_empty:
        return messages
    return [message for message in messages if message.get("role") is not None or message.get("content") is not None]


def read_tool_call(span: Mapping[str, Any]) -> dict[str, Any]:
    """Read canonical tool identity/input/output and status from a tool span."""

    attrs = span_attributes(span)
    result: dict[str, Any] = {}
    name = attrs.get(semconv.GEN_AI_TOOL_NAME)
    tool_id = attrs.get(semconv.GEN_AI_TOOL_ID) or attrs.get(legacy_semconv.LEGACY_GEN_AI_TOOL_CALL_ID)
    if name is not None:
        result["name"] = deepcopy(name)
    if tool_id is not None:
        result["id"] = deepcopy(tool_id)
    if semconv.GEN_AI_TOOL_INPUT in attrs:
        result["input"] = _decode_structured_attribute(attrs[semconv.GEN_AI_TOOL_INPUT])
    elif legacy_semconv.LEGACY_GEN_AI_TOOL_CALL_ARGUMENTS in attrs:
        result["input"] = _decode_structured_attribute(attrs[legacy_semconv.LEGACY_GEN_AI_TOOL_CALL_ARGUMENTS])
    if semconv.GEN_AI_TOOL_OUTPUT in attrs:
        result["output"] = _decode_structured_attribute(attrs[semconv.GEN_AI_TOOL_OUTPUT])
    elif legacy_semconv.LEGACY_GEN_AI_TOOL_CALL_RESULT in attrs:
        result["output"] = _decode_structured_attribute(attrs[legacy_semconv.LEGACY_GEN_AI_TOOL_CALL_RESULT])
    error = read_span_error(span)
    if error is not None:
        result["error"] = error
    return deepcopy(result)


def read_usage(span: Mapping[str, Any]) -> dict[str, int]:
    """Return token usage using observability's canonical names."""

    attrs = span_attributes(span)
    mapping = (
        (
            "prompt_tokens",
            (semconv.GEN_AI_USAGE_PROMPT_TOKENS, legacy_semconv.LEGACY_GEN_AI_USAGE_INPUT_TOKENS),
        ),
        (
            "completion_tokens",
            (semconv.GEN_AI_USAGE_COMPLETION_TOKENS, legacy_semconv.LEGACY_GEN_AI_USAGE_OUTPUT_TOKENS),
        ),
        ("total_tokens", (semconv.GEN_AI_USAGE_TOTAL_TOKENS,)),
    )
    result: dict[str, int] = {}
    for output_key, input_keys in mapping:
        value = next((attrs[key] for key in input_keys if key in attrs), None)
        if value is None:
            continue
        try:
            result[output_key] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _first_suffix_attribute(attrs: Mapping[str, Any], suffixes: Sequence[str]) -> Any:
    for key in suffixes:
        if key in attrs:
            return deepcopy(attrs[key])
    return None


def _coerce_int_list(value: Any) -> list[int] | None:
    value = _decode_structured_attribute(value)
    if not isinstance(value, list):
        return None
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result or None


def _coerce_float_list(value: Any) -> list[float] | None:
    value = _decode_structured_attribute(value)
    if isinstance(value, Mapping):
        value = value.get("content")
    if not isinstance(value, list):
        return None
    result: list[float] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("logprob")
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result or None


def read_rl_fields(span: Mapping[str, Any]) -> dict[str, Any]:
    """Read optional RL fields without copying model response data."""

    attrs = span_attributes(span)
    fields: dict[str, Any] = {}
    for output_key, suffixes in (
        (
            "prompt_token_ids",
            ("evolution.rl.prompt_token_ids", "openjiuwen.rl.prompt_token_ids"),
        ),
        (
            "completion_token_ids",
            ("evolution.rl.completion_token_ids", "openjiuwen.rl.completion_token_ids"),
        ),
        ("logprobs", ("evolution.rl.logprobs", "openjiuwen.rl.logprobs")),
        ("reward", ("evolution.rl.reward", "openjiuwen.rl.reward")),
    ):
        value = _first_suffix_attribute(attrs, suffixes)
        if value is None:
            continue
        if output_key in {"prompt_token_ids", "completion_token_ids"}:
            normalized = _coerce_int_list(value)
            if normalized is not None:
                fields[output_key] = normalized
        elif output_key == "logprobs":
            normalized = _coerce_float_list(value)
            if normalized is not None:
                fields[output_key] = normalized
        else:
            fields[output_key] = decode_json_attribute(value)
    return fields


def read_span_error(span: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read canonical status/exception failure information."""

    status = span_status(span)
    code = str(status.get("code") or status.get("status_code") or "").upper()
    message = status.get("message")
    exception_event = next(
        (event for event in span_events(span) if str(event.get("name") or "").lower() == "exception"),
        None,
    )
    if code not in {"ERROR", "STATUS_CODE_ERROR"} and not message and exception_event is None:
        return None
    error: dict[str, Any] = {"status": code or "ERROR"}
    if message:
        error["message"] = deepcopy(message)
    for event in span_events(span):
        name = str(event.get("name") or "").lower()
        if name != "exception":
            continue
        event_attrs = attributes_to_map(event.get("attributes"))
        error["exception"] = {key: deepcopy(value) for key, value in event_attrs.items()}
        break
    return error


__all__ = [
    "Span",
    "SpanIdentity",
    "attributes_from_map",
    "attributes_to_map",
    "crop_trajectory",
    "decode_json_attribute",
    "decode_otlp_value",
    "encode_otlp_value",
    "iter_spans",
    "merge_payloads",
    "merge_spans",
    "merge_trajectories",
    "normalize_otlp",
    "normalize_span",
    "read_llm_exchange",
    "read_llm_messages",
    "read_rl_fields",
    "read_span_error",
    "read_tool_call",
    "read_usage",
    "span_attributes",
    "span_events",
    "span_identity",
    "span_sort_key",
    "span_status",
    "trim_spans",
    "trim_trajectory",
]
