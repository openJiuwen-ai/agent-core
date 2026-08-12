# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stateless Team span selectors and parent/child forest projections.

Team trajectories remain ordinary canonical ``Trajectory`` values.  The
helpers below only project detached dictionaries for a single read; no member
registry, accumulator, or ``TeamTrajectory`` model is introduced.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Iterator

from openjiuwen.agent_evolving.trajectory import legacy_semconv
from openjiuwen.extensions.observability import semconv

from openjiuwen.agent_evolving.trajectory.spans import (
    Span,
    iter_spans,
    span_attributes,
    span_identity,
    span_sort_key,
)


TEAM_CATEGORIES = frozenset(
    {
        "llm",
        "tool",
        "team",
        "agent",
        "task",
        "message",
        "member",
        "plan",
        "event",
    }
)
CONTEXT_CATEGORIES = frozenset({"team", "agent", "task", "message", "member", "plan", "event"})


def span_category(span: Mapping[str, Any]) -> str | None:
    """Classify a canonical span by its stable name/semantic attributes."""

    name = str(span.get("name") or "").strip().lower()
    if name == "llm.reasoning" or name.startswith("llm.reasoning."):
        return None
    if name == "llm.call" or name.startswith("llm."):
        return "llm"
    if name.startswith("tool.") or name.startswith("execute_tool"):
        return "tool"
    attrs = span_attributes(span)
    operation = str(attrs.get(semconv.GEN_AI_OPERATION_NAME) or "").lower()
    explicit_kind = str(attrs.get(legacy_semconv.LEGACY_TRAJECTORY_STEP_KIND) or "").lower()
    if operation in {"chat", "text_completion", "generate_content"} or explicit_kind == "llm":
        return "llm"
    if operation == "execute_tool" or explicit_kind == "tool":
        return "tool"
    for category, prefixes in (
        ("team", ("team.",)),
        ("agent", ("agent.",)),
        ("task", ("task.",)),
        ("message", ("msg.", "message.")),
        ("member", ("member.",)),
        ("plan", ("plan.",)),
        ("event", ("event.",)),
    ):
        if any(name.startswith(prefix) for prefix in prefixes):
            return category
    if attrs.get(semconv.AT_EVENT_TYPE) is not None:
        return "event"
    if attrs.get(semconv.AT_TASK_ID) is not None:
        return "task"
    if attrs.get(semconv.AT_MEMBER_ID) is not None:
        return "member"
    return None


def is_team_span(span: Mapping[str, Any]) -> bool:
    """Return whether a span is declared in the Team trajectory vocabulary."""

    return span_category(span) in TEAM_CATEGORIES


def _normalise_categories(categories: Iterable[str] | None) -> frozenset[str]:
    if categories is None:
        return frozenset(TEAM_CATEGORIES)
    values = {str(category).lower() for category in categories}
    # ``team`` is the processor's shorthand for team/plan/event context spans.
    if "team" in values:
        values.update({"team", "plan", "event"})
    return frozenset(values)


def select_team_spans(
    value: Any,
    *,
    categories: Iterable[str] | None = None,
    team_id: str | None = None,
    trace_id: str | None = None,
) -> list[Span]:
    """Select Team vocabulary spans as detached dictionaries.

    ``categories=None`` selects LLM/tool plus all collaboration context spans;
    ``llm.reasoning`` is always excluded by :func:`span_category`.
    """

    wanted = _normalise_categories(categories)
    all_spans = list(iter_spans(value))
    # Team/member context is intentionally not duplicated onto every LLM/tool
    # span.  A team selector therefore routes by a matching context span's
    # native trace ID, then applies the declared category filter to that tree.
    if trace_id is not None:
        routed_trace_ids = {str(trace_id)}
    elif team_id is not None:
        routed_trace_ids = {
            str(span.get("traceId") or "")
            for span in all_spans
            if str(span_attributes(span).get(semconv.AT_TEAM_ID) or "") == str(team_id) and span.get("traceId")
        }
    else:
        routed_trace_ids = None
    if team_id is not None and not routed_trace_ids:
        return []
    result: list[Span] = []
    for span in all_spans:
        category = span_category(span)
        if category not in wanted:
            continue
        if routed_trace_ids is not None and str(span.get("traceId") or "") not in routed_trace_ids:
            continue
        result.append(span)
    result.sort(key=span_sort_key)
    return result


