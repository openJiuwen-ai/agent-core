# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Collection, List, Optional

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    bind_usage_delegation,
    build_usage_delegation_attribution,
    current_usage_invocation_id,
    reset_usage_delegation,
)
from openjiuwen.harness.execution_subject import (
    ExecutionSubject,
    current_execution_subject,
    execution_subject_scope,
)
from openjiuwen.harness.kv_cache import kv_cache_subagent_lifecycle
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.subagent_lifecycle import (
    cleanup_subagent_task_resources,
    prepare_subagent_task_resources,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

try:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
        browser_agent_log_info,
    )
except Exception:  # pragma: no cover - browser runtime is optional here
    browser_agent_log_info = None


# Keep one delegation above the browser runtime's 540-second complex-task
# slice so startup, state reconciliation, and final result assembly can finish.
DEFAULT_SUBAGENT_TASK_TIMEOUT_S = 720.0
_BROWSER_QUERY_STATE_KEY = "__browser_query_delegation_state__"
_BROWSER_SIMPLE_QUERY_BUDGET_S = 240.0
_BROWSER_COMPLEX_QUERY_BUDGET_S = 600.0
_BROWSER_QUERY_RESUME_LIMIT = 1


def _summarize_task_description(task_description: Any) -> dict[str, Any]:
    task_text = str(task_description or "")
    task_hash = ""
    if task_text:
        task_hash = hashlib.sha256(
            task_text.encode("utf-8", errors="ignore")
        ).hexdigest()[:12]

    return {
        "redacted": True,
        "length": len(task_text),
        "sha256_12": task_hash,
    }


async def _run_subagent_with_observable_stream(
    subagent: Any,
    inputs: dict[str, Any],
    session: Session | None = None,
) -> dict[str, Any]:
    """Run a subagent through its public stream while returning invoke-style output.

    Builtin DeepAgent subagents expose both ``invoke`` and ``stream``.  The
    streaming path is required for truthful first-token and generation timing;
    chunks stay inside this task tool and only the terminal answer is returned
    to the parent agent.  Third-party test/adaptor agents that only implement
    ``invoke`` retain their existing behavior.
    """
    invoke_kwargs = {"session": session} if session is not None else {}
    stream = getattr(subagent, "stream", None)
    if not callable(stream):
        return await subagent.invoke(inputs, **invoke_kwargs)

    output_parts: list[str] = []
    terminal_result: dict[str, Any] | None = None
    async for chunk in stream(inputs, **invoke_kwargs):
        chunk_type = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None)
        if isinstance(chunk, dict):
            chunk_type = chunk.get("type", chunk_type)
            payload = chunk.get("payload", payload)
        if not isinstance(payload, dict):
            continue
        if chunk_type == "llm_output":
            content = payload.get("content")
            if isinstance(content, str):
                output_parts.append(content)
            continue
        if chunk_type != "answer":
            continue
        terminal_result = dict(payload)
        terminal_result.setdefault("result_type", "answer")
        if "output" not in terminal_result:
            content = terminal_result.get("content")
            if isinstance(content, str):
                terminal_result["output"] = content

    if terminal_result is None:
        terminal_result = {
            "output": "".join(output_parts),
            "result_type": "answer",
        }
    if terminal_result.get("result_type") == "error":
        raise RuntimeError(str(terminal_result.get("output") or "subagent failed"))
    return terminal_result


@dataclass
class _BrowserQueryContext:
    """State shared by one initial browser delegation and its focused resume."""

    records: dict[str, dict[str, Any]]
    record: dict[str, Any]
    key: tuple[str, str]
    task_description: Any
    resume_task_id: str
    early_output: ToolOutput | None = None


@dataclass(frozen=True)
class _SubagentInputContext:
    """Related invocation metadata used to build one subagent request."""

    sub_session_id: str
    parent_session_id: str
    parent_invocation_id: str | None
    affinity_enabled: bool
    browser_query: _BrowserQueryContext | None


