# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded evidence assembly for TTSE induce / blame prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from openjiuwen.agent_evolving.optimizer.skill_call.conversation_snippet import (
    build_conversation_snippet,
)
from openjiuwen.agent_evolving.optimizer.skill_call.tool_call_chain import build_tool_call_chain

BANK_SECTION_MAX_CHARS = 4000

_CHAIN_PLACEHOLDERS = frozenset(
    {
        "(无执行轨迹)",
        "(No execution trace)",
        "(无工具调用轨迹；参见对话历史)",
        "(No tool calls; see conversation history)",
    }
)


@dataclass(frozen=True)
class InduceEvidence:
    """Structured, bounded evidence fed to induce / blame."""

    task_query: str
    conversation_snippet: str
    tool_call_chain: str
    grader_note: str = ""
    evidence_text: str = ""

    @property
    def is_empty(self) -> bool:
        snippet = (self.conversation_snippet or "").strip()
        chain = (self.tool_call_chain or "").strip()
        chain_empty = not chain or chain in _CHAIN_PLACEHOLDERS
        return not snippet and chain_empty


def format_bank_section(rules: Sequence[str], *, max_chars: int = BANK_SECTION_MAX_CHARS) -> str:
    """Join ``- {rule}`` lines until ``max_chars``; never split a line mid-rule."""
    if max_chars <= 0:
        return ""
    lines: list[str] = []
    used = 0
    for rule in rules:
        text = str(rule).strip()
        if not text:
            continue
        line = f"- {text}"
        # +1 for the newline that will separate from the previous line
        extra = len(line) if not lines else len(line) + 1
        if used + extra > max_chars:
            break
        lines.append(line)
        used += extra
    return "\n".join(lines)


def _build_grader_note(
    dim_scores: Optional[Mapping[str, Any]],
    overall: Optional[float],
) -> str:
    if not isinstance(dim_scores, dict) or not dim_scores:
        return ""
    parts = [f"{k}={float(v):.2f}" for k, v in dim_scores.items() if v is not None]
    if overall is not None:
        parts.append(f"overall={float(overall):.2f}")
    if not parts:
        return ""
    return (
        "[GRADER SCORES 0-1 per dimension, lower = weaker] "
        + " ".join(parts)
        + " — account for BOTH high dimensions (what worked) and low "
        "dimensions (what was weak / should improve) when extracting rules."
    )


def _compose_evidence_text(
    *,
    task_query: str,
    conversation_snippet: str,
    tool_call_chain: str,
    grader_note: str,
    traj_char_budget: Optional[int],
) -> str:
    parts: list[str] = []
    if grader_note:
        parts.append(grader_note)
    if task_query:
        parts.append(f"User query (task):\n{task_query}")
    if conversation_snippet.strip():
        parts.append(f"Conversation snippet:\n{conversation_snippet}")
    chain = (tool_call_chain or "").strip()
    if chain and chain not in _CHAIN_PLACEHOLDERS:
        parts.append(f"Tool call chain:\n{tool_call_chain}")
    text = "\n\n".join(parts)
    if traj_char_budget is not None and traj_char_budget > 0 and len(text) > traj_char_budget:
        return text[:traj_char_budget]
    return text


def build_induce_evidence(
    messages: Sequence[dict],
    *,
    task_query: str,
    dim_scores: Optional[Mapping[str, Any]] = None,
    overall: Optional[float] = None,
    traj_char_budget: Optional[int] = None,
    language: str = "en",
) -> InduceEvidence:
    """Build SkillEvolution-aligned induce evidence from conversation messages."""
    msg_list = list(messages or [])
    snippet = build_conversation_snippet(msg_list, language=language)
    chain = build_tool_call_chain(msg_list, language=language)
    grader_note = _build_grader_note(dim_scores, overall)
    evidence_text = _compose_evidence_text(
        task_query=task_query or "",
        conversation_snippet=snippet,
        tool_call_chain=chain,
        grader_note=grader_note,
        traj_char_budget=traj_char_budget,
    )
    return InduceEvidence(
        task_query=task_query or "",
        conversation_snippet=snippet,
        tool_call_chain=chain,
        grader_note=grader_note,
        evidence_text=evidence_text,
    )


__all__ = [
    "BANK_SECTION_MAX_CHARS",
    "InduceEvidence",
    "build_induce_evidence",
    "format_bank_section",
]
