#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime wiring for browser tool registration and service lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit
from weakref import WeakSet

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.browser_context import (
    reset_browser_agent_log_context,
    set_browser_agent_log_context,
)
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from ..controllers import ActionController, BaseController, validate_batch_steps
from ..utils.parsing import extract_json_object
from .browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
)
from .browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
    write_browser_agent_audit_artifact,
)
from .browser_tools import ensure_browser_runtime_client_patch
from .browser_working_context import (
    BROWSER_TASK_STATE_KEY,
    BrowserWorkingContextStore,
    latest_browser_user_request,
)
from .config import BrowserInstanceConfig, BrowserRunGuardrails
from .probes import (
    build_browser_state_metadata_js,
    build_card_probe_js,
    build_interactive_probe_js,
)
from .page_state import CARD_EVIDENCE_FIELDS, BrowserPageState, BrowserTarget
from .probe_semantics import normalize_card_probe_payload
from .semantic_state import SemanticStateTracker, price_interval_signature
from .service import MAX_ITERATION_MESSAGE, BrowserService, BrowserTaskProgressState
from .site_profiles import (
    get_selector_cache,
    infer_profile_evidence_entity,
    site_profiles_for_url,
)
from .status_logging import BrowserSubagentStatusLogger, is_browser_subagent_status_log_enabled


