# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for Code Graph DeepAgent tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.schema.code_graph import (
    DEFAULT_GRAPH_QUERY_POLICY,
    CodeGraphRunState,
    GraphQueryPolicy,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

# Keys whose lists carry the bulk of a payload; trimmed tail-first when a single
# result would otherwise dominate the prompt.
_TRIMMABLE_KEYS = (
    "matches",
    "symbols",
    "related",
    "unresolved",
    "chunks",
    "definitions",
    "paths",
    "nodes",
)


@dataclass
class CodeGraphToolContext:
    """Runtime context shared by Code Graph tools. Index is not built here."""

    repo_root: str
    config: CodeGraphConfig
    language: str = "en"
    agent_id: str | None = None
    run_state: CodeGraphRunState | None = None
    policy: GraphQueryPolicy = DEFAULT_GRAPH_QUERY_POLICY
    # Conversation / locate-run id. Never used as the graph cache key.
    session_id: str = ""
    resolve_root: Any = None


def trim_payload(payload: dict[str, Any], policy: GraphQueryPolicy) -> dict[str, Any]:
    """Bound one result's size without hiding that it was cut.

    Trims the tail of the largest list first: results are ranked, so the tail is
    the least useful part. The payload keeps its status; callers see
    ``truncated`` and a warning telling them to narrow the query.
    """
    limit = max(1000, int(policy.max_payload_chars))
    if len(repr(payload)) <= limit:
        return payload
    for key in sorted(
        (key for key in _TRIMMABLE_KEYS if isinstance(payload.get(key), list)),
        key=lambda key: len(payload[key]),
        reverse=True,
    ):
        items = payload[key]
        while len(items) > 1 and len(repr(payload)) > limit:
            items = items[: max(1, len(items) // 2)]
            payload[key] = items
        if len(repr(payload)) <= limit:
            break
    payload["truncated"] = True
    warnings = payload.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.append("result truncated to fit the prompt; narrow the symbol, path_prefix, or depth")
    return payload


class CodeGraphBaseTool(Tool):
    """Thin Tool wrapper around ``CodeGraphService``."""

    def __init__(
        self,
        metadata_name: str,
        tool_id_prefix: str,
        context: CodeGraphToolContext,
        *,
        parallel_safe: bool = True,
    ) -> None:
        super().__init__(
            build_tool_card(
                metadata_name,
                tool_id_prefix,
                context.language,
                agent_id=context.agent_id,
                options=ToolCardBuildOptions(parallel_safe=parallel_safe, idempotent=parallel_safe),
            )
        )
        self.context = context

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)

    def current_repo_root(self) -> str:
        resolver = getattr(self.context, "resolve_root", None)
        if callable(resolver):
            try:
                resolved = resolver()
            except Exception:  # noqa: BLE001 — fall back to the bound root
                resolved = None
            if resolved:
                return str(Path(resolved).resolve())
        return self.context.repo_root

    async def _service(self) -> CodeGraphService:
        from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

        manager = get_code_graph_manager(self.context.config)
        root = self.current_repo_root()
        self.context.repo_root = root
        return await manager.get_service(root, self.context.config, ensure=True)

    @property
    def policy(self) -> GraphQueryPolicy:
        return self.context.policy or DEFAULT_GRAPH_QUERY_POLICY

    def _default_results(self) -> int:
        """Result count when the caller asked for none.

        A bound graph run uses the smaller default so a search reads like
        candidate generation. Tools invoked without run state keep the wider
        default.
        """
        state = self.context.run_state
        if state is not None:
            return self.policy.default_results
        return self.policy.max_results

    def _touch_budget(self) -> ToolOutput | None:
        """Count the call, and refuse it only where a locator budget applies.

        The graph profile inside a coding agent keeps the counter for the
        trajectory but is never cut off: exhausting a total graph-call budget
        used to leave that agent with no retrieval and a half-written patch.
        """
        state = self.context.run_state
        if state is None:
            return None
        state.tool_calls += 1
        if state.skips_locator_budget:
            return None
        if state.over_budget() and not state.finished:
            state.warnings.append("max_tool_calls budget reached")
            return ToolOutput(
                success=True,
                data=status_payload(
                    CodeGraphStatus.PARTIAL,
                    message=(f"budget exhausted; call {state.terminal_tool_name} with status PARTIAL"),
                    extra={"tool_calls": state.tool_calls},
                ),
            )
        return None

    def _persist_session(self) -> None:
        """Graph hops stay in ``run_state``; submit/select persist the packet."""
        return

    async def _invoke_service(
        self,
        operation: Callable[[CodeGraphService], Awaitable[dict[str, Any]]],
    ) -> ToolOutput:
        budget = self._touch_budget()
        if budget is not None:
            return budget
        try:
            service = await self._service()
            payload = await operation(service)
        except Exception as exc:  # noqa: BLE001 — tools must not crash the agent
            payload = status_payload(
                CodeGraphStatus.UNAVAILABLE,
                message=str(exc),
            )
        if isinstance(payload, dict):
            payload = trim_payload(payload, self.policy)
        if self.context.run_state is not None and isinstance(payload, dict):
            self.context.run_state.remember_payload(payload)
            self._persist_session()
        success = str(payload.get("status")) not in {
            CodeGraphStatus.UNAVAILABLE.value,
            CodeGraphStatus.ERROR.value,
        }
        return ToolOutput(success=success, data=payload, error=None if success else str(payload.get("message")))


def resolve_repo_root(
    *,
    explicit: str | None = None,
    workspace_root: str | Path | None = None,
    cwd: str | Path | None = None,
    project_root: str | Path | None = None,
) -> str:
    """Pick the repository root from DeepAgent context. Never from LLM input."""
    for candidate in (explicit, project_root, cwd, workspace_root):
        if candidate:
            return str(Path(candidate).resolve())
    return str(Path.cwd().resolve())
