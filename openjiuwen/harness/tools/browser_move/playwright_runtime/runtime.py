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

from ..controllers import ActionController, BaseController, validate_batch_steps
from ..utils.parsing import extract_json_object
from .browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
)
from .browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
)
from .browser_tools import ensure_browser_runtime_client_patch
from .config import BrowserInstanceConfig, BrowserRunGuardrails
from .probes import (
    build_browser_state_metadata_js,
    build_card_probe_js,
    build_interactive_probe_js,
)
from .page_state import BrowserPageState, BrowserTarget
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
_BROWSER_PAGE_STATE_TAG = "browser_page_state"
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
_BATCH_MODEL_LOCATOR_KEYS = frozenset(
    {"target_id", "ref", "selector", "role", "name", "label", "placeholder", "text", "testid"}
)
_BATCH_REGISTERED_TARGET_OPS = frozenset(
    {
        "click",
        "fill",
        "type",
        "autocomplete",
        "select_option",
        "set_checked",
        "select_visible_text",
        "extract_text",
        "extract_value",
    }
)
_BATCH_MUTATING_TARGET_OPS = frozenset(
    {"click", "fill", "type", "autocomplete", "select_option", "set_checked", "select_visible_text"}
)
_BATCH_EXPLICIT_SELECTOR_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
    }
)
_SINGLE_BATCH_CONDITION_OPS = frozenset({"wait_for_text"})
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


def _contains_any_token(value: str, tokens: Iterable[str]) -> bool:
    for token in tokens:
        if token in value:
            return True
    return False


