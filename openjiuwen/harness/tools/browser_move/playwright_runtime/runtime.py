#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime wiring for browser tool registration and service lifecycle."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
import logging

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..controllers import ActionController, BaseController
from .browser_tools import ensure_browser_runtime_client_patch
from .config import BrowserRunGuardrails
from .service import BrowserService, BrowserTaskProgressState, MAX_ITERATION_MESSAGE

from ..utils.parsing import extract_json_object
from .probes import build_card_probe_js, build_interactive_probe_js
from .site_profiles import builtin_site_profiles, get_selector_cache

logger = logging.getLogger(__name__)

_BROWSER_PROGRESS_STATE_KEY = "__browser_subagent_progress_state__"
_BROWSER_PROGRESS_TASK_KEY = "__browser_subagent_last_task__"
_BROWSER_PROGRESS_SECTION_NAME = "browser_progress_continuation"
_BROWSER_PROGRESS_FORMAT_SECTION_NAME = "browser_progress_format"
_BROWSER_PROGRESS_TAG_RE = re.compile(
    r"<browser_progress>\s*(\{.*?\})\s*</browser_progress>",
    re.DOTALL | re.IGNORECASE,
)
_BROWSER_PROGRESS_FORMAT_GUIDANCE = {
    "en": (
        "When you stop and answer without another browser tool call, append exactly one "
        "<browser_progress>{...}</browser_progress> JSON block. "
        "Use status=completed only when the requested browser outcome is evidenced. "
        "Include compact fields: status, completed_steps, remaining_steps, next_step, "
        "completion_evidence, missing_requirements."
    ),
    "cn": (
        "当您暂停并回答问题，且未调用其他浏览器工具时，请在后面接上且仅接一个 "
        "<browser_progress>{...}</browser_progress> JSON 块。"
        "仅在请求的浏览器结果得到验证时才使用 status=completed。 "
        "包含以下紧凑字段：status、completed_steps、remaining_steps、next_step、"
        "completion_evidence、missing_requirements。"
    ),
}


