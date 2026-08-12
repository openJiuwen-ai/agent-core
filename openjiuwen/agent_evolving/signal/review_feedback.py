# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Standalone attribution of aggregated task-review feedback.

This module intentionally has no runtime wiring.  It does not subscribe to
team events, create evolution signals, update a Skill, or request Skill
creation.  Callers may use the structured result to decide which of those
actions, if any, should happen at a later integration boundary.

The post-LLM policy is deliberately stricter than the classifier prompt:

* an existing Skill can only be selected from Skills proven to have been read;
* an execution mistake is recorded as a task failure, never a Skill change;
* a new-Skill suggestion requires evidence that the work pattern repeated;
* missing, malformed, or untrusted classifier output fails closed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.agent_evolving.signal.base import EvolutionSignal, make_evolution_signal
from openjiuwen.agent_evolving.trajectory import Trajectory, trajectory_steps
from openjiuwen.core.common.logging import logger

_MAX_FEEDBACK_CHARS = 4_000
_MAX_TASK_CONTEXT_CHARS = 6_000
_MAX_TRAJECTORY_CHARS = 8_000
_MAX_SKILL_CONTENT_CHARS = 6_000
_MAX_TRAJECTORY_STEPS = 20
REVIEW_FEEDBACK_SIGNAL = "review_feedback"
REVIEW_FEEDBACK_SOURCE = "scheduler_review_feedback"


class ReviewFeedbackAction(str, Enum):
    """Safe downstream disposition for one aggregated review feedback item."""

    EVOLVE_EXISTING_SKILL = "evolve_existing_skill"
    SUGGEST_NEW_SKILL = "suggest_new_skill"
    RECORD_TASK_FAILURE = "record_task_failure"
    SKIP_UNATTRIBUTED = "skip_unattributed"


class ReviewFeedbackClassification(str, Enum):
    """Semantic classification requested from the attribution model."""

    SKILL_ISSUE = "skill_issue"
    NEW_SKILL_PATTERN = "new_skill_pattern"
    EXECUTOR_ERROR = "executor_error"
    UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class ReviewFeedbackContext:
    """Evidence accompanying aggregated reviewer feedback.

    ``skill_reads`` must be derived from concrete trajectory evidence such as a
    read of ``<skill>/SKILL.md``.  Merely having a Skill installed is not proof
    that it influenced the failed task.

    ``repetition_count`` and ``repeated_pattern_evidence`` are only gates for a
    new-Skill suggestion.  This module never creates the Skill itself.
    """

    task_id: str = ""
    review_round: int = 0
    task_objective: str = ""
    trajectory_excerpt: str = ""
    skill_reads: Sequence[str] = ()
    skill_contents: Mapping[str, str] = field(default_factory=dict)
    repetition_count: int = 1
    repeated_pattern_evidence: Sequence[str] = ()


@dataclass(frozen=True)
class ReviewFeedbackAttribution:
    """Normalized, policy-enforced attribution result."""

    action: ReviewFeedbackAction
    classification: ReviewFeedbackClassification
    is_skill_actionable: bool
    skill_name: str | None
    target: EvolutionTarget | None
    reason: str
    reusable_guidance: str
    confidence: float
    feedback_excerpt: str

    @property
    def should_create_skill(self) -> bool:
        """Whether a later integration may hand this to Skill creation."""

        return self.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL

    @property
    def should_record_task_failure(self) -> bool:
        """Whether the outcome belongs to task failure history only."""

        return self.action == ReviewFeedbackAction.RECORD_TASK_FAILURE


class ReviewFeedbackLLM(Protocol):
    """Minimal async model surface required by the standalone attributor."""

    async def invoke(self, **kwargs: Any) -> Any:
        """Return a response carrying JSON in ``content`` or ``text``."""