_BROWSER_PROGRESS_STATE_KEY = "__browser_subagent_progress_state__"
_BROWSER_PROGRESS_TASK_KEY = "__browser_subagent_last_task__"
_BROWSER_PROGRESS_FORMAT_SECTION_NAME = "browser_progress_format"
_BROWSER_IMAGE_CAPABILITY_SECTION_NAME = "browser_image_input_capability"
_BROWSER_PHASE_STATE_KEY = BROWSER_TASK_STATE_KEY
_BROWSER_SCREENSHOT_TOOL_NAMES = frozenset({"browser_take_screenshot"})
_BROWSER_LOG_CONTEXT_TOKEN_KEY = "__browser_agent_log_context_token__"
_BROWSER_TOOL_RUNTIME_STATE_KEY = "__browser_tool_runtime_state__"
_BROWSER_ACTION_GROUP_BY_CALL_KEY = "__browser_action_group_by_call__"
_BROWSER_ACTION_GROUP_RESULTS_KEY = "__browser_action_group_results__"
_BROWSER_SKIP_TOOL_CALLS_KEY = "_skip_tool_calls"
_BROWSER_TASK_DEADLINE_KEY = "__browser_task_deadline__"
_BROWSER_MODEL_RETRY_LIMIT = 1
_BROWSER_MODEL_PROTOCOL_RETRY_LIMIT = 1
_BROWSER_REPLAN_DENIAL_LIMIT = 3
_BROWSER_READ_ONLY_RECOVERY_LIMIT = 1
_BROWSER_TASK_RESUME_LIMIT = 1
_BROWSER_TERMINAL_SYNTHESIS_KEY = "terminal_synthesis_started"
_BROWSER_RUNTIME_TOOL_NAMES = frozenset(
    {
        "browser_batch_interact",
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_custom_action",
        "browser_list_custom_actions",
        "browser_cancel_run",
        "browser_clear_cancel",
        "browser_runtime_health",
    }
)
_ACTIVE_BROWSER_RUNTIMES: WeakSet[Any] = WeakSet()
_BROWSER_PROGRESS_TAG_RE = re.compile(
    r"<browser_progress>\s*(\{.*?\})\s*</browser_progress>",
    re.DOTALL | re.IGNORECASE,
)
_BROWSER_UNFINISHED_TOOL_INTENT_RE = re.compile(
    r"(?:"
    r"<[^>]*(?:dsml|tool[_ ]?calls?)[^>]*>"
    r"|(?:让我|我(?:将|会|需要|要))继续(?:调用|使用|执行).{0,100}"
    r"(?:工具|browser_|evaluate|probe|navigate|snapshot|batch)"
    r"|(?:let me|i(?:'ll| will| need to))\s+(?:continue|now)\s+"
    r"(?:call|calling|use|using|run).{0,100}"
    r"(?:tool|browser_|evaluate|probe|navigate|snapshot|batch)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_BROWSER_OUTPUT_REQUEST_CUE_RE = re.compile(
    r"(?:返回|告诉我|提取|获取|输出|给出|列出|汇报|报告|比较|对比|"
    r"return|tell\s+me|extract|retrieve|output|provide|list|report|compare)"
    r"[^。；;.!?\n]{0,240}",
    re.IGNORECASE,
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
_BROWSER_REF_ALIAS_KEYS = {
    "ref": "target",
    "startRef": "startTarget",
    "endRef": "endTarget",
}
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
    }
)
_BATCH_SAFE_READ_SELECTOR_OPS = frozenset({"extract_text", "extract_value"})
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
_BATCH_SAFE_GENERATION_REFRESH_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_text",
        "wait_for_load_state",
        "wait_for_url",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
        "wait_for_tab",
    }
)
_SINGLE_BATCH_CONDITION_OPS = frozenset({"wait_for_text"})
_BROWSER_TERMINAL_STATUSES = frozenset({"blocked", "partial", "completed"})
_BROWSER_NON_RETRYABLE_BLOCKER_TOKENS = frozenset(
    {
        "captcha",
        "login_required",
        "permission_denied",
        "access_denied",
        "user_intervention_required",
        "payment_required",
        "security_verification",
    }
)
_BROWSER_FIELD_ALIASES: Dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "标题", "名称", "电影", "商品", "结果"),
    "url": ("url", "link", "href", "primary link", "primary_link", "链接", "网址"),
    "price": ("price", "cost", "价格", "价钱", "费用"),
    "rating": ("rating", "score", "评分", "星级"),
    "product_rating": ("product rating", "item rating", "商品评分", "宝贝评分"),
    "shop_rating": ("shop rating", "seller rating", "store rating", "店铺评分", "卖家评分"),
    "author": ("author", "creator", "writer", "作者", "博主", "发布者", "回答者"),
    "likes": ("likes", "like count", "upvotes", "点赞", "点赞数", "赞同", "赞同数", "获赞", "获赞数"),
    "favorites": ("favorites", "favourites", "bookmarks", "收藏", "收藏数"),
    "comments": ("comments", "comment count", "replies", "评论", "评论数", "回复数"),
    "shop": ("shop", "store", "merchant", "seller", "店铺", "商家", "卖家"),
    "duration": ("duration", "video length", "时长", "视频长度"),
    "views": ("views", "view count", "play count", "播放量", "观看数"),
    "exchange_rate": ("exchange rate", "conversion rate", "汇率", "兑换率"),
    "high_temperature": (
        "high temperature",
        "maximum temperature",
        "highest temperature",
        "最高温",
        "最高气温",
        "高温",
    ),
    "low_temperature": (
        "low temperature",
        "minimum temperature",
        "lowest temperature",
        "最低温",
        "最低气温",
        "低温",
    ),
    "sort_state": ("sort state", "sort order", "ordering", "排序状态", "排序方式", "排序"),
    "date": ("date", "日期", "哪天"),
    "time": ("time", "时间", "几点"),
    "address": ("address", "location", "地址", "地点"),
}
_BROWSER_PROGRESS_FORMAT_GUIDANCE = {
    "en": (
        "When you stop and answer without another browser tool call, append exactly one "
        "<browser_progress>{...}</browser_progress> JSON block. "
        "This is a text protocol in your response, never a tool call. "
        "Use status=completed only when the requested browser outcome is evidenced. "
        "Include compact fields: status, completed_steps, remaining_steps, next_step, "
        "completion_evidence, missing_requirements."
    ),
    "cn": (
        "当您暂停并回答问题，且未调用其他浏览器工具时，请在后面接上且仅接一个 "
        "<browser_progress>{...}</browser_progress> JSON 块。"
        "这是回答中的文本协议，绝不能作为工具调用。"
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


def canonicalize_playwright_tool_name(tool_name: str) -> str:
    """Normalize the common Playwright MCP server/tool separator spelling."""
    value = str(tool_name or "")
    return re.sub(
        r"(playwright[-_]official)-browser_",
        r"\1_browser_",
        value,
        flags=re.IGNORECASE,
    )


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
            CORE_BROWSER_TOOL_NAMES if allowed_tool_names is None else tuple(dict.fromkeys(allowed_tool_names))
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
        self._semantic_state_tracker = SemanticStateTracker()
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
                (self._last_observed_url and normalized != self._last_observed_url)
                or (not self._last_observed_url and bool(self._reference_generations))
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
        explicit = str(parsed.get("primary_link") or parsed.get("href") or parsed.get("url") or "").strip()
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
                return BrowserAgentRuntime._unwrap_mcp_text_result(raw.get("payload"))

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
        resolution_elapsed_ms = int(max(0.0, (time.perf_counter() - resolution_started_at) * 1000))

        invoke_started_at = time.perf_counter()
        result = await tool.invoke({"code": js_code})
        invoke_elapsed_ms = int(max(0.0, (time.perf_counter() - invoke_started_at) * 1000))

        success = getattr(result, "success", None)
        if success is False:
            error = str(getattr(result, "error", "") or "").strip()
            raise RuntimeError(error or f"{tool_name} failed")

        data = getattr(result, "data", None)
        if data is not None:
            payload = data
        else:
            payload = result
        transport_response_size_bytes = len(str(payload).encode("utf-8", "ignore"))
        compact_payload = self._compact_run_code_payload(payload)
        return {
            "__browser_compact_rpc__": True,
            "payload": compact_payload,
            "rpc_metrics": {
                "tool_name": tool_name,
                "tool_resolution_elapsed_ms": resolution_elapsed_ms,
                "transport_invoke_elapsed_ms": invoke_elapsed_ms,
                "rpc_total_elapsed_ms": int(max(0.0, (time.perf_counter() - total_started_at) * 1000)),
                "script_size_bytes": len(js_code.encode("utf-8", "ignore")),
                "transport_response_size_bytes": transport_response_size_bytes,
                "response_size_bytes": len(str(compact_payload).encode("utf-8", "ignore")),
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

    async def capture_browser_state(self, *, action_group_id: str = "") -> Dict[str, Any]:
        """Capture a fresh, non-cached browser observation for the next model call."""
        await self.ensure_runtime_ready()

        dom = ""
        dom_error = None
        snapshot_captured = False
        snapshot_audit: Dict[str, Any] = {}
        try:
            raw_snapshot = await self._call_playwright_tool("browser_snapshot", {})
            raw_snapshot = self._unwrap_mcp_text_result(raw_snapshot)
            snapshot_audit = write_browser_agent_audit_artifact("ax_snapshot", raw_snapshot)
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
            raw_metadata = await self._call_playwright_run_code_unsafe(build_browser_state_metadata_js())
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
        page_state = self.export_page_state()

        errors = [error for error in (dom_error, metadata_error) if error]
        semantic_state = metadata.get("semantic_state")
        if not isinstance(semantic_state, dict):
            semantic_state = {}
        semantic_state.update(
            {
                "url": metadata.get("url") or "",
                "field_coverage": sorted(self._ensure_page_state().field_coverage),
            }
        )
        semantic_tracker = self._ensure_semantic_state_tracker()
        if metadata_error:
            semantic_progress = semantic_tracker.latest
            semantic_progress.update(
                {
                    "progress": "unknown",
                    "observable_progress": False,
                    "capture_error": metadata_error,
                }
            )
        else:
            semantic_progress = semantic_tracker.observe(
                semantic_state,
                action_group_id=action_group_id,
            )
        return {
            "ok": not errors,
            "error": "; ".join(errors) or None,
            "url": metadata.get("url") or "",
            "title": metadata.get("title") or "",
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state.get("field_coverage") or [],
            "page_state": page_state,
            "dom": "",
            "dom_error": dom_error,
            "audit": {"ax_snapshot": snapshot_audit} if snapshot_audit else {},
        }

    async def capture_compact_browser_state(self, *, action_group_id: str) -> Dict[str, Any]:
        """Merge completed read-only observations without another browser round trip."""
        page_state = self.export_page_state()
        semantic_tracker = self._ensure_semantic_state_tracker()
        semantic_state = semantic_tracker.current_state
        semantic_state.update(
            {
                "url": page_state.get("url") or semantic_state.get("url") or "",
                "field_coverage": page_state.get("field_coverage") or semantic_state.get("field_coverage") or [],
            }
        )
        semantic_progress = semantic_tracker.observe(
            semantic_state,
            action_group_id=action_group_id,
        )
        return {
            "ok": True,
            "error": None,
            "url": page_state.get("url") or "",
            "title": page_state.get("title") or "",
            "tabs": [],
            "page_position": {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state["field_coverage"],
            "page_state": page_state,
            "dom": "",
            "dom_error": None,
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
        if op in _BATCH_EXPLICIT_SELECTOR_OPS or op in _BATCH_SAFE_READ_SELECTOR_OPS:
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
        requires_actionability = op in _BATCH_MUTATING_TARGET_OPS and target.source != "ax"
        if requires_actionability:
            is_unavailable = not target.visible or not target.enabled or not target.actionable
            if is_unavailable:
                raise ValueError(f"PageState target {target.target_id} is not actionable in {target.generation_id}")
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

    async def _validate_safe_read_locators(self, steps: list[Dict[str, Any]]) -> None:
        """Validate model-provided read locators once before batch execution."""
        checks: list[Dict[str, Any]] = []
        for index, step in enumerate(steps):
            operation = str(step.get("op") or "").strip().lower()
            selector = str(step.get("selector") or "").strip()
            resolved_target_id = str(step.get("resolved_target_id") or "").strip()
            if operation not in _BATCH_SAFE_READ_SELECTOR_OPS:
                continue
            if not selector or resolved_target_id:
                continue
            checks.append({"index": index, "selector": selector})
        if not checks:
            return
        script = f"""() => {{
          const checks = {json.dumps(checks, ensure_ascii=False)};
          const results = checks.map((check) => {{
            let nodes = [];
            try {{ nodes = Array.from(document.querySelectorAll(check.selector)); }}
            catch (error) {{
              return {{...check, match_count: 0, visible: false, error: String(error)}};
            }}
            const element = nodes[0];
            const visible = Boolean(element && element.isConnected &&
              element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
            return {{...check, match_count: nodes.length, visible}};
          }});
          return JSON.stringify({{ok: true, results}});
        }}"""
        raw = await self._call_playwright_run_code_unsafe(script)
        parsed = extract_json_object(self._unwrap_mcp_text_result(raw))
        results = parsed.get("results") if isinstance(parsed, dict) else None
        if not isinstance(results, list):
            raise ValueError("Could not validate safe batch read locators")
        invalid = [
            item
            for item in results
            if not isinstance(item, dict) or item.get("match_count") != 1 or item.get("visible") is not True
        ]
        if invalid:
            first = invalid[0] if isinstance(invalid[0], dict) else {}
            raise ValueError(
                "Read-only batch selector must match exactly one visible element: "
                f"step={first.get('index')}, selector={first.get('selector')}, "
                f"match_count={first.get('match_count')}"
            )

    async def _refresh_stale_batch_targets(
        self,
        steps: Any,
        *,
        generation_id: str,
    ) -> tuple[list[Dict[str, Any]], str]:
        """Refresh stale Probe target IDs without reviving refs or CSS."""

        page_state = self._ensure_page_state()
        if str(generation_id or "").strip() == page_state.generation_id:
            return [dict(step) for step in steps], ""

        refreshed_steps = [dict(step) for step in steps]
        refreshed_any = False
        target_alias_groups = (
            ("target_id",),
            ("option_target_id", "choose_target_id"),
        )
        for step in refreshed_steps:
            step_refreshed = await self._refresh_stale_batch_step(
                step,
                page_state=page_state,
                target_alias_groups=target_alias_groups,
            )
            refreshed_any = refreshed_any or step_refreshed

        condition_only = all(
            str(step.get("op") or "").strip().lower() in _BATCH_SAFE_GENERATION_REFRESH_OPS for step in refreshed_steps
        )
        if not refreshed_any and not condition_only:
            raise ValueError(
                f"Stale PageState generation {generation_id}; current generation is "
                f"{page_state.generation_id}. Probe or snapshot the current page again."
            )
        return refreshed_steps, str(generation_id or "").strip()

    async def _refresh_stale_batch_step(
        self,
        step: Dict[str, Any],
        *,
        page_state: BrowserPageState,
        target_alias_groups: tuple[tuple[str, ...], ...],
    ) -> bool:
        op = str(step.get("op") or "").strip().lower()
        refreshed = False
        for aliases in target_alias_groups:
            populated_alias = next(
                (alias for alias in aliases if str(step.get(alias) or "").strip()),
                "",
            )
            if not populated_alias:
                continue
            stale_target_id = str(step[populated_alias])
            try:
                refreshed_target_id = page_state.refresh_target_id(stale_target_id)
            except ValueError:
                refreshed_target_id = await self._refresh_runtime_owned_target(
                    stale_target_id,
                    require_actionable=op in _BATCH_MUTATING_TARGET_OPS,
                )
            step[populated_alias] = refreshed_target_id
            refreshed = True

        if _has_non_empty_mapping_value(step, ("ref", "option_ref", "choose_ref")):
            raise ValueError(
                "Native AX refs cannot be refreshed across PageState generations; capture the current snapshot again"
            )
        has_model_selector = _has_non_empty_mapping_value(
            step,
            ("selector", "option_selector", "choose_selector"),
        )
        if has_model_selector and not refreshed and op not in _BATCH_SAFE_GENERATION_REFRESH_OPS:
            raise ValueError(
                "Model-authored selectors cannot refresh a stale PageState generation; "
                "use a current generation-scoped target_id"
            )
        return refreshed

    async def _refresh_runtime_owned_target(
        self,
        target_id: str,
        *,
        require_actionable: bool,
    ) -> str:
        """Refresh a stable Probe locator after observation-only generation churn."""
        page_state = self._ensure_page_state()
        stale = page_state.get_target(target_id)
        if stale is None:
            raise ValueError(f"Unknown PageState target_id: {target_id}")
        if stale.source == "ax":
            raise ValueError(f"Stale AX target_id {target_id} cannot cross generations; capture a current snapshot")
        if stale.href and not stale.selector:
            return page_state.rebind_target(
                target_id,
                visible=True,
                enabled=True,
                actionable=True,
            )
        selector = str(stale.selector or stale.locator.get("selector") or "").strip()
        if not selector:
            raise ValueError(f"Stale target_id {target_id} has no runtime-owned selector to refresh")
        await self.ensure_runtime_ready()
        script = f"""() => {{
          const selector = {json.dumps(selector)};
          let nodes = [];
          try {{ nodes = Array.from(document.querySelectorAll(selector)); }}
          catch (error) {{ return JSON.stringify({{ok: false, error: String(error)}}); }}
          const element = nodes[0];
          if (!element) return JSON.stringify({{ok: true, match_count: nodes.length, visible: false}});
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          const visible = Boolean(element.isConnected && rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' && style.visibility !== 'hidden');
          const enabled = !element.disabled && element.getAttribute('aria-disabled') !== 'true';
          const hit = visible ? document.elementFromPoint(
            Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
            Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
          ) : null;
          const actionable = Boolean(visible && enabled && hit && (hit === element || element.contains(hit)));
          return JSON.stringify({{ok: true, match_count: nodes.length, visible, enabled, actionable}});
        }}"""
        raw = await self._call_playwright_run_code_unsafe(script)
        validation = extract_json_object(self._unwrap_mcp_text_result(raw))
        valid = (
            (
                validation.get("ok") is True
                and validation.get("match_count") == 1
                and validation.get("visible") is True
                and (not require_actionable or validation.get("actionable") is True)
            )
            if isinstance(validation, dict)
            else False
        )
        if not valid:
            raise ValueError(
                f"Stale target_id {target_id} could not be refreshed in {page_state.generation_id}: "
                f"{validation or 'validation unavailable'}"
            )
        return page_state.rebind_target(
            target_id,
            visible=bool(validation.get("visible")),
            enabled=bool(validation.get("enabled")),
            actionable=bool(validation.get("actionable")),
        )

    def _batch_recovery_candidates(self, steps: Any) -> list[Dict[str, Any]]:
        page_state = self._ensure_page_state()
        candidates: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            for key in ("target_id", "option_target_id", "choose_target_id"):
                target_id = str(step.get(key) or "").strip()
                if not target_id:
                    continue
                for candidate in page_state.recovery_candidates(target_id):
                    candidate_id = str(candidate.get("target_id") or "")
                    if candidate_id and candidate_id not in seen:
                        candidates.append(candidate)
                        seen.add(candidate_id)
                if len(candidates) >= 5:
                    return candidates
        return candidates

    @staticmethod
    def _single_batch_primitive_spec(
        step: Dict[str, Any],
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """Map a semantically equivalent one-step batch to an MCP primitive."""
        op = str(step.get("op") or "").strip().lower()
        selector = str(step.get("selector") or "").strip()
        element = str(step.get("description") or step.get("resolved_target_id") or selector or op)
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
        validation_errors.extend(self._validate_batch_target_contract(steps))
        if validation_errors:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_validation_failed: {validation_errors[0]}",
                "validation_errors": validation_errors,
                "page_state": page_state.export(),
            }
        recovered_generation = ""
        try:
            refreshed_steps, recovered_generation = await self._refresh_stale_batch_targets(
                steps,
                generation_id=generation_id,
            )
            effective_generation_id = page_state.generation_id
            page_state.validate_generation(effective_generation_id)
            await self.ensure_runtime_ready()
            resolved_steps = await self._resolve_batch_steps(
                refreshed_steps,
                generation_id=effective_generation_id,
                allow_navigation_rewrite=len(refreshed_steps) == 1,
            )
            await self._validate_safe_read_locators(resolved_steps)
        except ValueError as exc:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_target_validation_failed: {exc}",
                "generation_id": page_state.generation_id,
                "current_generation": page_state.generation_id,
                "candidate_fresh_targets": self._batch_recovery_candidates(steps),
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
                generation_id=effective_generation_id,
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
            if recovered_generation:
                result["generation_recovered_from"] = recovered_generation
            result["page_state"] = self.export_page_state()
        return result

    @staticmethod
    def _validate_batch_target_contract(steps: Any) -> list[str]:
        """Keep mutating Batch actions on one generation-scoped target contract."""
        errors: list[str] = []
        for index, step in enumerate(steps if isinstance(steps, list) else []):
            if not isinstance(step, dict):
                continue
            op = str(step.get("op") or "").strip().lower()
            if op in _BATCH_MUTATING_TARGET_OPS and not str(step.get("target_id") or "").strip():
                errors.append(f"steps[{index}] op={op} requires target_id from the current PageState")
            if op not in _BATCH_SAFE_READ_SELECTOR_OPS:
                continue
            read_target_keys = [key for key in ("target_id", "selector") if str(step.get(key) or "").strip()]
            if len(read_target_keys) != 1:
                errors.append(f"steps[{index}] op={op} requires exactly one of target_id or selector")
        return errors

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
        max_items: int = 30,
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

        effective_max_items = min(40, max(1, int(max_items)))
        js_code = build_interactive_probe_js(
            max_items=effective_max_items,
            viewport_only=viewport_only,
            query=query,
            site_profiles=site_profiles_for_url(self._ensure_page_state().url),
            generation_id=self.generation_id,
        )

        try:
            raw = await self._code_executor(js_code)
            raw = self._unwrap_mcp_text_result(raw)
            raw_audit = write_browser_agent_audit_artifact("interactive_probe", raw)
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
                "audit": raw_audit,
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
        exported = page_state.export()
        return {
            "ok": bool(parsed.get("ok")),
            "error": parsed.get("error"),
            "url": parsed.get("url") or exported.get("url"),
            "title": parsed.get("title") or exported.get("title"),
            "generation_id": exported["generation_id"],
            "count": len(exported["interactives"]),
            "elements": exported["interactives"],
            "page_state": exported,
            "diagnostics": {
                "query": str(query or "")[:160],
                "query_widened": bool(parsed.get("query_widened")),
                "total_candidates": int(parsed.get("total_candidates") or 0),
            },
            "audit": raw_audit,
        }

    async def probe_cards(
        self,
        *,
        max_cards: int = 12,
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

        site_profiles = site_profiles_for_url(self._ensure_page_state().url)
        selector_cache = get_selector_cache()
        selector_cache_records = selector_cache.export_for_probe()

        effective_max_cards = min(20, max(1, int(max_cards)))
        js_code = build_card_probe_js(
            max_cards=effective_max_cards,
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
            raw_audit = write_browser_agent_audit_artifact("card_probe", raw)
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
                "audit": raw_audit,
                "cards": [],
                "page_state": self.export_page_state(),
            }

        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("cards", [])
        self._observe_page_url(parsed.get("url"))
        self._annotate_probe_generation(parsed)
        normalize_card_probe_payload(parsed)
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

        exported = page_state.export()
        return {
            "ok": bool(parsed.get("ok")),
            "error": parsed.get("error"),
            "url": parsed.get("url") or exported.get("url"),
            "title": parsed.get("title") or exported.get("title"),
            "generation_id": exported["generation_id"],
            "count": len(exported["cards"]),
            "cards": exported["cards"],
            "page_state": exported,
            "diagnostics": parsed.get("diagnostics") or parsed.get("cache_diagnostics") or {},
            "audit": raw_audit,
        }

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
            self._ensure_semantic_state_tracker().reset()

    async def reset(self) -> None:
        """Release the current browser and restart lazily on the next task."""
        try:
            await self._service.reset()
        finally:
            self._advance_page_generation()
            self._last_observed_url = ""
            self._ensure_semantic_state_tracker().reset()

    @property
    def semantic_progress(self) -> Dict[str, Any]:
        """Return the latest task-local semantic progress observation."""
        return self._ensure_semantic_state_tracker().latest

    def acknowledge_semantic_replan(self) -> None:
        """Acknowledge one materially different action selected after replanning."""
        self._ensure_semantic_state_tracker().acknowledge_replan()

    def reset_semantic_task(self) -> None:
        """Start semantic loop tracking for a new user task without resetting Chrome."""
        self._ensure_semantic_state_tracker().reset()

    def _ensure_semantic_state_tracker(self) -> SemanticStateTracker:
        tracker = getattr(self, "_semantic_state_tracker", None)
        if not isinstance(tracker, SemanticStateTracker):
            tracker = SemanticStateTracker()
            self._semantic_state_tracker = tracker
        return tracker


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
            try:
                timeout_s = max(1, int(self._runtime.service.guardrails.timeout_s))
            except (TypeError, ValueError, AttributeError):
                timeout_s = 600
            ctx.extra.setdefault(_BROWSER_TASK_DEADLINE_KEY, time.monotonic() + timeout_s)
            run_context = ctx.extra.get("run_context")
            if isinstance(run_context, dict) and run_context.get("browser_resume") is True:
                ctx.extra["_browser_resume_requested"] = True
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
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
            self._ensure_task_state(
                session,
                task_text,
                resume=bool(ctx.extra.get("_browser_resume_requested")),
            )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_model_call", ctx)
        session = getattr(ctx, "session", None)
        state: Dict[str, Any] = {}
        if session is not None:
            messages = getattr(getattr(ctx, "inputs", None), "messages", None) or []
            task_text = latest_browser_user_request(messages)
            if task_text:
                self._ensure_task_state(
                    session,
                    task_text,
                    resume=bool(ctx.extra.get("_browser_resume_requested")),
                )
            self._sync_semantic_progress(session)
            loaded_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
            state = loaded_state if isinstance(loaded_state, dict) else {}

        if self._prepare_terminal_synthesis(ctx, session, state):
            return

        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return
        if str(state.get("status") or "").strip().lower() not in _BROWSER_TERMINAL_STATUSES:
            builder.remove_section("browser_terminal_synthesis")

        image_input_supported = self._image_input_supported(ctx.agent)
        builder.add_section(
            PromptSection(
                name=_BROWSER_IMAGE_CAPABILITY_SECTION_NAME,
                content=_BROWSER_IMAGE_CAPABILITY_GUIDANCE[image_input_supported],
                priority=85,
            )
        )
        builder.add_section(
            PromptSection(
                name=_BROWSER_PROGRESS_FORMAT_SECTION_NAME,
                content=_BROWSER_PROGRESS_FORMAT_GUIDANCE,
                priority=84,
            )
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_model_call", ctx)
        response = getattr(getattr(ctx, "inputs", None), "response", None)
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if (
            tool_calls
            and isinstance(state, dict)
            and str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES
        ):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return
        response_text = str(getattr(response, "content", "") or "")
        if self._is_unfinished_model_protocol_response(state, tool_calls, response_text):
            if self._request_model_protocol_recovery(ctx, session, state):
                return
            self._finish_model_protocol_failure(ctx, session, state)
            return
        call_ids = [str(tool_call.id) for tool_call in tool_calls if getattr(tool_call, "id", None)]
        extra = getattr(ctx, "extra", None)
        if not call_ids or not isinstance(extra, dict):
            return
        action_group_id = hashlib.sha256("\x1f".join(call_ids).encode("utf-8")).hexdigest()[:16]
        group_by_call = extra.setdefault(_BROWSER_ACTION_GROUP_BY_CALL_KEY, {})
        for call_id in call_ids:
            group_by_call[call_id] = action_group_id
        extra.setdefault(_BROWSER_ACTION_GROUP_RESULTS_KEY, {})[action_group_id] = {
            "expected": call_ids,
            "completed": [],
        }

    @staticmethod
    def _has_unfinished_tool_intent(response_text: str) -> bool:
        return _BROWSER_UNFINISHED_TOOL_INTENT_RE.search(str(response_text or "")) is not None

    @classmethod
    def _is_unfinished_model_protocol_response(
        cls,
        state: Any,
        tool_calls: list[Any],
        response_text: str,
    ) -> bool:
        if tool_calls or not isinstance(state, dict):
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            return False
        return cls._has_unfinished_tool_intent(response_text)

    @staticmethod
    def _request_model_protocol_recovery(
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        retry_count = int(state.get("model_protocol_retry_count") or 0)
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        deadline_exhausted = bool(deadline and deadline - time.monotonic() <= 5.0)
        if retry_count >= _BROWSER_MODEL_PROTOCOL_RETRY_LIMIT or deadline_exhausted:
            return False
        state["model_protocol_retry_count"] = retry_count + 1
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
        ctx.push_steering(
            "Your previous response described another browser tool call but did not emit a real tool call. "
            "Continue in this same task: emit the actual tool call, or finish with a browser_progress block "
            "using only evidence already collected."
        )
        return True

    @classmethod
    def _finish_model_protocol_failure(
        cls,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> None:
        evidence_available = bool(
            state.get("structured_evidence") or state.get("evidence_slots") or state.get("field_coverage")
        )
        state["status"] = "partial" if evidence_available else "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "model_tool_protocol_error"
        blockers = list(state.get("blockers") or [])
        if "model_tool_protocol_error" not in blockers:
            blockers.append("model_tool_protocol_error")
        state["blockers"] = blockers[:10]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        ctx.request_force_finish(cls._structured_terminal_result(state))

    def _prepare_terminal_synthesis(
        self,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        """Allow one tool-disabled prose pass, then force the runtime result."""

        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        if state.get(_BROWSER_TERMINAL_SYNTHESIS_KEY):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return True

        state[_BROWSER_TERMINAL_SYNTHESIS_KEY] = True
        state["next_action_class"] = "finish"
        if session is not None:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            inputs.tools = []
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            authoritative = self._authoritative_terminal_payload(state)
            builder.add_section(
                PromptSection(
                    name="browser_terminal_synthesis",
                    content={
                        "en": (
                            "Browser execution has ended. Do not call tools. Summarize the runtime-owned "
                            f"result without changing status, blockers, missing fields, or evidence: {authoritative}"
                        ),
                        "cn": (
                            "浏览器执行已结束。不要调用工具。请说明以下由运行时确定的结果，不得修改状态、"
                            f"阻断项、缺失字段或证据：{authoritative}"
                        ),
                    },
                    priority=100,
                )
            )
        return False

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_model_exception", ctx)
        exception = getattr(ctx, "exception", None)
        if not self._is_retryable_model_exception(exception):
            return
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        remaining_s = deadline - time.monotonic() if deadline else 0.0
        if ctx.retry_attempt < _BROWSER_MODEL_RETRY_LIMIT and remaining_s > 5.0:
            ctx.request_retry(delay_seconds=min(0.5, max(0.0, remaining_s - 5.0)))
            return
        ctx.request_force_finish(self._structured_model_failure(ctx, exception))

    @staticmethod
    def _is_retryable_model_exception(exception: Optional[Exception]) -> bool:
        if isinstance(exception, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        message = str(exception or "").lower()
        retryable_tokens = (
            "timeout",
            "timed out",
            "429",
            "500",
            "502",
            "503",
            "504",
            "upstream",
            "rate limit",
            "service unavailable",
        )
        return _contains_any_token(message, retryable_tokens)

    def _structured_model_failure(
        self,
        ctx: AgentCallbackContext,
        exception: Optional[Exception],
    ) -> Dict[str, Any]:
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        evidence_available = bool(
            state.get("structured_evidence") or state.get("evidence_slots") or state.get("field_coverage")
        )
        status = "partial" if evidence_available else "failed"
        if state:
            state["status"] = "partial" if evidence_available else "blocked"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "model_provider_unavailable"
            blockers = list(state.get("blockers") or [])
            if "model_provider_unavailable" not in blockers:
                blockers.append("model_provider_unavailable")
            state["blockers"] = blockers[:10]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        payload = {
            "ok": False,
            "status": status,
            "error": {
                "code": "model_provider_unavailable",
                "message": str(exception or "model provider failed")[:500],
                "retry_attempts": int(ctx.retry_attempt) + 1,
            },
            "progress": {
                "task_id": state.get("task_id"),
                "status": state.get("status"),
                "current_phase": state.get("current_phase"),
                "required_fields": list(state.get("required_fields") or [])[:32],
                "field_coverage": list(state.get("field_coverage") or [])[:32],
                "required_evidence_slots": list(state.get("required_evidence_slots") or [])[:12],
                "evidence_slots": list(state.get("evidence_slots") or [])[-12:],
                "blockers": list(state.get("blockers") or [])[:10],
            }
            if state
            else {},
        }
        return {
            "output": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "result_type": "error",
            "error": "model_provider_unavailable",
            "progress_state": self._load_progress_state(session).to_dict() if session is not None else {},
        }

    @classmethod
    def _authoritative_terminal_payload(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        status = str(state.get("status") or "partial").strip().lower()
        missing = cls._missing_completion_requirements(state)
        blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        missing_slots = cls._missing_evidence_slots(state)
        retryable = cls._terminal_result_retryable(state, missing)
        return {
            "status": status,
            "task_id": state.get("task_id"),
            "current_phase": state.get("current_phase"),
            "missing_fields": missing[:32],
            "missing_slots": missing_slots[:12],
            "requested_slots": [
                dict(slot)
                for slot in (state.get("required_evidence_slots") or [])[:12]
                if isinstance(slot, dict)
            ],
            "blockers": blockers[:10],
            "field_coverage": list(state.get("field_coverage") or [])[:32],
            "evidence": list(state.get("evidence_slots") or [])[-12:],
            "current_page": dict(state.get("last_page") or {}),
            "terminal_reason": state.get("terminal_reason"),
            "retryable": retryable,
            "recommended_recovery": cls._recommended_recovery(state, missing),
            "resume_count": int(state.get("resume_count") or 0),
        }

    @classmethod
    def _terminal_result_retryable(
        cls,
        state: Dict[str, Any],
        missing: Optional[list[str]] = None,
    ) -> bool:
        status = str(state.get("status") or "").strip().lower()
        if status == "completed" or int(state.get("resume_count") or 0) >= _BROWSER_TASK_RESUME_LIMIT:
            return False
        blockers = " ".join(str(item or "").strip().lower() for item in state.get("blockers") or [])
        if any(token in blockers for token in _BROWSER_NON_RETRYABLE_BLOCKER_TOKENS):
            return False
        if "phase_budget_exhausted" in blockers:
            return False
        requirements = missing if missing is not None else cls._missing_completion_requirements(state)
        return bool(
            status in {"partial", "blocked"}
            and (
                requirements
                or state.get("structured_evidence")
                or state.get("evidence_slots")
                or state.get("terminal_reason") in {
                    "model_provider_unavailable",
                    "model_tool_protocol_error",
                    "runtime_completion_requirements_missing",
                    "runtime_blocked",
                    "semantic_replan_denial_budget_exhausted",
                }
            )
        )

    @staticmethod
    def _recommended_recovery(state: Dict[str, Any], missing: list[str]) -> str:
        reported = str(state.get("worker_reported_next_action") or "").strip()
        if reported:
            return reported[:200]
        reason = str(state.get("terminal_reason") or "").strip().lower()
        if reason in {"model_provider_unavailable", "model_tool_protocol_error"}:
            return "retry_model_step_from_current_page"
        if missing:
            return "collect_missing_evidence_from_current_page"
        return "replan_from_current_page"

    @classmethod
    def _structured_terminal_result(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = cls._authoritative_terminal_payload(state)
        status = str(payload.get("status") or "partial")
        return {
            "output": json.dumps(
                {"browser_result": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "result_type": "answer" if status == "completed" else "error",
            "error": None if status == "completed" else "browser_task_incomplete",
            "authoritative_browser_result": payload,
        }

    @classmethod
    def _render_authoritative_terminal_output(
        cls,
        state: Dict[str, Any],
        model_summary: str,
    ) -> tuple[str, Dict[str, Any]]:
        payload = cls._authoritative_terminal_payload(state)
        if payload["status"] == "completed" and str(model_summary or "").strip():
            payload["summary"] = str(model_summary).strip()[:8_000]
        return (
            json.dumps({"browser_result": payload}, ensure_ascii=False, separators=(",", ":")),
            payload,
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_tool_call", ctx)
        try:
            self._prepare_tool_call(ctx)
        except ValueError as exc:
            self._deny_tool_call(ctx, exc)

    def _prepare_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if tool_name.strip().lower() == "browser_progress":
            raise ValueError(
                "<browser_progress> is a text protocol appended to the final response, not a callable tool."
            )
        canonical_tool_name = self._canonicalize_tool_name(tool_name)
        if canonical_tool_name != tool_name:
            inputs.tool_name = canonical_tool_name
            tool_name = canonical_tool_name
        tool_args = getattr(inputs, "tool_args", None)
        normalized_args = self._normalize_playwright_ref_args(tool_name, tool_args)
        if normalized_args is not tool_args:
            inputs.tool_args = normalized_args
        normalized_tool_name = tool_name.strip().lower()
        if "browser_batch_interact" in normalized_tool_name and not self._image_input_supported(
            getattr(ctx, "agent", None)
        ):
            batch_args = self._coerce_tool_args(normalized_args)
            steps = batch_args.get("steps")
            if any(
                isinstance(step, dict) and str(step.get("op") or "").strip().lower() == "screenshot"
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
            self._runtime.validate_reference_values(self._extract_playwright_ref_values(normalized_args))
        session = getattr(ctx, "session", None)
        self._sync_semantic_progress(session)
        action_class = self._consume_phase_budget(
            session,
            tool_name,
            normalized_args,
            current_page_state=self._runtime.export_page_state(),
        )
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            tool_call_id = self._tool_call_id(inputs)
            tool_runtime_state = extra.setdefault(_BROWSER_TOOL_RUNTIME_STATE_KEY, {})
            tool_runtime_state[tool_call_id] = {
                "started_at": time.perf_counter(),
                "action_class": action_class,
            }

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
            self._mark_action_group_call_completed(ctx, {})
            return
        progress_delta = self._record_phase_result(
            session,
            tool_name,
            getattr(inputs, "tool_args", None),
            tool_result,
        )
        progress_delta.update(
            {
                "executed": True,
                "denied": False,
                "state_changed": self._tool_may_change_browser_state(tool_name),
            }
        )
        extra = getattr(ctx, "extra", None)
        tool_runtime_state = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY, {}) if isinstance(extra, dict) else {}
        call_state = (
            tool_runtime_state.pop(self._tool_call_id(inputs), {}) if isinstance(tool_runtime_state, dict) else {}
        )
        if isinstance(extra, dict) and not tool_runtime_state:
            extra.pop(_BROWSER_TOOL_RUNTIME_STATE_KEY, None)
        started_at = call_state.get("started_at") if isinstance(call_state, dict) else None
        action_class = call_state.get("action_class", "") if isinstance(call_state, dict) else ""
        elapsed_ms = int(max(0.0, (time.perf_counter() - started_at) * 1000)) if started_at else 0
        recovered = self._record_recent_action(
            session,
            tool_name=tool_name,
            tool_args=getattr(inputs, "tool_args", None),
            tool_result=tool_result,
            action_class=str(action_class or ""),
            elapsed_ms=elapsed_ms,
            progress_delta=progress_delta,
        )
        if recovered:
            self._runtime.acknowledge_semantic_replan()
        if not self._is_browser_progress_tool(tool_name):
            self._mark_action_group_call_completed(ctx, progress_delta)
            return
        self._persist_service_progress_to_session(session)
        self._mark_action_group_call_completed(ctx, progress_delta)

    @staticmethod
    def _tool_call_id(inputs: Any) -> str:
        tool_call = getattr(inputs, "tool_call", None)
        call_id = str(getattr(tool_call, "id", "") or "").strip()
        return call_id or f"tool-context-{id(inputs)}"

    @classmethod
    def _denial_code(cls, message: str) -> str:
        normalized = str(message or "").lower()
        if "already" in normalized and "browser task" in normalized:
            return "browser_task_terminal"
        if "semantic" in normalized or "replan" in normalized:
            return "browser_replan_required"
        if "budget exhausted" in normalized:
            return "browser_phase_budget_exhausted"
        if "browser_progress" in normalized:
            return "browser_progress_is_text_protocol"
        if "screenshot input" in normalized:
            return "browser_image_input_unavailable"
        if "known url" in normalized:
            return "browser_direct_navigation_required"
        return "browser_action_denied"

    def _deny_tool_call(self, ctx: AgentCallbackContext, exc: ValueError) -> None:
        inputs = getattr(ctx, "inputs", None)
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        status = str(state.get("status") or "in_progress").strip().lower()
        payload = {
            "ok": False,
            "status": "denied",
            "executed": False,
            "state_changed": False,
            "denied": True,
            "error": {
                "code": self._denial_code(str(exc)),
                "message": str(exc),
                "terminal": status in _BROWSER_TERMINAL_STATUSES,
                "replan_required": bool(state.get("replan_required")),
            },
            "runtime_state": {
                "task_status": status,
                "current_phase": state.get("current_phase"),
                "next_action_class": state.get("next_action_class"),
                "blockers": list(state.get("blockers") or [])[:10],
            },
        }
        call_id = self._tool_call_id(inputs)
        inputs.tool_result = payload
        inputs.tool_msg = ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=call_id,
            metadata={
                "executed": False,
                "state_changed": False,
                "denied": True,
            },
        )
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra.setdefault(_BROWSER_SKIP_TOOL_CALLS_KEY, {})[call_id] = True
        self._mark_action_group_call_completed(
            ctx,
            {
                "executed": False,
                "state_changed": False,
                "denied": True,
                "new_evidence_fields": [],
            },
        )

    @staticmethod
    def _tool_may_change_browser_state(tool_name: str) -> bool:
        normalized = str(tool_name or "").strip().lower()
        observation_tokens = (
            "browser_probe_cards",
            "browser_probe_interactives",
            "browser_snapshot",
            "browser_find",
        )
        return not _contains_any_token(normalized, observation_tokens)

    def _canonicalize_tool_name(self, tool_name: str) -> str:
        canonical = canonicalize_playwright_tool_name(tool_name)
        normalized = canonical.strip().lower()
        if not normalized.startswith("browser_") or normalized in _BROWSER_RUNTIME_TOOL_NAMES:
            return canonical
        configured = set(self._runtime.service.allowed_tool_names or CORE_BROWSER_TOOL_NAMES)
        if normalized not in configured:
            return canonical
        server_name = str(getattr(self._runtime.service.mcp_cfg, "server_name", "") or "").strip()
        if not server_name:
            return canonical
        return f"mcp_{server_name}_{normalized}"

    def _mark_action_group_call_completed(
        self,
        ctx: AgentCallbackContext,
        progress_delta: Dict[str, Any],
    ) -> None:
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        call_id = self._tool_call_id(getattr(ctx, "inputs", None))
        group_by_call = extra.get(_BROWSER_ACTION_GROUP_BY_CALL_KEY)
        group_id = group_by_call.pop(call_id, "") if isinstance(group_by_call, dict) else ""
        groups = extra.get(_BROWSER_ACTION_GROUP_RESULTS_KEY)
        group = groups.get(group_id) if isinstance(groups, dict) and group_id else None
        if isinstance(group, dict):
            completed = group.setdefault("completed", [])
            if call_id not in completed:
                completed.append(call_id)
            if progress_delta.get("executed") is False:
                denied = group.setdefault("denied", [])
                if call_id not in denied:
                    denied.append(call_id)
            elif progress_delta.get("executed") is True:
                executed = group.setdefault("executed", [])
                if call_id not in executed:
                    executed.append(call_id)
            if progress_delta.get("state_changed") is True:
                group["state_changed"] = True
            evidence_fields = group.setdefault("evidence_fields", [])
            for field_name in progress_delta.get("new_evidence_fields") or []:
                if field_name not in evidence_fields:
                    evidence_fields.append(field_name)
            if set(completed) >= set(group.get("expected") or []):
                group["ready"] = True
                session = getattr(ctx, "session", None)
                state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
                if isinstance(state, dict):
                    state["last_action_group"] = {
                        "action_group_id": group_id,
                        "call_count": len(group.get("expected") or []),
                        "executed_count": len(group.get("executed") or []),
                        "denied_count": len(group.get("denied") or []),
                        "state_changed": bool(group.get("state_changed")),
                        "evidence_fields": evidence_fields[:32],
                    }
                    session.update_state({_BROWSER_PHASE_STATE_KEY: state})
                groups.pop(group_id, None)
                if not groups:
                    extra.pop(_BROWSER_ACTION_GROUP_RESULTS_KEY, None)
        if isinstance(group_by_call, dict) and not group_by_call:
            extra.pop(_BROWSER_ACTION_GROUP_BY_CALL_KEY, None)

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
        if _contains_any_token(normalized_name, ("browser_snapshot", "browser_find")):
            compact_observation = {
                "ok": True,
                "observation": "compact_page_state",
                "page_state": page_state,
                "audit": write_browser_agent_audit_artifact("direct_ax_observation", tool_result),
            }
            tool_msg.content = json.dumps(
                compact_observation,
                ensure_ascii=False,
                separators=(",", ":"),
            )
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
                self._apply_worker_progress_to_task_state(session, progress_payload, clean_output)
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                self._runtime.service.set_progress_state(session_id, progress_state)
                exported = progress_state.to_dict()
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
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
                progress_state = self._load_progress_state(session)
                self._runtime.service.set_progress_state(session_id, progress_state)
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
                result["progress_state"] = progress_state.to_dict()
                result["output"] = failure_summary
                self._persist_service_progress_to_session(session)
                return

            if str(result.get("result_type", "")).lower() == "answer":
                task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
                if not isinstance(task_state, dict) or not task_state:
                    self._clear_progress_state(session)
                    return
                self._apply_worker_progress_to_task_state(
                    session,
                    {"status": "completed"},
                    clean_output,
                )
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                exported = progress_state.to_dict()
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
                    result["progress_state"] = exported
                    self._clear_progress_state(session)
                    return
                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error="empty_or_unverified_browser_result",
                    page_url=progress_state.last_page_url,
                    page_title=progress_state.last_page_title,
                    final=clean_output,
                    screenshot=progress_state.last_screenshot,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["result_type"] = "error"
                result["failure_summary"] = failure_summary
                result["progress_state"] = exported
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
                self._persist_service_progress_to_session(session)
                return

            progress_state = self._load_progress_state(session)
            if self._finalize_terminal_invoke(session, result, clean_output):
                return
            exported = progress_state.to_dict() if not progress_state.is_empty() else None
            if exported:
                self._runtime.service.set_progress_state(session_id, progress_state)
                result["progress_state"] = exported
                self._persist_service_progress_to_session(session)

        finally:
            extra = getattr(ctx, "extra", None)
            if isinstance(extra, dict):
                token = extra.pop(_BROWSER_LOG_CONTEXT_TOKEN_KEY, None)
                if token is not None:
                    reset_browser_agent_log_context(token)

    def _finalize_terminal_invoke(
        self,
        session: Any,
        result: Dict[str, Any],
        model_summary: str,
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if not isinstance(state, dict):
            return False
        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        output, payload = self._render_authoritative_terminal_output(state, model_summary)
        progress_state = self._load_progress_state(session)
        result["output"] = output
        result["result_type"] = "answer" if status == "completed" else "error"
        result["error"] = None if status == "completed" else "browser_task_incomplete"
        result["progress_state"] = progress_state.to_dict()
        result["authoritative_browser_result"] = payload
        if status == "completed":
            self._clear_progress_state(session)
        else:
            result["failure_summary"] = self._runtime.service.build_failure_summary(
                task=self._load_task_text(session),
                error="browser_task_incomplete",
                page_url=progress_state.last_page_url,
                page_title=progress_state.last_page_title,
                final="",
                screenshot=progress_state.last_screenshot,
                attempt=1,
                progress_state=progress_state,
            )
            self._persist_service_progress_to_session(session)
        return True

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
        """Rewrite reference aliases to the Playwright MCP target contract."""
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
        for alias, target_key in _BROWSER_REF_ALIAS_KEYS.items():
            if alias not in parsed:
                continue
            if target_key not in parsed:
                parsed[target_key] = parsed[alias]
            parsed.pop(alias, None)
            changed = True

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
    def _build_progress_result(
        progress_state: BrowserTaskProgressState,
        clean_output: str,
    ) -> Dict[str, Any]:
        """Build completion output from runtime-owned progress only."""

        status = str(progress_state.status or "").strip().lower() or "partial"
        progress = progress_state.to_dict()
        return {
            "ok": status == "completed",
            "status": status,
            "progress": progress,
            "final": clean_output,
            "error": None if status == "completed" else "browser_task_incomplete",
        }

    @classmethod
    def _apply_worker_progress_to_task_state(
        cls,
        session: Any,
        payload: Dict[str, Any],
        final: str,
    ) -> None:
        """Validate a worker completion request against authoritative runtime state."""

        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return
        reported_status = str(payload.get("status") or "partial").strip().lower()
        state["worker_reported_status"] = reported_status
        reported_blockers = payload.get("blockers") or payload.get("missing_requirements") or []
        if isinstance(reported_blockers, str):
            reported_blockers = [reported_blockers]
        state["worker_reported_blockers"] = [str(item)[:300] for item in reported_blockers if str(item).strip()][:10]
        next_action = payload.get("next_action") or payload.get("next_step")
        if next_action:
            state["worker_reported_next_action"] = str(next_action)[:200]
        if final:
            state["last_worker_final"] = str(final)[:2_000]

        current_status = str(state.get("status") or "in_progress").strip().lower()
        if current_status in _BROWSER_TERMINAL_STATUSES:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return

        runtime_blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        missing_fields = cls._missing_completion_requirements(state)
        phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
        completed_phase_count = sum(
            1 for details in phases.values() if isinstance(details, dict) and details.get("status") == "completed"
        )
        evidence_available = bool(state.get("structured_evidence") or state.get("field_coverage"))
        runtime_ready = bool(completed_phase_count or evidence_available)
        if state.get("replan_required"):
            runtime_blockers = list(dict.fromkeys([*runtime_blockers, "semantic_replan_required"]))

        completion_requirements_met = not runtime_blockers and not missing_fields and runtime_ready
        if reported_status == "completed" and completion_requirements_met:
            state["status"] = "completed"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "runtime_completion_validated"
        elif reported_status in _BROWSER_TERMINAL_STATUSES:
            state["status"] = "blocked" if runtime_blockers else "partial"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = (
                "runtime_blocked" if runtime_blockers else "runtime_completion_requirements_missing"
            )
            if runtime_blockers:
                state["blockers"] = runtime_blockers[:10]
            elif missing_fields:
                state["blockers"] = [f"missing_required_field:{field}" for field in missing_fields[:10]]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})

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
        task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(task_state, dict) and task_state:
            return BrowserTaskProgressState.from_task_state(task_state)
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
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            exported: Dict[str, Any] = {}
        else:
            self._runtime.service.set_progress_state(session_id, progress_state)
            exported = progress_state.to_dict()
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: exported,
            }
        )

    def _ensure_task_state(self, session: Any, task: str, *, resume: bool = False) -> Dict[str, Any]:
        normalized_task = str(task or "").strip()
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(state, dict) and str(state.get("task") or "").strip() == normalized_task:
            return state
        if isinstance(state, dict) and resume:
            return self._resume_task_state(session, state, normalized_task)
        self._runtime.reset_semantic_task()
        self._runtime.service.clear_progress_state(session.get_session_id())
        state = self._build_phase_state(normalized_task)
        session.update_state(
            {
                _BROWSER_PROGRESS_TASK_KEY: normalized_task,
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PHASE_STATE_KEY: state,
            }
        )
        return state

    def _resume_task_state(
        self,
        session: Any,
        state: Dict[str, Any],
        resume_instruction: str,
    ) -> Dict[str, Any]:
        if str(state.get("resume_instruction") or "").strip() == resume_instruction:
            return state
        missing = self._missing_completion_requirements(state)
        if not self._terminal_result_retryable(state, missing):
            return state

        state["resume_count"] = int(state.get("resume_count") or 0) + 1
        state["resume_instruction"] = resume_instruction[:2_000]
        state["status"] = "in_progress"
        state["next_action_class"] = (
            "collect_missing_evidence" if missing else "materially_different_strategy"
        )
        state["terminal_reason"] = ""
        state.pop(_BROWSER_TERMINAL_SYNTHESIS_KEY, None)
        state["replan_required"] = False
        state["replan_trial_pending"] = False
        state["replan_count"] = 0
        state["replan_denial_count"] = 0
        state["blocked_strategy"] = ""
        state["trial_strategy"] = ""
        state["model_protocol_retry_count"] = 0
        recoverable_prefixes = (
            "semantic_",
            "missing_required_field:",
            "missing_evidence_slot:",
            "model_provider_unavailable",
            "model_tool_protocol_error",
        )
        state["blockers"] = [
            blocker
            for blocker in state.get("blockers") or []
            if not str(blocker).strip().lower().startswith(recoverable_prefixes)
        ][:10]
        known_urls = list(state.get("known_urls") or [])
        for url in re.findall(r"https?://[^\s<>\"]+", resume_instruction):
            if url not in known_urls:
                known_urls.append(url)
        state["known_urls"] = known_urls[:4]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        self._runtime.reset_semantic_task()
        return state

    @staticmethod
    def _build_phase_state(task: str) -> Dict[str, Any]:
        normalized = str(task or "").lower()
        complex_tokens = (
            "form",
            "filter",
            "sort",
            "compare",
            "book",
            "checkout",
            "register",
            "login",
            "purchase",
            "\u8868\u5355",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u6bd4\u8f83",
            "\u9884\u8ba2",
            "\u767b\u5f55",
            "\u6ce8\u518c",
            "\u7ed3\u8d26",
            "\u8d2d\u4e70",
        )
        task_type = "complex" if any(token in normalized for token in complex_tokens) else "simple"
        phase_names = tuple(_BROWSER_PHASE_DEFINITIONS) if task_type == "complex" else ("navigation", "extraction")
        phases = {
            name: {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[name]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[name]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            }
            for name in phase_names
        }
        return {
            "task_id": hashlib.sha256(str(task).encode("utf-8")).hexdigest()[:16],
            "task": task,
            "goal": task,
            "task_type": task_type,
            "status": "in_progress",
            "phases": phases,
            "current_phase": phase_names[0],
            "known_urls": re.findall(r"https?://[^\s<>\"]+", task)[:4],
            "constraints": [],
            "required_fields": BrowserRuntimeRail._infer_required_fields(task),
            "required_evidence_slots": BrowserRuntimeRail._infer_required_evidence_slots(task),
            "replan_count": 0,
            "replan_denial_count": 0,
            "resume_count": 0,
            "direct_navigation_guidance_count": 0,
            "read_only_recovery_counts": {},
            "model_protocol_retry_count": 0,
            "replan_required": False,
            "replan_trial_pending": False,
            "blocked_strategy": "",
            "failed_strategies": [],
            "trial_strategy": "",
            "last_strategy_fingerprint": "",
            "next_action_class": "navigation",
            "semantic_revision": 0,
            "structured_evidence": [],
            "evidence_slots": [],
            "field_coverage": [],
            "blockers": [],
            "recent_actions": [],
            "action_sequence": 0,
            "last_page": {},
        }

    @classmethod
    def _infer_required_fields(cls, task: str) -> list[str]:
        normalized = str(task or "").lower()
        request_scopes = _BROWSER_OUTPUT_REQUEST_CUE_RE.findall(normalized)
        output_scope = " ".join(request_scopes).strip() or normalized
        inferred = [
            field
            for field, aliases in _BROWSER_FIELD_ALIASES.items()
            if any(cls._contains_field_alias(output_scope, alias) for alias in aliases)
        ]
        if "product_rating" in inferred or "shop_rating" in inferred:
            inferred = [field_name for field_name in inferred if field_name != "rating"]
        if "product_rating" in inferred and not any(
            cls._contains_field_alias(normalized, alias) for alias in ("title", "name", "标题", "名称", "电影", "结果")
        ):
            inferred = [field_name for field_name in inferred if field_name != "title"]
        if "shop_rating" in inferred:
            without_shop_rating = re.sub(
                r"shop\s+rating|seller\s+rating|store\s+rating|店铺评分|卖家评分",
                "",
                normalized,
                flags=re.IGNORECASE,
            )
            if not any(
                cls._contains_field_alias(without_shop_rating, alias) for alias in _BROWSER_FIELD_ALIASES["shop"]
            ):
                inferred = [field_name for field_name in inferred if field_name != "shop"]
        return inferred

    @classmethod
    def _infer_required_evidence_slots(cls, task: str) -> list[Dict[str, str]]:
        """Create one provenance-bearing evidence slot per requested field."""
        normalized = str(task or "").lower()
        required_fields = cls._infer_required_fields(task)
        entity = cls._infer_evidence_entity(normalized)
        comprehensive_requested = any(
            token in normalized for token in ("comprehensive", "relevance", "综合", "默认排序")
        )
        latest_requested = any(token in normalized for token in ("latest", "newest", "最新", "最近发布"))
        split_title_variants = bool(
            "title" in required_fields and comprehensive_requested and latest_requested
        )
        slots = [
            {"entity": entity, "variant": "default", "field": field_name}
            for field_name in required_fields
            if not (field_name == "title" and split_title_variants)
        ]
        if split_title_variants:
            slots.extend(
                [
                    {"entity": entity, "variant": "comprehensive", "field": "title"},
                    {"entity": entity, "variant": "latest", "field": "title"},
                ]
            )
        return slots

    @staticmethod
    def _infer_evidence_entity(normalized_task: str) -> str:
        profile_entity = infer_profile_evidence_entity(normalized_task)
        if profile_entity:
            return profile_entity
        entities = (
            (("商品",), "product"),
            (("回答",), "article_or_answer"),
            (("article", "文章"), "article"),
            (("电影",), "movie"),
            (("weather", "天气", "气温"), "weather"),
            (("hotel", "酒店"), "hotel"),
            (("video", "视频"), "video"),
        )
        for aliases, entity in entities:
            if any(alias in normalized_task for alias in aliases):
                return entity
        return "task_result"

    @staticmethod
    def _contains_field_alias(text: str, alias: str) -> bool:
        normalized_alias = str(alias or "").strip().lower()
        if not normalized_alias:
            return False
        if any("\u4e00" <= character <= "\u9fff" for character in normalized_alias):
            return normalized_alias in text
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_])", text) is not None

    @classmethod
    def _canonical_field_name(cls, value: Any) -> str:
        normalized = " ".join(str(value or "").strip().lower().replace("_", " ").split())
        if not normalized:
            return ""
        for field_name, aliases in _BROWSER_FIELD_ALIASES.items():
            candidates = (field_name.replace("_", " "), *aliases)
            if any(normalized == candidate.lower() for candidate in candidates):
                return field_name
        return str(value or "").strip()[:80]

    @classmethod
    def _field_mentioned(cls, field_name: str, text: str) -> bool:
        aliases = _BROWSER_FIELD_ALIASES.get(str(field_name), (str(field_name),))
        return any(cls._contains_field_alias(text, alias) for alias in aliases)

    def _sync_semantic_progress(self, session: Any) -> None:
        if session is None:
            return
        progress = self._runtime.semantic_progress
        if BrowserWorkingContextStore.sync_semantic_progress(session, progress):
            self._runtime.acknowledge_semantic_replan()

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
        filter_terms = (
            "filter",
            "sort",
            "price",
            "rating",
            "category",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u4ef7\u683c",
            "\u8bc4\u5206",
            "\u5206\u7c7b",
        )
        filter_terms_present = _contains_any_token(serialized, filter_terms)
        script_tool = any(token in name for token in ("evaluate", "run_code"))
        filter_intent = bool(
            filter_terms_present
            and (
                not script_tool
                or cls._operation_intent(name, args) == "script_mutation"
            )
        )
        if "browser_batch_interact" in name:
            steps = args.get("steps")
            operations = {str(step.get("op") or "").lower() for step in steps or [] if isinstance(step, dict)}
            extraction_operations = {"extract_text", "extract_value"}
            mutating_operations = _BATCH_MUTATING_TARGET_OPS | {"press", "navigate", "navigate_back"}
            if operations & extraction_operations and not operations & mutating_operations:
                return "extraction"
            if filter_intent:
                return "filtering"
            if operations & {
                "fill",
                "type",
                "autocomplete",
                "select_option",
                "select_visible_text",
                "set_checked",
            }:
                return "form"
            if operations & {"extract_text", "extract_value"}:
                return "extraction"
        if filter_intent:
            return "filtering"
        if any(token in name for token in ("fill", "type", "select", "press", "file_upload")):
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
        semantic_keys = (
            "url",
            "href",
            "name",
            "text",
            "query",
            "value",
            "values",
            "text_value",
            "label_value",
            "option_value",
            "option_label",
            "option_text",
            "choose_text",
            "checked",
            "key",
            "field",
            "action",
            "element",
        )
        actions: list[Dict[str, Any]] = []
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    compact = _copy_non_none_values(step, semantic_keys)
                    compact["op"] = str(step.get("op") or "").strip().lower()
                    actions.append(compact)
        else:
            compact = _copy_non_none_values(args, semantic_keys)
            normalized_name = str(tool_name or "").strip().lower()
            operation = normalized_name.rsplit("browser_", 1)[-1]
            compact["op"] = operation
            actions.append(compact)
        return json.dumps(
            actions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:600]

    @classmethod
    def _strategy_fingerprint(
        cls,
        state: Dict[str, Any],
        tool_name: str,
        tool_args: Any,
        action_class: str,
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Identify a material strategy without selector or generation noise."""

        args = cls._coerce_tool_args(tool_args)
        fields = cls._requested_strategy_fields(state, args)
        intent = cls._operation_intent(tool_name, args)
        descriptor = {
            "action_class": action_class,
            "page": cls._normalize_navigation_url(cls._current_page_url(state, current_page_state)),
            "target": cls._strategy_target(args, current_page_state),
            "fields": fields,
            "intent": intent,
        }
        serialized = json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        field_label = ",".join(fields[:4]) or "none"
        return (f"{action_class}:{intent}:{field_label}:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]}")[
            :240
        ]

    @staticmethod
    def _current_page_url(
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        return str(page_state.get("url") or last_page.get("url") or "")

    @classmethod
    def _strategy_target(
        cls,
        args: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        url = str(args.get("url") or args.get("href") or "").strip()
        if url:
            return cls._normalize_navigation_url(url)
        target_id = str(args.get("target_id") or "").strip()
        if target_id:
            return cls._target_semantic_identity(target_id, current_page_state)
        for key in ("name", "label", "placeholder", "text", "element"):
            value = " ".join(str(args.get(key) or "").split()).lower()
            if value:
                return f"semantic:{value[:120]}"
        steps = args.get("steps")
        if isinstance(steps, list):
            labels = []
            for step in steps[:12]:
                if not isinstance(step, dict):
                    continue
                step_target_id = str(step.get("target_id") or "").strip()
                if step_target_id:
                    labels.append(cls._target_semantic_identity(step_target_id, current_page_state))
                    continue
                label = next(
                    (
                        " ".join(str(step.get(key) or "").split()).lower()
                        for key in ("name", "label", "placeholder", "text", "element")
                        if str(step.get(key) or "").strip()
                    ),
                    "",
                )
                labels.append(label[:80] or "page_target")
            if labels:
                return "batch:" + "|".join(labels)
        return (
            "page_target" if _has_non_empty_mapping_value(args, ("target_id", "target", "ref", "selector")) else "page"
        )

    @staticmethod
    def _target_semantic_identity(
        target_id: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}

        def find_target(value: Any) -> Optional[Dict[str, Any]]:
            if isinstance(value, dict):
                if str(value.get("target_id") or "") == target_id:
                    return value
                for nested in value.values():
                    match = find_target(nested)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for nested in value:
                    match = find_target(nested)
                    if match is not None:
                        return match
            return None

        target = find_target(page_state)
        if target is None:
            return "registered_target"
        identity = {
            key: " ".join(str(target.get(key) or "").split()).lower()[:160]
            for key in ("href", "field", "role", "kind", "region", "text")
            if str(target.get(key) or "").strip()
        }
        return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:400]

    @classmethod
    def _requested_strategy_fields(cls, state: Dict[str, Any], args: Dict[str, Any]) -> list[str]:
        fields: set[str] = set()
        for value in (args.get("field"), args.get("fields")):
            values = value if isinstance(value, list) else [value]
            for item in values:
                canonical = cls._canonical_field_name(item)
                if canonical:
                    fields.add(canonical)
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                canonical = cls._canonical_field_name(step.get("field"))
                if canonical:
                    fields.add(canonical)
        serialized = json.dumps(args, ensure_ascii=False, default=str).lower()
        for field_name in state.get("required_fields") or []:
            if cls._field_mentioned(str(field_name), serialized):
                fields.add(str(field_name))
        return sorted(fields)

    @staticmethod
    def _operation_intent(tool_name: str, args: Dict[str, Any]) -> str:
        steps = args.get("steps")
        if isinstance(steps, list):
            operations = sorted(
                {
                    str(step.get("op") or "").strip().lower()
                    for step in steps
                    if isinstance(step, dict) and str(step.get("op") or "").strip()
                }
            )
            return "+".join(operations)[:120] or "batch"
        name = str(tool_name or "").strip().lower().rsplit("browser_", 1)[-1]
        if name in {"evaluate", "run_code", "run_code_unsafe"}:
            expression = str(
                args.get("function") or args.get("expression") or args.get("script") or args.get("code") or ""
            ).lower()
            if re.search(r"\.click\s*\(|dispatchEvent|\.value\s*=|setAttribute\s*\(", expression):
                return "script_mutation"
            if re.search(r"textContent|innerText|return|querySelector|document\.title", expression):
                return "script_extraction"
            return "script_inspection"
        return name or "other"

    @staticmethod
    def _price_interval_signature(tool_name: str, tool_args: Any) -> str:
        """Delegate cross-site filter normalization to semantic-state logic."""
        return price_interval_signature(tool_name, tool_args)

    @classmethod
    def _classify_action_class(cls, tool_name: str, tool_args: Any, state: Dict[str, Any]) -> str:
        name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if any(token in name for token in ("navigate", "navigate_back", "tabs")):
            return "navigation"
        if any(token in name for token in ("mouse_wheel", "scroll")):
            return "viewport_exploration"
        if any(token in name for token in ("evaluate", "run_code")):
            return "script_exploration"
        if "probe_cards" in name:
            return "structured_extraction"
        if any(token in name for token in ("probe_interactives", "snapshot", "find")):
            return "target_discovery"
        phase = cls._classify_tool_phase(tool_name, tool_args, state)
        if phase == "extraction":
            return "structured_extraction"
        if phase in {"form", "filtering"}:
            return phase
        if any(token in name for token in ("click", "hover", "press", "drag", "drop")):
            return "interaction"
        if any(token in serialized for token in ("filter", "sort", "price", "rating", "筛选", "排序", "价格")):
            return "filtering"
        return "other"

    @classmethod
    def _consume_phase_budget(
        cls,
        session: Any,
        tool_name: str,
        tool_args: Any,
        *,
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        if session is None:
            return "other"
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return "other"
        cls._reject_terminal_state(state)
        if cls._is_replan_exempt_tool(tool_name):
            return cls._classify_action_class(tool_name, tool_args, state)
        phases = state.setdefault("phases", {})
        phase = cls._classify_tool_phase(tool_name, tool_args, state)
        details = cls._phase_details(phases, phase)
        signature = cls._phase_action_signature(tool_name, tool_args)
        action_class = cls._classify_action_class(tool_name, tool_args, state)
        strategy_fingerprint = cls._strategy_fingerprint(
            state,
            tool_name,
            tool_args,
            action_class,
            current_page_state,
        )

        attempts = int(details.get("attempts") or 0)
        budget = max(1, int(details.get("budget") or 1))
        if attempts >= budget:
            details["status"] = "replan_required"
            details["blocked_signature"] = signature
            state["current_phase"] = phase
            state["status"] = "partial"
            state["blockers"] = [f"{phase}_phase_budget_exhausted"]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                f"Browser {phase} phase budget exhausted ({attempts}/{budget}). "
                "Finish with available evidence or return partial/blocked."
            )

        cls._reject_revisited_price_interval(details, phase, tool_name, tool_args)

        if cls._direct_navigation_is_required(state, phases, phase, current_page_state):
            state["direct_navigation_guidance_count"] = int(state.get("direct_navigation_guidance_count") or 0) + 1
            recommended_url = str((state.get("known_urls") or [""])[0])
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                "The task already contains a known URL. Navigate to it directly before selector exploration: "
                f"{recommended_url}"
            )

        try:
            cls._consume_replan_gate(
                state,
                tool_name,
                tool_args,
                action_class,
                strategy_fingerprint,
            )
        except ValueError:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise

        details["attempts"] = attempts + 1
        details["status"] = "in_progress"
        details["last_signature"] = signature
        details["last_semantic_signature"] = signature
        state["current_phase"] = phase
        state["last_action_class"] = action_class
        state["last_strategy_fingerprint"] = strategy_fingerprint
        state["next_action_class"] = ""
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return action_class

    @classmethod
    def _direct_navigation_is_required(
        cls,
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        if (
            not state.get("known_urls")
            or phase == "navigation"
            or int(state.get("direct_navigation_guidance_count") or 0) >= 1
        ):
            return False
        total_attempts = sum(int(item.get("attempts") or 0) for item in phases.values() if isinstance(item, dict))
        return total_attempts == 0 and not cls._known_url_navigation_satisfied(
            state,
            current_page_state,
        )

    @classmethod
    def _known_url_navigation_satisfied(
        cls,
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        current_url = str(page_state.get("url") or last_page.get("url") or "").strip()
        if not current_url:
            return False
        current_normalized = cls._normalize_navigation_url(current_url)
        current_document = cls._normalize_navigation_url(current_url, include_query=False)
        try:
            current_parts = urlsplit(current_url)
        except ValueError:
            current_parts = None
        for known_url in state.get("known_urls") or []:
            known_normalized = cls._normalize_navigation_url(known_url)
            if known_normalized and known_normalized == current_normalized:
                return True
            known_document = cls._normalize_navigation_url(known_url, include_query=False)
            if known_document and known_document == current_document:
                return True
            try:
                known_parts = urlsplit(str(known_url))
            except ValueError:
                known_parts = None
            if current_parts is None or known_parts is None:
                continue
            if current_parts.netloc.lower() != known_parts.netloc.lower():
                continue
            known_path = (known_parts.path or "/").rstrip("/") or "/"
            current_path = (current_parts.path or "/").rstrip("/") or "/"
            if known_path == "/" and (current_path != "/" or bool(current_parts.query)):
                return True
            if known_path != "/" and current_path.startswith(f"{known_path}/"):
                return True
        return False

    @staticmethod
    def _normalize_navigation_url(value: Any, *, include_query: bool = True) -> str:
        raw = str(value or "").strip().rstrip(".,;，。；)")
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw.lower()
        if not parsed.netloc:
            return raw.lower().rstrip("/")
        path = unquote(parsed.path or "/").rstrip("/") or "/"
        query = ""
        if include_query:
            query_items = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
            ]
            query = urlencode(sorted(query_items))
        normalized = f"{parsed.netloc.lower()}{path}"
        return f"{normalized}?{query}" if query else normalized

    @staticmethod
    def _reject_terminal_state(state: Dict[str, Any]) -> None:
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            raise ValueError(
                f"Browser task is already {status}; do not issue another browser action. "
                "Finish with the runtime evidence and blockers."
            )

    @staticmethod
    def _phase_details(phases: Dict[str, Any], phase: str) -> Dict[str, Any]:
        return phases.setdefault(
            phase,
            {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[phase]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[phase]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            },
        )

    @classmethod
    def _consume_replan_gate(
        cls,
        state: Dict[str, Any],
        tool_name: str,
        tool_args: Any,
        action_class: str,
        strategy_fingerprint: str = "",
    ) -> None:
        cls._reject_terminal_state(state)
        if not state.get("replan_required"):
            return
        if cls._is_replan_exempt_tool(tool_name):
            return
        if state.get("replan_trial_pending"):
            cls._consume_replan_denial(state, "replan_trial_pending")
            raise ValueError(
                "A replan trial already ran without verified semantic progress. "
                "Wait for the runtime observation, or finish partial/blocked instead of trying "
                "another selector, generation, tool, or phase."
            )
        replan_count = int(state.get("replan_count") or 0)
        if replan_count >= 2:
            state["status"] = "blocked"
            state["blockers"] = ["semantic_replan_budget_exhausted"]
            raise ValueError(
                "Semantic progress remained blocked after two replan trials. "
                "Return blocked or partial with the available structured evidence."
            )
        blocked_strategy = str(
            state.get("blocked_strategy")
            or state.get("last_strategy_fingerprint")
            or state.get("last_action_class")
            or ""
        )
        failed_strategies = {str(item) for item in state.get("failed_strategies") or [] if str(item).strip()}
        if blocked_strategy:
            failed_strategies.add(blocked_strategy)
        current_strategy = strategy_fingerprint or action_class
        if current_strategy in failed_strategies or action_class in failed_strategies:
            if cls._allow_read_only_recovery(state, tool_name, tool_args, current_strategy):
                state["replan_count"] = replan_count + 1
                state["replan_trial_pending"] = True
                state["trial_strategy"] = current_strategy
                state["status"] = "replan_trial"
                return
            cls._consume_replan_denial(state, "repeated_strategy")
            raise ValueError(
                "Semantic loop detected. Changing selector, generation, tool name, or phase does not "
                f"change the {action_class} target/field/intent strategy. Re-plan materially or finish "
                "partial/blocked."
            )
        state["replan_count"] = replan_count + 1
        state["replan_trial_pending"] = True
        state["trial_strategy"] = current_strategy
        state["status"] = "replan_trial"

    @staticmethod
    def _is_replan_exempt_tool(tool_name: str) -> bool:
        normalized_name = str(tool_name or "").strip().lower()
        exempt_tokens = (
            "browser_recall_offload",
            "browser_runtime_health",
            "browser_list_custom_actions",
        )
        return _contains_any_token(normalized_name, exempt_tokens)

    @classmethod
    def _allow_read_only_recovery(
        cls,
        state: Dict[str, Any],
        tool_name: str,
        tool_args: Any,
        strategy_fingerprint: str,
    ) -> bool:
        if not cls._is_read_only_recovery(tool_name, tool_args):
            return False
        counts = state.setdefault("read_only_recovery_counts", {})
        count = int(counts.get(strategy_fingerprint) or 0)
        if count >= _BROWSER_READ_ONLY_RECOVERY_LIMIT:
            return False
        counts[strategy_fingerprint] = count + 1
        return True

    @classmethod
    def _is_read_only_recovery(cls, tool_name: str, tool_args: Any) -> bool:
        normalized_name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        if any(token in normalized_name for token in ("probe", "find", "snapshot", "screenshot")):
            return True
        if "browser_tabs" in normalized_name:
            return str(args.get("action") or "list").strip().lower() in {"list", "select"}
        if any(token in normalized_name for token in ("evaluate", "run_code")):
            return cls._operation_intent(normalized_name, args) != "script_mutation"
        if "browser_batch_interact" not in normalized_name:
            return False
        steps = args.get("steps")
        if not isinstance(steps, list) or not steps:
            return False
        read_only_ops = _BATCH_SAFE_READ_SELECTOR_OPS | _BATCH_EXPLICIT_SELECTOR_OPS | {
            "wait_for_text",
            "wait_for_load_state",
            "wait_for_url",
            "wait_for_tab",
        }
        return all(
            isinstance(step, dict) and str(step.get("op") or "").strip().lower() in read_only_ops
            for step in steps
        )

    @staticmethod
    def _consume_replan_denial(state: Dict[str, Any], reason: str) -> None:
        denial_count = int(state.get("replan_denial_count") or 0) + 1
        state["replan_denial_count"] = denial_count
        state["last_denial_reason"] = str(reason or "")[:120]
        if denial_count < _BROWSER_REPLAN_DENIAL_LIMIT:
            return
        blockers = list(state.get("blockers") or [])
        if "semantic_replan_denial_budget_exhausted" not in blockers:
            blockers.append("semantic_replan_denial_budget_exhausted")
        state["blockers"] = blockers[:10]
        state["status"] = "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "semantic_replan_denial_budget_exhausted"

    @classmethod
    def _reject_revisited_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        tool_name: str,
        tool_args: Any,
    ) -> None:
        price_interval = cls._price_interval_signature(tool_name, tool_args)
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if phase == "filtering" and price_interval and price_interval in visited_intervals:
            raise ValueError(
                f"Price interval {price_interval} was already visited in this task. "
                "Use the existing evidence or choose a different interval."
            )

    @classmethod
    def _record_phase_result(
        cls,
        session: Any,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
    ) -> Dict[str, Any]:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return {}
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return {
                "phase": state.get("current_phase") or "unknown",
                "success": BrowserAgentRuntime.tool_result_succeeded(tool_result),
                "new_evidence_fields": [],
                "evidence_added": False,
                "recovered": False,
            }
        phase = cls._classify_tool_phase(tool_name, tool_args, state)
        phases = state.get("phases")
        if not isinstance(phases, dict):
            return {}
        details = phases.get(phase)
        if not isinstance(details, dict):
            return {}
        succeeded = BrowserAgentRuntime.tool_result_succeeded(tool_result)
        if not succeeded:
            cls._record_failed_phase_result(state, details, tool_result)
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return {"phase": phase, "success": False, "new_evidence_fields": [], "evidence_added": False}

        details["successes"] = int(details.get("successes") or 0) + 1
        details["last_error"] = ""
        args = cls._coerce_tool_args(tool_args)
        result = tool_result if isinstance(tool_result, dict) else {"result": tool_result}
        cls._record_price_interval(details, phase, tool_name, args)
        evidence_delta = cls._record_tool_evidence(state, result, tool_name=tool_name, tool_args=args)
        if evidence_delta.get("recovered"):
            BrowserWorkingContextStore.mark_replan_recovered(state)
        completion_evidence = cls._phase_completion_evidence(
            phase,
            str(tool_name or "").strip().lower(),
            args,
            result,
        )
        missing_fields = cls._missing_completion_requirements(state) if phase == "extraction" else []
        if completion_evidence and not missing_fields:
            cls._complete_phase(state, phases, phase, details, completion_evidence)
        elif details.get("status") != "replan_required":
            details["status"] = "in_progress"
            details["missing_fields"] = missing_fields
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return {"phase": phase, "success": True, **evidence_delta}

    @staticmethod
    def _missing_required_fields(state: Dict[str, Any]) -> list[str]:
        required = {str(item) for item in state.get("required_fields") or [] if str(item).strip()}
        coverage = {str(item) for item in state.get("field_coverage") or [] if str(item).strip()}
        return sorted(required - coverage)

    @classmethod
    def _missing_completion_requirements(cls, state: Dict[str, Any]) -> list[str]:
        missing = cls._missing_required_fields(state)
        missing_field_names = set(missing)
        missing_slots = cls._missing_evidence_slots(state)
        missing.extend(
            f"evidence_slot:{slot['entity']}:{slot['variant']}:{slot['field']}"
            for slot in missing_slots
            if not (slot["variant"] == "default" and slot["field"] in missing_field_names)
        )
        return missing

    @classmethod
    def _missing_evidence_slots(cls, state: Dict[str, Any]) -> list[Dict[str, str]]:
        required_slots = {
            cls._evidence_slot_key(slot)
            for slot in state.get("required_evidence_slots") or []
            if isinstance(slot, dict)
        }
        covered_slots = {
            cls._evidence_slot_key(slot)
            for slot in state.get("evidence_slots") or []
            if isinstance(slot, dict) and slot.get("value") not in (None, "")
        }
        return [
            {"entity": entity, "variant": variant, "field": field_name}
            for entity, variant, field_name in sorted(required_slots - covered_slots)
        ]

    @staticmethod
    def _evidence_slot_key(slot: Dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(slot.get("entity") or "").strip().lower(),
            str(slot.get("variant") or "").strip().lower(),
            str(slot.get("field") or "").strip().lower(),
        )

    @staticmethod
    def _record_failed_phase_result(state: Dict[str, Any], details: Dict[str, Any], tool_result: Any) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "pending"
        if isinstance(tool_result, dict):
            details["last_error"] = str(tool_result.get("error") or "")[:300]
        if not state.get("replan_trial_pending"):
            return
        trial_strategy = str(state.get("trial_strategy") or "")
        state["replan_trial_pending"] = False
        state["replan_required"] = True
        state["blocked_strategy"] = trial_strategy
        BrowserWorkingContextStore.record_failed_strategy(state, trial_strategy)
        state["status"] = "replan_required"

    @classmethod
    def _record_tool_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        previous_evidence_count = len(state.get("structured_evidence") or [])
        previous_coverage = set(state.get("field_coverage") or [])
        cls._record_structured_evidence(state, result, tool_name=tool_name, tool_args=tool_args)
        evidence_added = len(state.get("structured_evidence") or []) > previous_evidence_count
        new_evidence_fields = sorted(set(state.get("field_coverage") or []) - previous_coverage)
        return {
            "new_evidence_fields": new_evidence_fields,
            "evidence_added": evidence_added,
            "recovered": bool(evidence_added or new_evidence_fields),
        }

    @classmethod
    def _record_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        price_interval = cls._price_interval_signature(tool_name, args)
        if phase != "filtering" or not price_interval:
            return
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if price_interval not in visited_intervals:
            visited_intervals.append(price_interval)

    @classmethod
    def _record_structured_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        evidence = cls._structured_evidence(result)
        if not evidence and "evaluate" in str(tool_name or "").lower():
            evidence = cls._evaluate_evidence(result, tool_args)
        if not evidence and "probe_interactives" in str(tool_name or "").lower():
            evidence = cls._interactive_probe_evidence(result)
        if not evidence:
            return
        stored_evidence = state.setdefault("structured_evidence", [])
        evidence_signature = cls._evidence_signature(evidence)
        known_signatures = {cls._evidence_signature(item) for item in stored_evidence if isinstance(item, dict)}
        if evidence_signature not in known_signatures:
            stored_evidence.append(evidence)
            del stored_evidence[:-20]
        coverage = set(state.get("field_coverage") or [])
        coverage.update(evidence.get("fields") or [])
        state["field_coverage"] = sorted(coverage)
        cls._record_evidence_slots(
            state,
            evidence,
            result=result,
            tool_name=tool_name,
            tool_args=tool_args,
        )

    @classmethod
    def _record_evidence_slots(
        cls,
        state: Dict[str, Any],
        evidence: Dict[str, Any],
        *,
        result: Dict[str, Any],
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        if not state.get("required_evidence_slots"):
            return
        variant, source_url = cls._evidence_variant_and_url(state, result, tool_args)
        values, generation, provenance = cls._evidence_values(evidence)
        if not values:
            return
        covered = {
            cls._evidence_slot_key(slot) for slot in state.setdefault("evidence_slots", []) if isinstance(slot, dict)
        }
        for required in state.get("required_evidence_slots") or []:
            if not isinstance(required, dict):
                continue
            key = cls._evidence_slot_key(required)
            if key in covered or (key[1] != "default" and key[1] != variant):
                continue
            slot = cls._build_evidence_slot(
                key,
                values=values,
                provenance=provenance,
                generation=generation,
                source_url=source_url,
                tool_name=tool_name,
            )
            if slot is None:
                continue
            state["evidence_slots"].append(slot)
            covered.add(key)
        del state["evidence_slots"][:-20]

    @staticmethod
    def _build_evidence_slot(
        key: tuple[str, str, str],
        *,
        values: Dict[str, Any],
        provenance: Dict[str, Any],
        generation: str,
        source_url: str,
        tool_name: str,
    ) -> Optional[Dict[str, Any]]:
        value = values.get(key[2])
        if value in (None, ""):
            return None
        item = provenance.get(key[2]) if isinstance(provenance, dict) else None
        item = item if isinstance(item, dict) else {}
        slot = {
            "entity": key[0],
            "variant": key[1],
            "field": key[2],
            "value": str(value)[:500],
            "source": str(item.get("source") or source_url or tool_name or "")[:500],
            "generation": str(item.get("generation_id") or generation or ""),
        }
        for provenance_key in ("selector", "raw_text"):
            provenance_value = str(item.get(provenance_key) or "").strip()
            if provenance_value:
                slot[provenance_key] = provenance_value[:600]
        return slot

    @staticmethod
    def _evidence_values(
        evidence: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
        values = evidence.get("values")
        if isinstance(values, dict):
            provenance = evidence.get("provenance")
            return (
                values,
                str(evidence.get("generation_id") or ""),
                provenance if isinstance(provenance, dict) else {},
            )
        cards = evidence.get("cards")
        if not isinstance(cards, list):
            return {}, str(evidence.get("generation_id") or ""), {}
        first = next((card for card in cards if isinstance(card, dict)), {})
        if not first:
            return {}, "", {}
        provenance = first.get("provenance")
        return (
            first,
            str(first.get("generation_id") or evidence.get("generation_id") or ""),
            provenance if isinstance(provenance, dict) else {},
        )

    @staticmethod
    def _evidence_variant_and_url(
        state: Dict[str, Any],
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
    ) -> tuple[str, str]:
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        source_url = str(
            BrowserAgentRuntime.extract_result_url(result)
            or page_state.get("url")
            or tool_args.get("url")
            or last_page.get("url")
            or ""
        ).strip()
        query: Dict[str, list[str]] = {}
        try:
            query = parse_qs(urlsplit(source_url).query)
        except ValueError:
            pass
        order = " ".join(query.get("order", []) + query.get("sort", [])).lower()
        serialized_args = json.dumps(tool_args, ensure_ascii=False, default=str).lower()
        if any(token in order for token in ("pubdate", "latest", "newest", "recent")) or any(
            token in serialized_args for token in ("最新", "latest", "newest", "pubdate")
        ):
            return "latest", source_url
        if any(token in order for token in ("totalrank", "relevance", "default", "comprehensive")) or any(
            token in serialized_args for token in ("综合", "comprehensive", "relevance", "totalrank")
        ):
            return "comprehensive", source_url
        required_variants = {
            str(slot.get("variant") or "")
            for slot in state.get("required_evidence_slots") or []
            if isinstance(slot, dict)
        }
        if required_variants == {"comprehensive", "latest"} and source_url and not order:
            return "comprehensive", source_url
        return "default", source_url

    @classmethod
    def _evidence_signature(cls, evidence: Dict[str, Any]) -> str:
        ignored_keys = {
            "generation_id",
            "selector",
            "selector_hint",
            "ref",
            "target_id",
            "provenance",
            "execution_provenance",
        }

        def semantic_value(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): semantic_value(item) for key, item in value.items() if str(key) not in ignored_keys}
            if isinstance(value, list):
                return [semantic_value(item) for item in value]
            return value

        return json.dumps(
            semantic_value(evidence),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _evaluate_evidence(cls, result: Dict[str, Any], tool_args: Dict[str, Any]) -> Dict[str, Any]:
        value: Any = result.get("result")
        if value is None:
            value = result.get("value")
        if value is None:
            value = result.get("data")
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                parsed = extract_json_object(value) or value
            value = parsed

        compact_values, fields = cls._compact_evaluate_values(value)
        raw_preview = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        expression = str(
            tool_args.get("function")
            or tool_args.get("expression")
            or tool_args.get("script")
            or tool_args.get("code")
            or ""
        )
        generation_id = str(result.get("generation_id") or tool_args.get("generation_id") or "")
        target = str(tool_args.get("target") or tool_args.get("element") or "")[:240]
        return {
            "kind": "targeted_evaluate",
            "generation_id": generation_id,
            "fields": fields,
            "values": compact_values,
            "preview": " ".join(raw_preview.split())[:800],
            "provenance": cls._evaluate_field_provenance(
                compact_values,
                target=target,
                generation_id=generation_id,
            ),
            "execution_provenance": {
                "target": target,
                "expression_sha256": hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16] if expression else "",
            },
        }

    @classmethod
    def _compact_evaluate_values(cls, value: Any) -> tuple[Dict[str, str], list[str]]:
        compact_values: Dict[str, str] = {}
        if isinstance(value, dict):
            for key, item in list(value.items())[:20]:
                if item in (None, "", [], {}):
                    continue
                canonical = cls._canonical_field_name(key)
                compact_values[canonical or str(key)] = str(item)[:300]
            return compact_values, sorted(compact_values)
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            fields = sorted({str(key) for item in value[:5] for key in item})[:20]
            compact_values["items"] = json.dumps(value[:3], ensure_ascii=False, default=str)[:800]
            return compact_values, fields
        return compact_values, []

    @staticmethod
    def _evaluate_field_provenance(
        values: Dict[str, str],
        *,
        target: str,
        generation_id: str,
    ) -> Dict[str, Dict[str, str]]:
        return {
            field_name: {
                "source": "browser_evaluate",
                "selector": target,
                "raw_text": str(field_value)[:600],
                "generation_id": generation_id,
            }
            for field_name, field_value in values.items()
        }

    @staticmethod
    def _interactive_probe_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
        elements = result.get("elements")
        if not isinstance(elements, list) or not elements:
            return {}
        targets = [
            {
                "target_id": str(item.get("target_id") or ""),
                "generation_id": str(item.get("generation_id") or result.get("generation_id") or ""),
                "kind": str(item.get("kind") or ""),
                "region": str(item.get("region") or ""),
                "name": str(item.get("accessible_name") or item.get("text") or "")[:160],
                "actionable": bool(item.get("actionable")),
            }
            for item in elements[:12]
            if isinstance(item, dict)
        ]
        return {
            "kind": "interactive_probe",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": [],
            "target_count": len(targets),
            "targets": targets,
        }

    @classmethod
    def _record_recent_action(
        cls,
        session: Any,
        *,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return False
        state["action_sequence"] = int(state.get("action_sequence") or 0) + 1
        recent_actions = deque(state.get("recent_actions") or [], maxlen=6)
        recent_actions.append(
            cls._build_action_record(
                state,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                action_class=action_class,
                elapsed_ms=elapsed_ms,
                progress_delta=progress_delta,
            )
        )
        state["recent_actions"] = list(recent_actions)
        cls._update_last_page(state, tool_result)
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return bool(progress_delta.get("recovered"))

    @classmethod
    def _build_action_record(
        cls,
        state: Dict[str, Any],
        *,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> Dict[str, Any]:
        succeeded = bool(progress_delta.get("success"))
        error = str(tool_result.get("error") or "").strip() if isinstance(tool_result, dict) else ""
        semantic_delta = "evidence_added" if progress_delta.get("evidence_added") else "pending"
        if not succeeded:
            semantic_delta = "error"
        return {
            "seq": state["action_sequence"],
            "phase": progress_delta.get("phase") or state.get("current_phase"),
            "action_class": action_class or cls._classify_action_class(tool_name, tool_args, state),
            "target_summary": cls._target_summary(tool_name, tool_args),
            "outcome": "success" if succeeded else f"error: {error[:180] or 'tool_failed'}",
            "semantic_delta": semantic_delta,
            "new_evidence_fields": list(progress_delta.get("new_evidence_fields") or []),
            "elapsed_ms": max(0, int(elapsed_ms)),
        }

    @staticmethod
    def _update_last_page(state: Dict[str, Any], tool_result: Any) -> None:
        result = tool_result if isinstance(tool_result, dict) else {}
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        url = BrowserAgentRuntime.extract_result_url(result) or str(page_state.get("url") or "")
        title = str(result.get("title") or page_state.get("title") or "")
        if url or title:
            last_page = state.setdefault("last_page", {})
            if url:
                last_page["url"] = url
            if title:
                last_page["title"] = title

    @classmethod
    def _target_summary(cls, tool_name: str, tool_args: Any) -> str:
        args = cls._coerce_tool_args(tool_args)
        name = str(tool_name or "").strip().rsplit(".", 1)[-1]
        summary: Dict[str, Any] = {"tool": name}
        for key in ("url", "target_id", "target", "ref", "name", "label", "text", "value"):
            value = args.get(key)
            if value not in (None, "", [], {}):
                summary[key] = str(value)[:160]
        steps = args.get("steps")
        if isinstance(steps, list):
            summary["ops"] = [str(step.get("op") or "") for step in steps[:12] if isinstance(step, dict)]
        expression = str(args.get("function") or args.get("expression") or args.get("script") or args.get("code") or "")
        if expression:
            summary["expression_sha256"] = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
            summary["expression_chars"] = len(expression)
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))[:600]

    @staticmethod
    def _phase_completion_evidence(
        phase: str,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> str:
        steps = args.get("steps")
        operations = {
            str(step.get("op") or "").strip().lower()
            for step in (steps if isinstance(steps, list) else [])
            if isinstance(step, dict)
        }
        if phase == "navigation":
            result_url = BrowserAgentRuntime.extract_result_url(result)
            if result_url or "navigate" in tool_name:
                return result_url or "navigation tool succeeded"
        if phase == "form" and operations & {
            "wait_for_url",
            "wait_for_selector",
            "wait_for_text",
            "wait_for_first_card_title",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_stable",
            "wait_for_tab",
        }:
            return "form batch completed with an observable condition"
        if phase == "filtering" and operations & {
            "wait_for_sort_state",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_first_card_title",
        }:
            return "requested state and changed results were observed"
        if phase == "extraction":
            extracted = result.get("extracted")
            cards = result.get("cards")
            if isinstance(extracted, dict) and extracted:
                return f"structured extraction returned {len(extracted)} field(s)"
            if isinstance(cards, list) and cards:
                excluded_regions = {"hot_search", "sidebar", "account", "chat"}
                excluded_kinds = {
                    "hot_search",
                    "paid_column",
                    "promotion",
                    "activity",
                    "account",
                    "shop",
                    "chat",
                }
                natural_cards = []
                for card in cards:
                    if not isinstance(card, dict) or card.get("is_ad") is True:
                        continue
                    if str(card.get("region") or "") in excluded_regions:
                        continue
                    if str(card.get("kind") or "") in excluded_kinds:
                        continue
                    natural_cards.append(card)
                if natural_cards:
                    return f"card probe returned {len(natural_cards)} structured result(s)"
        return ""

    @staticmethod
    def _complete_phase(
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        details: Dict[str, Any],
        completion_evidence: str,
    ) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "completed"
        details["completion_evidence"] = completion_evidence[:300]
        phase_order = list(phases)
        try:
            remaining_phases = phase_order[phase_order.index(phase) + 1:]
        except ValueError:
            remaining_phases = []
        next_phase = next(
            (
                candidate
                for candidate in remaining_phases
                if isinstance(phases.get(candidate), dict) and phases[candidate].get("status") != "completed"
            ),
            phase,
        )
        state["current_phase"] = next_phase
        if all(isinstance(item, dict) and item.get("status") == "completed" for item in phases.values()):
            state["status"] = "completed"
            state["next_action_class"] = "finish"
        else:
            state["next_action_class"] = next_phase

    @classmethod
    def _structured_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        extracted = result.get("extracted")
        if isinstance(extracted, dict) and extracted:
            return cls._structured_extraction_evidence(result, extracted)
        return cls._card_probe_evidence(result)

    @classmethod
    def _structured_extraction_evidence(
        cls,
        result: Dict[str, Any],
        extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        provenance = result.get("field_provenance")
        compact_values: Dict[str, str] = {}
        compact_provenance: Dict[str, Any] = {}
        for key, value in list(extracted.items())[:20]:
            if value in (None, "", [], {}):
                continue
            canonical = cls._canonical_field_name(key) or str(key)
            compact_values[canonical] = str(value)[:300]
            if isinstance(provenance, dict) and isinstance(provenance.get(key), dict):
                compact_provenance[canonical] = dict(provenance[key])
        return {
            "kind": "structured_extraction",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": sorted(compact_values),
            "values": compact_values,
            "provenance": compact_provenance,
        }

    @classmethod
    def _card_probe_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        cards = result.get("cards")
        if not isinstance(cards, list) or not cards:
            return {}
        compact_cards: list[Dict[str, Any]] = []
        fields: set[str] = set()
        for card in cards:
            compact = cls._compact_evidence_card(card, result)
            if compact is None:
                continue
            fields.update(
                field_name
                for field_name in compact
                if field_name not in {"generation_id", "result_index", "kind", "provenance"}
            )
            compact_cards.append(compact)
            if len(compact_cards) >= 5:
                break
        if not compact_cards:
            return {}
        return {
            "kind": "card_probe",
            "fields": sorted(fields),
            "cards": compact_cards,
        }

    @classmethod
    def _compact_evidence_card(
        cls,
        card: Any,
        result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(card, dict) or not cls._is_natural_evidence_card(card):
            return None
        compact: Dict[str, Any] = {
            "generation_id": str(card.get("generation_id") or result.get("generation_id") or ""),
            "result_index": card.get("result_index"),
            "kind": card.get("kind"),
        }
        for field_name in CARD_EVIDENCE_FIELDS:
            value = card.get(field_name)
            if value in (None, "", [], {}):
                continue
            compact.setdefault(cls._canonical_field_name(field_name) or field_name, str(value)[:300])
        provenance = card.get("field_provenance")
        if isinstance(provenance, dict):
            compact["provenance"] = {
                cls._canonical_field_name(field_name) or str(field_name): dict(item)
                for field_name, item in list(provenance.items())[:20]
                if isinstance(item, dict)
            }
        return compact

    @staticmethod
    def _is_natural_evidence_card(card: Dict[str, Any]) -> bool:
        region = str(card.get("region") or "")
        kind = str(card.get("kind") or "")
        if not (region or kind or "is_ad" in card):
            return True
        return (
            card.get("is_ad") is not True
            and region
            not in {
                "hot_search",
                "sidebar",
                "account",
                "chat",
            }
            and kind
            not in {
                "hot_search",
                "paid_column",
                "promotion",
                "activity",
                "account",
                "shop",
                "chat",
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
                _BROWSER_PHASE_STATE_KEY: {},
            }
        )

    @staticmethod
    def _image_input_supported(agent: Any) -> bool:
        deep_config = getattr(agent, "deep_config", None) or getattr(agent, "_deep_config", None)
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

        return tuple(tool_name for tool_name in configured if tool_name not in _BROWSER_SCREENSHOT_TOOL_NAMES)

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
