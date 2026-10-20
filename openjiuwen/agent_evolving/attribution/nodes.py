# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory node model, keyword search and three-tier disclosure rendering.

Nodes are message/tool rows projected from the host's trajectory snapshot
(see :mod:`openjiuwen.agent_evolving.attribution.snapshot`).  The three tiers
form a progressive-disclosure chain: overview (coordinates + rule anchors),
summary (per-node truncated lines), detail (one node, fields capped).
All "key line" detection is rule-based, never LLM-driven, and is fully
unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

_CJK_THRESHOLD = 0.05

_ZH_FIELD_LIMIT = 50
_EN_FIELD_LIMIT = 100
_DETAIL_FIELD_LIMIT = 1000
_KEYWORD_CONTEXT_CHARS = 40
_KEYWORD_CONTENT_PREVIEW = 100
_KEYWORD_DETAIL_MAX_HITS = 30

_OVERVIEW_CHUNK_SIZE = 50
_MARKER_CAP_PER_CLASS = 5
_ERROR_NODE_ID_CAP = 20
_LANDMARK_COUNT = 3

FIELD_WINDOW_LIMIT = 50_000
_FIELD_WINDOW_FIELDS = frozenset({"content", "tool_calls"})

_FILE_ARG_KEYS = frozenset(
    {
        "file_path",
        "path",
        "filename",
        "filepath",
        "target_file",
        "file",
        "abs_file_path",
        "abs_file_path_list",
        "file_name",
        "output_file",
        "input_file",
        "destination",
        "dest",
        "dst",
        "src",
        "source",
    }
)

_ERROR_PATTERNS = (
    "traceback",
    "exception",
    "assertionerror",
    "panic",
    "non-zero exit",
    "syntaxerror",
    "keyerror",
    "valueerror",
    "typeerror",
    "importerror",
    "错误",
    "异常",
    "失败",
    "报错",
    "出错",
    "超时",
)


@dataclass(frozen=True)
class TrajectoryNode:
    """One visible trajectory row (message or tool row)."""

    node_id: str
    subagent_key: str
    display_name: str
    kind: str
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    name: str | None = None
    tool_call_id: str | None = None
    message_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    call_id: str | None = None
    start_ts_ns: int = 0
    end_ts_ns: int = 0
    source_kind: str = "msg"
    error: bool = False

    def with_node_id(self, node_id: str) -> "TrajectoryNode":
        """Return a copy carrying the given node id."""
        return TrajectoryNode(
            node_id=node_id,
            subagent_key=self.subagent_key,
            display_name=self.display_name,
            kind=self.kind,
            role=self.role,
            content=self.content,
            tool_calls=[dict(call) for call in self.tool_calls],
            name=self.name,
            tool_call_id=self.tool_call_id,
            message_id=self.message_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
            call_id=self.call_id,
            start_ts_ns=self.start_ts_ns,
            end_ts_ns=self.end_ts_ns,
            source_kind=self.source_kind,
            error=self.error,
        )


def detect_language(text: str) -> str:
    """Return ``zh`` when CJK characters are at least 5% of the text."""
    if not text:
        return "en"
    cjk = len(_CJK_PATTERN.findall(text))
    if cjk / len(text) >= _CJK_THRESHOLD:
        return "zh"
    return "en"


def _field_limit(language: str) -> int:
    return _ZH_FIELD_LIMIT if str(language).strip().lower() in {"zh", "cn"} else _EN_FIELD_LIMIT


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit]


def _is_file_arg_key(key: str) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _FILE_ARG_KEYS


def _truncate_tool_calls(tool_calls: Any, limit: int) -> list[dict[str, Any]]:
    """Truncate tool-call fields; file-name arguments are kept verbatim."""
    if not isinstance(tool_calls, list):
        return []
    result: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, Mapping):
            continue
        item: dict[str, Any] = {}
        for key, value in call.items():
            if key == "arguments":
                item[key] = _truncate_arguments(value, limit)
            elif key == "function" and isinstance(value, Mapping):
                function: dict[str, Any] = {}
                for fkey, fvalue in value.items():
                    if fkey == "arguments":
                        function[fkey] = _truncate_arguments(fvalue, limit)
                    else:
                        function[fkey] = _truncate(fvalue, limit)
                item[key] = function
            else:
                item[key] = _truncate(value, limit)
        result.append(item)
    return result


