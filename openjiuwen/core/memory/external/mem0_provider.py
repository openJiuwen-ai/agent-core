# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mem0 memory provider — optimized for current ExternalMemoryRail usage."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.external.provider import MemoryProvider

_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120.0

PROFILE_SCHEMA = {
    "name": "mem0_profile",
    "description": (
        "Retrieve all stored memories about the user — preferences, facts, "
        "project context. Fast, no reranking. Use at conversation start."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": (
        "Search memories by meaning. Returns relevant facts ranked by similarity. "
        "Set rerank=true for higher accuracy on important queries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "rerank": {"type": "boolean", "description": "Enable reranking for precision (default: false)."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
        },
        "required": ["query"],
    },
}

CONCLUDE_SCHEMA = {
    "name": "mem0_conclude",
    "description": (
        "Store a durable fact about the user. Stored verbatim (no LLM extraction). "
        "Use for explicit preferences, corrections, or decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {"conclusion": {"type": "string", "description": "The fact to store."}},
        "required": ["conclusion"],
    },
}


class Mem0MemoryProvider(MemoryProvider):
    """Mem0 provider with robust engineering structure and rail-compatible behavior."""

    def __init__(
        self,
        *,
        api_key: str = "",
        user_id: str = "",
        agent_id: str = "",
        rerank: bool = False,
    ):
        self._api_key = api_key
        self._user_id = user_id
        self._agent_id = agent_id
        self._rerank = rerank

        self._client = None
        self._initialized = False
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0
        self._prefetch_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return "mem0"

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def initialize(self, **kwargs: Any) -> None:
        # Pure parameter-driven config: no environment fallback in provider layer.
        self._api_key = kwargs.get("api_key") or self._api_key
        self._user_id = kwargs.get("user_id") or self._user_id
        self._agent_id = kwargs.get("agent_id") or self._agent_id

        if "rerank" in kwargs:
            self._rerank = bool(kwargs["rerank"])

        if not self._api_key:
            raise ValueError("Mem0 API key is required. Provide api_key in provider initialization.")
        self._initialized = True

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from mem0 import AsyncMemoryClient  # type: ignore[import-not-found]
            self._client = AsyncMemoryClient(api_key=self._api_key)
            return self._client
        except ImportError as exc:
            raise RuntimeError(
                "AsyncMemoryClient is unavailable. Install/upgrade mem0ai to a version that supports async client."
            ) from exc

    async def _client_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call mem0 client method with coroutine-first semantics."""
        client = self._get_client()
        method = getattr(client, method_name)
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _read_filters(self) -> dict[str, Any]:
        filters = {"user_id": self._user_id}
        if self._agent_id:
            filters["agent_id"] = self._agent_id
        return filters

    def _write_filters(self) -> dict[str, Any]:
        return {"user_id": self._user_id, "agent_id": self._agent_id}

    @staticmethod
    def _unwrap_results(response: Any) -> list[dict[str, Any]]:
        if isinstance(response, dict):
            return response.get("results", [])
        if isinstance(response, list):
            return response
        return []

    def _is_breaker_open(self) -> bool:
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() >= self._breaker_open_until:
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "[Mem0MemoryProvider] Circuit breaker opened after %d failures. Cooldown: %ss",
                self._consecutive_failures,
                _BREAKER_COOLDOWN_SECS,
            )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [PROFILE_SCHEMA, SEARCH_SCHEMA, CONCLUDE_SCHEMA]

    def system_prompt_block(self) -> str:
        return (
            "# Mem0 Memory\n"
            f"Active. User: {self._user_id}.\n"
            "Use mem0_search to find memories, mem0_conclude to store facts, "
            "mem0_profile for a full overview."
        )

    async def queue_prefetch(self, query: str, **kwargs: Any) -> None:
        """Optional optimization path: warm up one prefetch query in background."""
        if not query or self._is_breaker_open():
            return

        async def _warmup() -> None:
            try:
                await self._client_call(
                    "search",
                    query=query,
                    filters=self._read_filters(),
                    rerank=self._rerank,
                    top_k=min(int(kwargs.get("top_k", 5)), 50),
                )
                self._record_success()
            except Exception as exc:
                self._record_failure()
                logger.debug("Mem0 queue_prefetch failed: %s", exc)

        self._prefetch_task = asyncio.create_task(_warmup())

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        """Rail-compatible behavior: always query Mem0 directly when needed."""
        if not query or self._is_breaker_open():
            return ""

        top_k = min(int(kwargs.get("top_k", 5)), 50)
        rerank = bool(kwargs.get("rerank", self._rerank))
        try:
            response = await self._client_call(
                "search",
                query=query,
                filters=self._read_filters(),
                rerank=rerank,
                top_k=top_k,
            )
            items = self._unwrap_results(response)
            if not items:
                self._record_success()
                return ""
            lines = [r.get("memory", "") for r in items if r.get("memory")]
            self._record_success()
            return ("## Mem0 Memory\n" + "\n".join(f"- {line}" for line in lines)) if lines else ""
        except Exception as exc:
            self._record_failure()
            logger.debug("Mem0 prefetch failed: %s", exc)
            return ""

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        if self._is_breaker_open() or not user_msg or not assistant_msg:
            return
        try:
            messages = [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ]
            await self._client_call("add", messages, **self._write_filters())
            self._record_success()
        except Exception as exc:
            self._record_failure()
            logger.warning("Mem0 sync failed: %s", exc)

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if self._is_breaker_open():
            return json.dumps(
                {"error": "Mem0 API temporarily unavailable (multiple consecutive failures). Will retry automatically."}
            )

        try:
            if tool_name == "mem0_profile":
                response = await self._client_call("get_all", filters=self._read_filters())
                items = self._unwrap_results(response)
                self._record_success()
                if not items:
                    return json.dumps({"result": "No memories stored yet."})
                lines = [m.get("memory", "") for m in items if m.get("memory")]
                return json.dumps({"result": "\n".join(lines), "count": len(lines)})

            if tool_name == "mem0_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                top_k = min(int(args.get("top_k", 10)), 50)
                rerank = bool(args.get("rerank", False))
                response = await self._client_call(
                    "search",
                    query=query,
                    filters=self._read_filters(),
                    rerank=rerank,
                    top_k=top_k,
                )
                items = self._unwrap_results(response)
                self._record_success()
                if not items:
                    return json.dumps({"result": "No relevant memories found."})
                payload = [{"memory": item.get("memory", ""), "score": item.get("score", 0)} for item in items]
                return json.dumps({"results": payload, "count": len(payload)})

            if tool_name == "mem0_conclude":
                conclusion = args.get("conclusion", "")
                if not conclusion:
                    return json.dumps({"error": "Missing required parameter: conclusion"})
                await self._client_call(
                    "add",
                    [{"role": "user", "content": conclusion}],
                    infer=False,
                    **self._write_filters(),
                )
                self._record_success()
                return json.dumps({"result": "Fact stored."})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            self._record_failure()
            return json.dumps({"error": str(exc)})

    async def shutdown(self) -> None:
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
            try:
                await asyncio.wait_for(self._prefetch_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._prefetch_task = None
        self._client = None
        self._initialized = False


__all__ = ["Mem0MemoryProvider"]