class ReviewFeedbackAttributor:
    """Classify review feedback and enforce safe Skill attribution invariants."""

    def __init__(
        self,
        *,
        llm: ReviewFeedbackLLM,
        model: str,
        language: str = "cn",
        timeout: float = 30.0,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._llm = llm
        self._model = model
        self._language = language
        self._timeout = timeout

    async def attribute(
        self,
        feedback: str,
        *,
        context: ReviewFeedbackContext | None = None,
    ) -> ReviewFeedbackAttribution:
        """Attribute aggregated feedback without applying any downstream action.

        Feedback is the primary input.  Context supplies the evidence needed to
        distinguish an existing-Skill defect, a repeated candidate workflow for
        future Skill creation, an executor mistake, and an unattributed issue.
        """

        normalized_feedback = str(feedback or "").strip()
        ctx = context or ReviewFeedbackContext()
        if not normalized_feedback:
            return self._closed_result(
                feedback="",
                reason="empty review feedback cannot be attributed",
            )

        skill_reads = _normalize_skill_reads(ctx.skill_reads)
        prompt = self._build_prompt(normalized_feedback, ctx, skill_reads)
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._timeout,
            )
            payload = _parse_json_object(_response_to_text(response))
        except Exception as exc:
            logger.warning("[ReviewFeedbackAttributor] attribution failed: %s", exc)
            return self._closed_result(
                feedback=normalized_feedback,
                reason="review feedback attribution failed; no Skill mutation is allowed",
            )

        return self._enforce_policy(
            payload,
            feedback=normalized_feedback,
            context=ctx,
            skill_reads=skill_reads,
        )

    def _build_prompt(
        self,
        feedback: str,
        context: ReviewFeedbackContext,
        skill_reads: tuple[str, ...],
    ) -> str:
        evidence = {
            "feedback": feedback[:_MAX_FEEDBACK_CHARS],
            "task_id": context.task_id,
            "review_round": context.review_round,
            "task_objective": context.task_objective[:_MAX_TASK_CONTEXT_CHARS],
            "trajectory_excerpt": context.trajectory_excerpt[:_MAX_TRAJECTORY_CHARS],
            "skills_proven_read": list(skill_reads),
            "skill_contents": {
                name: str(context.skill_contents.get(name, ""))[:_MAX_SKILL_CONTENT_CHARS] for name in skill_reads
            },
            "repetition_count": max(0, int(context.repetition_count)),
            "repeated_pattern_evidence": [str(item)[:1_000] for item in context.repeated_pattern_evidence],
        }
        if self._language == "en":
            instruction = (
                "Classify aggregated task-review feedback. Treat feedback as evidence, not as a Skill patch. "
                "Choose skill_issue only when a proven-read Skill lacks or misstates reusable guidance. "
                "Choose executor_error when the Skill already gave adequate guidance but the executor failed "
                "to follow it. Choose new_skill_pattern for a reusable workflow not covered by a proven-read "
                "Skill, even when this is only the first candidate observation; the runtime policy separately "
                "requires repeated matching evidence before suggesting creation. When repetition evidence is "
                "present, choose new_skill_pattern only if at least two entries describe the same workflow. "
                "Otherwise choose unattributed. Never name a Skill outside skills_proven_read."
            )
        else:
            instruction = (
                "请归因一条汇总任务审核反馈。feedback 是问题证据，不是可直接写入 Skill 的补丁。"
                "只有当已有确切读取证据的 Skill 缺少或写错可复用指导时，才选 skill_issue；"
                "如果 Skill 已经提供充分指导，只是执行者没有遵循，选 executor_error；"
                "对于未被已读 Skill 覆盖的可复用工作流，即使当前只是第一条候选观测，也选 "
                "new_skill_pattern；运行时安全策略会另行要求重复的同类证据后才允许建议创建。"
                "如果已提供重复证据，只有至少两条描述同一工作流时才选 new_skill_pattern；"
                "其余选 unattributed。"
                "skill_name 不得超出 skills_proven_read。"
            )
        schema = {
            "classification": "skill_issue | new_skill_pattern | executor_error | unattributed",
            "skill_name": "existing proven-read skill name or empty string",
            "target": "description | body | script | null",
            "reason": "short attribution reason",
            "reusable_guidance": "reusable improvement guidance or empty string",
            "is_reusable": True,
            "confidence": 0.0,
        }
        return (
            f"{instruction}\n\n"
            f"证据：\n{json.dumps(evidence, ensure_ascii=False)}\n\n"
            "只输出一个 JSON 对象，不要输出 Markdown。格式：\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    def _enforce_policy(
        self,
        payload: dict[str, Any],
        *,
        feedback: str,
        context: ReviewFeedbackContext,
        skill_reads: tuple[str, ...],
    ) -> ReviewFeedbackAttribution:
        classification = _parse_classification(payload.get("classification"))
        reason = str(payload.get("reason") or "").strip()
        guidance = str(payload.get("reusable_guidance") or "").strip()
        confidence = _normalize_confidence(payload.get("confidence"))
        is_reusable = payload.get("is_reusable") is True

        if classification == ReviewFeedbackClassification.EXECUTOR_ERROR:
            return ReviewFeedbackAttribution(
                action=ReviewFeedbackAction.RECORD_TASK_FAILURE,
                classification=classification,
                is_skill_actionable=False,
                skill_name=None,
                target=None,
                reason=reason or "review feedback indicates an execution mistake, not a Skill defect",
                reusable_guidance="",
                confidence=confidence,
                feedback_excerpt=feedback[:_MAX_FEEDBACK_CHARS],
            )

        if classification == ReviewFeedbackClassification.NEW_SKILL_PATTERN:
            repeated = max(0, int(context.repetition_count)) >= 2 or len(context.repeated_pattern_evidence) >= 2
            if repeated and is_reusable and guidance:
                return ReviewFeedbackAttribution(
                    action=ReviewFeedbackAction.SUGGEST_NEW_SKILL,
                    classification=classification,
                    is_skill_actionable=False,
                    skill_name=None,
                    target=None,
                    reason=reason or "a repeated reusable workflow may warrant a new Skill",
                    reusable_guidance=guidance,
                    confidence=confidence,
                    feedback_excerpt=feedback[:_MAX_FEEDBACK_CHARS],
                )
            return ReviewFeedbackAttribution(
                action=ReviewFeedbackAction.SKIP_UNATTRIBUTED,
                classification=classification,
                is_skill_actionable=False,
                skill_name=None,
                target=None,
                reason=(
                    "new-Skill classification rejected because repeated reusable evidence is "
                    "insufficient or reusable guidance is missing"
                ),
                # Preserve a safe candidate summary so an integration layer can
                # compare it with later task feedback.  It remains non-actionable
                # until the repeated-evidence gate above passes.
                reusable_guidance=guidance if is_reusable else "",
                confidence=confidence,
                feedback_excerpt=feedback[:_MAX_FEEDBACK_CHARS],
            )

        if classification != ReviewFeedbackClassification.SKILL_ISSUE:
            return self._closed_result(
                feedback=feedback,
                classification=classification,
                reason=reason or "review feedback could not be attributed to a Skill",
                confidence=confidence,
            )

        if not skill_reads:
            return self._closed_result(
                feedback=feedback,
                classification=classification,
                reason="no SKILL.md read evidence exists; existing Skill evolution is forbidden",
                confidence=confidence,
            )

        proposed_skill = str(payload.get("skill_name") or "").strip()
        if not proposed_skill and len(skill_reads) == 1:
            proposed_skill = skill_reads[0]
        if proposed_skill not in skill_reads:
            return self._closed_result(
                feedback=feedback,
                classification=classification,
                reason="the proposed Skill is not backed by SKILL.md read evidence",
                confidence=confidence,
            )

        target = _parse_target(payload.get("target"))
        if not is_reusable or target is None or not guidance:
            return self._closed_result(
                feedback=feedback,
                classification=classification,
                reason="the attributed issue lacks reusable, target-specific Skill guidance",
                confidence=confidence,
            )

        return ReviewFeedbackAttribution(
            action=ReviewFeedbackAction.EVOLVE_EXISTING_SKILL,
            classification=classification,
            is_skill_actionable=True,
            skill_name=proposed_skill,
            target=target,
            reason=reason or "review feedback identifies a reusable defect in a proven-read Skill",
            reusable_guidance=guidance,
            confidence=confidence,
            feedback_excerpt=feedback[:_MAX_FEEDBACK_CHARS],
        )

    @staticmethod
    def _closed_result(
        *,
        feedback: str,
        reason: str,
        classification: ReviewFeedbackClassification = ReviewFeedbackClassification.UNATTRIBUTED,
        confidence: float = 0.0,
    ) -> ReviewFeedbackAttribution:
        return ReviewFeedbackAttribution(
            action=ReviewFeedbackAction.SKIP_UNATTRIBUTED,
            classification=classification,
            is_skill_actionable=False,
            skill_name=None,
            target=None,
            reason=reason,
            reusable_guidance="",
            confidence=confidence,
            feedback_excerpt=feedback[:_MAX_FEEDBACK_CHARS],
        )


