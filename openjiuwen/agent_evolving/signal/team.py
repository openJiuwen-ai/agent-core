# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-domain signal helpers and detectors for team-skill evolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_evolving.protocols import TRAJECTORY_ISSUE_SIGNAL, USER_INTENT_SIGNAL
from openjiuwen.agent_evolving.signal.base import EvolutionSignal, make_evolution_signal
from openjiuwen.agent_evolving.trajectory.types import Trajectory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

_TEAM_USER_REQUEST_PROMPT_CN = (
    "判断以下用户输入是否包含对当前团队任务或团队协作方式的改进意见。\n"
    "如果是，提取改进意图的摘要。\n\n"
    "团队技能描述：{team_skill_description}\n"
    "当前角色：{roles}\n"
    "用户输入：{user_messages}\n\n"
    '输出 JSON: {{"is_improvement": true/false, "intent": "str"}}\n'
)

_TEAM_USER_REQUEST_PROMPT_EN = (
    "Determine if the following user input contains improvement suggestions "
    "for the current team task or collaboration approach.\n"
    "If yes, extract a summary of the improvement intent.\n\n"
    "Team skill description: {team_skill_description}\n"
    "Current roles: {roles}\n"
    "User input: {user_messages}\n\n"
    'Output JSON: {{"is_improvement": true/false, "intent": "str"}}\n'
)

_TEAM_TRAJECTORY_ISSUE_PROMPT_CN = (
    "分析以下执行轨迹，判断团队技能是否存在不足需要演进。\n\n"
    "当前团队技能：\n{skill_content}\n\n"
    "执行轨迹摘要：\n{trajectory_summary}\n\n"
    "请从以下维度分析：\n"
    "- 角色配合是否恰当（是否有角色间协作断裂、数据未传递）\n"
    "- 约束是否被违反（超时、产出格式不合规）\n"
    "- 流程是否低效（重复调用、多余步骤）\n"
    "- 角色能力是否不足（某角色多次失败或产出质量不达标）\n\n"
    "如果存在不足，输出 JSON 数组：\n"
    '[{{"issue_type": str, "description": str, "affected_role": str, "severity": "low"|"medium"|"high"}}]\n'
    "如果没有问题，输出空数组 []。"
)

_TEAM_TRAJECTORY_ISSUE_PROMPT_EN = (
    "Analyze the following execution trajectory and determine whether the team skill has deficiencies.\n\n"
    "Current team skill:\n{skill_content}\n\n"
    "Trajectory summary:\n{trajectory_summary}\n\n"
    "Analyze from these dimensions:\n"
    "- Role coordination (collaboration breaks, data not passed)\n"
    "- Constraint violations (timeout, output format issues)\n"
    "- Workflow inefficiency (redundant calls, extra steps)\n"
    "- Role capability gaps (repeated failures, poor output quality)\n\n"
    "If issues exist, output a JSON array:\n"
    '[{{"issue_type": str, "description": str, "affected_role": str, "severity": "low"|"medium"|"high"}}]\n'
    "If no issues, output empty array [].\n"
)


class TeamSignalType(str, Enum):
    """Team-domain signal types.

    `USER_REQUEST` is kept as a compatibility synonym for older API/tests.
    """

    USER_INTENT = USER_INTENT_SIGNAL
    USER_REQUEST = "user_request"
    TRAJECTORY_ISSUE = TRAJECTORY_ISSUE_SIGNAL


@dataclass(frozen=True)
class UserIntent:
    """Parsed user improvement intention."""

    is_improvement: bool
    intent: str


@dataclass(frozen=True)
class TrajectoryIssue:
    """Normalized trajectory issue detected from team execution traces."""

    issue_type: str
    description: str
    affected_role: str = ""
    severity: str = "medium"


