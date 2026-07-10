# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Signals for automatic Skill creation suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.trajectory import TrajectoryBuilder

SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE = "prompt_eligible"
SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER = "skill_tool_cover"
FIRST_PROMPT_TOOL_ITERATION_THRESHOLD = 6
FIRST_PROMPT_TOOL_CALL_THRESHOLD = 10
REPROMPT_TOOL_ITERATION_THRESHOLD = 2
REPROMPT_TOOL_CALL_THRESHOLD = 4

_EXCLUDED_TOOL_NAMES = {
    "ask_user",
    "skill_tool",
    "prepare_skill_evolution",
    "evolve_review_task",
    "evolve_skill_experiences",
    "spawn_member",
    "spawn_teammate",
    "spawn_human_agent",
    "spawn_bridge_agent",
    "spawn_external_cli",
    "send_message",
    "view_task",
}
_EXCLUDED_TOOL_KEYWORDS = (
    "follow_up",
    "followup",
    "heartbeat",
    "cron",
    "background",
)


@dataclass(frozen=True)
class SkillCreationWindowMetrics:
    """Windowed metrics used to detect Skill creation follow-up signals."""

    total_raw_tool_calls: int
    window_effective_tool_calling_iterations: int
    window_effective_tool_calls: int
    total_effective_tool_calling_iterations: int
    total_effective_tool_calls: int
    skill_tool_used_in_window: bool


@dataclass(frozen=True)
class SkillCreationSignal:
    """Detected skill-creation signal from tool trajectory metrics."""

    signal_type: str
    metrics: SkillCreationWindowMetrics
    reason: str


class SkillCreationSignalDetector:
    """Detect Skill creation signals from trajectory tool windows."""

    def collect_metrics(
        self,
        builder: TrajectoryBuilder | None,
        *,
        raw_tool_call_watermark: int = 0,
    ) -> SkillCreationWindowMetrics:
        """Collect deterministic effective metrics for skill-creation triggers."""
        if builder is None:
            return SkillCreationWindowMetrics(0, 0, 0, 0, 0, False)

        tool_steps = [step for step in builder.steps if step.kind == "tool" and step.detail]
        total_raw_tool_calls = len(tool_steps)
        watermark = max(0, raw_tool_call_watermark)
        window_tool_steps = tool_steps[watermark:]

        effective_call_ids_after_watermark: set[str] = set()
        window_effective_tool_calls = 0
        total_effective_tool_calls = 0
        skill_tool_used_in_window = False
        for index, step in enumerate(tool_steps):
            tool_name = getattr(step.detail, "tool_name", "")
            if index >= watermark and normalize_tool_name(tool_name) == "skill_tool":
                skill_tool_used_in_window = True
            if not is_effective_task_tool(tool_name):
                continue
            total_effective_tool_calls += 1
            if index >= watermark:
                window_effective_tool_calls += 1
                tool_call_id = getattr(step.detail, "tool_call_id", None)
                if tool_call_id:
                    effective_call_ids_after_watermark.add(str(tool_call_id))

        total_effective_tool_calling_iterations = count_tool_calling_iterations(builder)
        window_effective_tool_calling_iterations = _count_new_effective_tool_calling_iterations(
            builder,
            effective_call_ids_after_watermark,
            window_tool_steps,
        )

        return SkillCreationWindowMetrics(
            total_raw_tool_calls=total_raw_tool_calls,
            window_effective_tool_calling_iterations=window_effective_tool_calling_iterations,
            window_effective_tool_calls=window_effective_tool_calls,
            total_effective_tool_calling_iterations=total_effective_tool_calling_iterations,
            total_effective_tool_calls=total_effective_tool_calls,
            skill_tool_used_in_window=skill_tool_used_in_window,
        )

    def detect(
        self,
        builder: TrajectoryBuilder | None,
        *,
        raw_tool_call_watermark: int = 0,
        prompted_snapshot: tuple[int, int] | None = None,
        metrics: SkillCreationWindowMetrics | None = None,
    ) -> list[SkillCreationSignal]:
        """Detect Skill creation signals for the current trajectory window.

        When metrics is provided, detection uses that snapshot directly and
        does not rescan the builder or apply raw_tool_call_watermark again.
        """
        if builder is None:
            return []
        current_metrics = metrics or self.collect_metrics(
            builder,
            raw_tool_call_watermark=raw_tool_call_watermark,
        )

        if current_metrics.skill_tool_used_in_window:
            return [
                SkillCreationSignal(
                    signal_type=SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER,
                    metrics=current_metrics,
                    reason="skill_tool_used",
                )
            ]

        if prompted_snapshot is None:
            if (
                current_metrics.window_effective_tool_calling_iterations
                >= FIRST_PROMPT_TOOL_ITERATION_THRESHOLD
                or current_metrics.window_effective_tool_calls >= FIRST_PROMPT_TOOL_CALL_THRESHOLD
            ):
                return [
                    SkillCreationSignal(
                        signal_type=SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE,
                        metrics=current_metrics,
                        reason="first_prompt_threshold",
                    )
                ]
            return []

        prompted_iterations, prompted_calls = prompted_snapshot
        new_iterations = max(
            0,
            current_metrics.total_effective_tool_calling_iterations - prompted_iterations,
        )
        new_calls = max(0, current_metrics.total_effective_tool_calls - prompted_calls)
        if (
            new_iterations >= REPROMPT_TOOL_ITERATION_THRESHOLD
            or new_calls >= REPROMPT_TOOL_CALL_THRESHOLD
        ):
            return [
                SkillCreationSignal(
                    signal_type=SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE,
                    metrics=current_metrics,
                    reason="reprompt_threshold",
                )
            ]
        return []