class ReviewFeedbackContextBuilder:
    """Build trusted attribution context from task metadata and a trajectory.

    The builder only treats concrete tool-call arguments as Skill-read proof.
    Installed Skill names, model prose, and tool results are not sufficient.
    """

    def __init__(self, *, store: Any) -> None:
        self._store = store

    async def build(
        self,
        *,
        task_id: str = "",
        review_round: int = 0,
        task_objective: str = "",
        trajectory: Trajectory | None = None,
        repetition_count: int = 1,
        repeated_pattern_evidence: Sequence[str] = (),
    ) -> ReviewFeedbackContext:
        """Return bounded evidence suitable for :class:`ReviewFeedbackAttributor`."""

        known_skills = tuple(self._store.list_skill_names())
        skill_reads = _extract_skill_reads_from_trajectory(trajectory, known_skills)
        skill_contents: dict[str, str] = {}
        for skill_name in skill_reads:
            try:
                content = await self._store.read_skill_content(skill_name, strict=True)
            except Exception as exc:
                logger.warning(
                    "[ReviewFeedbackContextBuilder] failed to read proven Skill '%s': %s",
                    skill_name,
                    exc,
                )
                continue
            skill_contents[skill_name] = content

        # A read whose definition can no longer be loaded is not actionable.
        trusted_reads = tuple(name for name in skill_reads if name in skill_contents)
        return ReviewFeedbackContext(
            task_id=str(task_id or ""),
            review_round=max(0, int(review_round)),
            task_objective=str(task_objective or "")[:_MAX_TASK_CONTEXT_CHARS],
            trajectory_excerpt=_trajectory_excerpt(trajectory),
            skill_reads=trusted_reads,
            skill_contents=skill_contents,
            repetition_count=max(0, int(repetition_count)),
            repeated_pattern_evidence=tuple(str(item) for item in repeated_pattern_evidence),
        )


