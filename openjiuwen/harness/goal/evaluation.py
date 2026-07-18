# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""GoalEvaluator — evaluate goal completion using configurable strategies.

Implements AGENT_REPORT, TRANSCRIPT, and HYBRID strategies. The default
HYBRID strategy trusts ``continue`` reports to reduce cost but verifies
``complete`` and ``blocked`` via transcript assessment.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalRecord,
    GoalStopConfig,
    GoalStopStrategy,
)

logger = logging.getLogger(__name__)

_FALLBACK_CONTINUE = GoalAssessment(
    status=GoalAssessmentStatus.CONTINUE,
    evidence="assessment_error: no reliable assessment available, treating as continue.",
    remaining_work="Continue making verifiable progress toward the objective.",
    next_instruction="Continue advancing the objective with verifiable evidence.",
)

_JSON_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _parse_assessment_json(text: str) -> Optional[GoalAssessment]:
    """Parse JSON from raw assessor output, with fenced-block fallback."""
    text = text.strip()
    data = None

    try:
        data = json.loads(text)
    except ValueError:
        match = _JSON_BLOCK_PATTERN.search(text)
        if match:
            try:
                data = json.loads(match.group(1).strip())
            except ValueError:
                data = None

    if not isinstance(data, dict):
        return None

    status_str = data.get("status")
    evidence = data.get("evidence")
    if not status_str or not evidence:
        return None

    valid_statuses = {"continue", "complete", "blocked"}
    if status_str not in valid_statuses:
        return None

    return GoalAssessment(
        status=GoalAssessmentStatus(status_str),
        evidence=evidence,
        remaining_work=data.get("remaining_work") or None,
        next_instruction=data.get("next_instruction") or None,
    )


