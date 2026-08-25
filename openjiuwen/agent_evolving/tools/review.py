# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Restricted evolution tools for the stable Skill review subagent."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openjiuwen.agent_evolving.experience.draft_schema import MAX_EVOLUTION_REVIEW_PROPOSALS
from openjiuwen.agent_evolving.experience.query import ExperienceQueryService
from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES, VALID_SECTIONS
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_exchange,
    read_span_error,
    read_tool_call,
    span_attributes,
    span_identity,
    span_sort_key,
    span_status,
)
from openjiuwen.agent_evolving.trajectory.team import TEAM_CATEGORIES, span_category
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.agent_evolving.tools.base import BaseEvolutionTool
from openjiuwen.agent_evolving.prompts.tools import build_evolution_subject_schema

REVIEW_EVOLUTION_TOOL_NAMES = (
    "list_skill_experiences",
    "read_skill_experiences",
    "list_trajectory_spans",
    "read_trajectory_spans",
    "submit_evolution_review",
)

_SECTION_VALUES = sorted(VALID_SECTIONS)
_TARGET_VALUES = list(EVOLUTION_TARGET_VALUES)
_TRAJECTORY_LIST_DEFAULT_LIMIT = 25
_TRAJECTORY_LIST_MAX_LIMIT = 50
_TRAJECTORY_READ_MAX_REFS = 8
_REVIEW_TEXT_PREVIEW_CHARS = 240
_REVIEW_DETAIL_TEXT_CHARS = 1200
_REVIEW_ARG_TEXT_CHARS = 800
_REVIEW_LLM_MESSAGE_TEXT_CHARS = 500
_REVIEW_LLM_MESSAGE_LIMIT = 3