def _has_non_empty_mapping_value(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> bool:
    for key in keys:
        if value.get(key) not in (None, ""):
            return True
    return False


def _copy_non_none_values(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> Dict[str, Any]:
    copied: Dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item is not None:
            copied[key] = item
    return copied


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
        resolved_allowed_tool_names = (
            CORE_BROWSER_TOOL_NAMES
            if allowed_tool_names is None
            else tuple(dict.fromkeys(allowed_tool_names))
        )
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
            instance=instance,
            allowed_tool_names=resolved_allowed_tool_names,
        )
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._code_executor = None
        self._browser_probe_interactives_tool = None
        self._browser_probe_cards_tool = None
        self._browser_batch_interact_tool = None
        self._page_state = BrowserPageState()
        self._page_generation = self._page_state.generation
        self._reference_generations = self._page_state.reference_generations
        self._selector_primary_links = self._page_state.selector_primary_links
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

    def _ensure_page_state(self) -> BrowserPageState:
        """Create PageState lazily for compatibility with lightweight test runtimes."""
        page_state = getattr(self, "_page_state", None)
        if isinstance(page_state, BrowserPageState):
            return page_state
        page_state = BrowserPageState(
            generation=int(getattr(self, "_page_generation", 0) or 0),
        )
        page_state.reference_generations.update(getattr(self, "_reference_generations", {}) or {})
        page_state.selector_primary_links.update(getattr(self, "_selector_primary_links", {}) or {})
        self._page_state = page_state
        self._reference_generations = page_state.reference_generations
        self._selector_primary_links = page_state.selector_primary_links
        return page_state

    @property
    def page_id(self) -> str:
        return self._ensure_page_state().page_id

    @property
    def generation_id(self) -> str:
        return self._ensure_page_state().generation_id

    def export_page_state(self) -> Dict[str, Any]:
        return self._ensure_page_state().export()

    def _advance_page_generation(self) -> None:
        page_state = self._ensure_page_state()
        page_state.advance()
        self._page_generation = page_state.generation

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
        self._ensure_page_state().observe(url=normalized)
        if normalized:
            self._last_observed_url = normalized

    @classmethod
    def extract_result_url(cls, value: Any) -> str:
        if isinstance(value, dict):
            direct = value.get("url")
            if direct:
                return str(direct)
            page = value.get("page")
            if isinstance(page, dict) and page.get("url"):
                return str(page["url"])
            for nested in value.values():
                resolved = cls.extract_result_url(nested)
                if resolved:
                    return resolved
        elif isinstance(value, (list, tuple)):
            for nested in value:
                resolved = cls.extract_result_url(nested)
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

    @classmethod
    def _extract_result_title(cls, value: Any) -> str:
        if isinstance(value, dict):
            direct = value.get("title")
            if direct:
                return str(direct)
            page = value.get("page")
            if isinstance(page, dict) and page.get("title"):
                return str(page["title"])
            for nested in value.values():
                resolved = cls._extract_result_title(nested)
                if resolved:
                    return resolved
        elif isinstance(value, (list, tuple)):
            for nested in value:
                resolved = cls._extract_result_title(nested)
                if resolved:
                    return resolved
        elif isinstance(value, str):
            match = re.search(
                r"(?:Page\s+Title|title)\s*[:=]\s*([^\r\n]+)",
                value,
                re.IGNORECASE,
            )
            if match is not None:
                return match.group(1).strip()
        return ""

    @staticmethod
    def tool_result_succeeded(value: Any) -> bool:
        if hasattr(value, "success") and getattr(value, "success", None) is False:
            return False
        if isinstance(value, dict):
            if value.get("ok") is False or value.get("success") is False:
                return False
            if value.get("error") and not value.get("ok"):
                return False
        return True

    def _register_snapshot_refs(self, value: Any, *, replace: bool = False) -> None:
        page_state = self._ensure_page_state()
        registered = page_state.replace_ax_snapshot(value) if replace else page_state.register_ax_snapshot(value)
        if registered:
            return
        # Preserve ref-only snapshots that do not follow the normal AX line shape.
        text = value if isinstance(value, str) else str(value)
        for ref_value in _BROWSER_SNAPSHOT_REF_RE.findall(text):
            page_state.reference_generations[str(ref_value)] = page_state.generation

    def validate_reference_values(self, values: Iterable[str]) -> None:
        page_state = self._ensure_page_state()
        stale_values: set[str] = set()
        for ref_value in values:
            ref_generation = page_state.reference_generations.get(ref_value)
            if ref_generation is not None and ref_generation != page_state.generation:
                stale_values.add(ref_value)
        stale = sorted(stale_values)
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
        if not self.tool_result_succeeded(tool_result):
            return
        result_url = self.extract_result_url(tool_result)
        result_title = self._extract_result_title(tool_result)
        navigation_like = _contains_any_token(
            normalized_name,
            ("browser_navigate", "browser_navigate_back", "browser_tabs"),
        )
        if "browser_tabs" in normalized_name and isinstance(tool_args, dict):
            navigation_like = str(tool_args.get("action") or "").lower() in {
                "new",
                "select",
                "close",
            }
        self._observe_page_url(result_url, force_navigation=navigation_like)
        self._ensure_page_state().observe(url=result_url, title=result_title)
        if "browser_snapshot" in normalized_name or "browser_find" in normalized_name:
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
        page_state = self._ensure_page_state()
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
                    page_state.selector_primary_links[selector] = (
                        page_state.generation,
                        href,
                    )

    def resolve_primary_link(self, tool_args: Any) -> str:
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except ValueError:
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
            if generation_and_href and generation_and_href[0] == self._ensure_page_state().generation:
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

    async def _call_playwright_tool(self, tool_name: str, inputs: Dict[str, Any]) -> Any:
        """Invoke one registered Playwright MCP tool and unwrap its result data."""
        tool = await self._get_playwright_mcp_tool(tool_name)
        result = await tool.invoke(inputs)

        success = getattr(result, "success", None)
        if success is False:
            error = str(getattr(result, "error", "") or "").strip()
            raise RuntimeError(error or f"{tool_name} failed")

        data = getattr(result, "data", None)
        if data is not None:
            return data
        return result

    async def ensure_runtime_ready(self) -> None:
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        await self._service.ensure_runtime_ready()
        if self._code_executor is not None:
            return

        async def _direct_code_executor(js_code: str):
            return await self._call_playwright_run_code_unsafe(js_code)

        self._code_executor = _direct_code_executor
        self._controller.bind_code_executor(_direct_code_executor)
        self._controller.register_builtin_actions()

    async def capture_browser_state(self) -> Dict[str, Any]:
        """Capture a fresh, non-cached browser observation for the next model call."""
        await self.ensure_runtime_ready()

        dom = ""
        dom_error = None
        snapshot_captured = False
        try:
            raw_snapshot = await self._call_playwright_tool("browser_snapshot", {})
            raw_snapshot = self._unwrap_mcp_text_result(raw_snapshot)
            snapshot_captured = True
            if isinstance(raw_snapshot, str):
                dom = raw_snapshot
            elif raw_snapshot is not None:
                dom = json.dumps(raw_snapshot, ensure_ascii=False)
        except Exception as exc:
            dom_error = f"browser_snapshot failed: {exc}"
            logger.warning(
                "[BrowserAgentRuntime] current DOM snapshot capture failed: %s",
                exc,
                exc_info=True,
            )

        metadata: Dict[str, Any] = {}
        metadata_error = None
        try:
            raw_metadata = await self._call_playwright_run_code_unsafe(
                build_browser_state_metadata_js()
            )
            raw_metadata = self._unwrap_mcp_text_result(raw_metadata)
            metadata = extract_json_object(raw_metadata)
            if not metadata:
                metadata_error = "Could not parse browser state metadata result JSON"
            elif metadata.get("ok") is False:
                metadata_error = str(metadata.get("error") or "browser state metadata capture failed")
        except Exception as exc:
            metadata_error = f"browser state metadata capture failed: {exc}"
            logger.warning(
                "[BrowserAgentRuntime] current browser metadata capture failed: %s",
                exc,
                exc_info=True,
            )

        self._observe_page_url(metadata.get("url"))
        self._ensure_page_state().observe(title=metadata.get("title"))
        if snapshot_captured:
            self._register_snapshot_refs(dom, replace=True)

        errors = [error for error in (dom_error, metadata_error) if error]
        return {
            "ok": not errors,
            "error": "; ".join(errors) or None,
            "url": metadata.get("url") or "",
            "title": metadata.get("title") or "",
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "dom": dom,
            "dom_error": dom_error,
        }

    async def acquire_task_resources(self) -> None:
        """Acquire one task reference before invoking a reusable subagent."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        self._service.acquire_task_binding()

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

    async def _materialize_ax_target(self, target: BrowserTarget) -> BrowserTarget:
        """Convert an MCP AX ref into a temporary DOM marker without model translation."""
        if target.locator.get("selector"):
            return target
        ref_value = str(target.ref or target.locator.get("ref") or "").strip()
        if not ref_value:
            raise ValueError(f"PageState target has no executable locator: {target.target_id}")

        attribute = "data-openjiuwen-target-id"
        marker_value = target.target_id
        function = (
            f"(element) => {{element.setAttribute({json.dumps(attribute)}, {json.dumps(marker_value)});return true;}}"
        )
        tool = await self._get_playwright_mcp_tool("browser_evaluate")
        result = await tool.invoke(
            {
                "target": ref_value,
                "element": f"PageState target {target.target_id}",
                "function": function,
            }
        )
        if getattr(result, "success", None) is False:
            error = str(getattr(result, "error", "") or "").strip()
            raise ValueError(error or f"Failed to resolve AX ref {ref_value} for batch execution")
        selector = f'[{attribute}="{marker_value}"]'
        self._ensure_page_state().update_target_locator(
            target.target_id,
            {"selector": selector},
        )
        return target

    async def _resolve_batch_target(
        self,
        step: Dict[str, Any],
        *,
        generation_id: str,
        option: bool = False,
        allow_navigation_rewrite: bool = False,
    ) -> None:
        page_state = self._ensure_page_state()
        prefix = "option_" if option else ""
        target_id = str(
            step.get(f"{prefix}target_id") or (step.get("choose_target_id") if option else "") or ""
        ).strip()
        ref_value = str(step.get(f"{prefix}ref") or (step.get("choose_ref") if option else "") or "").strip()
        selector = str(step.get(f"{prefix}selector") or (step.get("choose_selector") if option else "") or "").strip()
        op = str(step.get("op") or "").strip().lower()

        if not target_id and not ref_value and not selector:
            return
        require_registered_selector = op in _BATCH_REGISTERED_TARGET_OPS
        if op in _BATCH_EXPLICIT_SELECTOR_OPS:
            require_registered_selector = False
        target = page_state.resolve_target(
            generation_id=generation_id,
            target_id=target_id,
            ref=ref_value,
            selector=selector,
            require_registered_selector=require_registered_selector,
        )
        if target is None:
            return
        if target.href and target.source in {"card", "card_primary_link"} and op == "click":
            if allow_navigation_rewrite:
                for key in _BATCH_MODEL_LOCATOR_KEYS:
                    step.pop(key, None)
                step["resolved_target_id"] = target.target_id
                step["_navigate_url"] = target.href
                return
            raise ValueError(
                f"Target {target.target_id} has primary_link={target.href}; "
                "call browser_navigate directly instead of clicking it in a batch"
            )
        requires_actionability = (
            op in _BATCH_MUTATING_TARGET_OPS and target.source != "ax"
        )
        if requires_actionability:
            is_unavailable = (
                not target.visible or not target.enabled or not target.actionable
            )
            if is_unavailable:
                raise ValueError(
                    f"PageState target {target.target_id} is not actionable "
                    f"in {target.generation_id}"
                )
        if target.locator.get("ref"):
            target = await self._materialize_ax_target(target)

        locator = dict(target.locator)
        if option:
            for key in (
                "option_target_id",
                "choose_target_id",
                "option_ref",
                "choose_ref",
                "option_selector",
                "choose_selector",
                "option_role",
                "choose_role",
                "option_name",
                "choose_name",
                "option_text",
                "choose_text",
                "text_to_choose",
            ):
                step.pop(key, None)
            if locator.get("selector"):
                step["option_selector"] = locator["selector"]
            elif locator.get("role"):
                step["option_role"] = locator["role"]
                if locator.get("name"):
                    step["option_name"] = locator["name"]
            elif locator.get("text"):
                step["option_text"] = locator["text"]
            else:
                raise ValueError(f"PageState option target is not executable: {target.target_id}")
            step["resolved_option_target_id"] = target.target_id
            return

        for key in _BATCH_MODEL_LOCATOR_KEYS:
            step.pop(key, None)
        step.update(locator)
        step["resolved_target_id"] = target.target_id

    async def _resolve_batch_steps(
        self,
        steps: Any,
        *,
        generation_id: str,
        allow_navigation_rewrite: bool = False,
    ) -> list[Dict[str, Any]]:
        resolved_steps = [dict(step) for step in steps]
        for step in resolved_steps:
            await self._resolve_batch_target(
                step,
                generation_id=generation_id,
                allow_navigation_rewrite=allow_navigation_rewrite,
            )
            option_target_keys = (
                "option_target_id",
                "choose_target_id",
                "option_ref",
                "choose_ref",
                "option_selector",
                "choose_selector",
            )
            if _has_non_empty_mapping_value(step, option_target_keys):
                await self._resolve_batch_target(
                    step,
                    generation_id=generation_id,
                    option=True,
                )
        return resolved_steps

    @staticmethod
    def _single_batch_primitive_spec(
        step: Dict[str, Any],
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """Map a semantically equivalent one-step batch to an MCP primitive."""
        op = str(step.get("op") or "").strip().lower()
        selector = str(step.get("selector") or "").strip()
        element = str(
            step.get("description")
            or step.get("resolved_target_id")
            or selector
            or op
        )
        target_args = {"target": selector, "element": element} if selector else {}

        navigation_url = str(step.get("_navigate_url") or "").strip()
        if op == "click" and navigation_url:
            return "browser_navigate", {"url": navigation_url}
        if op == "click" and target_args:
            return "browser_click", target_args
        if op in {"fill", "type"} and target_args:
            try:
                typing_delay_ms = int(step.get("delay_ms") or 0)
            except (TypeError, ValueError):
                typing_delay_ms = 0
            return "browser_type", {
                **target_args,
                "text": str(step.get("value") or ""),
                "slowly": op == "type" or typing_delay_ms > 0,
            }
        if op == "select_option" and target_args:
            values = step.get("values")
            if values is None:
                values = step.get("option_value", step.get("value"))
            if values is not None:
                normalized_values = values if isinstance(values, list) else [values]
                return "browser_select_option", {
                    **target_args,
                    "values": [str(value) for value in normalized_values],
                }
        if op == "press" and not any(step.get(key) for key in _BATCH_MODEL_LOCATOR_KEYS):
            return "browser_press_key", {"key": str(step.get("key") or "Enter")}
        if op == "wait_for_text":
            return "browser_wait_for", {"text": str(step.get("text") or "")}
        if op == "sleep":
            wait_ms = max(0, int(step.get("ms", step.get("time_ms", 0)) or 0))
            return "browser_wait_for", {"time": wait_ms / 1000}
        if op == "screenshot":
            args: Dict[str, Any] = {
                "type": "png",
                "fullPage": bool(step.get("full_page", False)),
            }
            if step.get("path"):
                args["filename"] = str(step["path"])
            return "browser_take_screenshot", args
        return None

    async def _run_single_batch_primitive(
        self,
        step: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        spec = self._single_batch_primitive_spec(step)
        if spec is None:
            return None

        tool_name, tool_args = spec
        op = str(step.get("op") or "").strip().lower()
        started_at = time.perf_counter()
        try:
            tool = await self._get_playwright_mcp_tool(tool_name)
        except RuntimeError:
            logger.debug(
                "Single-step primitive %s is unavailable; using compact runtime RPC",
                tool_name,
                exc_info=True,
            )
            return None
        try:
            tool_result = await tool.invoke(tool_args)
            success = self.tool_result_succeeded(tool_result)
            error = str(getattr(tool_result, "error", "") or "").strip()
            payload = getattr(tool_result, "data", None)
            if payload is None:
                payload = tool_result
        except Exception as exc:
            success = False
            error = str(exc)
            payload = None

        elapsed_ms = int(max(0.0, (time.perf_counter() - started_at) * 1000))
        if success:
            recorded_payload = payload
            if tool_name == "browser_navigate":
                target_url = str(tool_args.get("url") or "")
                if isinstance(payload, dict):
                    recorded_payload = dict(payload)
                    recorded_payload.setdefault("url", target_url)
                else:
                    recorded_payload = {"url": target_url, "result": payload}
            self.record_tool_reference_state(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=recorded_payload,
            )

        runtime_url = self.extract_result_url(payload)
        if tool_name == "browser_navigate" and success:
            runtime_url = runtime_url or str(tool_args.get("url") or "")
        runtime_title = self._extract_result_title(payload)
        step_result = {
            "index": 0,
            "op": op,
            "ok": success,
            "status": "completed" if success else "failed",
            "elapsed_ms": elapsed_ms,
        }
        if error:
            step_result["error"] = error
        conditions = []
        if op in _SINGLE_BATCH_CONDITION_OPS:
            condition = dict(step_result)
            condition["observed"] = {"text": str(step.get("text") or "")}
            conditions.append(condition)
        return {
            "ok": success,
            "status": "completed" if success else "failed",
            "error": error or None,
            "action": "browser_batch_interact",
            "execution_mode": "primitive",
            "generation_id": self.generation_id,
            "steps": [step_result],
            "extracted": {},
            "conditions": conditions,
            "metrics": {
                "tool_name": tool_name,
                "executor_elapsed_ms": elapsed_ms,
                "response_size_bytes": len(str(payload).encode("utf-8", "ignore")),
            },
            "_runtime_page": {"url": runtime_url, "title": runtime_title},
        }

    async def batch_interact(
        self,
        *,
        steps: Any,
        generation_id: str,
        timeout_ms: Any = None,
        condition_timeout_ms: Any = None,
        wait_after_each_ms: Any = None,
        continue_on_error: bool = False,
        global_timeout_ms: Any = None,
        session_id: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        page_state = self._ensure_page_state()
        validation_errors = validate_batch_steps(steps)
        if validation_errors:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_validation_failed: {validation_errors[0]}",
                "validation_errors": validation_errors,
                "page_state": page_state.export(),
            }
        try:
            page_state.validate_generation(generation_id)
            await self.ensure_runtime_ready()
            resolved_steps = await self._resolve_batch_steps(
                steps,
                generation_id=generation_id,
                allow_navigation_rewrite=len(steps) == 1,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_target_validation_failed: {exc}",
                "page_state": page_state.export(),
            }
        result = None
        if len(resolved_steps) == 1:
            result = await self._run_single_batch_primitive(resolved_steps[0])
        if result is None:
            self._controller.bind_runtime(self)
            if self._code_executor is not None:
                self._controller.bind_code_executor(self._code_executor)
            result = await self._controller.run_action(
                action="browser_batch_interact",
                session_id=session_id,
                request_id=request_id,
                steps=resolved_steps,
                timeout_ms=timeout_ms,
                condition_timeout_ms=condition_timeout_ms,
                wait_after_each_ms=wait_after_each_ms,
                continue_on_error=continue_on_error,
                global_timeout_ms=global_timeout_ms,
                generation_id=generation_id,
            )
        if isinstance(result, dict):
            runtime_page = result.pop("_runtime_page", {})
            runtime_url = runtime_page.get("url") if isinstance(runtime_page, dict) else ""
            runtime_title = runtime_page.get("title") if isinstance(runtime_page, dict) else ""
            if result.get("execution_mode") != "primitive":
                self._observe_page_url(runtime_url)
            self._ensure_page_state().observe(
                url=runtime_url,
                title=runtime_title,
            )
            extracted = result.get("extracted")
            if isinstance(extracted, dict):
                self._ensure_page_state().add_field_coverage(extracted.keys())
            result["generation_id"] = self.generation_id
            result["page_state"] = self.export_page_state()
        return result

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
                "page_state": self.export_page_state(),
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
                "page_state": self.export_page_state(),
            }

        parsed = extract_json_object(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Could not parse browser_probe_interactives result JSON",
                "raw_preview": str(raw)[:400],
                "elements": [],
                "page_state": self.export_page_state(),
            }

        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("elements", [])
        self._observe_page_url(parsed.get("url"))
        self._annotate_probe_generation(parsed)
        page_state = self._ensure_page_state()
        page_state.register_interactives(parsed)
        parsed["page_state"] = page_state.export()
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
                "page_state": self.export_page_state(),
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
                "page_state": self.export_page_state(),
            }

        parsed = extract_json_object(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Could not parse browser_probe_cards result JSON",
                "raw_preview": str(raw)[:400],
                "cards": [],
                "page_state": self.export_page_state(),
            }

        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("cards", [])
        self._observe_page_url(parsed.get("url"))
        self._annotate_probe_generation(parsed)
        page_state = self._ensure_page_state()
        page_state.register_cards(parsed)
        self.register_card_primary_links(parsed)
        parsed["page_state"] = page_state.export()

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
        try:
            await self._service.shutdown()
        finally:
            _ACTIVE_BROWSER_RUNTIMES.discard(self)

    async def release_task_resources(self) -> None:
        """Release task bindings while preserving Chrome and its profile."""
        fully_released = await self._service.release_task_binding()
        if fully_released:
            self._advance_page_generation()
            self._last_observed_url = ""

    async def reset(self) -> None:
        """Release the current browser and restart lazily on the next task."""
        try:
            await self._service.reset()
        finally:
            self._advance_page_generation()
            self._last_observed_url = ""


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


async def reset_managed_browser_runtime(
    *,
    browser_key: str = "",
    profile_name: str = "jiuwenclaw",
    display_mode: str,
    browser_binary: str = "",
) -> int:
    """Reset only the configured managed browser, including its idle handle."""
    normalized_binary = BrowserService.normalize_browser_binary(browser_binary)
    normalized_key = str(browser_key or "").strip()
    normalized_profile = str(profile_name or "").strip()
    normalized_mode = str(display_mode or "").strip().lower()

    reset_count = 0
    for runtime in list(_ACTIVE_BROWSER_RUNTIMES):
        identity = runtime.service.lifecycle_identity
        matches_browser = identity.browser_key == normalized_key
        matches_profile = identity.profile_name == normalized_profile
        matches_mode = identity.display_mode == normalized_mode
        matches_binary = identity.browser_binary == normalized_binary
        if identity.driver_mode != "managed":
            continue
        if not matches_browser or not matches_profile:
            continue
        if not matches_mode or not matches_binary:
            continue
        try:
            await runtime.reset()
        except Exception as exc:
            logger.warning(
                "Failed to reset managed browser runtime %s: %s",
                id(runtime),
                exc,
            )
        else:
            reset_count += 1

    reset_count += await BrowserService.reset_registered_managed_browser(
        browser_key=normalized_key,
        profile_name=normalized_profile,
        display_mode=normalized_mode,
        browser_binary=normalized_binary,
    )
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
        self._attach_page_state(inputs, tool_name, tool_result)
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

    def _attach_page_state(
        self,
        inputs: Any,
        tool_name: str,
        tool_result: Any,
    ) -> None:
        normalized_name = str(tool_name or "").strip().lower()
        page_state_tokens = (
            "browser_navigate",
            "browser_tabs",
            "browser_snapshot",
            "browser_find",
            "browser_probe_",
            "browser_batch_interact",
        )
        if not _contains_any_token(normalized_name, page_state_tokens):
            return

        page_state = self._runtime.export_page_state()
        already_embedded = isinstance(tool_result, dict) and isinstance(
            tool_result.get("page_state"),
            dict,
        )
        if isinstance(tool_result, dict) and not already_embedded:
            tool_result["page_state"] = page_state

        if already_embedded:
            return
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        marker = (
            f"\n<{_BROWSER_PAGE_STATE_TAG}>"
            f"{json.dumps(page_state, ensure_ascii=False, separators=(',', ':'))}"
            f"</{_BROWSER_PAGE_STATE_TAG}>"
        )
        if f"<{_BROWSER_PAGE_STATE_TAG}>" not in content:
            tool_msg.content = content.rstrip() + marker

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
            except ValueError:
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
            except ValueError:
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
            except ValueError:
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
        extraction_tokens = (
            "probe",
            "snapshot",
            "evaluate",
            "run_code",
            "console",
            "network_request",
        )
        if _contains_any_token(name, extraction_tokens):
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
        target_keys = (
            "url",
            "href",
            "selector",
            "target",
            "ref",
            "role",
            "name",
            "text",
            "query",
        )
        targets = _copy_non_none_values(args, target_keys)
        steps = args.get("steps")
        if isinstance(steps, list):
            step_keys = (
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
            compact_steps = []
            for step in steps:
                if isinstance(step, dict):
                    compact_steps.append(_copy_non_none_values(step, step_keys))
            targets["steps"] = compact_steps
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
        if not BrowserAgentRuntime.tool_result_succeeded(tool_result):
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
            result_url = BrowserAgentRuntime.extract_result_url(tool_result)
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
            next_phase = phase
            try:
                remaining_phases = phase_order[phase_order.index(phase) + 1:]
            except ValueError:
                remaining_phases = []
            for candidate in remaining_phases:
                candidate_state = phases.get(candidate)
                if not isinstance(candidate_state, dict):
                    continue
                if candidate_state.get("status") == "completed":
                    continue
                next_phase = candidate
                break
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

    async def _effective_browser_tool_allowlist(
        self,
        agent: Any,
        mcp_cfg: McpServerConfig,
    ) -> tuple[str, ...]:
        del mcp_cfg
        configured = self._runtime.service.allowed_tool_names or CORE_BROWSER_TOOL_NAMES
        if self._image_input_supported(agent):
            return tuple(configured)

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
