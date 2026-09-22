# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeAce memory PC provider — chat-service appapi backed.

PC 部署形态：记忆搜索与对话上报均走 chat-service 的 appapi 接口，
不依赖 AgentArts SDK：
* 搜索：POST /v1/appapi/memory/search（X-Chat-User-Id 头鉴权）
* 上报：POST /v1/appapi/memory/pc-threads/{thread_id}/messages（X-Chat-User-Id 头鉴权）

凭据/endpoint 复用上层下发的 per-session api_key + base_url。
任何失败均不抛异常（调用方 prefetch/handle_tool_call/sync_turn 零阻断）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.external.provider import MemoryProvider

DEFAULT_BASE_URL = "https://memory.cn-southwest-2.huaweicloud-agentarts.com"

# appapi 端点路径与超时。
_APPAPI_SEARCH_PATH = "/v1/appapi/memory/search"
_APPAPI_MESSAGES_PATH_FMT = "/v1/appapi/memory/pc-threads/{thread_id}/messages"
_APPAPI_TIMEOUT = 15.0

_STRATEGY_TYPES = ["semantic", "summary", "user_preference", "episodic", "event", "custom"]

EXTERNAL_MEMORY_SEARCH_SCHEMA = {
    "name": "external_memory_search",
    "description": (
        "Search long-term external memory for durable facts, user preferences, and prior conversation context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Memory search query."},
            "top_k": {"type": "integer", "description": "Max results, default 10, max 100."},
            "strategy_type": {
                "type": "string",
                "enum": _STRATEGY_TYPES,
                "description": "Optional memory strategy type filter. Acceptable values: "
                "semantic, summary, user_preference, episodic, event, custom.",
            },
            "min_score": {
                "type": "number",
                "description": "Optional minimum similarity score.",
            },
        },
        "required": ["query"],
    },
}