class BrowserAgentRuntime:
    """Runtime kernel for browser lifecycle and deterministic helper actions."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
    ) -> None:
        ensure_browser_runtime_client_patch()
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
        )
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._code_executor = None
        self._browser_probe_interactives_tool = None
        self._browser_probe_cards_tool = None

    @property
    def service(self) -> BrowserService:
        return self._service

    @property
    def browser_custom_action_tool(self) -> Any:
        return self._browser_custom_action_tool

    @property
    def browser_list_actions_tool(self) -> Any:
        return self._browser_list_actions_tool
    
    @property
    def browser_probe_interactives_tool(self) -> Any:
        return self._browser_probe_interactives_tool
    
    @property
    def browser_probe_cards_tool(self) -> Any:
        return self._browser_probe_cards_tool

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def code_executor(self) -> Any:
        return self._code_executor

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.runtime")
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
    
    def _playwright_client_lookup_keys(self) -> list[str]:
        """Return likely registry keys for the active Playwright MCP client."""
        server_id = str(getattr(self._service.mcp_cfg, "server_id", "") or "").strip()
        server_name = str(getattr(self._service.mcp_cfg, "server_name", "") or "").strip()

        candidates = [
            server_id,
            server_name,
            server_id.replace("-", "_"),
            server_id.replace("_", "-"),
            server_name.replace("-", "_"),
            server_name.replace("_", "-"),
        ]

        # Common IDs seen in the direct Playwright runtime path.
        candidates.extend(
            [
                "playwright_official_stdio",
                "playwright-official",
                "playwright",
            ]
        )

        result: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @staticmethod
    def _unwrap_mcp_text_result(raw: Any) -> Any:
        """Extract text payload from common MCP tool-result shapes."""
        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, list):
                texts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(str(item.get("text") or ""))
                if texts:
                    return "\n".join(texts)

            if "result" in raw:
                return raw.get("result")

            if "text" in raw:
                return raw.get("text")

            if "data" in raw:
                return raw.get("data")

        return raw

    async def _get_playwright_mcp_tool(self, tool_name: str) -> Any:
        """Resolve a registered Playwright MCP tool through Runner.resource_mgr."""
        server_id = str(getattr(self._service.mcp_cfg, "server_id", "") or "").strip()
        server_name = str(getattr(self._service.mcp_cfg, "server_name", "") or "").strip()

        server_id_candidates = [
            server_id,
            server_id.replace("-", "_"),
            server_id.replace("_", "-"),
            "playwright_official_stdio",
            "playwright-official-stdio",
            "playwright",
        ]

        server_name_candidates = [
            server_name,
            server_name.replace("-", "_"),
            server_name.replace("_", "-"),
            "playwright-official",
            "playwright_official",
            "playwright",
        ]

        def _first_tool(value: Any) -> Any:
            if isinstance(value, list):
                return next((item for item in value if item is not None), None)
            return value

        tried: list[str] = []

        for candidate in server_id_candidates:
            if not candidate:
                continue

            tried.append(f"server_id={candidate}")

            tool = None
            try:
                tool = await Runner.resource_mgr.get_mcp_tool(
                    name=tool_name,
                    server_id=candidate,
                    skip_if_tag_not_exists=True,
                    ignore_exception=True,
                )
                tool = _first_tool(tool)
            except Exception:
                logger.debug(
                    "Failed to resolve MCP tool %s using server_id=%s",
                    tool_name,
                    candidate,
                    exc_info=True,
                )

            if tool is not None:
                return tool

        for candidate in server_name_candidates:
            if not candidate:
                continue

            tried.append(f"server_name={candidate}")

            tool = None
            try:
                tool = await Runner.resource_mgr.get_mcp_tool(
                    name=tool_name,
                    server_name=candidate,
                    skip_if_tag_not_exists=True,
                    ignore_exception=True,
                )
                tool = _first_tool(tool)
            except Exception:
                logger.debug(
                    "Failed to resolve MCP tool %s using server_name=%s",
                    tool_name,
                    candidate,
                    exc_info=True,
                )

            if tool is not None:
                return tool

        raise RuntimeError(
            f"Registered Playwright MCP tool not found: {tool_name}. "
            f"Tried {', '.join(tried)}"
        )

    async def _get_playwright_run_code_tool(self) -> tuple[Any, str]:
        """Resolve browser_run_code_unsafe, with browser_run_code as compatibility fallback."""
        try:
            return await self._get_playwright_mcp_tool("browser_run_code_unsafe"), "browser_run_code_unsafe"
        except RuntimeError:
            logger.debug(
                "browser_run_code_unsafe is unavailable; falling back to browser_run_code",
                exc_info=True,
            )

        return await self._get_playwright_mcp_tool("browser_run_code"), "browser_run_code"

    async def _call_playwright_run_code_unsafe(self, js_code: str) -> Any:
        """Execute Playwright page code through the registered run-code MCP tool."""
        tool, tool_name = await self._get_playwright_run_code_tool()

        result = await tool.invoke({"code": js_code})

        success = getattr(result, "success", None)
        if success is False:
            error = str(getattr(result, "error", "") or "").strip()
            raise RuntimeError(error or f"{tool_name} failed")

        data = getattr(result, "data", None)
        if data is not None:
            return data

        return result

    async def ensure_runtime_ready(self) -> None:
        await self._service.ensure_runtime_ready()
        if self._code_executor is not None:
            return

        async def _direct_code_executor(js_code: str):
            return await self._call_playwright_run_code_unsafe(js_code)

        self._code_executor = _direct_code_executor
        self._controller.bind_code_executor(_direct_code_executor)
        self._controller.register_builtin_actions()

    async def ensure_started(self) -> None:
        await self.ensure_runtime_ready()
        await self._service.ensure_started()
        if self._browser_custom_action_tool is not None:
            return
        from .runtime_tools import (
            BrowserCustomActionTool,
            BrowserListActionsTool,
            BrowserProbeCardsTool,
            BrowserProbeInteractivesTool,
        )

        self._browser_custom_action_tool = BrowserCustomActionTool(self, language="en")
        self._browser_list_actions_tool = BrowserListActionsTool(self, language="en")
        self._browser_probe_interactives_tool = BrowserProbeInteractivesTool(self, language="en")
        self._browser_probe_cards_tool = BrowserProbeCardsTool(self, language="en")
        self._register_runtime_tool(
            self._browser_custom_action_tool,
            tool_name="browser_custom_action",
        )
        self._register_runtime_tool(
            self._browser_list_actions_tool,
            tool_name="browser_list_custom_actions",
        )
        self._register_runtime_tool(
            self._browser_probe_interactives_tool,
            tool_name="browser_probe_interactives",
        )
        self._register_runtime_tool(
            self._browser_probe_cards_tool,
            tool_name="browser_probe_cards",
        )

        # Legacy browser_run_task compatibility: let the worker agent call
        # deterministic controller helpers without introducing another planner.
        if self._service.browser_agent is not None:
            self._service.browser_agent.ability_manager.add(
                self._browser_custom_action_tool.card
            )
            self._service.browser_agent.ability_manager.add(
                self._browser_list_actions_tool.card
            )
            self._service.browser_agent.ability_manager.add(
                self._browser_probe_interactives_tool.card
            )
            self._service.browser_agent.ability_manager.add(
                self._browser_probe_cards_tool.card
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

    async def run_custom_action(
        self,
        *,
        action: str,
        session_id: str = "",
        request_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_runtime_ready()
        self._controller.bind_runtime(self)
        if self._code_executor is not None:
            self._controller.bind_code_executor(self._code_executor)
        return await self._controller.run_action(
            action=action,
            session_id=session_id,
            request_id=request_id,
            **(params or {}),
        )
    
    async def probe_interactives(
        self,
        *,
        max_items: int = 50,
        viewport_only: bool = True,
        query: str = "",
    ) -> Dict[str, Any]:
        """Return compact visible/high-value interactive elements from the current page."""
        await self.ensure_runtime_ready()

        if self._code_executor is None:
            return {
                "ok": False,
                "error": "browser_code_executor_not_ready",
                "elements": [],
            }

        js_code = build_interactive_probe_js(
            max_items=max_items,
            viewport_only=viewport_only,
            query=query,
        )

        try:
            raw = await self._code_executor(js_code)
            raw = self._unwrap_mcp_text_result(raw)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"browser_probe_interactives failed: {exc}",
                "elements": [],
            }

        parsed = extract_json_object(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Could not parse browser_probe_interactives result JSON",
                "raw_preview": str(raw)[:400],
                "elements": [],
            }

        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("elements", [])
        return parsed
    
    async def probe_cards(
        self,
        *,
        max_cards: int = 20,
        viewport_only: bool = True,
        include_buttons: bool = True,
        query: str = "",
    ) -> Dict[str, Any]:
        """Return compact repeated card/listing structures from the current page."""
        await self.ensure_runtime_ready()

        if self._code_executor is None:
            return {
                "ok": False,
                "error": "browser_code_executor_not_ready",
                "cards": [],
            }

        site_profiles = builtin_site_profiles()
        selector_cache = get_selector_cache()
        selector_cache_records = selector_cache.export_for_probe()

        js_code = build_card_probe_js(
            max_cards=max_cards,
            viewport_only=viewport_only,
            include_buttons=include_buttons,
            query=query,
            site_profiles=site_profiles,
            selector_cache_records=selector_cache_records,
        )

        try:
            raw = await self._code_executor(js_code)
            raw = self._unwrap_mcp_text_result(raw)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"browser_probe_cards failed: {exc}",
                "cards": [],
            }

        parsed = extract_json_object(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Could not parse browser_probe_cards result JSON",
                "raw_preview": str(raw)[:400],
                "cards": [],
            }

        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("cards", [])

        if parsed.get("ok") and parsed.get("cards"):
            try:
                selector_cache.record_card_probe_result(parsed)
            except Exception:
                logger.debug(
                    "Failed to record card probe result in selector cache",
                    exc_info=True,
                )
        
        return parsed

    async def list_actions(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "actions": self._controller.list_actions(),
            "details": self._controller.describe_actions(),
        }

    async def runtime_health(self) -> Dict[str, Any]:
        return {
            "ok": bool(self._service.connection_healthy),
            "started": bool(self._service.started),
            "last_heartbeat_ok": self._service.last_heartbeat_ok,
            "provider": self._service.provider,
            "api_base": self._service.api_base,
            "model_name": self._service.model_name,
        }

    async def shutdown(self) -> None:
        await self._service.shutdown()


class BrowserRuntimeRail(AgentRail):
    """Rail that makes direct browser sessions resumable and completion-aware."""

    def __init__(self, runtime: BrowserAgentRuntime) -> None:
        super().__init__()
        self._runtime = runtime

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._runtime.ensure_runtime_ready()
        self._ensure_browser_mcp_ability(ctx)
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._hydrate_service_progress_from_session(session)
        query = getattr(getattr(ctx, "inputs", None), "query", None)
        task_text = str(query or "").strip()
        if task_text:
            session.update_state({_BROWSER_PROGRESS_TASK_KEY: task_text})

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if session is None or builder is None:
            return

        builder.add_section(
            PromptSection(
                name=_BROWSER_PROGRESS_FORMAT_SECTION_NAME,
                content=_BROWSER_PROGRESS_FORMAT_GUIDANCE,
                priority=84,
            )
        )

        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            builder.remove_section(_BROWSER_PROGRESS_SECTION_NAME)
            return

        progress_context = BrowserService.build_progress_context(progress_state)
        if not progress_context:
            builder.remove_section(_BROWSER_PROGRESS_SECTION_NAME)
            return

        continuation_text_en = (
            f"{progress_context}\n"
            "Use this stored browser progress as continuation context. "
            "Avoid repeating completed actions unless recovery requires it."
        )
        continuation_text_cn = (
            f"{progress_context}\n"
            "将此存储的浏览器进度用作延续上下文。"
            "除非恢复操作有此需求，否则请避免重复已完成的操作。"
        )
        builder.add_section(
            PromptSection(
                name=_BROWSER_PROGRESS_SECTION_NAME,
                content={
                    "en": continuation_text_en,
                    "cn": continuation_text_cn,
                },
                priority=83,
            )
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        if session is None:
            return
        tool_name = str(getattr(getattr(ctx, "inputs", None), "tool_name", "") or "").strip()
        if not self._is_browser_progress_tool(tool_name):
            return
        tool_result = self._normalize_tool_result(
            getattr(getattr(ctx, "inputs", None), "tool_result", None)
        )
        session_id = session.get_session_id()
        self._runtime.service.record_tool_progress(
            session_id=session_id,
            request_id="",
            tool_name=tool_name,
            tool_result=tool_result,
        )
        self._persist_service_progress_to_session(session)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        result = getattr(getattr(ctx, "inputs", None), "result", None)
        if session is None or not isinstance(result, dict):
            return

        session_id = session.get_session_id()
        self._hydrate_service_progress_from_session(session)
        output_text = str(result.get("output") or "")
        clean_output, progress_payload = self._extract_progress_payload(output_text)
        if clean_output != output_text:
            result["output"] = clean_output

        if progress_payload is not None:
            parsed_progress = self._build_progress_result(progress_payload, clean_output)
            self._runtime.service.record_worker_progress(
                session_id=session_id,
                request_id="",
                parsed=parsed_progress,
            )
            progress_state = self._runtime.service.get_progress_state(session_id)
            exported = self._runtime.service.export_progress_state(session_id)
            if self._runtime.service.should_treat_as_completed(parsed_progress):
                result["result_type"] = "answer"
                result["progress_state"] = exported
                self._clear_progress_state(session)
                return

            failure_summary = self._runtime.service.build_failure_summary(
                task=self._load_task_text(session),
                error=str(parsed_progress.get("error") or "browser_task_incomplete"),
                page_url=progress_state.last_page_url if progress_state is not None else "",
                page_title=progress_state.last_page_title if progress_state is not None else "",
                final=clean_output,
                screenshot=progress_state.last_screenshot if progress_state is not None else None,
                attempt=1,
                progress_state=progress_state,
            )
            result["result_type"] = "error"
            result["failure_summary"] = failure_summary
            result["progress_state"] = exported
            result["output"] = (
                failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
            )
            self._persist_service_progress_to_session(session)
            return

        if self._is_max_iteration_result(result):
            progress_state = self._runtime.service.get_progress_state(session_id)
            failure_summary = self._runtime.service.build_failure_summary(
                task=self._load_task_text(session),
                error="max_iterations_reached",
                page_url=progress_state.last_page_url if progress_state is not None else "",
                page_title=progress_state.last_page_title if progress_state is not None else "",
                final=clean_output or output_text,
                screenshot=progress_state.last_screenshot if progress_state is not None else None,
                attempt=1,
                progress_state=progress_state,
            )
            result["failure_summary"] = failure_summary
            result["progress_state"] = self._runtime.service.export_progress_state(session_id)
            result["output"] = failure_summary
            self._persist_service_progress_to_session(session)
            return

        if str(result.get("result_type", "")).lower() == "answer":
            self._clear_progress_state(session)
            return

        exported = self._runtime.service.export_progress_state(session_id)
        if exported is not None:
            result["progress_state"] = exported
            self._persist_service_progress_to_session(session)

    @staticmethod
    def _normalize_tool_result(tool_result: Any) -> Any:
        if hasattr(tool_result, "data") and hasattr(tool_result, "success"):
            data = getattr(tool_result, "data", None)
            if data is not None:
                return data
            error = str(getattr(tool_result, "error", "") or "").strip()
            if error:
                return {"ok": False, "error": error}
        return tool_result

    @staticmethod
    def _is_browser_progress_tool(tool_name: str) -> bool:
        name = (tool_name or "").strip().lower()
        if not name:
            return False
        if name in {
            "browser_cancel_run",
            "browser_clear_cancel",
            "browser_list_custom_actions",
            "browser_runtime_health",
        }:
            return False
        return name.startswith("browser_") or ".browser_" in name

    @staticmethod
    def _extract_progress_payload(output_text: str) -> tuple[str, Optional[Dict[str, Any]]]:
        text = str(output_text or "")
        match = _BROWSER_PROGRESS_TAG_RE.search(text)
        if match is None:
            return text, None
        payload_text = match.group(1).strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return text, None
        cleaned = _BROWSER_PROGRESS_TAG_RE.sub("", text, count=1).strip()
        return cleaned, payload if isinstance(payload, dict) else None

    @staticmethod
    def _build_progress_result(progress_payload: Dict[str, Any], clean_output: str) -> Dict[str, Any]:
        status = str(progress_payload.get("status") or "").strip().lower() or "partial"
        return {
            "ok": status == "completed",
            "status": status,
            "progress": progress_payload,
            "final": clean_output,
            "error": None if status == "completed" else "browser_task_incomplete",
        }

    @staticmethod
    def _is_max_iteration_result(result: Dict[str, Any]) -> bool:
        output = str(result.get("output") or "").strip()
        result_type = str(result.get("result_type") or "").strip().lower()
        return result_type == "error" and MAX_ITERATION_MESSAGE.lower() in output.lower()

    @staticmethod
    def _load_task_text(session: Any) -> str:
        if session is None:
            return ""
        return str(session.get_state(_BROWSER_PROGRESS_TASK_KEY) or "").strip()

    @staticmethod
    def _load_progress_state(session: Any) -> BrowserTaskProgressState:
        if session is None:
            return BrowserTaskProgressState()
        return BrowserTaskProgressState.from_dict(session.get_state(_BROWSER_PROGRESS_STATE_KEY))

    def _hydrate_service_progress_from_session(self, session: Any) -> BrowserTaskProgressState:
        session_id = session.get_session_id()
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            return progress_state
        self._runtime.service.set_progress_state(session_id, progress_state)
        return progress_state

    def _persist_service_progress_to_session(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        exported = self._runtime.service.export_progress_state(session_id)
        progress_state = self._runtime.service.get_progress_state(session_id)
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: (
                    exported
                    if isinstance(exported, dict) and exported
                    else progress_state.to_dict()
                    if progress_state is not None and not progress_state.is_empty()
                    else {}
                )
            }
        )

    def _clear_progress_state(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        self._runtime.service.clear_progress_state(session_id)
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PROGRESS_TASK_KEY: "",
            }
        )

    def _ensure_browser_mcp_ability(self, ctx: AgentCallbackContext) -> None:
        agent = getattr(ctx, "agent", None)
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        ability_manager.add(self._runtime.service.mcp_cfg)
