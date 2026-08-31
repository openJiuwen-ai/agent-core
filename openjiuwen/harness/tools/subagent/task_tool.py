# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional


if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.kv_cache import kv_cache_hooks
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
try:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
        browser_agent_log_info,
    )
except Exception:  # pragma: no cover - browser runtime is optional here
    browser_agent_log_info = None


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


def _build_success_tool_content(
    output: Any,
    *,
    subagent_type: str,
    language: str = "cn",
) -> str:
    """Build a parent-facing tool message after a subagent completes."""
    output_text = str(output or "").strip()
    if language == "en":
        summary = (
            f"Subagent '{subagent_type}' completed successfully. "
            "Any permissions required inside the subagent were already handled. "
            "Do NOT call task_tool again or read the file directly — use this result."
        )
    else:
        summary = (
            f"子智能体「{subagent_type}」已完成任务。"
            "子任务内的权限审批已处理完毕，无需再次向用户确认或重复调用 task_tool / read_file。"
            "请直接根据下方结果回复用户。"
        )
    if output_text:
        return f"{summary}\n\n{output_text}"
    return summary


def resolve_task_tool_model(
    parent_agent: Any,
    *,
    model_name: str = "",
    model_tier: str = "",
) -> Any | None:
    """Resolve an optional per-call Model via host-bound ``resolve_subagent_model``.

    Product layers (e.g. jiuwenswarm) may bind
    ``parent_agent.resolve_subagent_model(model_name=..., model_tier=...)``
    returning ``(Model, err|None)`` or a bare ``Model``. When unbound or both
    selectors are empty, returns ``None`` so ``create_subagent`` keeps its
    existing ``spec.model or deep_config.model`` fallback.
    """
    name = (model_name or "").strip()
    tier = (model_tier or "").strip().lower()
    if not name and not tier:
        return None
    resolver = getattr(parent_agent, "resolve_subagent_model", None)
    if not callable(resolver):
        logger.debug(
            "[TaskTool] model_name/model_tier ignored: resolve_subagent_model not bound"
        )
        return None
    try:
        result = resolver(model_name=name, model_tier=tier)
    except Exception as exc:  # noqa: BLE001 — never break spawn
        logger.warning("[TaskTool] resolve_subagent_model failed: %s", exc)
        return None
    if isinstance(result, tuple):
        return result[0] if result else None
    return result


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
        self._pending_subagents: dict[str, tuple[Any, bool]] = {}

    @staticmethod
    def _build_sub_session_id(
        parent_session_id: str,
        subagent_type: str,
        tool_call_id: Optional[str] = None,
    ) -> str:
        normalized_type = str(subagent_type or "").strip()
        if kv_cache_hooks.is_sticky_subagent_type(normalized_type):
            # Deterministic ID so the session can be resumed on a FAIL → fix → re-verify loop.
            return f"{parent_session_id}_sub_{normalized_type}"
        normalized_tool_call_id = str(tool_call_id or "").strip()
        if normalized_tool_call_id:
            # The interrupt handler retries the same outer tool call on resume.
            # Reusing its ID keeps the retry attached to the interrupted child session.
            return f"{parent_session_id}_sub_{normalized_type}_{normalized_tool_call_id}"
        return f"{parent_session_id}_sub_{normalized_type}_{uuid.uuid4().hex[:8]}"

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput | dict[str, Any]:
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
            thinking = str(inputs.get("thinking") or "").strip()
            model_name = str(inputs.get("model_name") or "").strip()
            model_tier = str(inputs.get("model_tier") or "").strip().lower()
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

        parent_session_id = parent_session.get_session_id()
        sub_session_id = self._build_sub_session_id(
            parent_session_id,
            str(subagent_type),
            tool_call_id=kwargs.get("tool_call_id"),
        )
        pending_subagent = self._pending_subagents.pop(sub_session_id, None)
        if pending_subagent is None:
            logger.info(
                f"[TaskTool] Creating subagent: {subagent_type}, "
                f"parent_session={parent_session_id}, sub_session={sub_session_id}"
            )

            task_model = resolve_task_tool_model(
                self.parent_agent,
                model_name=model_name,
                model_tier=model_tier,
            )
            create_kwargs = {}
            if task_model is not None:
                create_kwargs["model"] = task_model

            try:
                if browser_capabilities is None:
                    subagent = self.parent_agent.create_subagent(
                        subagent_type,
                        sub_session_id,
                        **create_kwargs,
                    )
                else:
                    subagent = self.parent_agent.create_subagent(
                        subagent_type,
                        sub_session_id,
                        browser_capabilities=browser_capabilities,
                        **create_kwargs,
                    )
            except Exception as exc:
                logger.error(f"[TaskTool] Subagent creation failed: type={subagent_type}, error={exc}")
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason=f"Subagent {subagent_type} creation failed: {exc}",
                ) from exc

            try:
                from openjiuwen.harness.tools.subagent.thinking_hook import (
                    apply_subagent_thinking,
                )

                model = getattr(getattr(subagent, "deep_config", None), "model", None)
                apply_subagent_thinking(subagent, thinking=thinking, model=model)
            except Exception as exc:  # noqa: BLE001 — never break spawn
                logger.warning("[TaskTool] subagent thinking hook skipped: %s", exc)

            affinity_enabled = False
        else:
            subagent, affinity_enabled = pending_subagent
            logger.info(
                f"[TaskTool] Resuming interrupted subagent: {subagent_type}, "
                f"parent_session={parent_session_id}, sub_session={sub_session_id}"
            )

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
        interrupted = False
        try:
            if pending_subagent is None:
                affinity_enabled = kv_cache_hooks.affinity_enabled(self.parent_agent)
            if affinity_enabled:
                kv_cache_hooks.prefetch_sticky_subagent(
                    self.parent_agent,
                    subagent_type=str(subagent_type),
                    sub_session_id=sub_session_id,
                    parent_session_id=parent_session_id,
                )
            query = inputs.get("query")
            if query is None:
                query = task_description
            subagent_inputs = {
                "query": query,
                "conversation_id": sub_session_id,
            }
            if affinity_enabled:
                subagent_inputs["parent_session_id"] = parent_session_id
            result = await subagent.invoke(subagent_inputs)
            succeeded = True
            if (
                isinstance(result, dict)
                and result.get("result_type") == "interrupt"
                and "interrupt_ids" in result
            ):
                interrupted = True
                self._pending_subagents[sub_session_id] = (subagent, affinity_enabled)
                return result
            output = result.get("output", "")
            content = _build_success_tool_content(
                output,
                subagent_type=str(subagent_type),
                language=self.language,
            )
            return ToolOutput(
                success=True,
                data={
                    "content": content,
                    "output": output,
                    "agent_id": subagent.card.id,
                },
                error=None,
            )
        except Exception as e:
            logger.error(f"[TaskTool] Subagent: {subagent_type} execution failed, error={e}")
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent {subagent_type} execution failed: {e}",
            ) from e
        finally:
            if affinity_enabled:
                await kv_cache_hooks.finish_subagent(
                    self.parent_agent,
                    subagent_type=str(subagent_type),
                    sub_session_id=sub_session_id,
                    parent_session_id=parent_session_id,
                    succeeded=succeeded,
                )

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

    return [TaskTool(card=card, parent_agent=parent_agent, language=language)]


__all__ = [
    "TaskTool",
    "create_task_tool",
    "resolve_task_tool_model",
    "_build_success_tool_content",
]