class TaskTool(Tool):
    """Tool for delegating tasks to ephemeral subagents with isolated context.

    This tool creates a new subagent instance, assigns it an independent
    session to prevent context pollution, and returns the subagent's
    final output after task completion.
    """

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
        allowed_subagent_types: Collection[str] | None = None,
    ):
        """Initialize TaskTool.

        Args:
            card: Tool metadata card.
            parent_agent: Parent DeepAgent instance used to clone config
                and create subagents.
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__(card)
        self.parent_agent = parent_agent
        self.language = language
        self._active_browser_queries: set[tuple[str, str]] = set()
        self.set_allowed_subagent_types(allowed_subagent_types)

    def set_allowed_subagent_types(
        self,
        allowed_subagent_types: Collection[str] | None,
    ) -> None:
        """Restrict which configured subagents may use synchronous delegation."""
        self._allowed_subagent_types = (
            None
            if allowed_subagent_types is None
            else frozenset(
                str(name).strip()
                for name in allowed_subagent_types
                if str(name).strip()
            )
        )

    @staticmethod
    def _build_sub_session_id(
        parent_session_id: str,
        subagent_type: str,
        resume_task_id: str = "",
    ) -> str:
        normalized_type = str(subagent_type or "").strip()
        normalized_resume_id = str(resume_task_id or "").strip()
        if normalized_resume_id:
            expected_prefix = f"{parent_session_id}_sub_{normalized_type}_"
            if normalized_type != "browser_agent" or not normalized_resume_id.startswith(expected_prefix):
                raise ValueError("resume_task_id is not valid for this parent browser task")
            return normalized_resume_id
        if kv_cache_subagent_lifecycle.is_sticky_subagent_type(normalized_type):
            # Deterministic ID so the session can be resumed on a FAIL → fix → re-verify loop.
            return f"{parent_session_id}_sub_{normalized_type}"
        return f"{parent_session_id}_sub_{normalized_type}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _extract_browser_result(result: Any, output: Any) -> dict[str, Any]:
        if isinstance(result, dict) and isinstance(result.get("authoritative_browser_result"), dict):
            return dict(result["authoritative_browser_result"])
        try:
            parsed = json.loads(str(output or ""))
        except (TypeError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        browser_result = parsed.get("browser_result")
        return dict(browser_result) if isinstance(browser_result, dict) else {}

    @classmethod
    def _build_result_data(
        cls,
        result: Any,
        output: Any,
        *,
        agent_id: str,
        subagent_type: str,
        sub_session_id: str,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"output": output, "agent_id": agent_id}
        if str(subagent_type) != "browser_agent":
            return data
        browser_result = cls._extract_browser_result(result, output)
        data["resume_task_id"] = sub_session_id
        if not browser_result:
            return data
        data["browser_result"] = browser_result
        data["retryable"] = bool(browser_result.get("retryable"))
        resume_context: dict[str, Any] = {}
        resume_keys = (
            "status",
            "missing_fields",
            "missing_slots",
            "requested_slots",
            "blockers",
            "evidence",
            "current_page",
            "recommended_recovery",
            "resume_count",
            "deadline",
        )
        for key in resume_keys:
            resume_context[key] = browser_result.get(key)
        data["resume_context"] = resume_context
        return data

    @staticmethod
    def _browser_query_id(parent_session_id: str, task_description: Any) -> str:
        invocation_id = str(current_usage_invocation_id() or "").strip()
        if invocation_id:
            return invocation_id[:128]
        fallback = f"{parent_session_id}\x1f{str(task_description or '').strip()}"
        return f"fallback-{hashlib.sha256(fallback.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _browser_query_budget(task_description: Any) -> float:
        normalized = str(task_description or "").lower()
        complex_tokens = (
            "compare",
            "filter",
            "sort",
            "form",
            "checkout",
            "cart",
            "login",
            "select",
            "submit",
            "比较",
            "对比",
            "筛选",
            "排序",
            "表单",
            "购物车",
            "登录",
            "选择",
            "提交",
            "预订",
        )
        if any(token in normalized for token in complex_tokens):
            return _BROWSER_COMPLEX_QUERY_BUDGET_S
        return _BROWSER_SIMPLE_QUERY_BUDGET_S

    @staticmethod
    def _load_browser_query_records(parent_session: Session) -> dict[str, dict[str, Any]]:
        records = parent_session.get_state(_BROWSER_QUERY_STATE_KEY)
        if not isinstance(records, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in records.items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _save_browser_query_records(
        parent_session: Session,
        records: dict[str, dict[str, Any]],
    ) -> None:
        parent_session.update_state({_BROWSER_QUERY_STATE_KEY: records})

    @staticmethod
    def _find_browser_query_record(
        records: dict[str, dict[str, Any]],
        query_id: str,
        resume_task_id: str,
    ) -> tuple[str, dict[str, Any] | None]:
        record = records.get(query_id)
        if record is not None or not resume_task_id:
            return query_id, record
        for stored_query_id, candidate in records.items():
            if str(candidate.get("sub_session_id") or "") == resume_task_id:
                return stored_query_id, candidate
        return query_id, None

    @staticmethod
    def _focused_browser_resume_task(record: dict[str, Any]) -> str:
        browser_result = record.get("browser_result")
        browser_result = browser_result if isinstance(browser_result, dict) else {}
        missing_slots = [
            dict(slot)
            for slot in browser_result.get("missing_slots") or []
            if isinstance(slot, dict)
        ][:12]
        if not missing_slots:
            missing_slots = [
                {"field": str(field_name)}
                for field_name in browser_result.get("missing_fields") or []
                if str(field_name).strip()
            ][:12]
        recovery = str(browser_result.get("recommended_recovery") or "").strip()
        return (
            "Resume the same browser task from its current page and retained evidence. "
            f"Collect only these unresolved evidence slots: {json.dumps(missing_slots, ensure_ascii=False)}. "
            f"Recovery hint: {recovery or 'collect_missing_evidence_from_current_page'}. "
            "Do not repeat satisfied fields, restart navigation, or expand the task scope."
        )

    @classmethod
    def _existing_browser_query_output(
        cls,
        record: dict[str, Any],
        *,
        code: str,
    ) -> ToolOutput:
        browser_result = record.get("browser_result")
        browser_result = dict(browser_result) if isinstance(browser_result, dict) else {}
        payload = {
            "status": str(browser_result.get("status") or "in_progress"),
            "code": code,
            "query_id": record.get("query_id"),
            "resume_task_id": record.get("sub_session_id"),
            "retryable": bool(browser_result.get("retryable")),
            "browser_result": browser_result,
        }
        return ToolOutput(
            success=True,
            data={
                "output": json.dumps({"browser_orchestration": payload}, ensure_ascii=False),
                **payload,
            },
            error=None,
        )

    @staticmethod
    def _failed_browser_result(reason: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "retryable": False,
            "missing_fields": [],
            "missing_slots": [],
            "blockers": [str(reason or "browser_subagent_failed")[:300]],
            "evidence": [],
            "terminal_reason": str(reason or "browser_subagent_failed")[:120],
        }

    def _parse_invocation_inputs(
        self,
        inputs: Input,
    ) -> tuple[str, Any, str, Optional[List[str]]]:
        if not isinstance(inputs, dict):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )
        subagent_type = inputs.get("subagent_type")
        task_description = inputs.get("task_description")
        resume_task_id = str(inputs.get("resume_task_id") or "").strip()
        if not subagent_type or not task_description:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="Both 'subagent_type' and 'task' are required",
            )
        normalized_type = str(subagent_type).strip()
        if self._allowed_subagent_types is not None and normalized_type not in self._allowed_subagent_types:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=(
                    f"Subagent type '{normalized_type}' is not available through "
                    "task_tool"
                ),
            )
        if normalized_type != "browser_agent":
            if resume_task_id:
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason="'resume_task_id' is supported only for browser_agent",
                )
            return normalized_type, task_description, "", None

        raw_capabilities = inputs.get("browser_capabilities")
        if raw_capabilities is None:
            return normalized_type, task_description, resume_task_id, []
        if isinstance(raw_capabilities, list) and all(
            isinstance(capability_name, str) for capability_name in raw_capabilities
        ):
            return normalized_type, task_description, resume_task_id, list(raw_capabilities)
        raise build_error(
            StatusCode.TOOL_TASK_TOOL_INVOKED,
            reason="'browser_capabilities' must be a list of strings",
        )

    def _prepare_browser_query(
        self,
        parent_session: Session,
        parent_session_id: str,
        task_description: Any,
        requested_resume_id: str,
    ) -> _BrowserQueryContext:
        query_id = self._browser_query_id(parent_session_id, task_description)
        records = self._load_browser_query_records(parent_session)
        query_id, existing_query = self._find_browser_query_record(
            records,
            query_id,
            requested_resume_id,
        )
        key = (parent_session_id, query_id)
        if key in self._active_browser_queries:
            active_query = existing_query or {
                "query_id": query_id,
                "status": "running",
                "retryable": False,
            }
            return _BrowserQueryContext(
                records,
                active_query,
                key,
                task_description,
                requested_resume_id,
                self._existing_browser_query_output(
                    active_query,
                    code="browser_query_already_running",
                ),
            )

        if existing_query is None:
            if requested_resume_id:
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason="resume_task_id has no browser query state in this parent session",
                )
            started_at = time.time()
            budget_s = self._browser_query_budget(task_description)
            record = {
                "query_id": query_id,
                "status": "running",
                "retryable": False,
                "resume_count": 0,
                "started_at": started_at,
                "deadline_at": started_at + budget_s,
                "budget_s": budget_s,
                "original_task": str(task_description),
                "updated_at": started_at,
            }
            return _BrowserQueryContext(records, record, key, task_description, "")

        query = _BrowserQueryContext(
            records=records,
            record=existing_query,
            key=key,
            task_description=task_description,
            resume_task_id=requested_resume_id,
        )
        return self._prepare_existing_browser_query(parent_session, query)

    def _prepare_existing_browser_query(
        self,
        parent_session: Session,
        query: _BrowserQueryContext,
    ) -> _BrowserQueryContext:
        """Validate and focus the only permitted continuation for a browser query."""

        records = query.records
        existing_query = query.record
        requested_resume_id = query.resume_task_id
        stored_resume_id = str(existing_query.get("sub_session_id") or "").strip()
        if requested_resume_id and requested_resume_id != stored_resume_id:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="resume_task_id does not match the active browser query",
            )
        browser_result = existing_query.get("browser_result")
        browser_result = browser_result if isinstance(browser_result, dict) else {}
        if not browser_result and str(existing_query.get("status") or "") == "running":
            code = "browser_query_already_running"
            if float(existing_query.get("deadline_at") or 0.0) <= time.time():
                self._set_browser_query_failure(
                    parent_session,
                    records,
                    existing_query,
                    "browser_query_deadline_expired",
                )
                code = "browser_query_resume_not_allowed"
            return _BrowserQueryContext(
                records,
                existing_query,
                query.key,
                query.task_description,
                stored_resume_id,
                self._existing_browser_query_output(existing_query, code=code),
            )

        terminal_status = str(browser_result.get("status") or existing_query.get("status") or "")
        can_resume = bool(browser_result.get("retryable")) and terminal_status in {"partial", "blocked"}
        resume_count = int(existing_query.get("resume_count") or 0)
        if not can_resume or resume_count >= _BROWSER_QUERY_RESUME_LIMIT:
            return _BrowserQueryContext(
                records,
                existing_query,
                query.key,
                query.task_description,
                stored_resume_id,
                self._existing_browser_query_output(
                    existing_query,
                    code="browser_query_resume_not_allowed",
                ),
            )
        existing_query.update(
            {
                "resume_count": resume_count + 1,
                "status": "running",
                "updated_at": time.time(),
            }
        )
        return _BrowserQueryContext(
            records,
            existing_query,
            query.key,
            self._focused_browser_resume_task(existing_query),
            stored_resume_id,
        )

    @classmethod
    def _set_browser_query_failure(
        cls,
        parent_session: Session,
        records: dict[str, dict[str, Any]],
        record: dict[str, Any],
        reason: str,
    ) -> None:
        record.update(
            {
                "browser_result": cls._failed_browser_result(reason),
                "status": "failed",
                "retryable": False,
                "updated_at": time.time(),
            }
        )
        records[str(record["query_id"])] = record
        cls._save_browser_query_records(parent_session, records)

    @classmethod
    def _save_browser_result(
        cls,
        parent_session: Session,
        query: _BrowserQueryContext,
        browser_result: dict[str, Any],
    ) -> None:
        query.record.update(
            {
                "browser_result": browser_result,
                "status": str(browser_result.get("status") or "failed"),
                "retryable": bool(browser_result.get("retryable")),
                "updated_at": time.time(),
            }
        )
        query.records[str(query.record["query_id"])] = query.record
        cls._save_browser_query_records(parent_session, query.records)

    @staticmethod
    def _browser_run_context(query: _BrowserQueryContext) -> dict[str, Any]:
        return {
            "browser_query_id": query.record["query_id"],
            "browser_query_started_at": query.record["started_at"],
            "browser_query_deadline_at": query.record["deadline_at"],
            "browser_query_budget_s": query.record["budget_s"],
            "browser_resume": bool(query.resume_task_id),
            "resume_task_id": query.record["sub_session_id"],
        }

    def _create_subagent(
        self,
        normalized_type: str,
        sub_session_id: str,
        browser_capabilities: Optional[List[str]],
        *,
        parent_session: Session,
        browser_query: _BrowserQueryContext | None,
    ) -> Any:
        try:
            if browser_capabilities is None:
                return self.parent_agent.create_subagent(normalized_type, sub_session_id)
            return self.parent_agent.create_subagent(
                normalized_type,
                sub_session_id,
                browser_capabilities=browser_capabilities,
            )
        except Exception as exc:
            if browser_query is not None:
                self._set_browser_query_failure(
                    parent_session,
                    browser_query.records,
                    browser_query.record,
                    "browser_subagent_creation_failed",
                )
                self._active_browser_queries.discard(browser_query.key)
            logger.error(f"[TaskTool] Subagent creation failed: type={normalized_type}, error={exc}")
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent {normalized_type} creation failed: {exc}",
            ) from exc

    @staticmethod
    def _build_subagent_inputs(
        task_description: Any,
        context: _SubagentInputContext,
    ) -> dict[str, Any]:
        subagent_inputs: dict[str, Any] = {
            "query": task_description,
            "conversation_id": context.sub_session_id,
        }
        if context.browser_query is not None:
            subagent_inputs["run_context"] = TaskTool._browser_run_context(context.browser_query)
        if not context.affinity_enabled:
            return subagent_inputs
        subagent_inputs.update(
            {
                "parent_session_id": context.parent_session_id,
                "delegation_id": context.sub_session_id,
            }
        )
        if context.parent_invocation_id:
            subagent_inputs["parent_invocation_id"] = context.parent_invocation_id
        return subagent_inputs

    async def _invoke_subagent(
        self,
        subagent: Any,
        subagent_inputs: dict[str, Any],
        *,
        parent_session_id: str,
        session: Session | None = None,
    ) -> Any:
        """Narrow dispatch seam for host-side tracing without replacing TaskTool."""
        del parent_session_id
        return await _run_subagent_with_observable_stream(
            subagent,
            subagent_inputs,
            session=session,
        )

    async def _invoke_with_usage_delegation(
        self,
        subagent: Any,
        subagent_inputs: dict[str, Any],
        *,
        parent_session_id: str,
        sub_session_id: str,
        parent_invocation_id: str | None,
        session: Session | None,
    ) -> Any:
        delegation_token = bind_usage_delegation(
            build_usage_delegation_attribution(
                agent_id=getattr(getattr(subagent, "card", None), "id", None),
                parent_session_id=parent_session_id,
                delegation_id=sub_session_id,
                parent_invocation_id=parent_invocation_id,
            )
        )
        try:
            return await self._invoke_subagent(
                subagent,
                subagent_inputs,
                parent_session_id=parent_session_id,
                session=session,
            )
        finally:
            reset_usage_delegation(delegation_token)

    def _build_task_output(
        self,
        result: Any,
        *,
        normalized_type: str,
        sub_session_id: str,
        parent_session: Session,
        browser_query: _BrowserQueryContext | None,
        subagent: Any,
    ) -> ToolOutput:
        output = result.get("output", "")
        data = self._build_result_data(
            result,
            output,
            agent_id=subagent.card.id,
            subagent_type=normalized_type,
            sub_session_id=sub_session_id,
        )
        if browser_query is None:
            return ToolOutput(success=True, data=data, error=None)
        browser_result = data.get("browser_result")
        if not isinstance(browser_result, dict):
            browser_result = self._failed_browser_result("authoritative_browser_result_missing")
            data.update(
                {
                    "browser_result": browser_result,
                    "retryable": False,
                    "resume_context": dict(browser_result),
                }
            )
        data["query_id"] = browser_query.record["query_id"]
        self._save_browser_result(parent_session, browser_query, browser_result)
        return ToolOutput(success=True, data=data, error=None)

    def _record_browser_execution_failure(
        self,
        parent_session: Session,
        browser_query: _BrowserQueryContext | None,
        reason: str,
    ) -> None:
        if browser_query is None:
            return
        self._set_browser_query_failure(
            parent_session,
            browser_query.records,
            browser_query.record,
            reason,
        )

    async def _invoke_created_subagent(
        self,
        subagent: Any,
        *,
        normalized_type: str,
        task_description: Any,
        sub_session_id: str,
        parent_session_id: str,
        parent_cache_id: str,
        parent_session: Session,
        browser_query: _BrowserQueryContext | None,
        affinity_enabled: bool,
    ) -> ToolOutput:
        succeeded = False
        child_session: Session | None = None
        parent_subject = current_execution_subject()
        parent_subject_id = parent_subject.subject_id if parent_subject else "main"
        subject = ExecutionSubject(
            subject_id=f"subagent:{uuid.uuid4().hex}",
            display_name=str(
                getattr(getattr(subagent, "card", None), "name", None)
                or normalized_type
            ),
            kind="subagent",
            parent_subject_id=parent_subject_id,
            session_id=sub_session_id,
        )
        owner_root = None
        try:
            from openjiuwen.extensions.observability.span_context import get_root_span
            from openjiuwen.harness.observability.span_context import (
                register_run_root_span,
                unregister_run_root_span,
            )

            owner_root = get_root_span(session_id=parent_session_id)
            if owner_root is not None and owner_root.is_recording():
                register_run_root_span(owner_root, session_id=sub_session_id)
            else:
                owner_root = None
        except Exception as exc:
            logger.debug(
                "[TaskTool] Failed to bind subagent observability owner: %s",
                exc,
            )

        with execution_subject_scope(subject):
            try:
                await prepare_subagent_task_resources(subagent)
                parent_invocation_id = current_usage_invocation_id()
                if affinity_enabled:
                    child_session = kv_cache_subagent_lifecycle.create_subagent_session(
                        parent_session,
                        sub_session_id=sub_session_id,
                        parent_cache_id=parent_cache_id,
                        card=subagent.card,
                    )
                subagent_inputs = self._build_subagent_inputs(
                    task_description,
                    _SubagentInputContext(
                        sub_session_id=sub_session_id,
                        parent_session_id=parent_cache_id,
                        parent_invocation_id=parent_invocation_id,
                        affinity_enabled=affinity_enabled,
                        browser_query=browser_query,
                    ),
                )
                if child_session is not None:
                    await child_session.pre_run(inputs=subagent_inputs)
                    await kv_cache_subagent_lifecycle.prepare_subagent(
                        child_session,
                        subagent_type=normalized_type,
                    )
                result = await self._invoke_with_usage_delegation(
                    subagent,
                    subagent_inputs,
                    parent_session_id=parent_session_id,
                    sub_session_id=sub_session_id,
                    parent_invocation_id=parent_invocation_id,
                    session=child_session,
                )
                succeeded = True
                return self._build_task_output(
                    result,
                    normalized_type=normalized_type,
                    sub_session_id=sub_session_id,
                    parent_session=parent_session,
                    browser_query=browser_query,
                    subagent=subagent,
                )
            except asyncio.CancelledError:
                self._record_browser_execution_failure(
                    parent_session,
                    browser_query,
                    "browser_subagent_cancelled",
                )
                raise
            except Exception as exc:
                self._record_browser_execution_failure(
                    parent_session,
                    browser_query,
                    "browser_subagent_execution_failed",
                )
                logger.error(
                    f"[TaskTool] Subagent: {normalized_type} execution failed, error={exc}"
                )
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason=f"Subagent {normalized_type} execution failed: {exc}",
                ) from exc
            finally:
                if owner_root is not None:
                    unregister_run_root_span(
                        owner_root,
                        session_id=sub_session_id,
                    )
                await cleanup_subagent_task_resources(subagent)
                if browser_query is not None:
                    self._active_browser_queries.discard(browser_query.key)
                if child_session is not None:
                    await kv_cache_subagent_lifecycle.finish_subagent(
                        child_session,
                        subagent_type=normalized_type,
                        succeeded=succeeded,
                    )
                    await child_session.post_run()

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """Execute task by delegating to a subagent.

        Args:
            inputs: input_params containing subagent_type and task description.
            **kwargs: Additional parameters, including 'session' for parent session context.

        Returns:
            subagent's final result.

        Raises:
            ToolError: If subagent creation or execution fails.
        """
        parent_session = kwargs.get("session", None)
        if not isinstance(parent_session, Session):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="TaskTool requires a valid session in kwargs",
            )

        normalized_type, task_description, resume_task_id, browser_capabilities = (
            self._parse_invocation_inputs(inputs)
        )
        runtime_parent_session_id = parent_session.get_session_id()
        affinity_enabled = kv_cache_subagent_lifecycle.affinity_enabled(self.parent_agent)
        parent_cache_id = runtime_parent_session_id
        if affinity_enabled:
            parent_cache_id = kv_cache_subagent_lifecycle.resolve_subagent_parent_cache_id(
                parent_session
            )
        browser_query: _BrowserQueryContext | None = None
        if normalized_type == "browser_agent":
            browser_query = self._prepare_browser_query(
                parent_session,
                runtime_parent_session_id,
                task_description,
                resume_task_id,
            )
            if browser_query.early_output is not None:
                return browser_query.early_output
            task_description = browser_query.task_description
            resume_task_id = browser_query.resume_task_id

        try:
            sub_session_id = self._build_sub_session_id(
                runtime_parent_session_id,
                normalized_type,
                str(resume_task_id or ""),
            )
        except ValueError as exc:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=str(exc),
            ) from exc
        if affinity_enabled and not resume_task_id:
            sub_session_id = kv_cache_subagent_lifecycle.scope_sub_session_id(
                sub_session_id,
                runtime_parent_session_id=runtime_parent_session_id,
                parent_cache_id=parent_cache_id,
            )
        if browser_query is not None:
            browser_query.record["sub_session_id"] = sub_session_id
            browser_query.records[str(browser_query.record["query_id"])] = browser_query.record
            self._save_browser_query_records(parent_session, browser_query.records)
            self._active_browser_queries.add(browser_query.key)
        logger.info(
            f"[TaskTool] Creating subagent: {normalized_type}, "
            f"runtime_parent_session={runtime_parent_session_id}, "
            f"cache_parent_session={parent_cache_id}, sub_session={sub_session_id}"
        )

        query_summary = _summarize_task_description(task_description)
        invoke_log = (
            "[TaskTool] Invoking subagent with isolated session: %s, "
            "subagent_type=%s, query_summary=%s"
        )
        if normalized_type == "browser_agent" and browser_agent_log_info is not None:
            browser_agent_log_info(invoke_log, sub_session_id, normalized_type, query_summary)
        else:
            logger.info(invoke_log, sub_session_id, normalized_type, query_summary)
        subagent = self._create_subagent(
            normalized_type,
            sub_session_id,
            browser_capabilities,
            parent_session=parent_session,
            browser_query=browser_query,
        )
        return await self._invoke_created_subagent(
            subagent,
            normalized_type=normalized_type,
            task_description=task_description,
            sub_session_id=sub_session_id,
            parent_session_id=runtime_parent_session_id,
            parent_cache_id=parent_cache_id,
            parent_session=parent_session,
            browser_query=browser_query,
            affinity_enabled=affinity_enabled,
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


def create_task_tool(
    parent_agent: "DeepAgent",
    available_agents: str,
    language: str = "cn",
    agent_id: Optional[str] = None,
    allowed_subagent_types: Collection[str] | None = None,
) -> List[Tool]:
    """Create TaskTool instance for the given parent agent.

    Args:
        parent_agent: Parent DeepAgent instance.
        available_agents: Formatted string describing available subagent types.
        language: Language for tool parameters ('cn' or 'en').
        agent_id: Optional agent ID for unique tool ID.

    Returns:
        List containing a single TaskTool instance.
    """
    card = build_tool_card(
        name="task_tool",
        tool_id="task_tool",
        language=language,
        agent_id=agent_id,
        options=ToolCardBuildOptions(format_args={"available_agents": available_agents}),
    )
    card.properties = {
        **(card.properties if isinstance(card.properties, dict) else {}),
        "resilience": {"timeout_s": DEFAULT_SUBAGENT_TASK_TIMEOUT_S},
    }

    return [
        TaskTool(
            card=card,
            parent_agent=parent_agent,
            language=language,
            allowed_subagent_types=allowed_subagent_types,
        )
    ]


__all__ = [
    "DEFAULT_SUBAGENT_TASK_TIMEOUT_S",
    "TaskTool",
    "create_task_tool",
]
