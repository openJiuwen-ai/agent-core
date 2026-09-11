# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Core coordination for reviewer-feedback-driven Team Skill evolution.

The coordinator owns task-level attribution, terminal team aggregation,
repetition policy, and ordering. A mounted TeamSkillEvolutionRail supplies the
standard evolution pipeline and host events; product runtimes do not
reimplement evolution policy or persistence.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.signal import (
    ReviewFeedbackAction,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
)
from openjiuwen.agent_evolving.trajectory.messages import trajectory_to_messages
from openjiuwen.agent_evolving.trajectory.spans import merge_spans, trim_trajectory
from openjiuwen.agent_evolving.trajectory.team import select_member_spans
from openjiuwen.core.common.logging import logger

SKILL_CREATION_EVENTS = "skill_creation"

_SAFE_MEMBER_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_AGGREGATED_FEEDBACK_CHARS = 16_000

RailProvider = Callable[[], Any | None]
EventSink = Callable[[str, Sequence[Any]], Awaitable[None]]
BoolProvider = Callable[[], bool]
FloatProvider = Callable[[], float]


@dataclass(frozen=True)
class TaskFeedbackObservation:
    """One task-level attribution retained for the terminal team pass."""

    task_id: str
    review_round: int
    assignee: str
    feedback: str
    skill_name: str
    signal: Any


@dataclass(frozen=True)
class NewSkillPatternObservation:
    """One reusable no-existing-Skill pattern retained across task reviews."""

    task_id: str
    review_round: int
    assignee: str
    feedback: str
    reusable_guidance: str
    reason: str
    confidence: float
    action: ReviewFeedbackAction
    supporting_evidence: tuple[str, ...]


