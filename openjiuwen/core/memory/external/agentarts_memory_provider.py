# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AgentArts memory provider."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.external.provider import MemoryProvider

DEFAULT_BASE_URL = "https://memory.cn-southwest-2.huaweicloud-agentarts.com"
AGENTARTS_EXTRA = "agentarts"
AGENTARTS_PACKAGE = "agentarts-sdk"
_MAX_TOP_K = 100
_DEFAULT_TOP_K = 10
_DEFAULT_MIN_SCORE = 0.5
_SESSION_MAPPING_KEY_PREFIX = "agentarts/session_mapping"

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
                "description": f"Optional minimum similarity score. (default: {_DEFAULT_MIN_SCORE:g})",
            },
        },
        "required": ["query"],
    },
}


def _read_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class AgentArtsMemoryProvider(MemoryProvider):
    """AgentArts provider with backend-neutral prompt and tool surface."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        space_id: str = "",
        actor_id: str | None = None,
        assistant_id: str | None = None,
        session_mapping_store: BaseKVStore | None = None,
    ):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._space_id = space_id
        self._default_actor_id = actor_id
        self._actor_id = self._default_actor_id
        self._default_assistant_id = assistant_id
        self._assistant_id = self._default_assistant_id
        self._client: Any | None = None
        self._session_id = ""
        self._session_mapping_store = session_mapping_store or InMemoryKVStore()
        self._initialized = False
        self._consecutive_failures = 0

    @property
    def name(self) -> str:
        return "agentarts"

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def is_available(self) -> bool:
        return bool(self._api_key and self._space_id)

    async def initialize(self, **kwargs: Any) -> None:
        logger.info("[AgentArtsMemoryProvider] initializing with params: %s", json.dumps(kwargs))

        self._actor_id = kwargs.get("user_id")
        self._assistant_id = kwargs.get("assistant_id") or kwargs.get("scope_id")

        session_id = kwargs.get("session_id")
        await self._ensure_memory_session(session_id, actor_id=self._actor_id, assistant_id=self._assistant_id)

        self._session_id = session_id
        self._initialized = True

    def _runtime_actor_id(self, params: dict[str, Any]) -> str:
        user_id = params.get("user_id")
        if user_id:
            return str(user_id)
        return self._actor_id or self._default_actor_id

    def _runtime_assistant_id(self, params: dict[str, Any]) -> str:
        assistant_id = params.get("assistant_id")
        if assistant_id:
            return str(assistant_id)
        scope_id = params.get("scope_id")
        if scope_id:
            return str(scope_id)
        return self._assistant_id or self._default_assistant_id

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from agentarts.sdk.memory import MemoryClient

            self._client = MemoryClient(api_key=self._api_key)
        except ImportError as exc:
            raise RuntimeError(
                f"AgentArts SDK is unavailable. Install with `pip install openjiuwen[{AGENTARTS_EXTRA}]` "
                f"or install `{AGENTARTS_PACKAGE}`."
            ) from exc

        self._configure_sdk_data_endpoint(self._client)
        return self._client

    def _configure_sdk_data_endpoint(self, client: Any) -> None:
        http_client = getattr(getattr(client, "_data_plane", None), "client", None)
        if http_client is None:
            return
        setattr(http_client, "_get_base_url", lambda space_id=None: self._base_url)

    def _memory_search_filter(self, query: str, args: dict[str, Any]) -> Any:
        from agentarts.sdk.memory.inner.config import MemorySearchFilter  # type: ignore[import-not-found]

        actor_id = self._runtime_actor_id(args)
        top_k = args.get("top_k")
        if top_k is None:
            top_k = _DEFAULT_TOP_K
        filter_kwargs: dict[str, Any] = {
            "query": query,
            "top_k": min(int(top_k), _MAX_TOP_K),
            "min_score": _DEFAULT_MIN_SCORE if args.get("min_score") is None else args["min_score"],
        }
        if actor_id:
            filter_kwargs["actor_id"] = actor_id
        if args.get("strategy_type") is not None:
            filter_kwargs["strategy_type"] = args["strategy_type"]
        return MemorySearchFilter(**filter_kwargs)

    async def _search(self, query: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._get_client()
        filters = self._memory_search_filter(query, args)
        logger.info(
            "[AgentArtsMemoryProvider] search agentarts memory space [%s] with filters: %s",
            self._space_id,
            json.dumps(filters, default=str),
        )
        response = await asyncio.to_thread(
            client.search_memories,
            space_id=self._space_id,
            filters=filters,
        )
        items = _read_attr_or_key(response, "results", []) or []
        logger.info("[AgentArtsMemoryProvider] found %d relevant memory records", len(items))
        normalized = []
        for item in items:
            record = _read_attr_or_key(item, "record", {})
            content = _read_attr_or_key(record, "content", "")
            if content:
                normalized.append({"memory": content, "score": _read_attr_or_key(item, "score", 0)})
        return normalized

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
            logger.debug("[AgentArtsMemoryProvider] prefetch failed: %s", exc)
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
            logger.warning("[AgentArtsMemoryProvider] failed to search relevant memories", exc)
            self._consecutive_failures += 1
            return json.dumps({"error": str(exc)})

    def _text_message(self, role: str, content: str, *, actor_id: str = "", assistant_id: str = "") -> Any:
        from agentarts.sdk.memory.inner.config import TextMessage  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {"role": role, "content": content}
        if actor_id:
            kwargs["actor_id"] = actor_id
        if assistant_id:
            kwargs["assistant_id"] = assistant_id
        return TextMessage(**kwargs)

    @staticmethod
    def _session_mapping_key(session_id: str) -> str:
        return f"{_SESSION_MAPPING_KEY_PREFIX}/{session_id}"

    @staticmethod
    def _normalize_memory_session_id(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def _ensure_memory_session(
        self,
        session_id: str | None = None,
        *,
        actor_id: str | None = None,
        assistant_id: str | None = None,
    ) -> str:
        session_id = session_id or self._session_id
        if not session_id:
            raise RuntimeError("`session_id` is required")

        actor_id = actor_id or self._actor_id or self._default_actor_id
        assistant_id = assistant_id or self._assistant_id or self._default_assistant_id

        mapping_key = self._session_mapping_key(session_id)
        memory_session_id = self._normalize_memory_session_id(await self._session_mapping_store.get(mapping_key))
        if memory_session_id:
            logger.info(
                "[AgentArtsMemoryProvider] use exist session mapping entry: %s -> %s", session_id, memory_session_id
            )
        else:
            client = self._get_client()
            payload: dict[str, Any] = {"space_id": self._space_id}
            if actor_id:
                payload["actor_id"] = actor_id
            if assistant_id:
                payload["assistant_id"] = assistant_id
            session = await asyncio.to_thread(client.create_memory_session, **payload)
            memory_session_id = _read_attr_or_key(session, "id", "")
            if not memory_session_id:
                raise RuntimeError("AgentArts create_memory_session did not return a session id")
            logger.info("[AgentArtsMemoryProvider] add session mapping entry: %s -> %s", session_id, memory_session_id)
            await self._session_mapping_store.set(mapping_key, memory_session_id)

        return memory_session_id

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        logger.info("[AgentArtsMemoryProvider] sync_turn with params: %s", json.dumps(kwargs))
        if not user_msg or not assistant_msg:
            return

        try:
            actor_id = self._runtime_actor_id(kwargs)
            assistant_id = self._runtime_assistant_id(kwargs)

            session_id = await self._ensure_memory_session(
                kwargs.get("session_id"),
                actor_id=actor_id,
                assistant_id=assistant_id,
            )
            client = self._get_client()
            await asyncio.to_thread(
                client.add_messages,
                space_id=self._space_id,
                session_id=session_id,
                messages=[
                    self._text_message("user", user_msg, actor_id=actor_id, assistant_id=assistant_id),
                    self._text_message("assistant", assistant_msg, actor_id=actor_id, assistant_id=assistant_id),
                ],
            )
            self._consecutive_failures = 0
        except Exception as exc:
            self._consecutive_failures += 1
            logger.warning("AgentArts sync failed: %s", exc)

    async def shutdown(self) -> None:
        self._client = None
        self._initialized = False


__all__ = [
    "AgentArtsMemoryProvider",
    "EXTERNAL_MEMORY_SEARCH_SCHEMA",
]
