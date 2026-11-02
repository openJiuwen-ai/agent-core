# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import copy
import math
from time import monotonic
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional


if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.kv_cache import kv_cache_hooks
from openjiuwen.harness.subagent_lifecycle import (
    cleanup_subagent_task_resources,
    prepare_subagent_task_resources,
)
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.subagent.task_manager import (
    AsyncSubagentTaskManager, TaskRequest,
)
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.subagent_types import (
    apply_subagent_type_enum,
    format_unknown_subagent_type,
    listed_subagent_types,
    listed_types_from_parent,
    resolved_type_from_parent,
    tool_owner_id_of,
)
# Keep the delegation deadline above the browser runtime's 600-second task
# budget so subagent startup and final response assembly are not cut short.
DEFAULT_SUBAGENT_TASK_TIMEOUT_S = 1800.0


class TaskTool(Tool):
    """Tool for delegating tasks to ephemeral subagents with isolated context.

    This tool creates a new subagent instance, assigns it an independent
    session to prevent context pollution, and returns the subagent's
    completed results as they become available.
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
        self.task_manager = AsyncSubagentTaskManager(tool_owner_id_of(parent_agent) or card.id)
        tracker = getattr(parent_agent, "_track_task_group_tool", None)
        if callable(tracker):
            tracker(self)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput | dict[str, Any]:
        if not isinstance(inputs, dict):
            return ToolOutput(success=False, error="invalid_arguments: inputs must be an object")
        return await self._invoke_group(inputs, **kwargs)

    def parse_group_inputs(self, inputs):
        """Validate and resolve a group call without submitting or consuming tasks."""
        action = inputs.get("action")
        if action not in {"dispatch", "wait", "cancel"}:
            raise ValueError("invalid_action")
        if any(key in inputs for key in ("subagent_type", "task_description", "browser_capabilities")):
            raise ValueError("invalid_arguments: do not mix single-task and action fields")
        task_ids = inputs.get("task_ids", [])
        if not isinstance(task_ids, list) or any(not isinstance(key, str) or not key.strip() for key in task_ids):
            raise ValueError("invalid_arguments: task_ids must be nonempty strings")
        task_ids = list(dict.fromkeys(task_ids))
        requests = []
        if action == "dispatch":
            tasks = inputs.get("tasks")
            if not isinstance(tasks, list) or not tasks or len(tasks) + len(task_ids) > 128:
                raise ValueError("invalid_arguments: a nonempty bounded tasks list is required")
            for task in tasks:
                if not isinstance(task, dict):
                    raise ValueError("invalid_arguments: each task must be an object")
                kind, description = task.get("subagent_type"), task.get("task_description")
                for value in (kind, description):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("invalid_arguments: each task needs a type and description")
                resolved = self._resolve_requested_type(kind)
                if isinstance(resolved, ToolOutput):
                    raise ValueError(str(resolved.error))
                capabilities = task.get("browser_capabilities")
                if capabilities is not None:
                    if (
                        resolved != "browser_agent"
                        or not isinstance(capabilities, list)
                        or any(not isinstance(item, str) for item in capabilities)
                    ):
                        raise ValueError("invalid_arguments: invalid browser_capabilities")
                    capabilities = tuple(capabilities)
                requests.append(TaskRequest(resolved, description, capabilities))
        elif "tasks" in inputs or not task_ids or len(task_ids) > 128:
            raise ValueError("invalid_arguments: wait/cancel requires task_ids and no tasks")
        timeout = inputs.get("timeout_s")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise ValueError("invalid_timeout")
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError("invalid_timeout")
        return action, requests, task_ids, timeout

    async def _invoke_group(self, inputs, **kwargs):
        started = monotonic()
        session = kwargs.get("session")
        if not isinstance(session, Session):
            raise build_error(StatusCode.TOOL_TASK_TOOL_INVOKED, reason="TaskTool requires a valid session")
        try:
            action, requests, task_ids, timeout = self.parse_group_inputs(inputs)
            call_id = kwargs.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("invalid_arguments: stable tool_call_id is required")
            manager = self.task_manager
            scope = manager.scope_for_session(session.get_session_id())
            if action == "dispatch":
                receipt = manager.submit_batch(
                    scope, call_id, requests, existing_ids=task_ids, executor_factory=self._group_executor
                )
                task_ids, submitted = receipt.wait_ids, receipt.submitted
            else:
                submitted = []
            if action == "cancel":
                states = await manager.cancel(scope, task_ids)
                manager.mark_delivered(scope, [item["task_id"] for item in states])
                return ToolOutput(
                    success=True,
                    data={
                        "tasks": states,
                        "pending_ids": [item["task_id"] for item in states if item["status"] == "cancelling"],
                    },
                )
            await self._resume_group_replies(scope, inputs.get("query"))
            properties = self.card.properties or {}
            configured = properties.get("resilience", {}).get("timeout_s", DEFAULT_SUBAGENT_TASK_TIMEOUT_S)
            wait_limit = max(0, float(configured) - (monotonic() - started) - 5)
            wait_limit = min(wait_limit, timeout) if timeout is not None else wait_limit
            result = await manager.wait_any(scope, task_ids, wait_limit)
            if result.interruptions:
                return self._group_interrupt(result.interruptions)
            manager.mark_delivered(scope, [item["task_id"] for item in result.ready])
            return ToolOutput(
                success=True,
                data={
                    "submitted": submitted,
                    "ready": result.ready,
                    "pending_ids": result.pending_ids,
                    "timed_out": result.timed_out,
                },
            )
        except (ValueError, TypeError) as exc:
            return ToolOutput(success=False, error=str(exc))

    def _group_executor(self, execution):
        async def execute(query):
            return await self._execute_subagent_task(execution, query)

        async def cleanup():
            try:
                if execution.subagent is not None:
                    await cleanup_subagent_task_resources(execution.subagent)
            finally:
                if execution.affinity_enabled:
                    await kv_cache_hooks.evict_subagent(
                        self.parent_agent,
                        sub_session_id=execution.child_session_id,
                        parent_session_id=execution.scope.parent_session_id,
                    )

        return execute, cleanup

    async def _execute_subagent_task(self, execution, query=None):
        request = execution.request
        if execution.subagent is None:
            extras = (
                {}
                if request.browser_capabilities is None
                else {"browser_capabilities": list(request.browser_capabilities)}
            )
            execution.subagent = self.parent_agent.create_subagent(
                request.subagent_type, execution.child_session_id, isolated=True, **extras
            )
        if not execution.resources_prepared:
            await prepare_subagent_task_resources(execution.subagent)
            execution.resources_prepared = True
            execution.affinity_enabled = kv_cache_hooks.affinity_enabled(self.parent_agent)
        child_inputs = {
            "query": request.task_description if query is None else query,
            "conversation_id": execution.child_session_id,
        }
        if execution.affinity_enabled:
            child_inputs["parent_session_id"] = execution.scope.parent_session_id
        result = await execution.subagent.invoke(child_inputs)
        execution.raw_result = result
        return result

    async def _resume_group_replies(self, scope, reply):
        from openjiuwen.core.session import InteractiveInput

        if not isinstance(reply, InteractiveInput):
            return
        for record in self.task_manager.interrupted_records(scope):
            selected = InteractiveInput()
            for inner_id in (record.execution.raw_result or {}).get("interrupt_ids", []):
                public_id = f"group:{record.execution.task_id}:{inner_id}"
                if public_id in reply.user_inputs:
                    selected.update(inner_id, reply.user_inputs[public_id])
            if selected.user_inputs:
                await self.task_manager.resume(scope, record.execution.task_id, selected)

    @staticmethod
    def _group_interrupt(interruptions):
        output = {"result_type": "interrupt", "interrupt_ids": [], "state": []}
        for task_id, raw in interruptions.items():
            identifiers = {key: f"group:{task_id}:{key}" for key in raw.get("interrupt_ids", [])}
            output["interrupt_ids"].extend(identifiers.values())
            for item in raw.get("state", []):
                item = copy.deepcopy(item)
                if getattr(item, "payload", None) is not None:
                    item.payload.id = identifiers.get(item.payload.id, item.payload.id)
                output["state"].append(item)
        return output

    async def close_task_groups(
        self, parent_session_id: str | None = None, *, reason: str = "parent_finished",
    ) -> dict | None:
        """Close the matching logical run, including children paused for approval."""
        scope = self.task_manager.scope_if_active(parent_session_id)
        if scope is None:
            return None
        report = await self.task_manager.close_run(scope, reason)
        return report

    def _resolve_requested_type(self, requested: str) -> str | ToolOutput:
        """Return a registered type name, or a model-visible error output."""
        resolved = resolved_type_from_parent(self.parent_agent, requested)
        if resolved is not None:
            return resolved
        return ToolOutput(
            success=False,
            error=format_unknown_subagent_type(
                requested,
                listed_types_from_parent(self.parent_agent),
                self.language,
            ),
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
    card.properties = {
        **(card.properties if isinstance(card.properties, dict) else {}),
        "resilience": {"timeout_s": DEFAULT_SUBAGENT_TASK_TIMEOUT_S},
    }
    tool = TaskTool(card=card, parent_agent=parent_agent, language=language)
    config = getattr(parent_agent, "deep_config", None)
    apply_subagent_type_enum(
        card,
        listed_subagent_types(getattr(config, "subagents", None)),
    )

    return [tool]


__all__ = [
    "DEFAULT_SUBAGENT_TASK_TIMEOUT_S",
    "TaskTool",
    "create_task_tool",
]
