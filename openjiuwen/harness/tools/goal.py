# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""submit_goal_report tool implementation.

Registered only during goal rounds. The tool does NOT write GoalRecord
directly; it saves the report to the GoalReportSink so that the
TaskCompletionRail can consume it after the round ends.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from openjiuwen.core.foundation.tool import Input, Output, Tool
from openjiuwen.harness.goal.schema import GoalAssessment, GoalAssessmentStatus
from openjiuwen.harness.prompts.tools import build_tool_card

if TYPE_CHECKING:
    from openjiuwen.harness.goal.manager import GoalManager

logger = logging.getLogger(__name__)


class GoalReportSink:
    """Thread-safe container for the current attempt's goal report.

    The sink is created per goal round and consumed by TaskCompletionRail
    after the round ends.
    """

    def __init__(self) -> None:
        self._report: Optional[GoalAssessment] = None
        self._session_id: Optional[str] = None
        self._goal_id: Optional[str] = None
        self._revision: Optional[int] = None
        self._attempt_index: int = 0

    @property
    def report(self) -> Optional[GoalAssessment]:
        return self._report

    @property
    def goal_id(self) -> Optional[str]:
        return self._goal_id

    @property
    def revision(self) -> Optional[int]:
        return self._revision

    @property
    def attempt_index(self) -> int:
        return self._attempt_index

    def begin_attempt(
        self,
        session_id: str,
        goal_id: str,
        revision: int,
        attempt_index: int,
    ) -> None:
        """Reset the sink for a new attempt."""
        self._report = None
        self._session_id = session_id
        self._goal_id = goal_id
        self._revision = revision
        self._attempt_index = attempt_index

    def submit(self, assessment: GoalAssessment) -> None:
        """Store the report (last-write wins within a single attempt)."""
        self._report = assessment
        logger.info(
            "[GoalReportSink] Report submitted: status=%s, goal_id=%s, revision=%s",
            assessment.status.value,
            self._goal_id,
            self._revision,
        )

    def consume(self) -> Optional[GoalAssessment]:
        """Return and clear the stored report."""
        report = self._report
        self._report = None
        return report


class SubmitGoalReportTool(Tool):
    """Tool that saves a structured goal report to the GoalReportSink."""

    def __init__(
        self,
        report_sink: GoalReportSink,
        language: str = "cn",
        agent_id: Optional[str] = None,
    ) -> None:
        card = build_tool_card(
            "submit_goal_report",
            "submit_goal_report",
            language=language,
            agent_id=agent_id,
        )
        super().__init__(card)
        self._sink = report_sink

    async def invoke(self, inputs: Input, **kwargs: Any) -> Output:
        """Process a submit_goal_report call."""
        params = inputs if isinstance(inputs, dict) else {}
        status_str = params.get("status", "continue")
        evidence = params.get("evidence", "")
        remaining_work = params.get("remaining_work")
        next_instruction = params.get("next_instruction")

        try:
            status = GoalAssessmentStatus(status_str)
        except ValueError:
            logger.warning(
                "[SubmitGoalReport] Invalid status %r, normalizing to continue",
                status_str,
            )
            status = GoalAssessmentStatus.CONTINUE

        assessment = GoalAssessment(
            status=status,
            evidence=evidence,
            remaining_work=remaining_work,
            next_instruction=next_instruction,
        )
        self._sink.submit(assessment)

        return {
            "result": "report_accepted",
            "status": status.value,
        }

    async def stream(self, inputs: Input, **kwargs: Any) -> AsyncIterator[Output]:
        result = await self.invoke(inputs, **kwargs)
        yield result


class GetCurrentGoalTool(Tool):
    """Tool that returns the current session goal so the model can re-orient.

    The main model loses the ``<goal_task>`` context on non-goal turns (e.g.
    when the user interjects a normal question).  This read-only tool lets the
    model recover the active objective, status, attempt count, and last
    assessment on demand instead of guessing from stale conversation history.
    """

    def __init__(
        self,
        goal_manager: GoalManager,
        language: str = "cn",
        agent_id: Optional[str] = None,
    ) -> None:
        card = build_tool_card(
            "get_current_goal",
            "get_current_goal",
            language=language,
            agent_id=agent_id,
        )
        super().__init__(card)
        self._goal_manager = goal_manager
        self._language = language

    async def invoke(self, inputs: Input, **kwargs: Any) -> Output:
        """Return the current goal record summary (read-only)."""
        record = None
        try:
            record = await self._goal_manager.get()
        except Exception:
            logger.debug("[GetCurrentGoal] Failed to load goal record", exc_info=True)
            record = None

        if record is None:
            return {
                "has_goal": False,
                "message": (
                    "当前会话没有设置持续目标。"
                    if self._language == "cn"
                    else "No persistent goal is set for this session."
                ),
            }

        assessment = record.last_assessment
        return {
            "has_goal": True,
            "goal_id": record.goal_id,
            "objective": record.objective,
            "status": record.status.value,
            "attempt_count": record.attempt_count,
            "last_assessment": assessment.to_dict() if assessment else None,
        }

    async def stream(self, inputs: Input, **kwargs: Any) -> AsyncIterator[Output]:
        result = await self.invoke(inputs, **kwargs)
        yield result


__all__ = ["GetCurrentGoalTool", "GoalReportSink", "SubmitGoalReportTool"]
