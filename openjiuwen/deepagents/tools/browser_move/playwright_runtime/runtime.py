#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime wiring for browser tool registration and service lifecycle."""

from __future__ import annotations

import contextvars
import uuid
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import McpServerConfig, tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..controllers import ActionController, BaseController
from .agents import build_main_agent
from .config import BrowserRunGuardrails
from .service import BrowserService

_ctx_parent_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_session_id",
    default="",
)
_ctx_parent_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_request_id",
    default="",
)


class BrowserAgentRuntime:
    """Runtime that wires main agent + browser backend tool contract."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
    ) -> None:
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
        )
        self._browser_tool = None
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._main_agent = None
        self._controller: BaseController = ActionController()
        self._code_executor = None

    @property
    def service(self) -> BrowserService:
        return self._service

    @property
    def main_agent(self) -> Any:
        return self._main_agent

    @main_agent.setter
    def main_agent(self, value: Any) -> None:
        self._main_agent = value

    @property
    def browser_tool(self) -> Any:
        return self._browser_tool

    @property
    def browser_custom_action_tool(self) -> Any:
        return self._browser_custom_action_tool

    @property
    def browser_list_actions_tool(self) -> Any:
        return self._browser_list_actions_tool

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def code_executor(self) -> Any:
        return self._code_executor

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.main_runtime")
        if add_result is None or getattr(add_result, "is_ok", lambda: False)():
            return
        error_value = getattr(add_result, "value", add_result)
        if "already exist" in str(error_value):
            return
        raise RuntimeError(f"Failed to register {tool_name} tool: {error_value}")

    async def cancel_run(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.request_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.clear_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def ensure_started(self) -> None:
        await self._service.ensure_started()
        if self._browser_tool is not None:
            return
        # Build a direct code executor — calls browser_run_code on the Playwright MCP
        # client without going through an LLM worker. Look up the client dynamically so
        # we survive service restarts that recreate the underlying subprocess.
        _playwright_server_id = (self._service.mcp_cfg.server_id or "").strip() or getattr(
            self._service.mcp_cfg, "server_name", ""
        )

        async def _direct_code_executor(js_code: str):
            from playwright_runtime.browser_tools import get_registered_client
            client = get_registered_client(_playwright_server_id)
            if client is None:
                raise RuntimeError(
                    f"Playwright MCP client not found (server_id={_playwright_server_id!r})"
                )
            return await client.call_tool("browser_run_code", {"code": js_code})

        self._code_executor = _direct_code_executor
        self._controller.bind_code_executor(_direct_code_executor)
        self._controller.register_builtin_actions()
        action_details = self._controller.describe_actions()
        action_summary_lines: list[str] = []
        for action_name in sorted(action_details.keys()):
            spec = action_details.get(action_name, {})
            summary = str(spec.get("summary", "")).strip() or "No summary."
            when_to_use = str(spec.get("when_to_use", "")).strip()
            if when_to_use:
                action_summary_lines.append(f"- {action_name}: {summary} Use when: {when_to_use}")
            else:
                action_summary_lines.append(f"- {action_name}: {summary}")
        action_summary_text = "\n".join(action_summary_lines) if action_summary_lines else "- (none)"

        @tool(
            name="browser_run_task",
            description=(
                "Run a browser task in a sticky logical session. "
                "Prefer one comprehensive task per request instead of many tiny retries. "
                "Use a long timeout and do not pass timeout_s below the configured default; "
                "omit timeout_s to use the default long timeout. "
                "Returns JSON with ok/session_id/final/page/screenshot/error/attempt/failure_summary."
            ),
        )
        async def browser_run_task(
            task: str,
            session_id: str = "",
            request_id: str = "",
            timeout_s: int = 180,
        ) -> Dict[str, Any]:
            effective_session_id = (session_id or "").strip() or _ctx_parent_session_id.get()
            effective_request_id = (request_id or "").strip() or _ctx_parent_request_id.get()
            result = await self._service.run_task(
                task=task,
                session_id=effective_session_id,
                request_id=effective_request_id,
                timeout_s=timeout_s,
            )

            # Strip base64 screenshot from LLM context — the data URL can be 100K+
            # tokens and blows up the context window. The frontend reads the full
            # result separately; the main agent only needs ok/final/page/error.
            screenshot = result.get("screenshot")
            if isinstance(screenshot, str) and screenshot.startswith("data:"):
                result = {**result, "screenshot": "[screenshot saved]"}
            return result

        @tool(
            name="browser_custom_action",
            description=(
                "Run a registered custom browser action by name. "
                "Use this for higher-level actions such as drag-and-drop helpers. "
                "For browser_get_element_coordinates provide at least element_source (element_target optional); "
                "for browser_drag_and_drop provide element_source + element_target. "
                "Or use (coord_source_x + coord_source_y + coord_target_x + coord_target_y). "
                "Aliases source/target and source_x/source_y/target_x/target_y are accepted.\n"
                "Current registered actions:\n"
                f"{action_summary_text}\n"
                "If uncertain about params, call browser_list_custom_actions first and use its details."
            ),
        )
        async def browser_custom_action(
            action: str,
            session_id: str = "",
            request_id: str = "",
            params: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            effective_session_id = (session_id or "").strip() or _ctx_parent_session_id.get()
            effective_request_id = (request_id or "").strip() or _ctx_parent_request_id.get()
            self._controller.bind_runtime(self)
            self._controller.bind_code_executor(_direct_code_executor)
            return await self._controller.run_action(
                action=action,
                session_id=effective_session_id,
                request_id=effective_request_id,
                **(params or {}),
            )

        @tool(
            name="browser_list_custom_actions",
            description=(
                "List available custom actions and detailed parameter guidance "
                "for browser_custom_action."
            ),
        )
        async def browser_list_custom_actions() -> Dict[str, Any]:
            return {
                "ok": True,
                "actions": self._controller.list_actions(),
                "details": self._controller.describe_actions(),
            }

        self._browser_tool = browser_run_task
        self._browser_custom_action_tool = browser_custom_action
        self._browser_list_actions_tool = browser_list_custom_actions
        self._main_agent = build_main_agent(
            provider=self._service.provider,
            api_key=self._service.api_key,
            api_base=self._service.api_base,
            model_name=self._service.model_name,
            browser_tool_card=self._browser_tool.card,
            custom_action_tool_card=self._browser_custom_action_tool.card,
            list_actions_tool_card=self._browser_list_actions_tool.card,
            artifacts_subdir=self._service.artifacts_subdir,
        )
        self._register_runtime_tool(self._browser_tool, tool_name="browser_run_task")
        self._register_runtime_tool(self._browser_custom_action_tool, tool_name="browser_custom_action")
        self._register_runtime_tool(
            self._browser_list_actions_tool,
            tool_name="browser_list_custom_actions",
        )

        # Give the worker agent direct access to controller actions so it can call
        # browser_custom_action (e.g. drag-and-drop, coordinates) within its own
        # iteration rather than requiring a round-trip through the main agent.
        if self._service.browser_agent is not None:
            self._service.browser_agent.ability_manager.add(
                self._browser_custom_action_tool.card
            )
            self._service.browser_agent.ability_manager.add(
                self._browser_list_actions_tool.card
            )

    async def run_browser_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        return await self._service.run_task(
            task=task,
            session_id=session_id,
            request_id=request_id,
            timeout_s=timeout_s,
        )

    async def handle_request(
        self,
        *,
        query: str,
        session_id: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        await self.ensure_started()
        if self._main_agent is None:
            raise RuntimeError("BrowserAgentRuntime main_agent is not initialized")

        effective_session_id = (session_id or "").strip() or f"browser-{uuid.uuid4().hex}"
        effective_request_id = (request_id or "").strip() or uuid.uuid4().hex
        token_session = _ctx_parent_session_id.set(effective_session_id)
        token_request = _ctx_parent_request_id.set(effective_request_id)
        try:
            result = await Runner.run_agent(
                self._main_agent,
                {
                    "query": query,
                    "conversation_id": effective_session_id,
                    "request_id": effective_request_id,
                },
            )
        finally:
            _ctx_parent_session_id.reset(token_session)
            _ctx_parent_request_id.reset(token_request)

        output = result.get("output") if isinstance(result, dict) else result
        final = str(output or "")
        is_error = isinstance(result, dict) and str(result.get("result_type", "")).lower() == "error"
        return {
            "ok": not is_error,
            "session_id": effective_session_id,
            "request_id": effective_request_id,
            "final": final,
            "error": final if is_error else None,
        }

    async def shutdown(self) -> None:
        await self._service.shutdown()


class BrowserRuntimeRail(AgentRail):
    """Rail that triggers BrowserAgentRuntime.ensure_started() before the first invoke.

    Attach this to the browser_move sub-agent so tools are registered before
    the agent's ReActAgent tries to call them.
    """

    def __init__(self, runtime: BrowserAgentRuntime) -> None:
        super().__init__()
        self._runtime = runtime

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._runtime.ensure_started()