def iter_team_spans(value: Any, **kwargs: Any) -> Iterator[Span]:
    """Iterator form of :func:`select_team_spans`."""

    yield from select_team_spans(value, **kwargs)


def _node_key(span: Mapping[str, Any], index: int) -> tuple[str, str] | tuple[str, int]:
    identity = span_identity(span)
    return identity if identity is not None else ("__anonymous__", index)


def _parent_identity(span: Mapping[str, Any]) -> tuple[str, str] | None:
    trace_id = span.get("traceId")
    parent_id = span.get("parentSpanId")
    if trace_id is None or parent_id is None:
        return None
    trace_text = str(trace_id).strip()
    parent_text = str(parent_id).strip()
    if not trace_text or not parent_text:
        return None
    return trace_text, parent_text


def build_team_forest(
    value: Any,
    *,
    categories: Iterable[str] | None = None,
    team_id: str | None = None,
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build a detached forest of Team spans using native parent identities.

    Each node is a plain ``{"span": dict, "children": list}``.  Missing
    parents are roots; no synthetic Team/root span is created.
    """

    spans = select_team_spans(
        value,
        categories=categories,
        team_id=team_id,
        trace_id=trace_id,
    )
    nodes: dict[tuple[Any, ...], dict[str, Any]] = {}
    ordered_keys: list[tuple[Any, ...]] = []
    for index, span in enumerate(spans):
        key = _node_key(span, index)
        # Selectors should already be detached.  Ignore an accidental duplicate
        # identity while preserving anonymous spans as independent nodes.
        if key in nodes and span_identity(span) is not None:
            continue
        nodes[key] = {"span": span, "children": []}
        ordered_keys.append(key)

    roots: list[dict[str, Any]] = []
    for key in ordered_keys:
        node = nodes[key]
        parent_key = _parent_identity(node["span"])
        parent = nodes.get(parent_key) if parent_key is not None else None
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent["children"].append(node)

    def sort_nodes(items: list[dict[str, Any]]) -> None:
        items.sort(key=lambda item: span_sort_key(item["span"]))
        for item in items:
            sort_nodes(item["children"])

    sort_nodes(roots)
    return roots


def team_forest(value: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Alias for :func:`build_team_forest`."""

    return build_team_forest(value, **kwargs)


def flatten_forest(forest: Iterable[Mapping[str, Any]]) -> list[Span]:
    """Flatten a forest into chronological detached spans."""

    result: list[Span] = []

    def visit(node: Mapping[str, Any]) -> None:
        span = node.get("span")
        if isinstance(span, Mapping):
            result.append(dict(span))
        for child in node.get("children") or []:
            if isinstance(child, Mapping):
                visit(child)

    for root in forest:
        if isinstance(root, Mapping):
            visit(root)
    result.sort(key=span_sort_key)
    return result


def _member_value(span: Mapping[str, Any]) -> str | None:
    attrs = span_attributes(span)
    for key in (semconv.AT_MEMBER_ID, semconv.AT_AGENT_ID, semconv.AT_MEMBER_NAME):
        value = attrs.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def _index_spans(spans: Sequence[Span]) -> dict[tuple[str, str], Span]:
    by_identity: dict[tuple[str, str], Span] = {}
    for span in spans:
        identity = span_identity(span)
        if identity is not None:
            by_identity[identity] = span
    return by_identity


def _descendant_keys(spans: Sequence[Span], target_keys: set[tuple[str, str]]) -> set[tuple[str, str]]:
    by_identity = _index_spans(spans)
    children: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for identity, span in by_identity.items():
        parent = _parent_identity(span)
        if parent is not None:
            children[parent].append(identity)
    result = set(target_keys)
    pending = list(target_keys)
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def select_member_spans(
    value: Any,
    member_id: str,
    *,
    team_id: str | None = None,
    categories: Iterable[str] | None = None,
    include_descendants: bool = True,
    include_ancestors: bool = False,
) -> list[Span]:
    """Select one member's spans and, by default, its child LLM/tool spans."""

    spans = select_team_spans(value, categories=categories, team_id=team_id)
    target = str(member_id)
    target_keys: set[tuple[str, str]] = set()
    for span in spans:
        if _member_value(span) != target:
            continue
        identity = span_identity(span)
        if identity is not None:
            target_keys.add(identity)
    selected_keys = set(target_keys)
    if include_descendants:
        selected_keys = _descendant_keys(spans, selected_keys)
    if include_ancestors:
        by_identity = _index_spans(spans)
        for identity in tuple(selected_keys):
            current = by_identity.get(identity)
            while current is not None:
                parent_id = _parent_identity(current)
                if parent_id is None or parent_id not in by_identity:
                    break
                selected_keys.add(parent_id)
                current = by_identity[parent_id]
    return [span for span in spans if span_identity(span) in selected_keys]


def member_spans(value: Any, member_id: str, **kwargs: Any) -> list[Span]:
    """Alias for :func:`select_member_spans`."""

    return select_member_spans(value, member_id, **kwargs)


def _task_value(span: Mapping[str, Any]) -> str | None:
    value = span_attributes(span).get(semconv.AT_TASK_ID)
    return str(value) if value is not None and str(value) else None


def select_task_spans(
    value: Any,
    task_id: str,
    *,
    team_id: str | None = None,
    categories: Iterable[str] | None = None,
    include_descendants: bool = True,
) -> list[Span]:
    """Select a task span and its native descendants."""

    spans = select_team_spans(value, categories=categories, team_id=team_id)
    target = str(task_id)
    target_keys: set[tuple[str, str]] = set()
    for span in spans:
        if _task_value(span) != target:
            continue
        identity = span_identity(span)
        if identity is not None:
            target_keys.add(identity)
    selected_keys = _descendant_keys(spans, target_keys) if include_descendants else target_keys
    return [span for span in spans if span_identity(span) in selected_keys]


def group_spans_by_member(
    value: Any,
    *,
    team_id: str | None = None,
    categories: Iterable[str] | None = None,
) -> dict[str, list[Span]]:
    """Group member-labelled spans without creating member trajectory copies."""

    groups: dict[str, list[Span]] = defaultdict(list)
    for span in select_team_spans(value, categories=categories, team_id=team_id):
        member = _member_value(span)
        if member is not None:
            groups[member].append(span)
    return {member: sorted(spans, key=span_sort_key) for member, spans in sorted(groups.items())}


def member_ids(value: Any, *, team_id: str | None = None) -> tuple[str, ...]:
    """Return stable member IDs observed in a Team trajectory."""

    return tuple(group_spans_by_member(value, team_id=team_id))


def read_team_attributes(span: Mapping[str, Any]) -> dict[str, Any]:
    """Read only canonical ``agentteam.*`` fields from a Team span."""

    attrs = span_attributes(span)
    return {key: value for key, value in attrs.items() if key.startswith("agentteam.")}


def descendant_spans(value: Any, parent: Mapping[str, Any], **kwargs: Any) -> list[Span]:
    """Return selected descendants of a span, excluding the span itself."""

    identity = span_identity(parent)
    if identity is None:
        return []
    spans = select_team_spans(value, **kwargs)
    selected = _descendant_keys(spans, {identity}) - {identity}
    return [span for span in spans if span_identity(span) in selected]


__all__ = [
    "CONTEXT_CATEGORIES",
    "TEAM_CATEGORIES",
    "build_team_forest",
    "descendant_spans",
    "flatten_forest",
    "group_spans_by_member",
    "is_team_span",
    "iter_team_spans",
    "member_ids",
    "member_spans",
    "read_team_attributes",
    "select_member_spans",
    "select_task_spans",
    "select_team_spans",
    "span_category",
    "team_forest",
]