def _truncate_arguments(arguments: Any, limit: int) -> Any:
    """Truncate tool-call arguments, exempting file-name parameter values."""
    if isinstance(arguments, str):
        try:
            import json

            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return _truncate(arguments, limit)
        if isinstance(parsed, Mapping):
            return _truncate_arguments(parsed, limit)
        return parsed
    if isinstance(arguments, Mapping):
        result: dict[str, Any] = {}
        for key, value in arguments.items():
            if _is_file_arg_key(key):
                result[key] = value
            elif isinstance(value, (Mapping, list)):
                result[key] = _truncate_arguments(value, limit)
            else:
                result[key] = _truncate(value, limit)
        return result
    if isinstance(arguments, list):
        return [_truncate_arguments(value, limit) for value in arguments]
    return _truncate(arguments, limit)


def is_error_node(node: TrajectoryNode) -> bool:
    """Whether a node is an errored tool row by rule."""
    if node.error:
        return True
    if node.role != "tool":
        return False
    content = str(node.content or "")
    if not content:
        return False
    lowered = content.lower()
    return any(pattern.lower() in lowered for pattern in _ERROR_PATTERNS)


def render_summary(nodes: list[TrajectoryNode], language: str) -> dict[str, Any]:
    """Render the summary tier: one truncated line per node.

    ``system/user/tool/assistant`` content fields and ``assistant``
    ``tool_calls`` are truncated per field (zh 50 / en 100 characters);
    file-name arguments inside tool calls are never truncated.
    """
    limit = _field_limit(language)
    rows: list[dict[str, Any]] = []
    for node in nodes:
        row: dict[str, Any] = {
            "node_id": node.node_id,
            "role": node.role,
            "ts_ns": node.start_ts_ns,
        }
        if node.content is not None:
            row["content"] = _truncate(node.content, limit)
        if node.tool_calls:
            row["tool_calls"] = _truncate_tool_calls(node.tool_calls, limit)
        if node.name:
            row["name"] = node.name
        if node.tool_call_id:
            row["tool_call_id"] = node.tool_call_id
        if node.message_id:
            row["message_id"] = node.message_id
        rows.append(row)
    return {"level": "summary", "node_count": len(rows), "language": language, "nodes": rows}


def render_overview(nodes: list[TrajectoryNode]) -> dict[str, Any]:
    """Render the overview tier for a >500-node subagent trajectory.

    Only coordinates and rule anchors are returned: user rows, errored tool
    rows (capped), per-chunk markers and head/tail landmarks.
    """
    user_ids = [node.node_id for node in nodes if node.role == "user"]
    error_ids = [node.node_id for node in nodes if is_error_node(node)]
    error_truncated = len(error_ids) > _ERROR_NODE_ID_CAP
    shown_error_ids = error_ids[:_ERROR_NODE_ID_CAP]

    chunks: list[dict[str, Any]] = []
    for start in range(0, len(nodes), _OVERVIEW_CHUNK_SIZE):
        chunk_nodes = nodes[start : start + _OVERVIEW_CHUNK_SIZE]
        markers: list[dict[str, Any]] = []
        markers_truncated = False
        for kind, predicate, brief in _marker_kinds():
            hits = [node for node in chunk_nodes if predicate(node)]
            if len(hits) > _MARKER_CAP_PER_CLASS:
                hits = hits[:_MARKER_CAP_PER_CLASS]
                markers_truncated = True
            for node in hits:
                markers.append({"node_id": node.node_id, "kind": kind, "brief": brief(node)})
        chunks.append(
            {
                "node_range": [chunk_nodes[0].node_id, chunk_nodes[-1].node_id],
                "time_range": {"start_ns": chunk_nodes[0].start_ts_ns, "end_ns": chunk_nodes[-1].end_ts_ns},
                "markers": markers,
                "markers_truncated": markers_truncated,
            }
        )

    time_range = {"start_ns": nodes[0].start_ts_ns, "end_ns": nodes[-1].end_ts_ns} if nodes else {}
    landmarks = {
        "first_nodes": [
            {"node_id": node.node_id, "role": node.role, "brief": _landmark_brief(node)}
            for node in nodes[:_LANDMARK_COUNT]
        ],
        "last_nodes": [
            {"node_id": node.node_id, "role": node.role, "brief": _landmark_brief(node)}
            for node in nodes[-_LANDMARK_COUNT:]
        ],
    }
    note_parts = []
    if error_truncated:
        note_parts.append(f"出错行共 {len(error_ids)} 个，此处列前 {_ERROR_NODE_ID_CAP}。")
    result: dict[str, Any] = {
        "level": "overview",
        "node_count": len(nodes),
        "time_range": time_range,
        "user_node_ids": user_ids,
        "error_node_ids": shown_error_ids,
        "chunks": chunks,
        "landmarks": landmarks,
        "drill_down": (
            "渐进式披露指引：从 user_node_ids / error_node_ids / markers 选定目标块 → "
            "view_partial_trajectory(块首, 块尾) 看该段摘要级 → view_node_detail 看单行；"
            "已知文本线索用 keyword_reg（正则）直接定位"
        ),
    }
    if note_parts:
        result["note"] = " ".join(note_parts)
    return result


