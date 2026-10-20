# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stage two (keyword part): process signals over trajectory nodes.

The deterministic rule mode takes failed tool calls and the user's follow-up
messages; the Jev mode asks the decision model once per candidate node.  Jev
unavailability degrades to rule mode.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource, jev_tier
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, user_request_nodes, user_request_text

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.attribution.jev import JevClient

_DATA_FETCH_TOOLS = frozenset(
    {
        "mcp_fetch_webpage",
        "fetch_webpage",
        "web_fetch",
        "search",
        "web_search",
        "google_search",
        "bing_search",
        "view_file",
        "read_file",
        "cat_file",
        "list_directory",
        "ls",
        "get_url",
        "curl",
        "wget",
        "ttse_consult",
    }
)

_TOOL_SCHEMA_PATTERN = re.compile(r"\{'content': '---\\nname: [^\n]+\\ndescription:")

_JEV_TEXT_LIMIT = 1024
_JEV_THRESHOLD = 0.8
SIGNAL_TEXT_LIMIT = 600
_MAX_TOOL_ERRORS = 10


def _source_for_role(role: str) -> SignalSource:
    return SignalSource.KEYWORD_TOOL if role == "tool" else SignalSource.KEYWORD_USER


def _tool_error_signals(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> list[DiagnosticSignal]:
    """Tool calls the runtime recorded as failed, one per distinct first line, in subject + time order."""
    seen: set[str] = set()
    signals: list[DiagnosticSignal] = []
    for subagent in sorted(nodes_by_subagent):
        for node in nodes_by_subagent[subagent]:
            if node.role != "tool" or not node.error:
                continue
            content = str(node.content or "").strip()
            head = content.splitlines()[0] if content else ""
            if head in seen:
                continue
            seen.add(head)
            name = str(node.name or "").strip()
            text = f"{name}: {content}" if name else content
            signals.append(
                DiagnosticSignal.create(
                    text[:SIGNAL_TEXT_LIMIT] or name or "tool error",
                    source=SignalSource.KEYWORD_TOOL,
                    node_ids=[node.node_id],
                    ts_ns=node.start_ts_ns,
                    message_id=node.message_id,
                )
            )
            if len(signals) == _MAX_TOOL_ERRORS:
                return signals
    return signals


def _user_follow_up_signals(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> list[DiagnosticSignal]:
    """The user's own messages in the selection after the session's first request, one per distinct text.

    A user who writes again after asking usually corrects, complains or asks for more; whether the
    message is a real complaint is left to attribution, which can mark it invalid.  The first request
    comes from the whole session when the slicer recorded it (``SlicedNodes.user_requests``).
    """
    requests = getattr(nodes_by_subagent, "user_requests", None)
    if requests is None:
        requests = user_request_nodes(nodes_by_subagent)
    first = user_request_text(requests[0]) if requests else None
    seen: set[str] = {first} if first else set()
    rows = sorted(
        (node for nodes in nodes_by_subagent.values() for node in nodes),
        key=lambda node: node.start_ts_ns,
    )
    signals: list[DiagnosticSignal] = []
    for node in rows:
        text = user_request_text(node)
        if not text or text in seen:
            continue
        seen.add(text)
        signals.append(
            DiagnosticSignal.create(
                text[:SIGNAL_TEXT_LIMIT],
                source=SignalSource.KEYWORD_USER,
                node_ids=[node.node_id],
                ts_ns=node.start_ts_ns,
                message_id=node.message_id,
            )
        )
    return signals


def detect_rule_signals(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> list[DiagnosticSignal]:
    """Process signals by rule: failed tool calls, then the user's follow-up messages.

    Only two places say something went wrong without reading meaning into words: a tool row the
    runtime itself recorded as failed (at most ten, distinct first lines), and a message the user
    wrote after the first request.  Error words inside documents, prompts, test output or team
    messages are not signals.
    """
    return _tool_error_signals(nodes_by_subagent) + _user_follow_up_signals(nodes_by_subagent)


def _node_text_for_jev(node: TrajectoryNode, limit: int = _JEV_TEXT_LIMIT) -> str:
    role = node.role
    content = str(node.content or "")
    if role == "tool":
        name = str(node.name or "").strip()
        text = f"{name}: {content}" if name else content
    elif content:
        text = f"{role}: {content}"
    else:
        text = role
    return text[:limit]


async def detect_jev_signals(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    client: "JevClient",
    *,
    threshold: float = _JEV_THRESHOLD,
    concurrency: int = 8,
) -> list[DiagnosticSignal]:
    """Ask Jev once per tool/user node and emit signals at ``P >= threshold``."""
    flat: list[TrajectoryNode] = []
    for subagent in sorted(nodes_by_subagent):
        for node in nodes_by_subagent[subagent]:
            if node.role in ("tool", "user"):
                if node.role == "tool":
                    if str(node.name or "").lower() in _DATA_FETCH_TOOLS:
                        continue
                    if _TOOL_SCHEMA_PATTERN.search(str(node.content or "")):
                        continue
                flat.append(node)

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def judge(node: TrajectoryNode) -> DiagnosticSignal | None:
        text = _node_text_for_jev(node)
        if not text:
            return None
        async with semaphore:
            answers = await client.system_one(
                {"category": node.role, "content": text},
                {
                    "is_diagnostic": {
                        "type": "noul",
                        "instructions": "该 span 是否包含负反馈、诊断信号或故障信号（错误、纠正、异常等）？",
                    }
                },
            )
        answer = answers.get("is_diagnostic") or {}
        try:
            probability = float(answer.get("noul") or 0.0)
        except (TypeError, ValueError):
            probability = 0.0
        if probability < threshold:
            return None
        return DiagnosticSignal.create(
            str(node.content or ""),
            source=_source_for_role(node.role),
            tier=jev_tier(probability * 100.0),
            node_ids=[node.node_id],
            ts_ns=node.start_ts_ns,
            message_id=node.message_id,
            confidence=probability,
        )

    results = await asyncio.gather(*(judge(node) for node in flat))
    return [signal for signal in results if signal is not None]


async def run_keyword_diagnosis(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    mode: str,
    jev_client: "JevClient | None" = None,
    *,
    jev_threshold: float = _JEV_THRESHOLD,
    jev_concurrency: int = 8,
) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
    """Run keyword diagnosis; a Jev failure degrades to rule mode.

    Returns ``(signals, run_meta)`` with the effective mode recorded.
    """
    if str(mode).strip().lower() != "jev":
        signals = detect_rule_signals(nodes_by_subagent)
        return signals, {"mode": "rule", "count": len(signals)}
    if jev_client is None:
        signals = detect_rule_signals(nodes_by_subagent)
        return signals, {"mode": "rule", "count": len(signals), "degraded": "jev_client_missing"}
    try:
        signals = await detect_jev_signals(
            nodes_by_subagent, jev_client, threshold=jev_threshold, concurrency=jev_concurrency
        )
        return signals, {"mode": "jev", "count": len(signals)}
    except Exception as exc:  # noqa: BLE001 - degrade on any Jev failure
        signals = detect_rule_signals(nodes_by_subagent)
        return signals, {"mode": "rule", "count": len(signals), "degraded": type(exc).__name__}


__all__ = [
    "detect_jev_signals",
    "detect_rule_signals",
    "run_keyword_diagnosis",
]
