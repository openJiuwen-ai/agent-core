# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Proper Tool subclasses wrapping BrowserAgentRuntime for DeepAgent registration.

Each class follows the openjiuwen Tool contract:
  - ``__init__`` builds a ToolCard and stores the runtime reference.
  - ``invoke``   returns ToolOutput; called by the agent runner.
  - ``stream``   not used for browser tools (no-op).


Usage::

    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import build_browser_runtime_tools

    runtime = BrowserAgentRuntime(...)
    tools = build_browser_runtime_tools(runtime, language="cn")

    # Pass Tool instances directly — create_deep_agent handles card extraction
    # and resource-manager registration automatically.
    agent = create_deep_agent(model=model, tools=tools, ...)
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from .runtime import BrowserAgentRuntime

_ctx_parent_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_session_id",
    default="",
)
_ctx_parent_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_request_id",
    default="",
)


# ---------------------------------------------------------------------------
# BrowserRunTaskTool
# ---------------------------------------------------------------------------

_RUN_TASK_DESC: Dict[str, str] = {
    "cn": (
        "在粘性逻辑会话中执行浏览器任务。"
        "每次请求优先发起一次全面的任务调用，避免多次重试。"
        "超时时间建议使用默认值，不要低于配置值。"
        "返回 JSON，包含 ok/session_id/final/page/screenshot/error/attempt/failure_summary 字段。"
    ),
    "en": (
        "Run a browser task in a sticky logical session. "
        "Prefer one comprehensive task per request instead of many tiny retries. "
        "Omit timeout_s to use the configured default. "
        "Returns JSON with ok/session_id/final/page/screenshot/error/attempt/failure_summary."
    ),
}

_RUN_TASK_PARAMS: Dict[str, Dict[str, Any]] = {
    "cn": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "要执行的浏览器任务描述"},
            "session_id": {"type": "string", "description": "粘性会话 ID（可选，留空则自动沿用上下文会话）"},
            "request_id": {"type": "string", "description": "请求 ID（可选）"},
            "timeout_s": {"type": "integer", "description": "超时时间（秒），默认 180"},
        },
        "required": ["task"],
    },
    "en": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Browser task description"},
            "session_id": {
                "type": "string",
                "description": "Sticky session ID (optional; inherits from context if empty)",
            },
            "request_id": {"type": "string", "description": "Request ID (optional)"},
            "timeout_s": {"type": "integer", "description": "Timeout in seconds, default 180"},
        },
        "required": ["task"],
    },
}


class BrowserRunTaskTool(Tool):
    """Execute a browser task via BrowserAgentRuntime."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        super().__init__(ToolCard(
            name="browser_run_task",
            description=_RUN_TASK_DESC.get(language, _RUN_TASK_DESC["cn"]),
            input_params=_RUN_TASK_PARAMS.get(language, _RUN_TASK_PARAMS["cn"]),
        ))
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._runtime.ensure_started()
        task = inputs.get("task", "")
        session_id = (inputs.get("session_id") or "").strip() or _ctx_parent_session_id.get()
        request_id = (inputs.get("request_id") or "").strip() or _ctx_parent_request_id.get()
        timeout_s = inputs.get("timeout_s", 180)
        try:
            result = await self._runtime.service.run_task(
                task=task,
                session_id=session_id,
                request_id=request_id,
                timeout_s=timeout_s,
            )
            screenshot = result.get("screenshot")
            if isinstance(screenshot, str) and screenshot.startswith("data:"):
                result = {**result, "screenshot": "[screenshot saved]"}
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        pass


# ---------------------------------------------------------------------------
# BrowserCancelTool
# ---------------------------------------------------------------------------

_CANCEL_DESC: Dict[str, str] = {
    "cn": (
        "取消正在进行的浏览器任务，通过 session_id 定位。"
        "可选传入 request_id 以取消会话中的特定请求。"
        "返回 JSON，包含 ok/session_id/request_id/error 字段。"
    ),
    "en": (
        "Cancel an in-progress browser task by session_id. "
        "Optionally pass request_id to target a specific request within the session. "
        "Returns JSON with ok/session_id/request_id/error."
    ),
}

_CANCEL_PARAMS: Dict[str, Dict[str, Any]] = {
    "cn": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "要取消的任务所属的会话 ID"},
            "request_id": {"type": "string", "description": "可选：要取消的特定请求 ID"},
        },
        "required": ["session_id"],
    },
    "en": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID of the task to cancel"},
            "request_id": {"type": "string", "description": "Optional: specific request ID to cancel"},
        },
        "required": ["session_id"],
    },
}


class BrowserCancelTool(Tool):
    """Cancel an in-progress browser task."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        super().__init__(ToolCard(
            name="browser_cancel_run",
            description=_CANCEL_DESC.get(language, _CANCEL_DESC["cn"]),
            input_params=_CANCEL_PARAMS.get(language, _CANCEL_PARAMS["cn"]),
        ))
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._runtime.ensure_started()
        session_id = inputs.get("session_id", "")
        request_id = inputs.get("request_id") or None
        try:
            result = await self._runtime.cancel_run(session_id=session_id, request_id=request_id)
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        pass


