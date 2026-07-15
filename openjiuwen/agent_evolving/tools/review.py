# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Restricted evolution tools for the stable Skill review subagent."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.experience.draft_schema import MAX_EVOLUTION_REVIEW_PROPOSALS
from openjiuwen.agent_evolving.experience.query import ExperienceQueryService
from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES, VALID_SECTIONS
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.agent_evolving.tools.base import BaseEvolutionTool
from openjiuwen.agent_evolving.prompts.tools import build_evolution_subject_schema

REVIEW_EVOLUTION_TOOL_NAMES = (
    "list_skill_experiences",
    "read_skill_experiences",
    "list_trajectory_steps",
    "read_trajectory_steps",
    "submit_evolution_review",
)

_SECTION_VALUES = sorted(VALID_SECTIONS)
_TARGET_VALUES = list(EVOLUTION_TARGET_VALUES)
_TRAJECTORY_LIST_DEFAULT_LIMIT = 25
_TRAJECTORY_LIST_MAX_LIMIT = 50
_TRAJECTORY_READ_MAX_REFS = 8


def _text(language: str, *, cn: str, en: str) -> str:
    return en if language == "en" else cn


def _values(values: list[str]) -> str:
    return ", ".join(values)


def _evolution_review_ref_param(language: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": _text(
            language,
            cn="当前 follow-up prompt 中提供的 evolution_review_ref。",
            en="Evolution review ref from the current follow-up prompt.",
        ),
    }


def _subject_param(language: str) -> dict[str, Any]:
    return build_evolution_subject_schema(language)


def create_evolution_review_tools(
    *,
    runtime: Any,
    query_service: Any | None = None,
    store: Any | None = None,
    language: str = "cn",
    agent_id: str | None = None,
) -> list[Tool]:
    """Create restricted tools for the stable evolution review subagent."""
    resolved_query_service = query_service or _query_service_from_store(store)
    return [
        EvolutionReviewListSkillExperiencesTool(
            runtime=runtime,
            query_service=resolved_query_service,
            store=store,
            agent_id=agent_id,
            language=language,
        ),
        EvolutionReviewReadSkillExperiencesTool(
            runtime=runtime,
            query_service=resolved_query_service,
            store=store,
            agent_id=agent_id,
            language=language,
        ),
        EvolutionReviewListTrajectoryStepsTool(
            runtime=runtime,
            agent_id=agent_id,
            language=language,
        ),
        EvolutionReviewReadTrajectoryStepsTool(
            runtime=runtime,
            agent_id=agent_id,
            language=language,
        ),
        SubmitEvolutionReviewResultTool(
            runtime=runtime,
            agent_id=agent_id,
            language=language,
        ),
    ]


class _EvolutionReviewTool(BaseEvolutionTool):
    """Base class for scope-bound review-agent tools."""

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        query_service: Any | None = None,
        store: Any | None = None,
        agent_id: str | None = None,
        language: str = "cn",
    ) -> None:
        self._language = language
        super().__init__(
            ToolCard(
                id=self._tool_id(agent_id),
                name=self.tool_name,
                description=self._description,
                input_params=self._input_params,
            )
        )
        self._runtime = runtime
        self._store = store
        self._query_service = query_service

    def _tool_id(self, agent_id: str | None) -> str:
        suffix = str(agent_id or "").strip()
        if not suffix:
            return self.tool_id
        return f"{self.tool_id}_{suffix}"

    @property
    def _description(self) -> str:
        return _text(
            self._language,
            cn=f"受限 Skill 演进审查工具：{self.tool_name}。",
            en=f"Restricted Skill evolution review tool: {self.tool_name}.",
        )

    @property
    def _input_params(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": _evolution_review_ref_param(self._language),
            },
            "required": ["evolution_review_ref"],
        }

    @staticmethod
    def _session_id(kwargs: dict[str, Any]) -> str:
        return str(kwargs.get("conversation_id") or kwargs.get("session_id") or "")

    def _runtime_for_ref(self, ref: str) -> Any:
        if self._runtime is None:
            raise KeyError(ref)
        return self._runtime

    def _store_for_ref(self, ref: str) -> Any:
        self._runtime_for_ref(ref)
        if self._store is None:
            raise KeyError(ref)
        return self._store

    def _query_service_for_ref(self, ref: str) -> Any:
        self._runtime_for_ref(ref)
        if self._query_service is not None:
            return self._query_service
        return _query_service_from_store(self._store)


