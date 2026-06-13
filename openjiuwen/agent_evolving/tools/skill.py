# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Main-agent evolution tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Optional

from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.agent_evolving.prompts.tools import build_evolution_subject_schema, build_evolution_tool_card
from openjiuwen.agent_evolving.tools.base import BaseEvolutionTool
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.subagent.task_tool import TaskTool

MAIN_EVOLUTION_TOOL_NAMES = (
    "prepare_skill_evolution",
    "evolve_review_task",
    "list_skill_experiences",
    "read_skill_experiences",
    "evolve_skill_experiences",
    "simplify_skill_experiences",
)

# Backward-compatible alias for callers that import the canonical main-agent set.
EVOLUTION_TOOL_NAMES = MAIN_EVOLUTION_TOOL_NAMES


def create_main_evolution_tools(
    *,
    query_service: Any = None,
    submission_service: Any = None,
    prepare_scope: Callable[..., Any] | None = None,
    review_runtime: Any = None,
    language: str = "cn",
    agent_id: Optional[str] = None,
    parent_agent: Any = None,
) -> list[Tool]:
    """Create canonical main-agent evolution tools bound to explicit services."""
    if query_service is None or submission_service is None or prepare_scope is None:
        raise ValueError("query_service, submission_service, and prepare_scope are required")
    return [
        PrepareSkillEvolutionReviewTool(
            prepare_scope=prepare_scope,
            language=language,
            agent_id=agent_id,
        ),
        EvolveReviewTaskTool(
            parent_agent=parent_agent,
            language=language,
            agent_id=agent_id,
        ),
        ListSkillExperiencesTool(
            query_service=query_service,
            language=language,
            agent_id=agent_id,
        ),
        ReadSkillExperiencesTool(
            query_service=query_service,
            language=language,
            agent_id=agent_id,
        ),
        EvolveSkillExperiencesTool(
            submission_service=submission_service,
            review_runtime=review_runtime,
            language=language,
            agent_id=agent_id,
        ),
        SimplifySkillExperiencesTool(
            submission_service=submission_service,
            language=language,
            agent_id=agent_id,
        ),
    ]