def _marker_kinds() -> list[tuple[str, Any, Any]]:
    def is_user(node: TrajectoryNode) -> bool:
        return node.role == "user"

    def is_tool_call(node: TrajectoryNode) -> bool:
        return bool(node.tool_calls) and node.role == "assistant"

    def brief_user(node: TrajectoryNode) -> str:
        return _truncate(node.content or "", 100)

    def brief_tool_call(node: TrajectoryNode) -> str:
        name = node.tool_calls[0].get("name") or node.tool_calls[0].get("function", {}).get("name") or "?"
        arg = _first_file_arg(node.tool_calls[0])
        suffix = f"({_truncate(arg, 50)})" if arg else ""
        return f"tool_call: {name}{suffix}"

    def brief_error(node: TrajectoryNode) -> str:
        return _truncate(node.content or "", 100)

    return [
        ("user", is_user, brief_user),
        ("tool_call", is_tool_call, brief_tool_call),
        ("error", is_error_node, brief_error),
    ]


def _landmark_brief(node: TrajectoryNode) -> str:
    if node.role == "assistant" and node.tool_calls:
        name = node.tool_calls[0].get("name") or node.tool_calls[0].get("function", {}).get("name") or "?"
        return f"tool_call: {name}"
    return _truncate(node.content or "", 100)