class ReviewFeedbackEvolutionCoordinator:
    """Translate task feedback, then evolve Team Skills at team completion.

    The class is deliberately transport- and product-agnostic. Its owning team
    Rail supplies the evolution target and an event sink; the product runtime
    only mounts that owner and transports the resulting standard host events.
    """

    def __init__(
        self,
        *,
        session_id: str,
        team_id: str,
        team_rail_provider: RailProvider,
        skill_create_rail_provider: RailProvider | None = None,
        event_sink: EventSink | None = None,
        enabled: bool | BoolProvider = True,
        min_confidence: float | FloatProvider = 0.7,
    ) -> None:
        self._session_id = str(session_id or "")
        self._team_id = str(team_id or "")
        self._team_rail_provider = team_rail_provider
        self._skill_create_rail_provider = skill_create_rail_provider
        self._event_sink = event_sink
        self._enabled = enabled
        self._min_confidence = min_confidence
        self._processed: set[tuple[str, int, str]] = set()
        self._lock = asyncio.Lock()
        self._task_attribution_lock = asyncio.Lock()
        self._team_evolution_lock = asyncio.Lock()
        self._observations: list[TaskFeedbackObservation] = []
        self._new_skill_patterns: list[NewSkillPatternObservation] = []
        self._team_observation_cursor = 0
        self._new_skill_pattern_cursor = 0

    async def __call__(self, payload: dict[str, Any]) -> None:
        """Process one settled failed-review payload from ``TeamScheduler``."""
        if not self._is_enabled():
            return

        task_id = str(payload.get("task_id") or "").strip()
        review_round = int(payload.get("review_round") or 0)
        feedback = str(payload.get("feedback") or "").strip()
        assignee = str(payload.get("assignee") or "").strip()
        if not task_id or not feedback or not self._is_safe_member_name(assignee):
            if task_id and feedback:
                logger.warning(
                    "[ReviewFeedbackEvolution] task feedback skipped because assignee "
                    "is missing or unsafe: task=%s assignee=%r",
                    task_id,
                    assignee,
                )
            return
        key = (task_id, review_round, feedback)
        async with self._lock:
            if key in self._processed:
                return
            self._processed.add(key)

        team_rail = self._team_evolution_rail()
        if team_rail is None:
            logger.warning(
                "[ReviewFeedbackEvolution] no Team Skill rail: session=%s task=%s",
                self._session_id,
                task_id,
            )
            return

        # Several tasks may fail close together. Serialize attribution so the
        # shared model and accumulated repetition evidence stay ordered.
        async with self._task_attribution_lock:
            trajectory = self._get_member_trajectory(assignee)
            task_objective = self._task_objective(payload)
            prior_pattern_evidence = tuple(
                self._format_new_skill_pattern_evidence(item)
                for item in self._new_skill_patterns
                if item.task_id != task_id
            )
            prior_pattern_task_count = len(
                {item.task_id for item in self._new_skill_patterns if item.task_id != task_id}
            )
            context = await ReviewFeedbackContextBuilder(store=team_rail.evolution_store).build(
                task_id=task_id,
                review_round=review_round,
                task_objective=task_objective,
                trajectory=trajectory,
                repetition_count=prior_pattern_task_count + 1,
                repeated_pattern_evidence=prior_pattern_evidence,
            )
            attributor = ReviewFeedbackAttributor(
                llm=team_rail.evolver.llm,
                model=team_rail.evolver.model,
                language=getattr(team_rail, "_language", "cn"),
            )
            attribution = await attributor.attribute(feedback, context=context)
            logger.info(
                "[ReviewFeedbackEvolution] task=%s assignee=%s round=%s action=%s "
                "classification=%s skill=%s confidence=%.2f reason=%s",
                task_id,
                assignee,
                review_round,
                attribution.action.value,
                attribution.classification.value,
                attribution.skill_name,
                attribution.confidence,
                attribution.reason,
            )

            threshold = self._confidence_threshold()
            if attribution.classification == ReviewFeedbackClassification.NEW_SKILL_PATTERN:
                if attribution.reusable_guidance and attribution.confidence >= threshold:
                    matching_patterns = self._matching_new_skill_patterns(
                        attribution.reusable_guidance,
                        exclude_task_id=task_id,
                    )
                    matching_evidence = tuple(
                        self._format_new_skill_pattern_evidence(item) for item in matching_patterns
                    )
                    pattern_action = (
                        ReviewFeedbackAction.SUGGEST_NEW_SKILL
                        if (attribution.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL and matching_patterns)
                        else ReviewFeedbackAction.SKIP_UNATTRIBUTED
                    )
                    self._new_skill_patterns.append(
                        NewSkillPatternObservation(
                            task_id=task_id,
                            review_round=review_round,
                            assignee=assignee,
                            feedback=feedback,
                            reusable_guidance=attribution.reusable_guidance,
                            reason=attribution.reason,
                            confidence=attribution.confidence,
                            action=pattern_action,
                            supporting_evidence=matching_evidence,
                        )
                    )
                return

            if attribution.action != ReviewFeedbackAction.EVOLVE_EXISTING_SKILL:
                return
            if attribution.confidence < threshold:
                logger.info(
                    "[ReviewFeedbackEvolution] actionable attribution below threshold: %.2f < %.2f",
                    attribution.confidence,
                    threshold,
                )
                return

            signal = attribution_to_evolution_signal(
                attribution,
                task_id=task_id,
                review_round=review_round,
            )
            if signal is None or not attribution.skill_name:
                return
            self._observations.append(
                TaskFeedbackObservation(
                    task_id=task_id,
                    review_round=review_round,
                    assignee=assignee,
                    feedback=feedback,
                    skill_name=attribution.skill_name,
                    signal=signal,
                )
            )
            logger.info(
                "[ReviewFeedbackEvolution] retained task observation: task=%s member=%s skill=%s",
                task_id,
                assignee,
                attribution.skill_name,
            )

    async def on_team_completed(self, _payload: dict[str, Any] | None = None) -> bool:
        """Aggregate task feedback into Team Skills or creation proposals."""
        if not self._is_enabled():
            return False

        async with self._team_evolution_lock:
            end = len(self._observations)
            observations = self._observations[self._team_observation_cursor:end]
            pattern_end = len(self._new_skill_patterns)
            pattern_observations = self._new_skill_patterns[self._new_skill_pattern_cursor:pattern_end]
            if not observations and not pattern_observations:
                return False

            team_rail = self._team_evolution_rail()
            if team_rail is None and observations:
                logger.warning("[ReviewFeedbackEvolution] team aggregation skipped: no Team Skill rail")
                return False

            grouped: dict[str, list[TaskFeedbackObservation]] = {}
            for observation in observations:
                if team_rail is not None and team_rail.evolution_store.skill_exists(observation.skill_name):
                    grouped.setdefault(observation.skill_name, []).append(observation)
                else:
                    logger.info(
                        "[ReviewFeedbackEvolution] unknown Team Skill omitted from aggregation: %s",
                        observation.skill_name,
                    )

            attempted = await self._evolve_team_groups(team_rail, grouped)
            if pattern_observations:
                attempted = await self._route_new_skill_patterns(pattern_observations) or attempted

            self._team_observation_cursor = end
            self._new_skill_pattern_cursor = pattern_end
            return attempted

    async def _evolve_team_groups(
        self,
        team_rail: Any | None,
        grouped: dict[str, list[TaskFeedbackObservation]],
    ) -> bool:
        if not grouped or team_rail is None:
            return False
        trajectory = self._get_team_trajectory()
        messages = trajectory_to_messages(trajectory) if trajectory is not None else []
        attempted = False
        for skill_name, skill_observations in grouped.items():
            try:
                result = await team_rail.evolve_from_external_signals(
                    signals=[item.signal for item in skill_observations],
                    messages=messages,
                    trajectory=trajectory,
                    user_query=self._format_aggregated_feedback(skill_observations),
                    requires_approval=not team_rail.auto_save,
                )
                attempted = True
                logger.info(
                    "[ReviewFeedbackEvolution] Team aggregate result: skill=%s "
                    "task_feedback_count=%d status=%s request=%s",
                    skill_name,
                    len(skill_observations),
                    result.status,
                    getattr(getattr(result, "request", None), "request_id", None),
                )
            except Exception as exc:
                logger.warning(
                    "[ReviewFeedbackEvolution] Team aggregate failed for skill=%s: %s",
                    skill_name,
                    exc,
                    exc_info=True,
                )
        return attempted

    async def _route_new_skill_patterns(
        self,
        observations: list[NewSkillPatternObservation],
    ) -> bool:
        suggestions = [item for item in observations if item.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL]
        if not suggestions:
            return False

        creation_rail = self._team_skill_create_rail()
        if creation_rail is None:
            logger.info("[ReviewFeedbackEvolution] repeated pattern detected but Skill creation Rail is unavailable")
            return False

        attempted = False
        routed_keys: set[str] = set()
        for suggestion in suggestions:
            proposal_key = self._new_skill_proposal_key(suggestion.reusable_guidance)
            if not proposal_key or proposal_key in routed_keys:
                continue
            routed_keys.add(proposal_key)
            evidence = tuple(
                dict.fromkeys(
                    (
                        *suggestion.supporting_evidence,
                        self._format_new_skill_pattern_evidence(suggestion),
                    )
                )
            )
            try:
                routed = await creation_rail.propose_from_external_evidence(
                    proposal_key=proposal_key,
                    reusable_guidance=suggestion.reusable_guidance,
                    evidence=evidence,
                    reason=suggestion.reason,
                )
                attempted = bool(routed) or attempted
            except Exception as exc:
                logger.warning(
                    "[ReviewFeedbackEvolution] new-Skill creation routing failed: %s",
                    exc,
                    exc_info=True,
                )
        if attempted:
            await self._push_skill_creation_events(creation_rail)
        return attempted

    async def _push_skill_creation_events(self, creation_rail: Any) -> None:
        await self._publish_pending_events(creation_rail, SKILL_CREATION_EVENTS)

    async def _publish_pending_events(self, rail: Any, event_group: str) -> None:
        events = await rail.drain_pending_approval_events(wait=False) or []
        if events and self._event_sink is not None:
            await self._event_sink(event_group, events)

    def _team_evolution_rail(self) -> Any | None:
        return self._team_rail_provider()

    def _team_skill_create_rail(self) -> Any | None:
        if self._skill_create_rail_provider is None:
            return None
        return self._skill_create_rail_provider()

    def _is_enabled(self) -> bool:
        try:
            return bool(self._enabled() if callable(self._enabled) else self._enabled)
        except Exception as exc:
            logger.warning("[ReviewFeedbackEvolution] enablement lookup failed: %s", exc)
            return False

    def _confidence_threshold(self) -> float:
        try:
            raw = self._min_confidence() if callable(self._min_confidence) else self._min_confidence
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 0.7

    @staticmethod
    def _task_objective(payload: dict[str, Any]) -> str:
        objective_parts = []
        for field_name in ("task_title", "task_content"):
            value = str(payload.get(field_name) or "").strip()
            if value:
                objective_parts.append(value)
        return "\n".join(objective_parts)

    @staticmethod
    def _is_safe_member_name(member_name: str) -> bool:
        return bool(member_name and member_name not in {".", ".."} and _SAFE_MEMBER_NAME.fullmatch(member_name))

    @staticmethod
    def _format_aggregated_feedback(
        observations: list[TaskFeedbackObservation],
    ) -> str:
        lines = ["团队全部任务完成。以下是归因到同一全局 Skill 的任务审核反馈汇总："]
        for item in observations:
            lines.append(f"- task={item.task_id}, round={item.review_round}, assignee={item.assignee}: {item.feedback}")
        return "\n".join(lines)[:_MAX_AGGREGATED_FEEDBACK_CHARS]

    @staticmethod
    def _format_new_skill_pattern_evidence(
        observation: NewSkillPatternObservation,
    ) -> str:
        return (
            f"task={observation.task_id}, round={observation.review_round}, "
            f"assignee={observation.assignee}: {observation.feedback}"
        )[:2_000]

    @staticmethod
    def _new_skill_proposal_key(reusable_guidance: str) -> str:
        return re.sub(r"[^\w]+", "-", reusable_guidance.strip().lower()).strip("-")[:160]

    def _matching_new_skill_patterns(
        self,
        reusable_guidance: str,
        *,
        exclude_task_id: str,
    ) -> list[NewSkillPatternObservation]:
        pattern_key = self._new_skill_proposal_key(reusable_guidance)
        if not pattern_key:
            return []
        matches = []
        for item in self._new_skill_patterns:
            if item.task_id == exclude_task_id:
                continue
            item_key = self._new_skill_proposal_key(item.reusable_guidance)
            if item_key == pattern_key:
                matches.append(item)
        return matches

    def _get_member_trajectory(self, assignee: str) -> Any | None:
        team_trajectory = self._get_team_trajectory()
        if team_trajectory is None:
            return None
        for member_id in (
            assignee,
            f"{self._team_id}_{assignee}",
            f"jiuwen_{self._team_id}_{assignee}",
        ):
            try:
                spans = select_member_spans(
                    team_trajectory,
                    member_id,
                    team_id=self._team_id,
                )
            except Exception as exc:
                logger.warning(
                    "[ReviewFeedbackEvolution] member trajectory projection failed: %s",
                    exc,
                )
                return None
            if spans:
                empty_team_trajectory = trim_trajectory(team_trajectory, max_spans=0)
                return merge_spans(empty_team_trajectory, spans)
        logger.info(
            "[ReviewFeedbackEvolution] no trajectory spans for assignee: team=%s member=%s",
            self._team_id,
            assignee,
        )
        return None

    def _get_team_trajectory(self) -> Any | None:
        rail = self._team_evolution_rail()
        getter = getattr(rail, "get_trajectory", None)
        if not callable(getter):
            return None
        try:
            return getter(
                session_id=self._session_id,
                team_id=self._team_id,
            )
        except Exception as exc:
            logger.warning("[ReviewFeedbackEvolution] trajectory lookup failed: %s", exc)
            return None


__all__ = [
    "SKILL_CREATION_EVENTS",
    "NewSkillPatternObservation",
    "ReviewFeedbackEvolutionCoordinator",
    "TaskFeedbackObservation",
]
