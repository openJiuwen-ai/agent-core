# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read team state and the public discussion, then ask the model once."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.conversation import ConversationMessage
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage

EMPTY_REPORT = "暂无进展"
_SCOPES = frozenset({"team", "member", "all"})
_HISTORY_LIMIT = 200
_BODY_LIMIT = 2000
_SYSTEM_PROMPT = (
    "你是团队进展汇报助手。根据材料用中文作答，固定四段：目标、计划、进度、进展质量。"
    "材料没有覆盖的段落写「材料不足」。不要编造成员发言，不要补充材料之外的事实。"
)


class ProgressReportService:
    """One-shot report. Nothing here is stored or kept running."""

    def __init__(
        self,
        *,
        team_name: str,
        session_id: str,
        scope: str = "all",
        member_name: Optional[str] = None,
        spec: TeamAgentSpec,
        workspace: Path | str | None = None,
    ) -> None:
        if scope not in _SCOPES:
            raise ValueError(f"invalid scope: {scope!r} (expected team|member|all)")
        self._team_name = team_name
        self._session_id = session_id
        self._scope = scope
        self._member_name = member_name
        self._spec = spec
        self._workspace = workspace

    async def generate(self) -> str:
        """Return the report text, or ``暂无进展`` when there is nothing to summarise."""
        team, members, tasks, history = await self._collect()
        if not tasks and not history:
            return EMPTY_REPORT

        model = self._build_model()
        if model is None:
            raise ValueError("report_model_unavailable")

        response = await model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                UserMessage(content=self._format(team, members, tasks, history)),
            ]
        )
        text = _message_text(response)
        return text or "材料不足"

    def _build_model(self):
        for key in ("leader", "teammate"):
            agent_spec = self._spec.agents.get(key)
            model_config = getattr(agent_spec, "model", None) if agent_spec is not None else None
            if model_config is not None:
                return model_config.build()
        if self._spec.model_pool:
            return self._spec.model_pool[0].to_team_model_config().build()
        return None

    async def _collect(self):
        token = set_session_id(self._session_id)
        db = TeamDatabase(self._spec.resolve_db_config())
        try:
            await db.initialize()
            team = await db.team.get_team(self._team_name)
            members = await db.member.get_team_members(self._team_name)
            tasks = await db.task.get_team_tasks(self._team_name)
        finally:
            await db.close()
            reset_session_id(token)
        return team, members, tasks, self._read_history()

    def _read_history(self) -> list[ConversationMessage]:
        log = GroupConversationLog(self._team_name, self._session_id, self._workspace)
        return log.read_messages()

    def _format(self, team, members, tasks, history: list[ConversationMessage]) -> str:
        lines: list[str] = []
        if self._scope == "member" and self._member_name:
            lines.append(f"只展开成员 {self._member_name}，整体段可以不写。")
        elif self._scope == "member":
            lines.append("按成员展开，不写团队整体段。")
        if self._scope in ("team", "all"):
            lines.extend(self._team_lines(team, members, tasks, history))
        if self._scope in ("member", "all"):
            selected = self._selected_members(members)
            if not selected and self._member_name:
                lines.append(f"成员 {self._member_name}：材料不足")
            for member in selected:
                lines.extend(self._member_lines(member, tasks, history))
        return "\n".join(lines)

    def _team_lines(self, team, members, tasks, history) -> list[str]:
        display_name = team.display_name if team is not None else self._spec.team_name
        desc = (team.desc if team is not None and team.desc else "") or self._spec.team_desc
        leader_name = team.leader_member_name if team is not None else ""
        leader = next((member for member in members if member.member_name == leader_name), None)
        lines = [
            f"团队：{display_name}",
            f"团队目标：{desc or '材料不足'}",
            f"Leader 描述：{leader.desc if leader is not None and leader.desc else '材料不足'}",
            "名册：",
        ]
        if members:
            for member in members:
                lines.append(
                    f"- {member.member_name} ({member.display_name}) "
                    f"role={member.role} status={member.status} {member.desc or ''}".rstrip()
                )
        else:
            lines.append("（暂无成员）")
        lines.append("任务黑板：")
        lines.extend(self._task_lines(tasks))
        lines.append("公开讨论：")
        lines.extend(self._history_lines(history))
        return lines

    def _member_lines(self, member, tasks, history) -> list[str]:
        own_tasks = [task for task in tasks if task.assignee == member.member_name]
        own_history = [
            item
            for item in history
            if item.sender == member.member_name or member.member_name in item.mentions
        ]
        lines = [
            f"成员：{member.member_name} ({member.display_name})",
            f"职责：{member.desc or '材料不足'}",
            f"状态：{member.status}",
            "负责的任务：",
        ]
        lines.extend(self._task_lines(own_tasks))
        lines.append("相关公开讨论：")
        lines.extend(self._history_lines(own_history))
        return lines

    def _selected_members(self, members):
        if self._member_name is not None:
            return [member for member in members if member.member_name == self._member_name]
        return [member for member in members if member.role != "leader"]

    @staticmethod
    def _task_lines(tasks) -> list[str]:
        if not tasks:
            return ["（暂无任务）"]
        lines = []
        for task in tasks:
            lines.append(f"- [{task.status}] {task.title} (负责人: {task.assignee or '未分配'})")
            if task.content:
                lines.append(f"  描述: {_clip(task.content)}")
        return lines

    @staticmethod
    def _history_lines(history: list[ConversationMessage]) -> list[str]:
        if not history:
            return ["（无）"]
        omitted = max(0, len(history) - _HISTORY_LIMIT)
        visible = history[-_HISTORY_LIMIT:]
        lines = []
        if omitted:
            lines.append(f"（更早的 {omitted} 条公开讨论已省略）")
        for item in visible:
            mentions = ",".join(item.mentions) if item.mentions else "无"
            lines.append(f"- {item.sender_name}: {_clip(item.content)} (mentions: {mentions})")
        return lines


def _clip(text: str) -> str:
    if len(text) <= _BODY_LIMIT:
        return text
    return text[:_BODY_LIMIT] + "…[truncated]"


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()