def create_evolve_review_task_tool(
    *,
    parent_agent: Any,
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> list[Tool]:
    """Create the dedicated main-agent tool for restricted evolution review."""
    return [
        EvolveReviewTaskTool(
            parent_agent=parent_agent,
            language=language,
            agent_id=agent_id,
        )
    ]


class _EvolutionTool(BaseEvolutionTool):
    """Base class for main-agent experience tools."""

    def __init__(
        self,
        *,
        query_service: Any = None,
        submission_service: Any = None,
        review_runtime: Any = None,
        language: str,
        agent_id: Optional[str],
    ) -> None:
        super().__init__(build_evolution_tool_card(self.tool_name, self.tool_id, language, agent_id=agent_id))
        self.card.input_params = _input_params_for_subject_kind(
            self.card.input_params,
            language=language,
        )
        self._query_service = query_service
        self._submission_service = submission_service
        self._review_runtime = review_runtime

    def _subject_from_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        subject = inputs.get("subject")
        if subject is None:
            raise ValueError("subject is required")
        normalized = self._normalize_subject_kind(subject)
        return normalized.to_payload()

    def _normalize_subject_kind(self, subject: dict[str, Any]):
        try:
            return normalize_subject(subject)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _session_id(kwargs: dict[str, Any]) -> str:
        return str(kwargs.get("conversation_id") or kwargs.get("session_id") or "")


class PrepareSkillEvolutionReviewTool(_EvolutionTool):
    """Thin adapter that asks the rail to prepare a review scope."""

    tool_name = "prepare_skill_evolution"
    tool_id = "PrepareSkillEvolutionReviewTool"

    def __init__(
        self,
        *,
        prepare_scope: Callable[..., Any],
        language: str,
        agent_id: Optional[str],
    ) -> None:
        super().__init__(
            language=language,
            agent_id=agent_id,
        )
        self._prepare_scope = prepare_scope

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = self.inputs_dict(inputs)
            if args.get("user_confirmed") is not True:
                raise ValueError("user_confirmed must be true before preparing skill evolution review")
            subject = self._subject_from_inputs(args)
            launch = self._prepare_scope(
                source="agent_detected_signal",
                subject=subject,
                session_id=str(kwargs.get("conversation_id") or kwargs.get("session_id") or ""),
                user_intent=str(args.get("user_intent") or ""),
            )
            result = {
                "success": True,
                "operation": "prepare_skill_evolution",
                "scope_id": launch.scope_id,
                "evolution_review_ref": launch.evolution_review_ref,
                "subject": launch.subject,
            }
            return self.to_output(result)
        except Exception as exc:
            return self.failure(exc)


class EvolveReviewTaskTool(BaseEvolutionTool):
    """Launch the stable restricted evolution review subagent."""

    tool_name = "evolve_review_task"
    tool_id = "EvolveReviewTaskTool"

    def __init__(
        self,
        *,
        parent_agent: Any | None,
        language: str,
        agent_id: Optional[str],
    ) -> None:
        super().__init__(build_evolution_tool_card(self.tool_name, self.tool_id, language, agent_id=agent_id))
        self._task_tool = TaskTool(card=self.card, parent_agent=parent_agent, language=language)

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.harness.rails.evolution.review.subagent import EVOLUTION_REVIEW_AGENT_NAME

        try:
            args = self.inputs_dict(inputs)
            ref = str(args.get("evolution_review_ref") or "").strip()
            if not ref:
                raise ValueError("evolution_review_ref is required")
            if self._task_tool.parent_agent is None:
                raise ValueError("parent_agent is required to run evolve_review_task")
            return await self._task_tool.invoke(
                {
                    "subagent_type": EVOLUTION_REVIEW_AGENT_NAME,
                    "task_description": self._build_task_description(args, ref),
                },
                **kwargs,
            )
        except Exception as exc:
            return self.failure(exc)

    @staticmethod
    def _build_task_description(args: dict[str, Any], ref: str) -> str:
        lines = [
            "Perform the restricted Skill evolution review for this prepared scope.",
            f"evolution_review_ref: {ref}",
            "Use the scope-bound read-only review tools and call submit_evolution_review before the final answer.",
            "Return only the exact JSON produced by submit_evolution_review.",
        ]
        subject = args.get("subject")
        if subject:
            lines.insert(2, f"subject: {subject}")
        user_intent = str(args.get("user_intent") or "").strip()
        if user_intent:
            lines.insert(2, f"user_intent: {user_intent}")
        return "\n".join(lines)


class ListSkillExperiencesTool(_EvolutionTool):
    """List bounded evolution experience index entries."""

    tool_name = "list_skill_experiences"
    tool_id = "ListSkillExperiencesTool"

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = self.inputs_dict(inputs)
            subject = self._subject_from_inputs(args)
            result = await self._query_service.list_experiences(
                subject,
                min_score=args.get("min_score"),
                limit=int(args.get("limit", 20) or 20),
                cursor=args.get("cursor"),
                target=args.get("target"),
                section=args.get("section"),
                query=args.get("query"),
                sort=str(args.get("sort", "score_desc") or "score_desc"),
            )
            return self.to_output(result)
        except Exception as exc:
            return self.failure(exc)


class ReadSkillExperiencesTool(_EvolutionTool):
    """Read selected evolution experience records."""

    tool_name = "read_skill_experiences"
    tool_id = "ReadSkillExperiencesTool"

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = self.inputs_dict(inputs)
            subject = self._subject_from_inputs(args)
            result = await self._query_service.read_experiences(
                subject,
                record_ids=list(args.get("record_ids") or []),
                max_content_chars=int(args.get("max_content_chars", 2000) or 2000),
            )
            return self.to_output(result)
        except Exception as exc:
            return self.failure(exc)


class EvolveSkillExperiencesTool(_EvolutionTool):
    """Accept reviewed evolution proposals."""

    tool_name = "evolve_skill_experiences"
    tool_id = "EvolveSkillExperiencesTool"

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = self.inputs_dict(inputs)
            subject = self._subject_from_inputs(args)
            if self._review_runtime is None:
                raise ValueError("review_runtime is required")
            ref = str(args.get("evolution_review_ref") or "")
            selected_proposal_ids = list(args.get("selected_proposal_ids") or [])
            session_id = self._session_id(kwargs)
            try:
                prepared = self._submission_service.prepare_evolve_submission(
                    review_runtime=self._review_runtime,
                    evolution_review_ref=ref,
                    subject=subject,
                    selected_proposal_ids=selected_proposal_ids,
                    session_id=session_id,
                )
            except KeyError as exc:
                raise ValueError("unknown or expired evolution_review_ref") from exc
            result = await self._submission_service.apply_prepared_evolve_submission(prepared)
            if bool(result.get("success", True)):
                self._review_runtime.consume_prepared_submission(prepared, session_id=session_id)
            return self.to_output(result)
        except Exception as exc:
            return self.failure(exc)


class SimplifySkillExperiencesTool(_EvolutionTool):
    """Apply approved simplify actions."""

    tool_name = "simplify_skill_experiences"
    tool_id = "SimplifySkillExperiencesTool"

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = self.inputs_dict(inputs)
            subject = self._subject_from_inputs(args)
            result = await self._submission_service.apply_simplify_actions(
                subject,
                list(args.get("actions") or []),
            )
            return self.to_output(result)
        except Exception as exc:
            return self.failure(exc)


__all__ = [
    "EVOLUTION_TOOL_NAMES",
    "MAIN_EVOLUTION_TOOL_NAMES",
    "EvolveReviewTaskTool",
    "EvolveSkillExperiencesTool",
    "ListSkillExperiencesTool",
    "PrepareSkillEvolutionReviewTool",
    "ReadSkillExperiencesTool",
    "SimplifySkillExperiencesTool",
    "create_evolve_review_task_tool",
    "create_main_evolution_tools",
]


def _input_params_for_subject_kind(
    input_params: dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    params = deepcopy(input_params)
    properties = params.get("properties")
    if isinstance(properties, dict) and "subject" in properties:
        properties["subject"] = build_evolution_subject_schema(language)
    return params