def attribution_to_evolution_signal(
    attribution: ReviewFeedbackAttribution,
    *,
    task_id: str = "",
    review_round: int = 0,
) -> EvolutionSignal | None:
    """Convert an actionable attribution into the standard evolution signal."""

    if attribution.action != ReviewFeedbackAction.EVOLVE_EXISTING_SKILL:
        return None
    if not attribution.is_skill_actionable:
        return None
    if not attribution.skill_name or attribution.target is None:
        return None
    if not attribution.reusable_guidance:
        return None

    section = {
        EvolutionTarget.DESCRIPTION: "Description",
        EvolutionTarget.BODY: "Reviewer Feedback",
        EvolutionTarget.SCRIPT: "Scripts",
    }[attribution.target]
    excerpt_parts = [attribution.feedback_excerpt.strip(), attribution.reusable_guidance.strip()]
    return make_evolution_signal(
        signal_type=REVIEW_FEEDBACK_SIGNAL,
        section=section,
        excerpt="\n\nReusable guidance: ".join(part for part in excerpt_parts if part),
        skill_name=attribution.skill_name,
        source=REVIEW_FEEDBACK_SOURCE,
        context={
            "task_id": str(task_id or ""),
            "review_round": max(0, int(review_round)),
            "target": attribution.target.value,
            "reason": attribution.reason,
            "reusable_guidance": attribution.reusable_guidance,
            "confidence": attribution.confidence,
            "classification": attribution.classification.value,
        },
    )


def _normalize_skill_reads(skill_reads: Sequence[str]) -> tuple[str, ...]:
    normalized_names: list[str] = []
    for raw in skill_reads:
        name = str(raw).strip()
        if name and name not in normalized_names:
            normalized_names.append(name)
    return tuple(normalized_names)


def _response_to_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("content") or response.get("text") or "")
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    return str(getattr(response, "text", "") or "")


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("attribution response does not contain a JSON object") from None
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("attribution response must be a JSON object")
    return parsed