class SubmitEvolutionReviewResultTool(_EvolutionReviewTool):
    tool_name = "submit_evolution_review"
    tool_id = "SubmitEvolutionReviewResultTool"

    @property
    def _input_params(self) -> dict[str, Any]:
        experience_schema = {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="一句话说明经验适用场景和建议行为。",
                        en="One sentence describing applicability and recommended behavior.",
                    ),
                },
                "content": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="可直接提交的经验正文。",
                        en="Experience content ready for submission.",
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": _TARGET_VALUES,
                    "description": _text(
                        self._language,
                        cn=f"写入目标；可选值：{_values(_TARGET_VALUES)}。",
                        en=f"Write target. Allowed values: {_values(_TARGET_VALUES)}.",
                    ),
                },
                "section": {
                    "type": "string",
                    "enum": _SECTION_VALUES,
                    "description": _text(
                        self._language,
                        cn=(
                            f"目标 section；可选值：{_values(_SECTION_VALUES)}。普通正文经验优先使用 Troubleshooting。"
                        ),
                        en=(
                            f"Target section. Allowed values: {_values(_SECTION_VALUES)}. "
                            "Prefer Troubleshooting for normal body experiences."
                        ),
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="推荐沉淀该经验的原因。",
                        en="Reason for recommending this experience.",
                    ),
                },
            },
            "required": ["summary", "content"],
        }
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": _evolution_review_ref_param(self._language),
                "subject": _subject_param(self._language),
                "outcome": {
                    "type": "string",
                    "enum": ["recommend_evolve", "no_evolution"],
                    "description": _text(
                        self._language,
                        cn="当前演进目标的审查结论。",
                        en="Review conclusion for the current evolution target.",
                    ),
                },
                "evidence_refs": {
                    "type": "array",
                    "description": _text(
                        self._language,
                        cn="产出结果前已读取的证据 ref。",
                        en="Evidence refs that were read before producing the result.",
                    ),
                    "items": {"type": "string"},
                },
                "proposals": {
                    "type": "array",
                    "maxItems": MAX_EVOLUTION_REVIEW_PROPOSALS,
                    "description": _text(
                        self._language,
                        cn="recommend_evolve 结论下已审查通过的 experience proposal。",
                        en="Reviewed experience proposals for recommend_evolve outcomes.",
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "proposal_id": {
                                "type": "string",
                                "description": _text(
                                    self._language,
                                    cn="本次 result 内唯一的 proposal id。",
                                    en="Proposal id unique within this result.",
                                ),
                            },
                            "experience": experience_schema,
                            "reason": {
                                "type": "string",
                                "description": _text(
                                    self._language,
                                    cn="该 proposal 的审查理由。",
                                    en="Review rationale for this proposal.",
                                ),
                            },
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["proposal_id", "experience"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": _text(self._language, cn="简短审查摘要。", en="Short review summary."),
                },
            },
            "required": [
                "evolution_review_ref",
                "subject",
                "outcome",
                "evidence_refs",
                "proposals",
            ],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = dict(inputs or {})
            ref = str(args.pop("evolution_review_ref"))
            session_id = self._session_id(kwargs)
            runtime = self._runtime_for_ref(ref)
            scope = runtime.record_review_result(ref, session_id=session_id, result=args)
            review_result = {
                **(scope.result or {}),
                "evolution_review_ref": ref,
                "status": scope.status,
            }
            proposal_ids = sorted(scope.proposal_ids)
            return ToolOutput(
                success=True,
                data={
                    "status": scope.status,
                    "evolution_review_ref": ref,
                    "proposal_ids": proposal_ids,
                    "review_result": review_result,
                    "proposal_selection_for_submission": {
                        "evolution_review_ref": ref,
                        "subject": dict(scope.subject),
                        "selected_proposal_ids": proposal_ids,
                    },
                },
            )
        except Exception as exc:
            return self.failure(exc)


class EvolutionReviewListSkillExperiencesTool(_EvolutionReviewTool):
    tool_name = "list_skill_experiences"
    tool_id = "EvolutionReviewListSkillExperiencesTool"

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        query_service: Any | None = None,
        store: Any | None = None,
        agent_id: str | None = None,
        language: str = "cn",
    ) -> None:
        super().__init__(
            runtime=runtime,
            query_service=query_service,
            store=store,
            agent_id=agent_id,
            language=language,
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = dict(inputs or {})
            ref = str(args.get("evolution_review_ref"))
            runtime = self._runtime_for_ref(ref)
            scope = runtime.resolve_scope(ref, session_id=self._session_id(kwargs))
            query_service = self._query_service_for_ref(ref)
            result = await query_service.list_experiences(
                dict(scope.subject),
                min_score=args.get("min_score"),
                limit=int(args.get("limit", 50) or 50),
                cursor=args.get("cursor"),
                target=args.get("target"),
                section=args.get("section"),
                query=args.get("query"),
                sort=str(args.get("sort", "score_desc") or "score_desc"),
            )
            return ToolOutput(success=True, data=dict(result))
        except Exception as exc:
            return self.failure(exc)


class EvolutionReviewReadSkillExperiencesTool(_EvolutionReviewTool):
    tool_name = "read_skill_experiences"
    tool_id = "EvolutionReviewReadSkillExperiencesTool"

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        query_service: Any | None = None,
        store: Any | None = None,
        agent_id: str | None = None,
        language: str = "cn",
    ) -> None:
        super().__init__(
            runtime=runtime,
            query_service=query_service,
            store=store,
            agent_id=agent_id,
            language=language,
        )

    @property
    def _input_params(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": _evolution_review_ref_param(self._language),
                "record_ids": {
                    "type": "array",
                    "description": _text(
                        self._language,
                        cn="要读取的经验记录 ID。",
                        en="Experience record IDs to read.",
                    ),
                    "items": {"type": "string"},
                },
                "max_content_chars": {
                    "type": "integer",
                    "default": 2000,
                    "description": _text(
                        self._language,
                        cn="每条记录最多返回的内容字符数。",
                        en="Maximum content characters per record.",
                    ),
                },
            },
            "required": ["evolution_review_ref", "record_ids"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = dict(inputs or {})
            ref = str(args.get("evolution_review_ref"))
            session_id = self._session_id(kwargs)
            runtime = self._runtime_for_ref(ref)
            scope = runtime.resolve_scope(ref, session_id=session_id)
            max_content_chars = int(args.get("max_content_chars", 2000) or 2000)
            query_service = self._query_service_for_ref(ref)
            result = await query_service.read_experiences(
                dict(scope.subject),
                record_ids=[str(record_id) for record_id in args.get("record_ids", [])],
                max_content_chars=max_content_chars,
            )
            payload = dict(result)
            items = list(payload.get("items") or [])
            read_ids = [str(item.get("record_id")) for item in items if item.get("record_id")]
            runtime.record_evidence_read(ref, session_id=session_id, refs=read_ids)
            return ToolOutput(success=True, data=payload)
        except Exception as exc:
            return self.failure(exc)


def _query_service_from_store(store: Any | None) -> ExperienceQueryService:
    if store is None:
        raise ValueError("query_service or store is required")
    return ExperienceQueryService(store=store)


class EvolutionReviewListTrajectoryStepsTool(_EvolutionReviewTool):
    tool_name = "list_trajectory_steps"
    tool_id = "EvolutionReviewListTrajectoryStepsTool"

    @property
    def _input_params(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": _evolution_review_ref_param(self._language),
                "cursor": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="上一轮 list 调用返回的零基游标。",
                        en="Opaque zero-based cursor returned by the previous list call.",
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": _TRAJECTORY_LIST_DEFAULT_LIMIT,
                    "description": _text(
                        self._language,
                        cn="最多返回的 trajectory 索引条目数。",
                        en="Maximum trajectory index items to return.",
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["llm", "tool"],
                    "description": _text(
                        self._language,
                        cn="可选 trajectory step 类型过滤。",
                        en="Optional trajectory step kind filter.",
                    ),
                },
                "tool_name": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="tool step 的可选工具名过滤。",
                        en="Optional tool-name filter for tool steps.",
                    ),
                },
                "has_error": {
                    "type": "boolean",
                    "description": _text(
                        self._language,
                        cn="可选错误状态过滤。",
                        en="Optional error-state filter.",
                    ),
                },
            },
            "required": ["evolution_review_ref"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = dict(inputs or {})
            ref = str(args.get("evolution_review_ref"))
            session_id = self._session_id(kwargs)
            runtime = self._runtime_for_ref(ref)
            scope = runtime.resolve_scope(ref, session_id=session_id)
            items = [
                dict(item)
                for item in list(scope.scoped_materials.get("trajectory_steps") or [])
                if isinstance(item, dict) and item.get("ref")
            ]

            kind = args.get("kind")
            if kind:
                items = [item for item in items if item.get("kind") == kind]

            tool_name = str(args.get("tool_name") or "")
            if tool_name:
                items = [item for item in items if str(item.get("tool_name") or "") == tool_name]

            if "has_error" in args:
                expected_has_error = bool(args.get("has_error"))
                items = [item for item in items if bool(item.get("has_error")) is expected_has_error]

            total = len(items)
            try:
                start = max(0, int(args.get("cursor", 0)))
            except (TypeError, ValueError):
                start = 0
            try:
                limit = int(args.get("limit", _TRAJECTORY_LIST_DEFAULT_LIMIT))
            except (TypeError, ValueError):
                limit = _TRAJECTORY_LIST_DEFAULT_LIMIT
            if limit <= 0:
                limit = _TRAJECTORY_LIST_DEFAULT_LIMIT
            limit = min(limit, _TRAJECTORY_LIST_MAX_LIMIT)
            page = items[slice(start, start + limit)]
            next_index = start + len(page)
            next_cursor = str(next_index) if next_index < total else None
            return ToolOutput(success=True, data={"items": page, "next_cursor": next_cursor, "total": total})
        except Exception as exc:
            return self.failure(exc)


class EvolutionReviewReadTrajectoryStepsTool(_EvolutionReviewTool):
    tool_name = "read_trajectory_steps"
    tool_id = "EvolutionReviewReadTrajectoryStepsTool"

    @property
    def _input_params(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": _evolution_review_ref_param(self._language),
                "refs": {
                    "type": "array",
                    "description": _text(
                        self._language,
                        cn="当前审查可读取的任务记录 ref。",
                        en="Task-record refs available to the current review.",
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["evolution_review_ref", "refs"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            args = dict(inputs or {})
            ref = str(args.get("evolution_review_ref"))
            refs = [str(item) for item in args.get("refs", [])]
            if len(refs) > _TRAJECTORY_READ_MAX_REFS:
                return ToolOutput(
                    success=False,
                    error=f"read at most {_TRAJECTORY_READ_MAX_REFS} trajectory refs per call",
                )
            session_id = self._session_id(kwargs)
            runtime = self._runtime_for_ref(ref)
            scope = runtime.resolve_scope(ref, session_id=session_id)
            details = scope.scoped_materials.get("trajectory_step_details") or {}
            if not isinstance(details, dict):
                details = {}
            by_ref = {str(item_ref): dict(item) for item_ref, item in details.items() if isinstance(item, dict)}
            missing = [item_ref for item_ref in refs if item_ref not in by_ref]
            if missing:
                return ToolOutput(success=False, error=f"unknown trajectory refs: {missing}")
            runtime.record_trajectory_read(ref, session_id=session_id, refs=refs)
            return ToolOutput(success=True, data={"items": [by_ref[item_ref] for item_ref in refs]})
        except Exception as exc:
            return self.failure(exc)


__all__ = [
    "REVIEW_EVOLUTION_TOOL_NAMES",
    "EvolutionReviewListSkillExperiencesTool",
    "EvolutionReviewListTrajectoryStepsTool",
    "EvolutionReviewReadSkillExperiencesTool",
    "EvolutionReviewReadTrajectoryStepsTool",
    "SubmitEvolutionReviewResultTool",
    "create_evolution_review_tools",
]
