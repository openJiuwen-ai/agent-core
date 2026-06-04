# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Briefing text handed to the remote bridge agent via ``adapter.connect``.

A bridge agent pairs a local jiuwen teammate with an external
independent agent. The remote needs to know two things to be useful:

1. **Who it is** — the persona it should adopt, plus the contract that
   it's an executor (not a planner) whose text output will be passed
   verbatim by the bridge to the team.
2. **What's around it** — a short roster summary of the team it's
   plugged into, so it can address members by name when relevant.

Both come from this module as pure-text strings. The framework calls
``adapter.connect(bridge_persona=..., team_overview=...)`` once per
bridge member at lifecycle start; the adapter then forwards them to
the remote via whatever the underlying protocol supports
(A2A AgentCard, ACP init message, CLI ``--system`` flag, etc.).

The remote is an INDEPENDENT entity. It does NOT receive jiuwen tool
schemas, the jiuwen system prompt, or anything that would let it
issue tool calls back into the team. The bridge's local LLM is the
only thing that drives team actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from openjiuwen.agent_teams.schema.team import TeamRole

__all__ = [
    "MemberSummary",
    "build_bridge_persona",
    "build_team_overview",
]


@dataclass(frozen=True, slots=True)
class MemberSummary:
    """Minimal roster row passed to :func:`build_team_overview`.

    Intentionally a compact subset of ``TeamMemberSpec`` — only the
    fields the remote agent might reference when answering. Adding
    fields here means more bytes the remote sees on every connect,
    so keep this lean.
    """

    member_name: str
    role: TeamRole
    persona: str = ""


def build_bridge_persona(*, member_name: str, persona: str, language: str = "cn") -> str:
    """Build the ``bridge_persona`` text handed to ``adapter.connect``.

    Args:
        member_name: Bridge member name (identity the remote should
            adopt).
        persona: Persona text from ``BridgeMemberSpec.persona``.
        language: ``"cn"`` (default) or ``"en"``.

    Returns:
        A short paragraph the remote uses as its system prompt.
    """
    if language == "en":
        return (
            f"You are {member_name} (persona: {persona}).\n"
            f"You are the EXECUTOR backing a bridge_agent member of the "
            f"same name on a jiuwen team. Each turn you receive a message "
            f"from the team, perform the requested work (code, analysis, "
            f"answer, ...) and return the result as plain text. Your "
            f"reply will be relayed VERBATIM back to the team by the "
            f"bridge agent, so respond with the final result directly — "
            f"no 'I suggest...' framing.\n"
            f"You do NOT have tools and cannot observe team state. The "
            f"bridge agent owns all team-facing actions (sending "
            f"messages, claiming/completing tasks)."
        )
    return (
        f"你是 {member_name}（人设：{persona}）。\n"
        f"你是 jiuwen 团队中同名 bridge_agent 成员的**实际执行者**。"
        f"每次你将收到一段来自团队的消息文本，请直接**执行**对应工作"
        f"（如代码、分析、答案）并返回执行结果文本。你的回复会被 bridge "
        f"agent **原样**转交给团队，所以请直接给出最终结果，"
        f"不要使用[建议你这么做]之类的提示性语言。\n"
        f"你**没有工具**也无法感知团队内部状态——所有与团队的交互"
        f"（发送消息、认领/完成任务）由 bridge agent 完成。"
    )


def build_team_overview(
    *,
    team_name: str,
    members: Iterable[MemberSummary],
    language: str = "cn",
) -> str:
    """Build the ``team_overview`` text handed to ``adapter.connect``.

    Args:
        team_name: Display name of the team.
        members: Roster summaries (the bridge itself MAY be included
            or excluded by the caller — the caller decides whether
            the remote needs to see its own row).
        language: ``"cn"`` (default) or ``"en"``.

    Returns:
        A short bulleted overview the remote can reference for context.
    """
    lines = [
        _overview_header(team_name=team_name, language=language),
    ]
    for m in members:
        lines.append(_format_member_line(m, language=language))
    lines.append(_overview_footer(language=language))
    return "\n".join(lines)


def _overview_header(*, team_name: str, language: str) -> str:
    if language == "en":
        return f"Team {team_name} roster:"
    return f"团队 {team_name} 当前成员："


def _format_member_line(m: MemberSummary, *, language: str) -> str:
    persona = m.persona or ("(no persona)" if language == "en" else "（无人设）")
    return f"- {m.member_name} ({m.role.value}): {persona}"


def _overview_footer(*, language: str) -> str:
    if language == "en":
        return "Use the above when crafting replies; do not assume any other team state."
    return "以上信息供你回答时参考；除此之外的团队状态请勿假设。"