def _parse_classification(value: Any) -> ReviewFeedbackClassification:
    try:
        return ReviewFeedbackClassification(str(value or "").strip().lower())
    except ValueError:
        return ReviewFeedbackClassification.UNATTRIBUTED


def _parse_target(value: Any) -> EvolutionTarget | None:
    if value is None:
        return None
    try:
        return EvolutionTarget(str(value).strip().lower())
    except ValueError:
        return None


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _extract_skill_reads_from_trajectory(
    trajectory: Trajectory | None,
    known_skills: Sequence[str],
) -> tuple[str, ...]:
    if trajectory is None:
        return ()

    normalized_skills = tuple(dict.fromkeys(str(name).strip() for name in known_skills if str(name).strip()))
    proven: list[str] = []
    for step in trajectory_steps(trajectory):
        if step.kind != "tool" or step.detail is None:
            continue
        tool_name = str(getattr(step.detail, "tool_name", "") or "").strip().lower()
        call_args = getattr(step.detail, "call_args", None)
        args_text = _stable_text(call_args)
        for skill_name in normalized_skills:
            if skill_name in proven:
                continue
            if _tool_call_proves_skill_read(tool_name, call_args, args_text, skill_name):
                proven.append(skill_name)
    return tuple(proven)


def _tool_call_proves_skill_read(
    tool_name: str,
    call_args: Any,
    args_text: str,
    skill_name: str,
) -> bool:
    path_pattern = re.compile(
        rf"(?:^|[/\\]){re.escape(skill_name)}[/\\]SKILL\.md(?:$|[\s'\"])",
        re.IGNORECASE,
    )
    has_skill_path = path_pattern.search(args_text) is not None
    base_tool_name = tool_name.rsplit(".", 1)[-1].replace("-", "_")
    if base_tool_name == "skill_tool" and skill_name in _named_skill_values(call_args):
        return True
    if not has_skill_path:
        return False
    if any(token in tool_name for token in ("read", "view", "open")):
        return True
    if any(token in tool_name for token in ("bash", "shell", "exec", "command")):
        return (
            re.search(
                r"\b(?:cat|sed|head|tail|less|more|rg|grep)\b",
                args_text,
                re.IGNORECASE,
            )
            is not None
        )
    return False


def _named_skill_values(value: Any, *, parent_key: str = "") -> set[str]:
    names: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in {"skill", "skill_name", "name"} and isinstance(item, str):
                names.add(item.strip())
            names.update(_named_skill_values(item, parent_key=normalized_key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            names.update(_named_skill_values(item, parent_key=parent_key))
    elif parent_key in {"skill", "skill_name", "name"} and isinstance(value, str):
        names.add(value.strip())
    return names


def _trajectory_excerpt(trajectory: Trajectory | None) -> str:
    if trajectory is None:
        return ""
    lines: list[str] = []
    for step in trajectory_steps(trajectory)[-_MAX_TRAJECTORY_STEPS:]:
        if step.detail is None:
            continue
        if step.kind == "tool":
            tool_name = str(getattr(step.detail, "tool_name", "") or "")
            call_args = _stable_text(getattr(step.detail, "call_args", None))[:800]
            call_result = _stable_text(getattr(step.detail, "call_result", None))[:800]
            lines.append(f"[tool] {tool_name} args={call_args} result={call_result}")
        elif step.kind == "llm":
            response = _stable_text(getattr(step.detail, "response", None))[:1_000]
            if response:
                lines.append(f"[assistant] {response}")
    return "\n".join(lines)[-_MAX_TRAJECTORY_CHARS:]


def _stable_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (bytes, bytearray)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            pass
    return str(value)


__all__ = [
    "REVIEW_FEEDBACK_SIGNAL",
    "REVIEW_FEEDBACK_SOURCE",
    "ReviewFeedbackAction",
    "ReviewFeedbackAttribution",
    "ReviewFeedbackAttributor",
    "ReviewFeedbackClassification",
    "ReviewFeedbackContext",
    "ReviewFeedbackContextBuilder",
    "ReviewFeedbackLLM",
    "attribution_to_evolution_signal",
]
