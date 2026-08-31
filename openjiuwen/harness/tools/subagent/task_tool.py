# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional


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
from openjiuwen.harness.kv_cache import kv_cache_subagent_lifecycle
from openjiuwen.harness.subagent_lifecycle import (
    cleanup_subagent_task_resources,
    prepare_subagent_task_resources,
)
from openjiuwen.harness.execution_subject import (
    ExecutionSubject,
    current_execution_subject,
    execution_subject_scope,
)
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
try:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
        browser_agent_log_info,
    )
except Exception:  # pragma: no cover - browser runtime is optional here
    browser_agent_log_info = None


# Keep the delegation deadline above the browser runtime's 600-second task
# budget so subagent startup and final response assembly are not cut short.
DEFAULT_SUBAGENT_TASK_TIMEOUT_S = 720.0


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
        )
        for key in resume_keys:
            resume_context[key] = browser_result.get(key)
        data["resume_context"] = resume_context
        return data

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

        # Parse inputs
        if isinstance(inputs, dict):
            subagent_type = inputs.get("subagent_type")
            task_description = inputs.get("task_description")
            resume_task_id = inputs.get("resume_task_id")
        else:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )

        if not subagent_type or not task_description:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="Both 'subagent_type' and 'task' are required",
            )

        browser_capabilities: Optional[List[str]] = None
        if str(subagent_type) == "browser_agent":
            raw_capabilities = inputs.get("browser_capabilities")
            if raw_capabilities is None:
                browser_capabilities = []
            elif isinstance(raw_capabilities, list) and all(
                isinstance(capability, str) for capability in raw_capabilities
            ):
                browser_capabilities = list(raw_capabilities)
            else:
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason="'browser_capabilities' must be a list of strings",
                )
        elif resume_task_id:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="'resume_task_id' is supported only for browser_agent",
            )

        runtime_parent_session_id = parent_session.get_session_id()
        affinity_enabled = kv_cache_subagent_lifecycle.affinity_enabled(self.parent_agent)
        parent_cache_id = runtime_parent_session_id
        if affinity_enabled:
            parent_cache_id = kv_cache_subagent_lifecycle.resolve_subagent_parent_cache_id(
                parent_session
            )
        try:
            sub_session_id = self._build_sub_session_id(
                runtime_parent_session_id,
                str(subagent_type),
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
        logger.info(
            f"[TaskTool] Creating subagent: {subagent_type}, "
            f"runtime_parent_session={runtime_parent_session_id}, "
            f"cache_parent_session={parent_cache_id}, sub_session={sub_session_id}"
        )

        try:
            if browser_capabilities is None:
                subagent = self.parent_agent.create_subagent(subagent_type, sub_session_id)
            else:
                subagent = self.parent_agent.create_subagent(
                    subagent_type,
                    sub_session_id,
                    browser_capabilities=browser_capabilities,
                )
        except Exception as exc:
            logger.error(f"[TaskTool] Subagent creation failed: type={subagent_type}, error={exc}")
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent {subagent_type} creation failed: {exc}",
            ) from exc

        query_summary = _summarize_task_description(task_description)
        invoke_log = (
            "[TaskTool] Invoking subagent with isolated session: %s, "
            "subagent_type=%s, query_summary=%s"
        )
        if str(subagent_type) == "browser_agent" and browser_agent_log_info is not None:
            browser_agent_log_info(invoke_log, sub_session_id, subagent_type, query_summary)
        else:
            logger.info(invoke_log, sub_session_id, subagent_type, query_summary)

        succeeded = False
        child_session = None
        parent_subject = current_execution_subject()
        parent_subject_id = parent_subject.subject_id if parent_subject else "main"
        subject = ExecutionSubject(
            subject_id=f"subagent:{uuid.uuid4().hex}",
            display_name=str(
                getattr(getattr(subagent, "card", None), "name", None)
                or subagent_type
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

            owner_root = get_root_span(session_id=runtime_parent_session_id)
            if owner_root is not None and owner_root.is_recording():
                # The subagent runtime binds its isolated sub-session in
                # callbacks that may execute outside the dispatch task's
                # ContextVars.  Register an explicit alias to the owning run
                # so those callbacks resolve A -> A, never "the only live"
                # unrelated run B.
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
                # Invoke subagent with isolated session_id
                subagent_inputs = {
                    "query": task_description,
                    "conversation_id": sub_session_id,
                }
                if str(subagent_type) == "browser_agent" and resume_task_id:
                    subagent_inputs["run_context"] = {
                        "browser_resume": True,
                        "resume_task_id": sub_session_id,
                    }
                if affinity_enabled:
                    subagent_inputs["parent_session_id"] = parent_cache_id
                    # The child owns a new request-local report.  Keep the
                    # delegation boundary explicit when the child/cache lineage
                    # feature is enabled, without changing the legacy disabled
                    # invocation payload.
                    subagent_inputs["delegation_id"] = sub_session_id
                    if parent_invocation_id:
                        subagent_inputs["parent_invocation_id"] = parent_invocation_id
                    await child_session.pre_run(inputs=subagent_inputs)
                    await kv_cache_subagent_lifecycle.prepare_subagent(
                        child_session,
                        subagent_type=str(subagent_type),
                    )
                delegation_token = bind_usage_delegation(
                    build_usage_delegation_attribution(
                        agent_id=getattr(getattr(subagent, "card", None), "id", None),
                        parent_session_id=runtime_parent_session_id,
                        delegation_id=sub_session_id,
                        parent_invocation_id=parent_invocation_id,
                    )
                )
                try:
                    result = await _run_subagent_with_observable_stream(
                        subagent,
                        subagent_inputs,
                        session=child_session,
                    )
                finally:
                    reset_usage_delegation(delegation_token)
                succeeded = True
                output = result.get("output", "")
                data = self._build_result_data(
                    result,
                    output,
                    agent_id=subagent.card.id,
                    subagent_type=str(subagent_type),
                    sub_session_id=sub_session_id,
                )
                return ToolOutput(success=True, data=data, error=None)
            except Exception as e:
                logger.error(f"[TaskTool] Subagent: {subagent_type} execution failed, error={e}")
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason=f"Subagent {subagent_type} execution failed: {e}",
                ) from e
            finally:
                if owner_root is not None:
                    unregister_run_root_span(
                        owner_root,
                        session_id=sub_session_id,
                    )
                await cleanup_subagent_task_resources(subagent)
                if child_session is not None:
                    await kv_cache_subagent_lifecycle.finish_subagent(
                        child_session,
                        subagent_type=str(subagent_type),
                        succeeded=succeeded,
                    )
                    await child_session.post_run()

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


def create_task_tool(
    parent_agent: "DeepAgent",
    available_agents: str,
    language: str = "cn",
    agent_id: Optional[str] = None,
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

    return [TaskTool(card=card, parent_agent=parent_agent, language=language)]


__all__ = [
    "DEFAULT_SUBAGENT_TASK_TIMEOUT_S",
    "TaskTool",
    "create_task_tool",
]