class GoalEvaluator:
    """Evaluate goal completion using the configured stop strategy.

    The assessor does not write GoalRecord; it only produces a GoalAssessment
    that the TaskCompletionRail uses to update state.
    """

    def __init__(self, config: Optional[GoalStopConfig] = None) -> None:
        self._config = config or GoalStopConfig()

    @property
    def strategy(self) -> GoalStopStrategy:
        return self._config.strategy

    def assess(
        self,
        record: GoalRecord,
        agent_report: Optional[GoalAssessment] = None,
        final_output: str = "",
        transcript_response: Optional[str] = None,
    ) -> GoalAssessment:
        """Run the configured assessment strategy.

        Args:
            record: Current GoalRecord.
            agent_report: Report from the main agent via submit_goal_report.
            final_output: Deprecated; no longer used by transcript assessment.
            transcript_response: Raw output from the transcript assessor model,
                if pre-fetched by the caller.

        Returns:
            GoalAssessment with the resolved status.
        """
        _ = final_output
        strategy = self._config.strategy

        if strategy is GoalStopStrategy.AGENT_REPORT:
            assessment = self._assess_agent_report(agent_report)
        elif strategy is GoalStopStrategy.TRANSCRIPT:
            assessment = self._assess_transcript(transcript_response)
        else:
            assessment = self._assess_hybrid(
                record, agent_report, transcript_response,
            )

        return self._apply_hard_limits(record, assessment)

    # -- Strategy implementations --

    def _assess_agent_report(
        self,
        agent_report: Optional[GoalAssessment],
    ) -> GoalAssessment:
        """AGENT_REPORT: trust the main agent's report directly."""
        if agent_report is None:
            logger.info("[GoalEvaluator] AGENT_REPORT: no report, fallback continue")
            return GoalAssessment(
                status=GoalAssessmentStatus.CONTINUE,
                evidence="no_report: no submit_goal_report was accepted for this attempt.",
                remaining_work="Submit a valid goal report for this attempt.",
                next_instruction="Continue the goal attempt and submit a structured report before finishing.",
            )
        return self._normalize_report(agent_report)

    @staticmethod
    def _assess_transcript(
        transcript_response: Optional[str],
    ) -> GoalAssessment:
        """TRANSCRIPT: parse the isolated assessor model's JSON response."""
        if transcript_response is None:
            logger.info("[GoalEvaluator] TRANSCRIPT: no response, fallback continue")
            return _FALLBACK_CONTINUE

        parsed = _parse_assessment_json(transcript_response)
        if parsed is None:
            logger.warning(
                "[GoalEvaluator] TRANSCRIPT: failed to parse response, fallback continue"
            )
            return _FALLBACK_CONTINUE
        return parsed

    def _assess_hybrid(
        self,
        record: GoalRecord,
        agent_report: Optional[GoalAssessment],
        transcript_response: Optional[str],
    ) -> GoalAssessment:
        """HYBRID: trust continue from agent; verify terminal statuses.

        Rules:
        1. No agent report → use transcript assessor.
        2. Transcript assessor fails → fallback continue.
        3. Agent report CONTINUE → trust directly (cost savings).
        4. Agent report COMPLETE or BLOCKED → verify via transcript when available.
        5. verification_interval hit → spot-check even for CONTINUE.
        6. Conflict → transcript assessor wins.
        7. Transcript verification fails on terminal report → fallback continue.
        8. Transcript not invoked (response is None) for terminal report → fallback continue.
        """
        if agent_report is None:
            logger.info("[GoalEvaluator] HYBRID: no agent report, using transcript")
            return self._assess_transcript(transcript_response)

        normalized = self._normalize_report(agent_report)

        if normalized.status is GoalAssessmentStatus.CONTINUE:
            if self.should_spot_check(record):
                logger.info(
                    "[GoalEvaluator] HYBRID: spot-check triggered at attempt %d",
                    record.attempt_count,
                )
                return self._verify_with_transcript(
                    normalized, transcript_response, trust_on_failure=True,
                )
            return normalized

        # COMPLETE or BLOCKED: require transcript verification. The caller must
        # invoke the transcript assessor before calling assess(); if no response
        # is available, continue conservatively rather than trusting self-report.
        if transcript_response is None:
            logger.info(
                "[GoalEvaluator] HYBRID: agent reported %s, transcript not invoked, "
                "falling back to continue",
                normalized.status.value,
            )
            return self._verify_with_transcript(
                normalized, transcript_response, trust_on_failure=False,
            )

        logger.info(
            "[GoalEvaluator] HYBRID: agent reported %s, verifying via transcript",
            normalized.status.value,
        )
        return self._verify_with_transcript(
            normalized, transcript_response, trust_on_failure=False,
        )

    @staticmethod
    def _verify_with_transcript(
        agent_assessment: GoalAssessment,
        transcript_response: Optional[str],
        *,
        trust_on_failure: bool,
    ) -> GoalAssessment:
        """Verify agent assessment against transcript assessor.

        Args:
            agent_assessment: The normalized agent report.
            transcript_response: Raw transcript assessor response.
            trust_on_failure: If True, fall back to agent assessment when
                transcript parsing fails; if False, fall back to continue.
        """
        if transcript_response is None:
            if trust_on_failure:
                return agent_assessment
            logger.info("[GoalEvaluator] transcript unavailable, fallback continue")
            return GoalAssessment(
                status=GoalAssessmentStatus.CONTINUE,
                evidence=(
                    f"transcript_unavailable: agent reported {agent_assessment.status.value} "
                    f"but transcript assessor was not invoked for verification."
                ),
                remaining_work=agent_assessment.remaining_work,
                next_instruction=agent_assessment.next_instruction,
            )

        transcript_result = _parse_assessment_json(transcript_response)
        if transcript_result is None:
            if trust_on_failure:
                return agent_assessment
            logger.warning(
                "[GoalEvaluator] transcript parse failed, fallback continue"
            )
            return GoalAssessment(
                status=GoalAssessmentStatus.CONTINUE,
                evidence=(
                    f"assessment_error: agent reported {agent_assessment.status.value} "
                    f"but transcript assessor response could not be parsed."
                ),
                remaining_work=agent_assessment.remaining_work,
                next_instruction=agent_assessment.next_instruction,
            )

        if transcript_result.status != agent_assessment.status:
            logger.info(
                "[GoalEvaluator] conflict: agent=%s, transcript=%s, using transcript",
                agent_assessment.status.value,
                transcript_result.status.value,
            )
        return transcript_result

    def should_spot_check(self, record: GoalRecord) -> bool:
        """Determine if a CONTINUE report should be spot-checked."""
        interval = self._config.verification_interval
        if interval is None or interval <= 0:
            return False
        return record.attempt_count > 0 and record.attempt_count % interval == 0

    @staticmethod
    def _normalize_report(report: GoalAssessment) -> GoalAssessment:
        """Normalize an agent report to a valid assessment."""
        try:
            GoalAssessmentStatus(report.status.value)
        except ValueError:
            logger.warning(
                "[GoalEvaluator] Invalid report status %r, normalizing to continue",
                report.status,
            )
            return GoalAssessment(
                status=GoalAssessmentStatus.CONTINUE,
                evidence=f"normalized_invalid_status: original was {report.status}",
                remaining_work=report.remaining_work,
                next_instruction=report.next_instruction,
            )
        return report

    def _apply_hard_limits(
        self,
        record: GoalRecord,
        assessment: GoalAssessment,
    ) -> GoalAssessment:
        """Apply hard limits after assessment. Only CONTINUE can be overridden."""
        if assessment.status is not GoalAssessmentStatus.CONTINUE:
            return assessment

        effective_max = self._config.max_attempts or record.max_attempts
        effective_budget = self._config.token_budget or record.token_budget

        if effective_max is not None and record.attempt_count >= effective_max:
            logger.info(
                "[GoalEvaluator] max_attempts exhausted: %d/%d",
                record.attempt_count,
                effective_max,
            )
            return GoalAssessment(
                status=GoalAssessmentStatus.BLOCKED,
                evidence=f"max_attempts_exhausted: {record.attempt_count}/{effective_max}",
                remaining_work=assessment.remaining_work,
                next_instruction=assessment.next_instruction,
            )

        if (
            effective_budget is not None
            and record.token_usage.total_tokens >= effective_budget
        ):
            logger.info(
                "[GoalEvaluator] token_budget exhausted: %d/%d",
                record.token_usage.total_tokens,
                effective_budget,
            )
            return GoalAssessment(
                status=GoalAssessmentStatus.BLOCKED,
                evidence=(
                    f"token_budget_exhausted: "
                    f"{record.token_usage.total_tokens}/{effective_budget}"
                ),
                remaining_work=assessment.remaining_work,
                next_instruction=assessment.next_instruction,
            )

        return assessment


__all__ = ["GoalEvaluator"]