_CONTEXT_ATTRIBUTE_KEYS = (
    semconv.AT_SESSION_ID,
    semconv.AT_TEAM_ID,
    semconv.AT_TEAM_NAME,
    semconv.AT_TEAM_DISPLAY_NAME,
    semconv.AT_TEAM_LEADER,
    semconv.AT_AGENT_ID,
    semconv.AT_AGENT_NAME,
    semconv.AT_AGENT_ROLE,
    semconv.AT_AGENT_INPUT,
    semconv.AT_AGENT_OUTPUT,
    semconv.AT_MEMBER_ID,
    semconv.AT_MEMBER_NAME,
    semconv.AT_MEMBER_STATUS_OLD,
    semconv.AT_MEMBER_STATUS_NEW,
    semconv.AT_MEMBER_RESTART_REASON,
    semconv.AT_MEMBER_RESTART_COUNT,
    semconv.AT_MEMBER_SHUTDOWN_FORCE,
    semconv.AT_MESSAGE_ID,
    semconv.AT_MESSAGE_FROM,
    semconv.AT_MESSAGE_TO,
    semconv.AT_MESSAGE_BROADCAST,
    semconv.AT_TASK_ID,
    semconv.AT_TASK_STATUS,
    semconv.AT_TASK_ASSIGNEE,
    semconv.AT_PLAN_APPROVED,
    semconv.AT_PLAN_SUBMITTED_BY,
    semconv.AT_EVENT_TYPE,
)


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
        EvolutionReviewListTrajectorySpansTool(
            runtime=runtime,
            agent_id=agent_id,
            language=language,
        ),
        EvolutionReviewReadTrajectorySpansTool(
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
        session_id = kwargs.get("conversation_id") or kwargs.get("session_id")
        if session_id:
            return str(session_id)
        session = kwargs.get("session")
        get_session_id = getattr(session, "get_session_id", None)
        return str(get_session_id()) if callable(get_session_id) else ""

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


def _span_ref(span: Mapping[str, Any]) -> str | None:
    identity = span_identity(span)
    if identity is None:
        return None
    return f"span:{identity[0]}:{identity[1]}"


def _parent_span_ref(span: Mapping[str, Any]) -> str | None:
    trace_id = str(span.get("traceId") or "").strip()
    parent_span_id = str(span.get("parentSpanId") or "").strip()
    if not trace_id or not parent_span_id:
        return None
    return f"span:{trace_id}:{parent_span_id}"


def _review_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().lower().startswith("data:image/"):
        return "<omitted:image_content>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        content_type = str(value.get("type") or "").lower()
        if content_type in {"image", "image_url", "input_image"}:
            return {"type": content_type, "omitted": "image_content"}
        result = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if "base64" in key_lower or key_lower in {"image_url", "bytes"}:
                continue
            result[key_text] = _review_safe(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_review_safe(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _review_safe(model_dump())
        except Exception:
            return str(value)
    return str(value)


def _bounded_value(value: Any, *, limit: int) -> dict[str, Any]:
    safe_value = _review_safe(value)
    if isinstance(safe_value, str):
        text = safe_value
    else:
        text = json.dumps(safe_value, ensure_ascii=False, sort_keys=True, default=str)
    result: dict[str, Any] = {
        "value": safe_value if len(text) <= limit else text[:limit],
        "truncated": len(text) > limit,
    }
    if result["truncated"]:
        result["original_chars"] = len(text)
    return result


def _bounded_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    selected = messages[-_REVIEW_LLM_MESSAGE_LIMIT:]
    items = []
    for message in selected:
        item: dict[str, Any] = {"role": str(message.get("role") or "")}
        if "content" in message:
            item["content"] = _bounded_value(
                message.get("content"),
                limit=_REVIEW_LLM_MESSAGE_TEXT_CHARS,
            )
        if "tool_calls" in message:
            item["tool_calls"] = _bounded_value(
                message.get("tool_calls"),
                limit=_REVIEW_ARG_TEXT_CHARS,
            )
        items.append(item)
    return {
        "items": items,
        "truncated": len(messages) > len(selected),
        "original_count": len(messages),
    }


def _span_common(span: Mapping[str, Any], *, include_error: bool = True) -> dict[str, Any]:
    item = {
        "ref": _span_ref(span),
        "parent_ref": _parent_span_ref(span),
        "name": str(span.get("name") or ""),
        "kind": span_category(span),
        "start_time_unix_nano": str(span.get("startTimeUnixNano") or ""),
        "end_time_unix_nano": str(span.get("endTimeUnixNano") or ""),
        "status": span_status(span),
    }
    if include_error:
        error = read_span_error(span)
        if error is not None:
            item["error"] = _bounded_value(error, limit=_REVIEW_DETAIL_TEXT_CHARS)
    return item


def _trajectory_span_index(trajectory: Any) -> list[tuple[str, dict[str, Any]]]:
    if trajectory is None:
        return []
    indexed = []
    for span in sorted(iter_spans(trajectory), key=span_sort_key):
        ref = _span_ref(span)
        if ref is not None:
            indexed.append((ref, span))
    return indexed


def _span_index_item(span: Mapping[str, Any]) -> dict[str, Any]:
    item = _span_common(span, include_error=False)
    item["has_error"] = read_span_error(span) is not None
    item.pop("status", None)
    attrs = span_attributes(span)
    if item["kind"] == "llm":
        model = attrs.get(semconv.GEN_AI_RESPONSE_MODEL) or attrs.get(semconv.GEN_AI_REQUEST_MODEL)
        if model is not None:
            item["model"] = str(model)
    elif item["kind"] == "tool":
        tool_name = read_tool_call(span).get("name")
        if tool_name is not None:
            item["tool_name"] = str(tool_name)
    return item


def _context_attributes(span: Mapping[str, Any]) -> dict[str, Any]:
    attrs = span_attributes(span)
    result = {}
    for key in _CONTEXT_ATTRIBUTE_KEYS:
        if key not in attrs:
            continue
        value = attrs[key]
        if key in {semconv.AT_AGENT_INPUT, semconv.AT_AGENT_OUTPUT}:
            result[key] = _bounded_value(value, limit=_REVIEW_DETAIL_TEXT_CHARS)
        elif isinstance(value, str) and len(value) > _REVIEW_TEXT_PREVIEW_CHARS:
            result[key] = _bounded_value(value, limit=_REVIEW_TEXT_PREVIEW_CHARS)
        else:
            result[key] = _review_safe(value)
    return result


def _span_detail(span: Mapping[str, Any]) -> dict[str, Any]:
    item = _span_common(span)
    kind = item["kind"]
    attrs = span_attributes(span)
    if kind == "llm":
        prompts, completions = read_llm_exchange(span)
        model = attrs.get(semconv.GEN_AI_RESPONSE_MODEL) or attrs.get(semconv.GEN_AI_REQUEST_MODEL)
        item["llm"] = {
            "model": str(model or ""),
            "input_messages": _bounded_messages(prompts),
            "output_messages": _bounded_messages(completions),
        }
    elif kind == "tool":
        tool_call = read_tool_call(span)
        tool_detail = {
            "name": str(tool_call.get("name") or ""),
            "id": str(tool_call.get("id") or ""),
        }
        if "input" in tool_call:
            tool_detail["input"] = _bounded_value(tool_call["input"], limit=_REVIEW_ARG_TEXT_CHARS)
        if "output" in tool_call:
            tool_detail["output"] = _bounded_value(tool_call["output"], limit=_REVIEW_DETAIL_TEXT_CHARS)
        item["tool"] = tool_detail
    else:
        context = _context_attributes(span)
        if context:
            item["context"] = context
    return item


class EvolutionReviewListTrajectorySpansTool(_EvolutionReviewTool):
    tool_name = "list_trajectory_spans"
    tool_id = "EvolutionReviewListTrajectorySpansTool"

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
                        cn="最多返回的 trajectory span 索引条目数。",
                        en="Maximum trajectory span index items to return.",
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(TEAM_CATEGORIES),
                    "description": _text(
                        self._language,
                        cn="可选 trajectory span 类型过滤。",
                        en="Optional trajectory span kind filter.",
                    ),
                },
                "name_contains": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="可选 span 名称大小写不敏感子串过滤。",
                        en="Optional case-insensitive span-name substring filter.",
                    ),
                },
                "tool_name": {
                    "type": "string",
                    "description": _text(
                        self._language,
                        cn="tool span 的可选工具名过滤。",
                        en="Optional tool-name filter for tool spans.",
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
            items = [_span_index_item(span) for _, span in _trajectory_span_index(scope.trajectory)]

            kind = args.get("kind")
            if kind:
                items = [item for item in items if item.get("kind") == kind]

            name_contains = str(args.get("name_contains") or "").casefold()
            if name_contains:
                items = [item for item in items if name_contains in str(item.get("name") or "").casefold()]

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


class EvolutionReviewReadTrajectorySpansTool(_EvolutionReviewTool):
    tool_name = "read_trajectory_spans"
    tool_id = "EvolutionReviewReadTrajectorySpansTool"

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
                        cn="当前审查通过 list_trajectory_spans 获得的 span ref。",
                        en="Span refs returned by list_trajectory_spans for the current review.",
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
            by_ref = dict(_trajectory_span_index(scope.trajectory))
            missing = [item_ref for item_ref in refs if item_ref not in by_ref]
            if missing:
                return ToolOutput(success=False, error=f"unknown trajectory refs: {missing}")
            runtime.record_trajectory_read(ref, session_id=session_id, refs=refs)
            return ToolOutput(success=True, data={"items": [_span_detail(by_ref[item_ref]) for item_ref in refs]})
        except Exception as exc:
            return self.failure(exc)


__all__ = [
    "REVIEW_EVOLUTION_TOOL_NAMES",
    "EvolutionReviewListSkillExperiencesTool",
    "EvolutionReviewListTrajectorySpansTool",
    "EvolutionReviewReadSkillExperiencesTool",
    "EvolutionReviewReadTrajectorySpansTool",
    "SubmitEvolutionReviewResultTool",
    "create_evolution_review_tools",
]