def _read_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OfficeAceMemoryPcProvider(MemoryProvider):
    """PC-side OfficeAce memory provider backed by chat-service appapi."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        actor_id: str | None = None,
    ):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._default_actor_id = actor_id
        self._actor_id = self._default_actor_id
        self._session_id = ""
        self._initialized = False
        self._consecutive_failures = 0

    @property
    def name(self) -> str:
        return "officeace_pc"

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def initialize(self, **kwargs: Any) -> None:
        logger.info("[OfficeAceMemoryPcProvider] initializing with params: %s", json.dumps(kwargs))
        self._actor_id = kwargs.get("user_id") or self._default_actor_id
        self._session_id = kwargs.get("session_id") or ""
        self._initialized = True

    def _runtime_actor_id(self, params: dict[str, Any]) -> str:
        user_id = params.get("user_id")
        if user_id:
            return str(user_id)
        return self._actor_id or self._default_actor_id or ""

    # ------------------------------------------------------------------
    # Search — POST /v1/appapi/memory/search
    # ------------------------------------------------------------------

    async def _search(self, query: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = self._runtime_actor_id(args)
        request_body = self._build_search_request_body(query, args)
        url = f"{self._base_url}{_APPAPI_SEARCH_PATH}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if user_id:
            headers["X-Chat-User-Id"] = user_id

        logger.info(
            "[OfficeAceMemoryPcProvider] appapi search (user=%s, base_url=%s, body=%s)",
            user_id or "(none)",
            self._base_url,
            json.dumps(request_body, ensure_ascii=False),
        )
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                verify=False,
                timeout=_APPAPI_TIMEOUT,
            ) as client:
                resp = await client.post(url, headers=headers, json=request_body)
        except httpx.HTTPError as exc:
            logger.warning("[OfficeAceMemoryPcProvider] appapi search network error: %s", exc)
            return []

        if resp.status_code != 200:
            logger.info(
                "[OfficeAceMemoryPcProvider] appapi search unexpected status %d (user=%s): %s",
                resp.status_code,
                user_id or "(none)",
                resp.text,
            )
            return []

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning("[OfficeAceMemoryPcProvider] appapi search response parse error: %s", exc)
            return []

        if not isinstance(data, dict):
            return []
        items = data.get("results") or []
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = _read_attr_or_key(item, "content", "")
            if not isinstance(content, str) or not content:
                continue
            normalized.append(
                {
                    "memory": content,
                    "score": _read_attr_or_key(item, "score", 0),
                }
            )
        logger.info("[OfficeAceMemoryPcProvider] appapi search found %d memory records", len(normalized))
        return normalized

    def _build_search_request_body(self, query: str, args: dict[str, Any]) -> dict[str, Any]:
        """构造 SearchAppMemoriesRequestBody，原样透传可选字段，由服务端校验。"""
        body: dict[str, Any] = {"query": query}
        top_k = args.get("top_k")
        if top_k is not None:
            body["top_k"] = top_k
        strategy_type = args.get("strategy_type")
        if strategy_type is not None:
            body["strategy_type"] = strategy_type
        min_score = args.get("min_score")
        if min_score is not None:
            body["min_score"] = min_score
        return body

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [EXTERNAL_MEMORY_SEARCH_SCHEMA]

    def system_prompt_block(self) -> str:
        return (
            "# External Memory\n"
            "Use `external_memory_search` to retrieve durable facts, user preferences, "
            "and prior conversation context from long-term external memory."
        )

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        if not query:
            return ""
        try:
            items = await self._search(query, kwargs)
            self._consecutive_failures = 0
        except Exception as exc:
            self._consecutive_failures += 1
            logger.debug("[OfficeAceMemoryPcProvider] prefetch failed: %s", exc)
            return ""
        if not items:
            return ""
        return "## External Memory\n" + "\n".join(f"- {item['memory']}" for item in items)

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if tool_name != "external_memory_search":
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Missing required parameter: query"})
        try:
            items = await self._search(query, args)
            self._consecutive_failures = 0
            if not items:
                return json.dumps({"result": "No relevant memories found.", "count": 0})
            return json.dumps({"results": items, "count": len(items)})
        except Exception as exc:
            logger.warning("[OfficeAceMemoryPcProvider] failed to search relevant memories", exc)
            self._consecutive_failures += 1
            return json.dumps({"error": str(exc)})

    # ------------------------------------------------------------------
    # sync_turn — POST /v1/appapi/memory/pc-threads/{thread_id}/messages
    # ------------------------------------------------------------------

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        if not user_msg or not assistant_msg:
            return
        user_id = self._runtime_actor_id(kwargs)
        thread_id = kwargs.get("session_id") or self._session_id
        if not thread_id:
            logger.debug("[OfficeAceMemoryPcProvider] sync_turn skipped: no thread_id")
            return

        url = f"{self._base_url}{_APPAPI_MESSAGES_PATH_FMT.format(thread_id=thread_id)}"
        request_body = self._build_messages_request_body(
            user_msg, assistant_msg, user_id=user_id, assistant_id=kwargs.get("scope_id")
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if user_id:
            headers["X-Chat-User-Id"] = user_id

        logger.info(
            "[OfficeAceMemoryPcProvider] appapi sync_turn (user=%s, thread=%s)",
            user_id or "(none)",
            thread_id,
        )
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                verify=False,
                timeout=_APPAPI_TIMEOUT,
            ) as client:
                resp = await client.post(url, headers=headers, json=request_body)
        except httpx.HTTPError as exc:
            logger.warning("[OfficeAceMemoryPcProvider] appapi sync_turn network error: %s", exc)
            return

        if resp.status_code not in (200, 201):
            logger.info(
                "[OfficeAceMemoryPcProvider] appapi sync_turn unexpected status %d (user=%s): %s",
                resp.status_code,
                user_id or "(none)",
                resp.text,
            )
            return
        logger.info("[OfficeAceMemoryPcProvider] appapi sync_turn ok (thread=%s)", thread_id)

    def _build_messages_request_body(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        user_id: str = "",
        assistant_id: str | None = None,
    ) -> dict[str, Any]:
        """构造 AddAppMemoryMessagesRequestBody（两条消息：user + assistant）。"""
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "parts": [{"type": "text", "text": user_msg}],
            },
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": assistant_msg}],
            },
        ]
        if assistant_id:
            for msg in messages:
                msg["assistant_id"] = assistant_id
        return {"messages": messages}

    async def shutdown(self) -> None:
        self._initialized = False


__all__ = ["OfficeAceMemoryPcProvider", "EXTERNAL_MEMORY_SEARCH_SCHEMA"]