# ---------------------------------------------------------------------------
# BrowserCustomActionTool
# ---------------------------------------------------------------------------

_CUSTOM_ACTION_DESC: Dict[str, str] = {
    "cn": (
        "按名称执行已注册的自定义浏览器动作。"
        "可用于拖拽、坐标获取等高级操作。"
        "如不确定参数，请先调用 browser_list_custom_actions 查看可用动作及参数说明。"
        "坐标参数支持 source/target、source_x/source_y/target_x/target_y 等别名。"
    ),
    "en": (
        "Run a registered custom browser action by name. "
        "Use for higher-level helpers such as drag-and-drop or coordinate resolution. "
        "Call browser_list_custom_actions first to discover available actions and parameters. "
        "Aliases source/target and source_x/source_y/target_x/target_y are accepted."
    ),
}

_CUSTOM_ACTION_PARAMS: Dict[str, Dict[str, Any]] = {
    "cn": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "要执行的自定义动作名称"},
            "session_id": {"type": "string", "description": "会话 ID（可选）"},
            "request_id": {"type": "string", "description": "请求 ID（可选）"},
            "params": {
                "type": "object",
                "description": "传递给动作的额外参数（键值对）",
                "properties": {},
                "required": [],
            },
        },
        "required": ["action"],
    },
    "en": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Name of the custom action to run"},
            "session_id": {"type": "string", "description": "Session ID (optional)"},
            "request_id": {"type": "string", "description": "Request ID (optional)"},
            "params": {
                "type": "object",
                "description": "Extra key-value parameters forwarded to the action",
                "properties": {},
                "required": [],
            },
        },
        "required": ["action"],
    },
}


class BrowserCustomActionTool(Tool):
    """Run a registered custom browser action."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        super().__init__(ToolCard(
            name="browser_custom_action",
            description=_CUSTOM_ACTION_DESC.get(language, _CUSTOM_ACTION_DESC["cn"]),
            input_params=_CUSTOM_ACTION_PARAMS.get(language, _CUSTOM_ACTION_PARAMS["cn"]),
        ))
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._runtime.ensure_started()
        action = inputs.get("action", "")
        session_id = (inputs.get("session_id") or "").strip() or _ctx_parent_session_id.get()
        request_id = (inputs.get("request_id") or "").strip() or _ctx_parent_request_id.get()
        params: Dict[str, Any] = inputs.get("params") or {}
        self._runtime.controller.bind_runtime(self._runtime)
        code_executor = self._runtime.code_executor
        if code_executor is not None:
            self._runtime.controller.bind_code_executor(code_executor)
        try:
            result = await self._runtime.controller.run_action(
                action=action,
                session_id=session_id,
                request_id=request_id,
                **params,
            )
            return ToolOutput(success=bool(result.get("ok", True)), data=result, error=result.get("error"))
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        pass


# ---------------------------------------------------------------------------
# BrowserListActionsTool
# ---------------------------------------------------------------------------

_LIST_ACTIONS_DESC: Dict[str, str] = {
    "cn": "列出 browser_custom_action 可调用的所有自定义动作及其详细参数说明。",
    "en": "List available custom browser actions and detailed parameter guidance for browser_custom_action.",
}

_LIST_ACTIONS_PARAMS: Dict[str, Dict[str, Any]] = {
    "cn": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "en": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


class BrowserListActionsTool(Tool):
    """List available custom browser actions."""

    def __init__(self, runtime: "BrowserAgentRuntime", language: str = "cn") -> None:
        super().__init__(ToolCard(
            name="browser_list_custom_actions",
            description=_LIST_ACTIONS_DESC.get(language, _LIST_ACTIONS_DESC["cn"]),
            input_params=_LIST_ACTIONS_PARAMS.get(language, _LIST_ACTIONS_PARAMS["cn"]),
        ))
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        await self._runtime.ensure_started()
        try:
            data = {
                "ok": True,
                "actions": self._runtime.controller.list_actions(),
                "details": self._runtime.controller.describe_actions(),
            }
            return ToolOutput(success=True, data=data)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        pass


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_browser_runtime_tools(
    runtime: "BrowserAgentRuntime",
    language: str = "cn",
) -> List[Tool]:
    """Build all four browser runtime Tool instances from *runtime*.

    Returns a list ready to be passed to ``create_deep_agent(tools=[...])``.
    ``create_deep_agent`` will extract ToolCards and register the instances
    in the runner's resource manager automatically.

    Args:
        runtime: A ``BrowserAgentRuntime`` instance to back the tools.
        language: Prompt language for tool descriptions (``'cn'`` or ``'en'``).

    Returns:
        ``[BrowserRunTaskTool, BrowserCancelTool, BrowserCustomActionTool,
        BrowserListActionsTool]``
    """
    return [
        BrowserRunTaskTool(runtime, language),
        BrowserCancelTool(runtime, language),
        BrowserCustomActionTool(runtime, language),
        BrowserListActionsTool(runtime, language),
    ]