def _try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _fix_json_text(text: str) -> str:
    """Apply lightweight cleanup for common LLM JSON formatting issues."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _extract_balanced_json(text: str, opener: str, closer: str) -> str | None:
    """Return the first balanced JSON-like substring."""
    start = text.find(opener)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_team_model_json(raw: str) -> dict[str, Any] | list[Any] | None:
    """Parse dict/list JSON from team-skill LLM outputs with light repair."""
    if not raw:
        return None

    candidates: list[str] = []
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        candidates.append(match.group(1).strip())
    candidates.append(raw.strip())
    candidates.append(_fix_json_text(raw))

    balanced_object = _extract_balanced_json(raw, "{", "}")
    if balanced_object:
        candidates.append(balanced_object)
        candidates.append(_fix_json_text(balanced_object))

    balanced_array = _extract_balanced_json(raw, "[", "]")
    if balanced_array:
        candidates.append(balanced_array)
        candidates.append(_fix_json_text(balanced_array))

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        data = _try_parse_json(candidate)
        if isinstance(data, (dict, list)):
            return data

    head = raw[:600].replace("\n", "\\n")
    logger.warning(
        "[TeamSignal] JSON parse failed (raw_len=%d, head=%r)",
        len(raw),
        head,
    )
    return None


def build_team_trajectory_summary(trajectory: Trajectory) -> str:
    """Summarize trajectory steps with higher detail for collaboration-critical tools."""
    tool_budget = 20000
    llm_budget = 10000
    key_tools = {"spawn_member", "create_task", "build_team", "view_task", "send_message"}
    tool_lines: list[str] = []
    llm_lines: list[str] = []
    llm_count = 0
    tool_count = 0

    for step in trajectory.steps:
        if step.kind == "tool" and step.detail:
            tool_count += 1
            tool_name = getattr(step.detail, "tool_name", "unknown")
            is_key = tool_name in key_tools
            args_limit = 500 if is_key else 150
            result_limit = 500 if is_key else 200
            args = str(getattr(step.detail, "call_args", ""))[:args_limit]
            result = str(getattr(step.detail, "call_result", ""))[:result_limit]
            tool_lines.append(f"[Tool:{tool_name}] args={args} result={result}")
        elif step.kind == "llm" and step.detail:
            llm_count += 1
            response = getattr(step.detail, "response", None)
            if response:
                llm_lines.append(f"[LLM] {str(response)[:300]}")

    tool_section = "\n".join(tool_lines)
    if len(tool_section) > tool_budget:
        tool_section = tool_section[:tool_budget] + "\n... (tool section truncated)"

    llm_section = "\n".join(llm_lines)
    if len(llm_section) > llm_budget:
        llm_section = llm_section[:llm_budget] + "\n... (LLM section truncated)"

    summary = f"### Tool Calls ({tool_count})\n{tool_section}\n\n### LLM Responses ({llm_count})\n{llm_section}"
    logger.info(
        "[TeamSignal] trajectory summary: %d LLM steps, %d tool steps, tool_section_len=%d, "
        "llm_section_len=%d, total_len=%d",
        llm_count,
        tool_count,
        len(tool_section),
        len(llm_section),
        len(summary),
    )
    return summary


def make_team_user_intent_signal(
    *,
    skill_name: str,
    user_intent: str,
) -> EvolutionSignal:
    """Build the standard explicit-request signal for team skill evolution."""
    return make_evolution_signal(
        signal_type=TeamSignalType.USER_INTENT.value,
        section="Instructions",
        excerpt=user_intent,
        skill_name=skill_name,
        source="explicit_request",
    )


def _extract_roles_summary(team_skill_content: str) -> str:
    """Extract a compact role summary from team skill content."""
    if not team_skill_content:
        return ""

    lines = team_skill_content.splitlines()
    role_lines: list[str] = []
    in_roles = False
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("roles:"):
            value = stripped.partition(":")[2].strip()
            if value:
                role_lines.append(value)
            in_roles = True
            continue
        if in_roles:
            if not stripped:
                continue
            if stripped.startswith("-") or line.startswith((" ", "\t")):
                role_lines.append(stripped)
                continue
            break

    if not role_lines:
        for line in lines:
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith(("role:", "角色：", "角色:")):
                role_lines.append(stripped)
            if len(role_lines) >= 5:
                break

    return "\n".join(role_lines)[:500]


_TEAM_TRAJECTORY_ISSUES_KEY = "trajectory_issues"
_TEAM_SKILL_CONTENT_KEY = "skill_content"


def make_team_trajectory_signal(
    *,
    skill_name: str,
    skill_content: str,
    trajectory_issues: list[dict[str, str]],
) -> EvolutionSignal:
    """Build the canonical passive trajectory signal for team-skill evolution."""
    return make_evolution_signal(
        signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
        section="",
        excerpt="Detected team skill trajectory issues requiring evolution.",
        skill_name=skill_name,
        source="passive_trajectory",
        context={
            _TEAM_TRAJECTORY_ISSUES_KEY: list(trajectory_issues),
            _TEAM_SKILL_CONTENT_KEY: skill_content,
        },
    )


def get_team_trajectory_issues(signal: EvolutionSignal) -> list[dict[str, str]]:
    """Read normalized trajectory issues from a team-domain signal."""
    context = signal.context or {}
    issues = context.get(_TEAM_TRAJECTORY_ISSUES_KEY)
    if not isinstance(issues, list):
        return []
    return [item for item in issues if isinstance(item, dict)]


def get_team_signal_skill_content(signal: EvolutionSignal) -> str | None:
    """Read the associated team-skill content from a team-domain signal."""
    context = signal.context or {}
    skill_content = context.get(_TEAM_SKILL_CONTENT_KEY)
    return str(skill_content) if skill_content is not None else None


class TeamSignalDetector:
    """Detect team-domain evolution signals from user input and trajectories."""

    def __init__(
        self,
        *,
        llm: Model,
        model: str,
        language: str = "cn",
        llm_policy: Optional["LLMInvokePolicy"] = None,
        trajectory_issue_llm_policy: Optional["LLMInvokePolicy"] = None,
        user_intent_llm_policy: Optional["LLMInvokePolicy"] = None,
    ) -> None:
        policy = llm_policy or trajectory_issue_llm_policy or user_intent_llm_policy
        if policy is None:
            raise ValueError("TeamSignalDetector requires at least one LLM policy")
        self._llm = llm
        self._model = model
        self._language = language
        self._trajectory_issue_llm_policy = trajectory_issue_llm_policy or policy
        self._user_intent_llm_policy = user_intent_llm_policy or policy

    async def detect_user_intent(
        self,
        *,
        messages: list[dict[str, Any]],
        team_skill_content: str,
    ) -> Optional[UserIntent]:
        """Detect whether recent user messages contain team-skill improvement intent."""
        from openjiuwen.agent_evolving.optimizer.llm_resilience import invoke_text_with_retry

        # Handle both dict and Pydantic model objects (SystemMessage, AssistantMessage, etc.)
        user_msgs = []
        for m in messages[-10:]:
            if isinstance(m, dict):
                role = m.get("role", "")
                if role == "user":
                    user_msgs.append(str(m.get("content", "")))
            else:
                # Pydantic model: use attribute access
                role = getattr(m, "role", "")
                if role == "user":
                    content = getattr(m, "content", "")
                    user_msgs.append(str(content))

        if not user_msgs:
            return None

        user_text = "\n".join(user_msgs)
        prompt_template = (
            _TEAM_USER_REQUEST_PROMPT_CN
            if self._language == "cn"
            else _TEAM_USER_REQUEST_PROMPT_EN
        )
        prompt = prompt_template.format(
            team_skill_description=team_skill_content[:1000] if team_skill_content else "",
            roles=_extract_roles_summary(team_skill_content),
            user_messages=user_text[:2000],
        )

        try:
            raw = await invoke_text_with_retry(
                llm=self._llm,
                model=self._model,
                prompt=prompt,
                policy=self._user_intent_llm_policy,
                is_result_usable=lambda text: isinstance(parse_team_model_json(text), dict),
            )
        except Exception as exc:
            logger.warning("[TeamSignalDetector] detect_user_intent failed: %s", exc)
            raise

        parsed = parse_team_model_json(raw)
        if isinstance(parsed, dict) and parsed.get("is_improvement"):
            return UserIntent(
                is_improvement=True,
                intent=str(parsed.get("intent", "") or ""),
            )
        return None

    async def detect_trajectory_signals(
        self,
        *,
        trajectory: Trajectory,
        skill_name: str,
        skill_content: str,
    ) -> list[EvolutionSignal]:
        """Analyze a team trajectory and return standard passive evolution signals."""
        issues = await self.detect_trajectory_issues(
            trajectory=trajectory,
            skill_content=skill_content,
        )
        if not issues:
            return []
        return [
            make_team_trajectory_signal(
                skill_name=skill_name,
                skill_content=skill_content,
                trajectory_issues=issues,
            )
        ]

    async def detect_trajectory_issues(
        self,
        *,
        trajectory: Trajectory,
        skill_content: str,
    ) -> list[dict[str, str]]:
        """Return normalized medium/high severity trajectory issues."""
        from openjiuwen.agent_evolving.optimizer.llm_resilience import invoke_text_with_retry

        trajectory_summary = build_team_trajectory_summary(trajectory)
        prompt_template = (
            _TEAM_TRAJECTORY_ISSUE_PROMPT_CN
            if self._language == "cn"
            else _TEAM_TRAJECTORY_ISSUE_PROMPT_EN
        )
        prompt = prompt_template.format(
            skill_content=skill_content[:10000],
            trajectory_summary=trajectory_summary,
        )

        try:
            raw = await invoke_text_with_retry(
                llm=self._llm,
                model=self._model,
                prompt=prompt,
                policy=self._trajectory_issue_llm_policy,
                is_result_usable=lambda text: isinstance(parse_team_model_json(text), list),
            )
        except Exception as exc:
            logger.warning("[TeamSignalDetector] detect_trajectory_issues failed: %s", exc)
            raise

        parsed = parse_team_model_json(raw)
        if not parsed or not isinstance(parsed, list):
            return []

        issues: list[dict[str, str]] = []
        for item in parsed:
            normalized = self._normalize_issue(item)
            if normalized is None:
                continue
            if normalized["severity"] in ("medium", "high"):
                issues.append(normalized)
        return issues

    @staticmethod
    def _normalize_issue(item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None

        severity = str(item.get("severity", "medium") or "medium")
        if severity not in ("low", "medium", "high"):
            severity = "medium"

        return {
            "issue_type": str(item.get("issue_type", "unknown") or "unknown"),
            "description": str(item.get("description", "") or ""),
            "affected_role": str(item.get("affected_role", "") or ""),
            "severity": severity,
        }


__all__ = [
    "TeamSignalDetector",
    "TeamSignalType",
    "TrajectoryIssue",
    "UserIntent",
    "build_team_trajectory_summary",
    "get_team_signal_skill_content",
    "get_team_trajectory_issues",
    "make_team_trajectory_signal",
    "make_team_user_intent_signal",
    "parse_team_model_json",
]
