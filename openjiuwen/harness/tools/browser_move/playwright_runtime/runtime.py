#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime wiring for browser tool registration and service lifecycle."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, Iterable, Optional
from weakref import WeakSet

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.browser_context import (
    reset_browser_agent_log_context,
    set_browser_agent_log_context,
)
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
    PromptAttachmentManager,
)

from ..controllers import ActionController, BaseController
from ..utils.parsing import extract_json_object
from .browser_capabilities import DEFAULT_BROWSER_CAPABILITIES
from .browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
)
from .browser_tools import ensure_browser_runtime_client_patch
from .config import BrowserInstanceConfig, BrowserRunGuardrails
from .probes import build_card_probe_js, build_interactive_probe_js
from .service import MAX_ITERATION_MESSAGE, BrowserService, BrowserTaskProgressState
from .site_profiles import builtin_site_profiles, get_selector_cache
from .status_logging import BrowserSubagentStatusLogger, is_browser_subagent_status_log_enabled


_BROWSER_PROGRESS_STATE_KEY = "__browser_subagent_progress_state__"
_BROWSER_PROGRESS_TASK_KEY = "__browser_subagent_last_task__"
_BROWSER_PROGRESS_SECTION_NAME = "browser_progress_continuation"
_BROWSER_PROGRESS_FORMAT_SECTION_NAME = "browser_progress_format"
_BROWSER_IMAGE_CAPABILITY_SECTION_NAME = "browser_image_input_capability"
_BROWSER_PHASE_SECTION_NAME = "browser_phase_budget"
_BROWSER_PHASE_STATE_KEY = "__browser_phase_budget_state__"
_BROWSER_SCREENSHOT_TOOL_NAMES = frozenset({"browser_take_screenshot"})
_BROWSER_LOG_CONTEXT_TOKEN_KEY = "__browser_agent_log_context_token__"
_ACTIVE_BROWSER_RUNTIMES: WeakSet[Any] = WeakSet()
_BROWSER_PROGRESS_TAG_RE = re.compile(
    r"<browser_progress>\s*(\{.*?\})\s*</browser_progress>",
    re.DOTALL | re.IGNORECASE,
)
_BROWSER_EXPLICIT_REF_RE = re.compile(
    r"""^\s*\[?\s*ref\s*=\s*["']?([^"'\]\s]+)["']?\s*\]?\s*$""",
    re.IGNORECASE,
)
_BROWSER_SNAPSHOT_REF_RE = re.compile(
    r"""\bref\s*=\s*["']?([A-Za-z0-9_.:-]+)""",
    re.IGNORECASE,
)
_BROWSER_REF_TARGET_KEYS = frozenset(
    {
        "target",
        "startTarget",
        "endTarget",
        "ref",
        "startRef",
        "endRef",
    }
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
_BROWSER_IMAGE_CAPABILITY_GUIDANCE = {
    True: {
        "en": (
            "The current model can inspect image input. Use browser_take_screenshot only when "
            "pixel-level visual evidence is required and DOM probes, accessibility snapshots, "
            "or targeted evaluation cannot answer the task."
        ),
        "cn": (
            "当前模型可以理解图片输入。仅当任务需要像素级视觉证据，且 DOM 探测、无障碍快照或"
            "定向脚本无法解决时，才使用 browser_take_screenshot。"
        ),
    },
    False: {
        "en": (
            "Image input is unavailable or unverified for this run. Do not request screenshots, "
            "including browser_take_screenshot or browser_batch_interact with op=screenshot. "
            "Use DOM probes, accessibility snapshots, and targeted evaluation instead."
        ),
        "cn": (
            "本次运行的图片输入能力不可用或尚未确认。不要请求截图，包括 "
            "browser_take_screenshot 或 browser_batch_interact 的 op=screenshot；"
            "请改用 DOM 探测、无障碍快照和定向脚本。"
        ),
    },
}
_BROWSER_PHASE_DEFINITIONS = {
    "navigation": {
        "budget": 12,
        "completion_condition": "Target URL or expected page identity is observed.",
    },
    "form": {
        "budget": 24,
        "completion_condition": "All required fields are populated and submission is evidenced.",
    },
    "filtering": {
        "budget": 20,
        "completion_condition": "Requested filter/sort state and changed results are observed.",
    },
    "extraction": {
        "budget": 20,
        "completion_condition": "One structured result contains every requested field.",
    },
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
        instance: Optional[BrowserInstanceConfig] = None,
        allowed_tool_names: Optional[Iterable[str]] = None,
    ) -> None:
        ensure_browser_runtime_client_patch()
        self._instance = instance
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
            instance=instance,
            allowed_tool_names=allowed_tool_names,
        )
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._code_executor = None
        self._browser_probe_interactives_tool = None
        self._browser_probe_cards_tool = None
        self._browser_batch_interact_tool = None
        self._page_generation = 0
        self._reference_generations: Dict[str, int] = {}
        self._selector_primary_links: Dict[str, tuple[int, str]] = {}
        self._last_observed_url = ""
        _ACTIVE_BROWSER_RUNTIMES.add(self)

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
    def browser_batch_interact_tool(self) -> Any:
        return self._browser_batch_interact_tool

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def code_executor(self) -> Any:
        return self._code_executor

    @property
    def generation_id(self) -> str:
        return f"g{self._page_generation}"

    def _advance_page_generation(self) -> None:
        self._page_generation += 1

    def _observe_page_url(self, url: Any, *, force_navigation: bool = False) -> None:
        normalized = str(url or "").strip()
        changed = bool(
            normalized
            and (
                (
                    self._last_observed_url
                    and normalized != self._last_observed_url
                )
                or (
                    not self._last_observed_url
                    and bool(self._reference_generations)
                )
            )
        )
        if force_navigation or changed:
            self._advance_page_generation()
        if normalized:
            self._last_observed_url = normalized

    @classmethod
    def _extract_result_url(cls, value: Any) -> str:
        if isinstance(value, dict):
            direct = value.get("url")
            if direct:
                return str(direct)
            page = value.get("page")
            if isinstance(page, dict) and page.get("url"):
                return str(page["url"])
            for nested in value.values():
                resolved = cls._extract_result_url(nested)
                if resolved:
                    return resolved
        elif isinstance(value, (list, tuple)):
            for nested in value:
                resolved = cls._extract_result_url(nested)
                if resolved:
                    return resolved
        elif isinstance(value, str):
            match = re.search(
                r"(?:Page\s+URL|url)\s*[:=]\s*(https?://[^\s<>\"]+)",
                value,
                re.IGNORECASE,
            )
            if match is not None:
                return match.group(1).rstrip(".,;)")
        return ""

    @staticmethod
    def _tool_result_succeeded(value: Any) -> bool:
        if hasattr(value, "success") and getattr(value, "success", None) is False:
            return False
        if isinstance(value, dict):
            if value.get("ok") is False or value.get("success") is False:
                return False
            if value.get("error") and not value.get("ok"):
                return False
        return True

    def _register_snapshot_refs(self, value: Any) -> None:
        text = value if isinstance(value, str) else str(value)
        for ref_value in _BROWSER_SNAPSHOT_REF_RE.findall(text):
            self._reference_generations[str(ref_value)] = self._page_generation

    def validate_reference_values(self, values: Iterable[str]) -> None:
        stale = sorted(
            {
                ref_value
                for ref_value in values
                if ref_value in self._reference_generations
                and self._reference_generations[ref_value] != self._page_generation
            }
        )
        if stale:
            raise ValueError(
                "Stale browser snapshot reference(s) "
                f"{', '.join(stale)} belong to an older page generation. "
                "Call browser_snapshot again and use refs from "
                f"generation {self.generation_id}."
            )

    def record_tool_reference_state(
        self,
        *,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
    ) -> None:
        normalized_name = str(tool_name or "").strip().lower()
        if not self._tool_result_succeeded(tool_result):
            return
        result_url = self._extract_result_url(tool_result)
        navigation_like = any(
            token in normalized_name
            for token in (
                "browser_navigate",
                "browser_navigate_back",
                "browser_tabs",
            )
        )
        if "browser_tabs" in normalized_name and isinstance(tool_args, dict):
            navigation_like = str(tool_args.get("action") or "").lower() in {
                "new",
                "select",
                "close",
            }
        self._observe_page_url(result_url, force_navigation=navigation_like)
        if "browser_snapshot" in normalized_name:
            self._register_snapshot_refs(tool_result)

    def _annotate_probe_generation(self, value: Any) -> None:
        if isinstance(value, dict):
            if "selector_hint" in value or "generation_id" in value:
                value["generation_id"] = self.generation_id
            for nested in value.values():
                self._annotate_probe_generation(nested)
        elif isinstance(value, list):
            for nested in value:
                self._annotate_probe_generation(nested)

    def register_card_primary_links(self, result: Dict[str, Any]) -> None:
        cards = result.get("cards")
        if not isinstance(cards, list):
            return
        for card in cards:
            if not isinstance(card, dict):
                continue
            href = str(card.get("primary_link") or card.get("href") or "").strip()
            if not href:
                continue
            for key in ("selector_hint", "primary_link_selector_hint"):
                selector = str(card.get(key) or "").strip()
                if selector:
                    self._selector_primary_links[selector] = (
                        self._page_generation,
                        href,
                    )

    def resolve_primary_link(self, tool_args: Any) -> str:
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (TypeError, ValueError, json.JSONDecodeError):
                return ""
        else:
            parsed = tool_args
        if not isinstance(parsed, dict):
            return ""
        explicit = str(
            parsed.get("primary_link")
            or parsed.get("href")
            or parsed.get("url")
            or ""
        ).strip()
        if explicit:
            return explicit
        for key in ("selector", "target"):
            selector = str(parsed.get(key) or "").strip()
            generation_and_href = self._selector_primary_links.get(selector)
            if (
                generation_and_href
                and generation_and_href[0] == self._page_generation
            ):
                return generation_and_href[1]
        return ""

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

        # Common IDs seen in the direct Playwright runtime path. Skipped for a
        # keyed instance so its lookup never resolves the legacy/unkeyed client.
        if not (self._instance and self._instance.key):
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
            if raw.get("__browser_compact_rpc__") is True:
                return BrowserAgentRuntime._unwrap_mcp_text_result(
                    raw.get("payload")
                )

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

    @classmethod
    def _compact_run_code_payload(cls, payload: Any) -> Any:
        """Keep the JSON result and discard generic run-code page-state text."""
        unwrapped = cls._unwrap_mcp_text_result(payload)
        parsed = extract_json_object(unwrapped)
        if isinstance(parsed, dict):
            return json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return payload

    async def _get_playwright_mcp_tool(self, tool_name: str) -> Any:
        """Resolve a registered Playwright MCP tool through Runner.resource_mgr."""
        server_id = str(getattr(self._service.mcp_cfg, "server_id", "") or "").strip()
        server_name = str(getattr(self._service.mcp_cfg, "server_name", "") or "").strip()

        # When this runtime is bound to a specific browser identity, the cfg
        # server_id is already unique; the generic fallbacks below could resolve
        # the legacy/unkeyed server, so they are skipped to keep isolation.
        keyed = bool(self._instance and self._instance.key)

        server_id_candidates = [
            server_id,
            server_id.replace("-", "_"),
            server_id.replace("_", "-"),
        ]
        if not keyed:
            server_id_candidates += [
                "playwright_official_stdio",
                "playwright-official-stdio",
                "playwright",
            ]

        server_name_candidates = [
            server_name,
            server_name.replace("-", "_"),
            server_name.replace("_", "-"),
        ]
        if not keyed:
            server_name_candidates += [
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

        raise RuntimeError(f"Registered Playwright MCP tool not found: {tool_name}. Tried {', '.join(tried)}")

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
        """Execute a compact runtime RPC over the registered Playwright transport."""
        total_started_at = time.perf_counter()
        resolution_started_at = time.perf_counter()
        tool, tool_name = await self._get_playwright_run_code_tool()
        resolution_elapsed_ms = int(
            max(0.0, (time.perf_counter() - resolution_started_at) * 1000)
        )

        invoke_started_at = time.perf_counter()
        result = await tool.invoke({"code": js_code})
        invoke_elapsed_ms = int(
            max(0.0, (time.perf_counter() - invoke_started_at) * 1000)
        )

        success = getattr(result, "success", None)
        if success is False:
            error = str(getattr(result, "error", "") or "").strip()
            raise RuntimeError(error or f"{tool_name} failed")

        data = getattr(result, "data", None)
        if data is not None:
            payload = data
        else:
            payload = result
        transport_response_size_bytes = len(
            str(payload).encode("utf-8", "ignore")
        )
        compact_payload = self._compact_run_code_payload(payload)
        return {
            "__browser_compact_rpc__": True,
            "payload": compact_payload,
            "rpc_metrics": {
                "tool_name": tool_name,
                "tool_resolution_elapsed_ms": resolution_elapsed_ms,
                "transport_invoke_elapsed_ms": invoke_elapsed_ms,
                "rpc_total_elapsed_ms": int(
                    max(0.0, (time.perf_counter() - total_started_at) * 1000)
                ),
                "script_size_bytes": len(js_code.encode("utf-8", "ignore")),
                "transport_response_size_bytes": transport_response_size_bytes,
                "response_size_bytes": len(
                    str(compact_payload).encode("utf-8", "ignore")
                ),
            },
        }

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
            BrowserBatchInteractTool,
            BrowserCustomActionTool,
            BrowserListActionsTool,
            BrowserProbeCardsTool,
            BrowserProbeInteractivesTool,
        )

        self._browser_probe_interactives_tool = BrowserProbeInteractivesTool(self, language="en")
        self._browser_probe_cards_tool = BrowserProbeCardsTool(self, language="en")
        self._browser_batch_interact_tool = BrowserBatchInteractTool(self, language="en")
        self._browser_custom_action_tool = BrowserCustomActionTool(self, language="en")
        self._browser_list_actions_tool = BrowserListActionsTool(self, language="en")
        self._register_runtime_tool(
            self._browser_probe_interactives_tool,
            tool_name="browser_probe_interactives",
        )
        self._register_runtime_tool(
            self._browser_probe_cards_tool,
            tool_name="browser_probe_cards",
        )
        self._register_runtime_tool(
            self._browser_batch_interact_tool,
            tool_name="browser_batch_interact",
        )
        self._register_runtime_tool(
            self._browser_custom_action_tool,
            tool_name="browser_custom_action",
        )
        self._register_runtime_tool(
            self._browser_list_actions_tool,
            tool_name="browser_list_custom_actions",
        )

        # Legacy browser_run_task compatibility: let the worker agent call
        # deterministic controller helpers without introducing another planner.
        if self._service.browser_agent is not None:
            # Add compact standalone helpers first so the browser worker sees the
            # same first-class affordances as the probe tools before MCP primitives.
            self._service.browser_agent.ability_manager.add(self._browser_probe_interactives_tool.card)
            self._service.browser_agent.ability_manager.add(self._browser_probe_cards_tool.card)
            self._service.browser_agent.ability_manager.add(self._browser_batch_interact_tool.card)
            self._service.browser_agent.ability_manager.add(self._browser_custom_action_tool.card)
            self._service.browser_agent.ability_manager.add(self._browser_list_actions_tool.card)

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

    async def batch_interact(
        self,
        *,
        steps: Any,
        timeout_ms: Any = None,
        condition_timeout_ms: Any = None,
        wait_after_each_ms: Any = None,
        continue_on_error: bool = False,
        global_timeout_ms: Any = None,
        session_id: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        await self.ensure_runtime_ready()
        self._controller.bind_runtime(self)
        if self._code_executor is not None:
            self._controller.bind_code_executor(self._code_executor)
        return await self._controller.run_action(
            action="browser_batch_interact",
            session_id=session_id,
            request_id=request_id,
            steps=steps,
            timeout_ms=timeout_ms,
            condition_timeout_ms=condition_timeout_ms,
            wait_after_each_ms=wait_after_each_ms,
            continue_on_error=continue_on_error,
            global_timeout_ms=global_timeout_ms,
            generation_id=self.generation_id,
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
            generation_id=self.generation_id,
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
        self._observe_page_url(parsed.get("url"))
        self._annotate_probe_generation(parsed)
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
            generation_id=self.generation_id,
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
        self._observe_page_url(parsed.get("url"))
        self._annotate_probe_generation(parsed)
        self.register_card_primary_links(parsed)

        if parsed.get("ok"):
            try:
                selector_cache.record_card_probe_cache_rejection(parsed)
            except Exception:
                logger.debug(
                    "Failed to record rejected card-probe selector cache attempt",
                    exc_info=True,
                )

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

    async def reset(self) -> None:
        """Release the current browser and restart lazily on the next task."""
        await self._service.reset()


async def reset_active_browser_runtimes() -> int:
    """Reset every live browser runtime owned by this process."""
    runtimes = list(_ACTIVE_BROWSER_RUNTIMES)
    if not runtimes:
        return 0

    results = await asyncio.gather(
        *(runtime.reset() for runtime in runtimes),
        return_exceptions=True,
    )
    reset_count = 0
    for runtime, result in zip(runtimes, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Failed to reset active browser runtime %s: %s",
                id(runtime),
                result,
            )
            continue
        reset_count += 1
    return reset_count


class BrowserRuntimeRail(AgentRail):
    """Rail that makes direct browser sessions resumable and completion-aware."""

    def __init__(self, runtime: BrowserAgentRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._status_logger = BrowserSubagentStatusLogger() if is_browser_subagent_status_log_enabled() else None
        browser_agent_log_info(
            "[BROWSER_SUBAGENT_BOOT] enabled=%s logger=%s",
            self._status_logger is not None,
            type(self._status_logger).__name__ if self._status_logger is not None else None,
        )

    def _emit_status(self, method_name: str, ctx: AgentCallbackContext) -> None:
        if self._status_logger is None:
            return
        method = getattr(self._status_logger, method_name, None)
        if not callable(method):
            browser_agent_log_warning(
                "[BROWSER_SUBAGENT_ERROR] missing_status_method=%s",
                method_name,
            )
            return
        try:
            method(ctx)
        except Exception:
            browser_agent_log_warning(
                "[BROWSER_SUBAGENT_ERROR] method=%s",
                method_name,
                exc_info=True,
            )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if isinstance(getattr(ctx, "extra", None), dict):
            ctx.extra[_BROWSER_LOG_CONTEXT_TOKEN_KEY] = set_browser_agent_log_context(True)
        self._emit_status("before_invoke", ctx)
        await self._runtime.ensure_runtime_ready()
        await self._ensure_browser_mcp_ability(ctx)
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._hydrate_service_progress_from_session(session)
        query = getattr(getattr(ctx, "inputs", None), "query", None)
        task_text = str(query or "").strip()
        if task_text:
            session.update_state({_BROWSER_PROGRESS_TASK_KEY: task_text})
            phase_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
            if not isinstance(phase_state, dict) or phase_state.get("task") != task_text:
                session.update_state(
                    {
                        _BROWSER_PHASE_STATE_KEY: self._build_phase_state(
                            task_text
                        )
                    }
                )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_model_call", ctx)
        session = getattr(ctx, "session", None)
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return

        image_input_supported = self._image_input_supported(ctx.agent)
        builder.add_section(
            PromptSection(
                name=_BROWSER_IMAGE_CAPABILITY_SECTION_NAME,
                content=_BROWSER_IMAGE_CAPABILITY_GUIDANCE[image_input_supported],
                priority=85,
            )
        )
        if session is None:
            return

        phase_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(phase_state, dict):
            builder.add_section(
                PromptSection(
                    name=_BROWSER_PHASE_SECTION_NAME,
                    content={
                        "en": self._render_phase_guidance(phase_state),
                        "cn": self._render_phase_guidance(phase_state),
                    },
                    priority=86,
                )
            )

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
            await self._clear_progress_attachment(ctx)
            return

        progress_context = BrowserService.build_progress_context(progress_state)
        if not progress_context:
            builder.remove_section(_BROWSER_PROGRESS_SECTION_NAME)
            await self._clear_progress_attachment(ctx)
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
        manager = getattr(ctx.agent, "prompt_attachment_manager", None)
        if not isinstance(manager, PromptAttachmentManager):
            builder.remove_section(_BROWSER_PROGRESS_SECTION_NAME)
            logger.warning(
                "[BrowserRuntimeRail] prompt attachment manager unavailable; "
                "skip browser progress continuation attachment"
            )
            return

        builder.remove_section(_BROWSER_PROGRESS_SECTION_NAME)
        language = getattr(builder, "language", "cn")
        continuation_text = continuation_text_cn if language == "cn" else continuation_text_en
        await self._upsert_progress_attachment(ctx, manager, continuation_text)

    async def _upsert_progress_attachment(
        self,
        ctx: AgentCallbackContext,
        manager: PromptAttachmentManager,
        content: str,
    ) -> None:
        writer = manager.bind_context(ctx)
        try:
            await writer.add_section(
                section=_BROWSER_PROGRESS_SECTION_NAME,
                content=content,
                kind=PromptAttachmentKind.RUNTIME,
                source="agent_core.browser_runtime",
                priority=83,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[BrowserRuntimeRail] skip progress attachment: %s", exc)

    async def _clear_progress_attachment(self, ctx: AgentCallbackContext) -> None:
        manager = getattr(ctx.agent, "prompt_attachment_manager", None)
        if not isinstance(manager, PromptAttachmentManager):
            return
        writer = manager.bind_context(ctx)
        try:
            await writer.clear_section(_BROWSER_PROGRESS_SECTION_NAME)
        except ValueError:
            return

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_model_call", ctx)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_model_exception", ctx)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_tool_call", ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        tool_args = getattr(inputs, "tool_args", None)
        normalized_args = self._normalize_playwright_ref_args(tool_name, tool_args)
        if normalized_args is not tool_args:
            inputs.tool_args = normalized_args
        normalized_tool_name = tool_name.strip().lower()
        if (
            "browser_batch_interact" in normalized_tool_name
            and not self._image_input_supported(getattr(ctx, "agent", None))
        ):
            batch_args = self._coerce_tool_args(normalized_args)
            steps = batch_args.get("steps")
            if any(
                isinstance(step, dict)
                and str(step.get("op") or "").strip().lower() == "screenshot"
                for step in (steps if isinstance(steps, list) else [])
            ):
                raise ValueError(
                    "Screenshot input is unavailable for this browser agent. "
                    "Remove the screenshot batch step and use DOM probes or "
                    "structured extraction."
                )
        if "playwright" in normalized_tool_name and normalized_tool_name.endswith("browser_click"):
            primary_link = self._runtime.resolve_primary_link(normalized_args)
            if isinstance(primary_link, str) and primary_link:
                inputs.tool_name = re.sub(
                    r"browser_click$",
                    "browser_navigate",
                    tool_name,
                    flags=re.IGNORECASE,
                )
                inputs.tool_args = {"url": primary_link}
                tool_name = inputs.tool_name
                normalized_args = inputs.tool_args
                normalized_tool_name = tool_name.strip().lower()
        if "playwright" in normalized_tool_name and "browser_" in normalized_tool_name:
            self._runtime.validate_reference_values(
                self._extract_playwright_ref_values(normalized_args)
            )
        self._consume_phase_budget(
            getattr(ctx, "session", None),
            tool_name,
            normalized_args,
        )

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_tool_exception", ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_tool_call", ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "").strip()
        tool_result = self._normalize_tool_result(getattr(inputs, "tool_result", None))
        self._runtime.record_tool_reference_state(
            tool_name=tool_name,
            tool_args=getattr(inputs, "tool_args", None),
            tool_result=tool_result,
        )
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._record_phase_result(
            session,
            tool_name,
            getattr(inputs, "tool_args", None),
            tool_result,
        )
        if not self._is_browser_progress_tool(tool_name):
            return
        session_id = session.get_session_id()
        self._runtime.service.record_tool_progress(
            session_id=session_id,
            request_id="",
            tool_name=tool_name,
            tool_result=tool_result,
        )
        self._persist_service_progress_to_session(session)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            self._emit_status("after_invoke", ctx)
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
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
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

        finally:
            extra = getattr(ctx, "extra", None)
            if isinstance(extra, dict):
                token = extra.pop(_BROWSER_LOG_CONTEXT_TOKEN_KEY, None)
                if token is not None:
                    reset_browser_agent_log_context(token)

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

    @classmethod
    def _normalize_playwright_ref_args(cls, tool_name: str, tool_args: Any) -> Any:
        """Normalize snapshot references before Playwright MCP parses targets."""
        normalized_name = str(tool_name or "").strip().lower()
        if "playwright" not in normalized_name or "browser_" not in normalized_name:
            return tool_args

        original_is_json = isinstance(tool_args, str)
        if original_is_json:
            try:
                parsed = json.loads(tool_args)
            except (TypeError, ValueError, json.JSONDecodeError):
                return tool_args
        elif isinstance(tool_args, dict):
            parsed = dict(tool_args)
        else:
            return tool_args

        if not isinstance(parsed, dict):
            return tool_args

        changed = False
        for key in _BROWSER_REF_TARGET_KEYS:
            value = parsed.get(key)
            if not isinstance(value, str):
                continue
            match = _BROWSER_EXPLICIT_REF_RE.fullmatch(value)
            if match is None:
                continue
            parsed[key] = match.group(1)
            changed = True

        if not changed:
            return tool_args
        if original_is_json:
            return json.dumps(parsed, ensure_ascii=False)
        return parsed

    @staticmethod
    def _extract_playwright_ref_values(tool_args: Any) -> tuple[str, ...]:
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (TypeError, ValueError, json.JSONDecodeError):
                return ()
        else:
            parsed = tool_args
        if not isinstance(parsed, dict):
            return ()
        values: list[str] = []
        for key in _BROWSER_REF_TARGET_KEYS:
            value = parsed.get(key)
            if not isinstance(value, str):
                continue
            normalized = value.strip()
            if normalized:
                values.append(normalized)
        return tuple(values)

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

    @staticmethod
    def _build_phase_state(task: str) -> Dict[str, Any]:
        normalized = str(task or "").lower()
        complex_tokens = (
            "form", "filter", "sort", "compare", "book", "checkout",
            "register", "login", "purchase",
            "\u8868\u5355", "\u7b5b\u9009", "\u6392\u5e8f",
            "\u6bd4\u8f83", "\u9884\u8ba2", "\u767b\u5f55",
            "\u6ce8\u518c", "\u7ed3\u8d26", "\u8d2d\u4e70",
        )
        task_type = (
            "complex"
            if any(token in normalized for token in complex_tokens)
            else "simple"
        )
        phase_names = (
            tuple(_BROWSER_PHASE_DEFINITIONS)
            if task_type == "complex"
            else ("navigation", "extraction")
        )
        phases = {
            name: {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[name]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[name][
                    "completion_condition"
                ],
                "blocked_signature": "",
            }
            for name in phase_names
        }
        return {
            "task": task,
            "task_type": task_type,
            "phases": phases,
            "current_phase": phase_names[0],
            "known_urls": re.findall(r"https?://[^\s<>\"]+", task)[:4],
            "replan_count": 0,
        }

    @staticmethod
    def _render_phase_guidance(state: Dict[str, Any]) -> str:
        phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
        compact = {
            name: {
                "status": details.get("status"),
                "attempts": details.get("attempts"),
                "budget": details.get("budget"),
                "completion_condition": details.get("completion_condition"),
            }
            for name, details in phases.items()
            if isinstance(details, dict)
        }
        return (
            "Enforced browser phase plan: "
            + json.dumps(
                {
                    "task_type": state.get("task_type"),
                    "current_phase": state.get("current_phase"),
                    "phases": compact,
                    "replan_count": state.get("replan_count", 0),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ". A phase whose budget is exhausted blocks the next repeated action. "
            "Choose a materially different strategy before continuing. Complete as soon "
            "as the stated evidence condition is met."
        )

    @staticmethod
    def _coerce_tool_args(tool_args: Any) -> Dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _classify_tool_phase(
        cls,
        tool_name: str,
        tool_args: Any,
        state: Dict[str, Any],
    ) -> str:
        name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if any(token in name for token in ("navigate", "navigate_back", "tabs")):
            return "navigation"
        if any(
            token in serialized
            for token in ("filter", "sort", "\u7b5b\u9009", "\u6392\u5e8f")
        ):
            return "filtering"
        if "browser_batch_interact" in name:
            steps = args.get("steps")
            operations = {
                str(step.get("op") or "").lower()
                for step in steps or []
                if isinstance(step, dict)
            }
            if operations & {
                "fill", "type", "autocomplete", "select_option",
                "select_visible_text", "set_checked",
            }:
                return "form"
            if operations & {"extract_text", "extract_value"}:
                return "extraction"
        if any(
            token in name
            for token in ("fill", "type", "select", "press", "file_upload")
        ):
            return "form"
        if any(
            token in name
            for token in (
                "probe", "snapshot", "evaluate", "run_code",
                "console", "network_request",
            )
        ):
            return "extraction"
        current = str(state.get("current_phase") or "navigation")
        return current if current in _BROWSER_PHASE_DEFINITIONS else "navigation"

    @classmethod
    def _phase_action_signature(
        cls,
        tool_name: str,
        tool_args: Any,
    ) -> str:
        args = cls._coerce_tool_args(tool_args)
        targets = {
            key: args.get(key)
            for key in (
                "url", "href", "selector", "target", "ref",
                "role", "name", "text", "query",
            )
            if args.get(key) is not None
        }
        steps = args.get("steps")
        if isinstance(steps, list):
            targets["steps"] = [
                {
                    key: step.get(key)
                    for key in (
                        "op",
                        "url",
                        "selector",
                        "role",
                        "name",
                        "label",
                        "placeholder",
                        "text",
                        "field",
                    )
                    if step.get(key) is not None
                }
                for step in steps
                if isinstance(step, dict)
            ]
        return (
            str(tool_name or "").strip().lower()
            + ":"
            + json.dumps(
                targets,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )[:600]

    @classmethod
    def _consume_phase_budget(
        cls,
        session: Any,
        tool_name: str,
        tool_args: Any,
    ) -> None:
        if session is None:
            return
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return
        phases = state.setdefault("phases", {})
        phase = cls._classify_tool_phase(tool_name, tool_args, state)
        details = phases.setdefault(
            phase,
            {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[phase]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[phase][
                    "completion_condition"
                ],
                "blocked_signature": "",
            },
        )
        signature = cls._phase_action_signature(tool_name, tool_args)
        blocked_signature = str(details.get("blocked_signature") or "")
        if details.get("status") == "replan_required":
            if signature == blocked_signature:
                raise ValueError(
                    f"Browser {phase} phase requires re-planning. The same action "
                    "was already blocked after exhausting its phase budget; choose "
                    "a different tool, target, direct URL, or structured probe."
                )
            details["status"] = "in_progress"
            details["attempts"] = 0
            details["blocked_signature"] = ""

        attempts = int(details.get("attempts") or 0)
        budget = max(1, int(details.get("budget") or 1))
        if attempts >= budget:
            details["status"] = "replan_required"
            details["blocked_signature"] = signature
            state["current_phase"] = phase
            state["replan_count"] = int(state.get("replan_count") or 0) + 1
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                f"Browser {phase} phase budget exhausted ({attempts}/{budget}). "
                "Re-plan before issuing another browser action; do not try another "
                "generic selector variant."
            )

        known_urls = state.get("known_urls")
        total_attempts = sum(
            int(item.get("attempts") or 0)
            for item in phases.values()
            if isinstance(item, dict)
        )
        if known_urls and total_attempts == 0 and phase != "navigation":
            raise ValueError(
                "The task already contains a known URL. Navigate to it directly "
                "before selector exploration."
            )

        details["attempts"] = attempts + 1
        details["status"] = "in_progress"
        details["last_signature"] = signature
        state["current_phase"] = phase
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})

    @classmethod
    def _record_phase_result(
        cls,
        session: Any,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
    ) -> None:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return
        phase = cls._classify_tool_phase(tool_name, tool_args, state)
        phases = state.get("phases")
        if not isinstance(phases, dict):
            return
        details = phases.get(phase)
        if not isinstance(details, dict):
            return
        if not BrowserAgentRuntime._tool_result_succeeded(tool_result):
            details["status"] = "pending"
            if isinstance(tool_result, dict):
                details["last_error"] = str(tool_result.get("error") or "")[:300]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return

        details["successes"] = int(details.get("successes") or 0) + 1
        details["last_error"] = ""
        name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        result = tool_result if isinstance(tool_result, dict) else {}
        completion_evidence = ""
        if phase == "navigation":
            result_url = BrowserAgentRuntime._extract_result_url(tool_result)
            if result_url or "navigate" in name:
                completion_evidence = result_url or "navigation tool succeeded"
        elif phase == "form":
            steps = args.get("steps")
            operations = {
                str(step.get("op") or "").strip().lower()
                for step in (steps if isinstance(steps, list) else [])
                if isinstance(step, dict)
            }
            if operations & {
                "wait_for_url",
                "wait_for_selector",
                "wait_for_text",
                "wait_for_first_card_title",
                "wait_for_result_count",
                "wait_for_dom_text_change",
                "wait_for_stable",
            }:
                completion_evidence = (
                    "form batch completed with an observable condition"
                )
        elif phase == "filtering":
            steps = args.get("steps")
            operations = {
                str(step.get("op") or "").strip().lower()
                for step in (steps if isinstance(steps, list) else [])
                if isinstance(step, dict)
            }
            if operations & {
                "wait_for_sort_state",
                "wait_for_result_count",
                "wait_for_dom_text_change",
                "wait_for_first_card_title",
            }:
                completion_evidence = (
                    "requested state and changed results were observed"
                )
        elif phase == "extraction":
            extracted = result.get("extracted")
            cards = result.get("cards")
            if isinstance(extracted, dict) and extracted:
                completion_evidence = (
                    f"structured extraction returned {len(extracted)} field(s)"
                )
            elif isinstance(cards, list) and cards:
                completion_evidence = (
                    f"card probe returned {len(cards)} structured result(s)"
                )

        if completion_evidence:
            details["status"] = "completed"
            details["completion_evidence"] = completion_evidence[:300]
            phase_order = list(phases)
            try:
                next_phase = next(
                    candidate
                    for candidate in phase_order[phase_order.index(phase) + 1:]
                    if isinstance(phases.get(candidate), dict)
                    and phases[candidate].get("status") != "completed"
                )
            except (ValueError, StopIteration):
                next_phase = phase
            state["current_phase"] = next_phase
        else:
            details["status"] = "in_progress"
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})

    def _clear_progress_state(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        self._runtime.service.clear_progress_state(session_id)
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PROGRESS_TASK_KEY: "",
                _BROWSER_PHASE_STATE_KEY: {},
            }
        )

    @staticmethod
    def _image_input_supported(agent: Any) -> bool:
        deep_config = (
            getattr(agent, "deep_config", None)
            or getattr(agent, "_deep_config", None)
        )
        return getattr(deep_config, "enable_read_image_multimodal", None) is True

    @staticmethod
    def _known_browser_tool_names() -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                tool_name
                for capability in DEFAULT_BROWSER_CAPABILITIES
                for tool_name in capability.tool_names
            )
        )

    async def _effective_browser_tool_allowlist(
        self,
        agent: Any,
        mcp_cfg: McpServerConfig,
    ) -> Optional[tuple[str, ...]]:
        configured = self._runtime.service.allowed_tool_names
        if self._image_input_supported(agent):
            return configured

        if configured is None:
            try:
                tool_infos = (
                    await Runner.resource_mgr.get_mcp_tool_infos(
                        server_id=mcp_cfg.server_id,
                    )
                    or []
                )
                configured = tuple(
                    tool_info.name
                    for tool_info in tool_infos
                    if tool_info is not None
                )
                if not configured:
                    configured = self._known_browser_tool_names()
            except Exception as exc:
                browser_agent_log_warning(
                    "[BROWSER_SUBAGENT] failed to enumerate MCP tools while "
                    "disabling screenshot input: %s",
                    exc,
                )
                configured = self._known_browser_tool_names()

        return tuple(
            tool_name
            for tool_name in configured
            if tool_name not in _BROWSER_SCREENSHOT_TOOL_NAMES
        )

    async def _ensure_browser_mcp_ability(self, ctx: AgentCallbackContext) -> None:
        agent = getattr(ctx, "agent", None)
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        mcp_cfg = self._runtime.service.mcp_cfg
        ability_manager.add(mcp_cfg)
        allowed_tool_names = await self._effective_browser_tool_allowlist(
            agent,
            mcp_cfg,
        )
        if allowed_tool_names is not None:
            ability_manager.set_mcp_tool_allowlist(mcp_cfg, allowed_tool_names)