def _first_file_arg(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    arguments = function.get("arguments") if isinstance(function, Mapping) else tool_call.get("arguments")
    if isinstance(arguments, str):
        try:
            import json

            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(arguments, Mapping):
        return ""
    for key, value in arguments.items():
        if _is_file_arg_key(key):
            return _truncate(value, 200) if isinstance(value, str) else str(value)[:200]
    return ""


def _node_field_text(node: TrajectoryNode, field_name: str) -> str:
    """Return the raw text of a windowable field (``content`` or ``tool_calls``).

    ``tool_calls`` is rendered exactly like the detail tier does (one JSON
    text via ``json.dumps``) so both tiers share the same source.
    """
    if field_name == "tool_calls":
        if not node.tool_calls:
            return ""
        import json

        return json.dumps(node.tool_calls, ensure_ascii=False)
    return str(node.content) if node.content is not None else ""


def render_node_field(node: TrajectoryNode, field_name: str, start: int, limit: int) -> dict[str, Any]:
    """Return one character window of a node field (cursor semantics).

    ``start`` and ``limit`` are character offsets into the raw field text;
    the window never exceeds ``limit`` characters.  ``end`` is the actual
    stop offset and ``has_more`` says whether more text follows, so callers
    continue by passing the returned ``end`` as the next ``start``.  A
    ``start`` at or past the total length is not an error: it returns an
    empty window with ``has_more=false``.
    """
    text = _node_field_text(node, field_name)
    total = len(text)
    if start >= total:
        return {"text": "", "start": start, "end": start, "total_chars": total, "has_more": False}
    end = min(start + limit, total)
    return {"text": text[start:end], "start": start, "end": end, "total_chars": total, "has_more": end < total}


def render_node_detail(node: TrajectoryNode) -> dict[str, Any]:
    """Render the detail tier: every field capped at 1000 characters.

    ``field_lengths`` carries the raw character counts of the windowable
    fields so the caller knows how much content a ``[truncated]`` field
    still holds (and whether ``view_node_field`` is worth calling).
    """
    result: dict[str, Any] = {
        "node_id": node.node_id,
        "role": node.role,
        "origin": node.source_kind,
        "subagent": {
            "id": node.subagent_key,
            "display_name": node.display_name,
            "kind": node.kind,
        },
        "start_ts_ns": node.start_ts_ns,
        "end_ts_ns": node.end_ts_ns,
    }
    for key, value in (
        ("content", node.content),
        ("name", node.name),
        ("tool_call_id", node.tool_call_id),
        ("message_id", node.message_id),
        ("trace_id", node.trace_id),
        ("span_id", node.span_id),
        ("call_id", node.call_id),
    ):
        if value is None:
            continue
        text = str(value)
        if len(text) > _DETAIL_FIELD_LIMIT:
            result[key] = f"{text[:_DETAIL_FIELD_LIMIT]}[truncated]"
        else:
            result[key] = text
    if node.tool_calls:
        import json

        rendered = json.dumps(node.tool_calls, ensure_ascii=False)
        if len(rendered) > _DETAIL_FIELD_LIMIT:
            result["tool_calls"] = f"{rendered[:_DETAIL_FIELD_LIMIT]}[truncated]"
        else:
            result["tool_calls"] = node.tool_calls
    field_lengths: dict[str, int] = {}
    if node.content is not None:
        field_lengths["content"] = len(str(node.content))
    if node.tool_calls:
        field_lengths["tool_calls"] = len(_node_field_text(node, "tool_calls"))
    result["field_lengths"] = field_lengths
    return result


def _node_text(node: TrajectoryNode) -> str:
    parts: list[str] = []
    if node.content:
        parts.append(str(node.content))
    if node.tool_calls:
        import json

        try:
            parts.append(json.dumps(node.tool_calls, ensure_ascii=False))
        except (TypeError, ValueError):
            pass
    return "\n".join(parts)


_USER_ORIGINS = frozenset({"external_user_authored"})
_TEAM_MEMBER_KIND = "team_member"


def user_request_text(node: TrajectoryNode) -> str | None:
    """Return what the user wrote when ``node`` is a message from the user, else ``None``.

    The host wraps a user's chat message as a line of preamble followed by a JSON envelope
    (``"type": "user input"`` or ``"origin_kind": "external_user_authored"``); the user's words
    are its ``content``.  A plain user row of the main agent counts as the user's words too.
    Rows that only look like user turns are not: a team member's inbound dispatches, system
    reminders, team events and tool results that the runtime replays as user messages.
    """
    if node.role != "user" or node.kind == _TEAM_MEMBER_KIND or not node.content:
        return None
    text = str(node.content).strip()
    start = text.find("{")
    if start >= 0:
        import json

        try:
            envelope, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            envelope = None
        if isinstance(envelope, Mapping) and isinstance(envelope.get("content"), str):
            if envelope.get("type") == "user input" or envelope.get("origin_kind") in _USER_ORIGINS:
                return envelope["content"].strip() or None
            return None
    if node.kind in {"main_agent", "main"} and text and not text.startswith(("<", "[", "{")):
        return text
    return None


def user_request_nodes(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    until_ns: int | None = None,
) -> list[TrajectoryNode]:
    """The user's own messages in time order, at most one row per distinct text.

    ``until_ns`` drops messages sent after that time (they ask for later work).
    """
    found: list[TrajectoryNode] = []
    seen: set[str] = set()
    rows = sorted(
        (node for nodes in nodes_by_subagent.values() for node in nodes),
        key=lambda node: node.start_ts_ns,
    )
    for node in rows:
        text = user_request_text(node)
        if text is None or text in seen:
            continue
        if until_ns is not None and node.start_ts_ns and node.start_ts_ns > until_ns:
            continue
        seen.add(text)
        found.append(node)
    return found


def search_keyword(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    pattern: str,
) -> dict[str, Any]:
    """Search node text by regex (``re.search``, case-insensitive).

    With fewer than 30 global hits every match returns its node id, a content
    preview and ±40-character context; at 30 or more hits only per-subagent
    counts are returned with a note suggesting a tighter pattern.
    """
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return {"error": f"invalid regular expression: {exc}"}

    hits: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for subagent, nodes in nodes_by_subagent.items():
        subject_hits: list[dict[str, Any]] = []
        for node in nodes:
            match = compiled.search(_node_text(node))
            if match is None:
                continue
            total += 1
            context = _excerpt(_node_text(node), match.start(), match.end())
            subject_hits.append(
                {
                    "node_id": node.node_id,
                    "content": _truncate(node.content or "", _KEYWORD_CONTENT_PREVIEW),
                    "tool_name": node.name or None,
                    "context": context,
                }
            )
        if subject_hits:
            hits[subagent] = subject_hits

    if total >= _KEYWORD_DETAIL_MAX_HITS:
        return {
            "counts": [
                {"subagent_id": subagent, "count": len(subject_hits)} for subagent, subject_hits in sorted(hits.items())
            ],
            "total": total,
            "note": "匹配上过多节点无法展示详情，建议收紧正则匹配约束",
        }
    return {"results": hits, "total": total}


def _excerpt(text: str, start: int, end: int) -> str:
    left = text[max(0, start - _KEYWORD_CONTEXT_CHARS) : start]
    right = text[end : end + _KEYWORD_CONTEXT_CHARS]
    return f"...{left}{text[start:end]}{right}..."


__all__ = [
    "FIELD_WINDOW_LIMIT",
    "TrajectoryNode",
    "detect_language",
    "is_error_node",
    "render_node_detail",
    "render_node_field",
    "render_overview",
    "render_summary",
    "search_keyword",
    "user_request_nodes",
    "user_request_text",
]