def normalize_tool_name(tool_name: str) -> str:
    """Normalize namespaced tool names to their base name."""
    tool = (tool_name or "").strip()
    if "." in tool:
        tool = tool.rsplit(".", 1)[-1]
    return tool


def is_effective_task_tool(tool_name: str) -> bool:
    """Return True for tools that represent task execution work."""
    tool = normalize_tool_name(tool_name)
    if not tool:
        return False
    if tool in _EXCLUDED_TOOL_NAMES:
        return False
    return not any(keyword in tool for keyword in _EXCLUDED_TOOL_KEYWORDS)


def count_tool_calling_iterations(builder: TrajectoryBuilder | None) -> int:
    """Count LLM iterations that requested at least one effective task tool."""
    if builder is None:
        return 0

    count = 0
    for step in builder.steps:
        if step.kind != "llm" or not step.detail:
            continue
        response = getattr(step.detail, "response", None)
        if _response_has_effective_tool_calls(response):
            count += 1
    return count


def _count_new_effective_tool_calling_iterations(
    builder: TrajectoryBuilder,
    effective_call_ids_after_watermark: set[str],
    new_tool_steps: list[Any],
) -> int:
    if not new_tool_steps:
        return 0

    if effective_call_ids_after_watermark:
        count = 0
        for step in builder.steps:
            if step.kind != "llm" or not step.detail:
                continue
            response = getattr(step.detail, "response", None)
            if _response_has_tool_call_id(response, effective_call_ids_after_watermark):
                count += 1
        return count

    # Fallback for trajectories without tool_call_id links: count effective
    # tool steps as distinct iterations rather than mixing raw and effective indexes.
    return sum(
        1
        for step in new_tool_steps
        if is_effective_task_tool(getattr(step.detail, "tool_name", ""))
    )


def _response_has_effective_tool_calls(response: Any) -> bool:
    return any(is_effective_task_tool(_tool_call_name(tool_call)) for tool_call in _iter_tool_calls(response))


def _response_has_tool_call_id(response: Any, tool_call_ids: set[str]) -> bool:
    for tool_call in _iter_tool_calls(response):
        tool_call_id = _tool_call_id(tool_call)
        if tool_call_id is not None and str(tool_call_id) in tool_call_ids:
            return True
    return False


def _iter_tool_calls(response: Any) -> list[Any]:
    if response is None:
        return []
    if isinstance(response, dict):
        return list(response.get("tool_calls") or [])
    return list(getattr(response, "tool_calls", None) or [])


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function.get("name"))
        return str(tool_call.get("name") or "")

    function = getattr(tool_call, "function", None)
    function_name = getattr(function, "name", None)
    if function_name:
        return str(function_name)
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        value = tool_call.get("id")
    else:
        value = getattr(tool_call, "id", None)
    if value is None:
        return None
    return str(value)


__all__ = [
    "SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE",
    "SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER",
    "SkillCreationSignal",
    "SkillCreationSignalDetector",
    "SkillCreationWindowMetrics",
]
