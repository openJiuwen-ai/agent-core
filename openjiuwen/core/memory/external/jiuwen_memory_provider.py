# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Jiuwen Memory Provider — unified provider with two switchable backends.

A single ``JiuwenMemoryProvider`` exposes the ``MemoryProvider`` interface while
delegating the actual work to one of two backends selected at construction time:

- ``mode="server"`` (default): talks to a remote ``memory_server`` HTTP service
  (``jiuwen_memory.server.memory_server``) over httpx. No local memory engine is
  built; only a network client is needed. Best for production deployments.
- ``mode="sdk"``: builds a local ``LongTermMemory`` engine in-process via
  ``jiuwen_memory`` and calls it directly. Requires the full ``jiuwen_memory``
  stack (stores + embedding + llm). Best for embedding the memory engine into
  the host process.

Both backends expose the same tools (``ltm_search`` / ``ltm_search_summary``)
and the same system prompt block, so the surrounding ``ExternalMemoryRail`` (or
any other consumer of ``MemoryProvider``) is agnostic to the chosen mode.
"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.memory.external.provider import MemoryProvider


DEFAULT_RECALL_USER_MEM_NUM = 5
DEFAULT_RECALL_HISTORY_MEM_NUM = 3

# Shared tool schemas — identical for both backends.
LTM_SEARCH_SCHEMA = {
    "name": "ltm_search",
    "description": "在长期记忆中搜索相关信息。搜索范围包括用户画像、情景记忆和语义记忆。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询内容"},
            "num": {"type": "integer", "description": "最大返回结果数量", "default": DEFAULT_RECALL_USER_MEM_NUM},
            "threshold": {"type": "number", "description": "最小相关性阈值 (0-1)", "default": 0.3},
        },
        "required": ["query"],
    },
}

LTM_SEARCH_SUMMARY_SCHEMA = {
    "name": "ltm_search_summary",
    "description": "在长期记忆中搜索历史会话摘要。用于回忆之前讨论的话题和达成的结论。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询内容"},
            "num": {"type": "integer", "description": "最大返回结果数量", "default": DEFAULT_RECALL_HISTORY_MEM_NUM},
        },
        "required": ["query"],
    },
}

# Shared system prompt block — identical for both backends.
_SYSTEM_PROMPT_BLOCK = (
    "# Long-Term Memory System\n\n"
    "You have long-term memory capabilities and can remember user information across sessions. "
    "The system automatically extracts valuable information from conversations and stores it in memory.\n\n"
    "## Memory Search\n\n"
    "When you need to recall previous information, use the `ltm_search` tool to search long-term memory.\n"
    "- Search queries should contain key information (names, dates, event keywords)\n"
    "- If results are insufficient, try searching again with different keywords\n\n"
    "## Automatic Memory\n\n"
    "The system automatically extracts from each conversation:\n"
    "- User profile (identity, preferences, habits)\n"
    "- Episodic memory (specific events, decisions)\n"
    "- Semantic memory (background knowledge, technical details)\n"
    "- Conversation summaries (key conclusions, main points)"
)


def _format_prefetch(mem_results: Any, summary_results: Any) -> str:
    """Render combined recall results into a single context string.

    Accepts objects exposing ``.mem_info`` (with ``.type`` / ``.content``) and
    ``.score`` (the ``MemResult`` shape from ``LongTermMemory``), as well as
    plain dicts (the shape returned by the memory_server HTTP API).
    """
    parts: list[str] = []

    def _content(r: Any) -> str:
        if isinstance(r, dict):
            return r.get("content", "")
        info = getattr(r, "mem_info", r)
        return getattr(info, "content", "") if not isinstance(info, dict) else info.get("content", "")

    def _score(r: Any) -> float:
        if isinstance(r, dict):
            return float(r.get("score", 0.0) or 0.0)
        return float(getattr(r, "score", 0.0) or 0.0)

    def _type_label(r: Any) -> str:
        if isinstance(r, dict):
            return str(r.get("type") or "unknown")
        info = getattr(r, "mem_info", r)
        t = getattr(info, "type", None) if not isinstance(info, dict) else info.get("type")
        return t.value if hasattr(t, "value") else str(t or "unknown")

    if mem_results:
        parts.append("## Related Memories")
        for r in mem_results:
            parts.append(f"- [{_type_label(r)}] {_content(r)} (score: {_score(r):.2f})")
    if summary_results:
        if parts:
            parts.append("")
        parts.append("## Related History Summaries")
        for r in summary_results:
            parts.append(f"- {_content(r)} (score: {_score(r):.2f})")
    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# SDK backend — in-process LongTermMemory engine
# ---------------------------------------------------------------------------


class _SDKBackend:
    """Backend that drives a local ``LongTermMemory`` engine in-process."""

    def __init__(
        self,
        config: dict | None = None,
        *,
        kv_store: Any | None = None,
        vector_store: Any | None = None,
        db_store: Any | None = None,
        embedding_model: Any | None = None,
        scope_config: Any | None = None,
        agent_memory_config: Any | None = None,
    ):
        self._config = config or {}
        self._kv_store = kv_store
        self._vector_store = vector_store
        self._db_store = db_store
        self._embedding_model = embedding_model
        self._scope_config = scope_config or self._parse_scope_config()
        self._agent_memory_config = agent_memory_config  # built lazily if None
        self._ltm: Any | None = None
        self._is_initialized = False
        self._user_id = "__default__"
        self._scope_id = "__default__"
        self._session_id = "__default__"

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    def is_available(self) -> bool:
        if self._kv_store and self._vector_store and self._db_store:
            return True
        return bool(self._config.get("embedding", {}).get("model_name"))

    async def initialize(self, **kwargs: Any) -> None:
        self._user_id = kwargs.get("user_id", self._user_id)
        self._scope_id = kwargs.get("scope_id", self._scope_id)
        self._session_id = kwargs.get("session_id", self._session_id)

        if self._kv_store is None:
            self._kv_store = self._create_kv_store()
        if self._vector_store is None:
            self._vector_store = self._create_vector_store()
        if self._db_store is None:
            self._db_store = await self._create_db_store()
        if self._embedding_model is None:
            self._embedding_model = self._create_embedding()

        if not self._kv_store or not self._vector_store or not self._db_store:
            memory_logger.error("[JiuwenMemoryProvider/sdk] store creation failed")
            return

        from jiuwen_memory.memory_core.long_term_memory import LongTermMemory

        self._ltm = LongTermMemory()
        if self._ltm.kv_store is None:
            await self._ltm.register_store(
                kv_store=self._kv_store,
                vector_store=self._vector_store,
                db_store=self._db_store,
                embedding_model=self._embedding_model,
            )

        if self._scope_config:
            await self._ltm.set_scope_config(self._scope_id, self._scope_config)

        self._is_initialized = True

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        if not self._ltm or not self._is_initialized or not query:
            return ""
        user_id = kwargs.get("user_id", self._user_id)
        scope_id = kwargs.get("scope_id", self._scope_id)
        mem_results: list = []
        summary_results: list = []
        try:
            mem_results = await self._ltm.search_user_mem(
                query=query,
                num=DEFAULT_RECALL_USER_MEM_NUM,
                user_id=user_id,
                scope_id=scope_id,
                threshold=0.3,
            )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/sdk] prefetch search_user_mem failed: %s", exc)
        try:
            summary_results = await self._ltm.search_user_history_summary(
                query=query,
                num=DEFAULT_RECALL_HISTORY_MEM_NUM,
                user_id=user_id,
                scope_id=scope_id,
                threshold=0.3,
            )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/sdk] prefetch search_user_history_summary failed: %s", exc)
        return _format_prefetch(mem_results, summary_results)

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        if not self._ltm or not self._is_initialized:
            return
        if not user_msg and not assistant_msg:
            return
        from jiuwen_memory.foundation.llm import UserMessage, AssistantMessage

        agent_config = self._agent_memory_config
        if agent_config is None:
            from jiuwen_memory.memory_core.config.config import AgentMemoryConfig
            agent_config = AgentMemoryConfig()

        messages: list[Any] = []
        if user_msg:
            messages.append(UserMessage(content=user_msg))
        if assistant_msg:
            messages.append(AssistantMessage(content=assistant_msg))
        if not messages:
            return

        try:
            await self._ltm.add_messages(
                messages,
                agent_config,
                user_id=kwargs.get("user_id", self._user_id),
                scope_id=kwargs.get("scope_id", self._scope_id),
                session_id=kwargs.get("session_id", self._session_id),
            )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/sdk] sync_turn add_messages failed: %s", exc)

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._ltm or not self._is_initialized:
            return json.dumps({"error": "Memory provider not initialized"})
        try:
            if tool_name == "ltm_search":
                return await self._handle_search(args)
            if tool_name == "ltm_search_summary":
                return await self._handle_search_summary(args)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:  
            return json.dumps({"error": str(exc), "results": []})

    async def shutdown(self) -> None:
        self._is_initialized = False
        self._ltm = None

    # ---- store/embedding factories (only used when no instance is injected) ----

    def _parse_scope_config(self) -> Any | None:
        scope_cfg = self._config.get("scope_config", {})
        if not scope_cfg:
            return None
        try:
            from jiuwen_memory.memory_core.config.config import MemoryScopeConfig
            return MemoryScopeConfig(**scope_cfg)
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/sdk] failed to parse scope_config: %s", exc)
            return None

    def _create_kv_store(self) -> Any | None:
        kv_cfg = self._config.get("kv", {})
        backend = kv_cfg.get("backend", "memory")
        try:
            if backend == "memory":
                from jiuwen_memory.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
                return InMemoryKVStore()
            if backend == "sqlite":
                from sqlalchemy.ext.asyncio import create_async_engine
                from jiuwen_memory.foundation.store.kv.db_based_kv_store import DbBasedKVStore
                path = kv_cfg.get("path", "memory_kv.db")
                engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
                return DbBasedKVStore(engine)
            if backend == "shelve":
                from jiuwen_memory.foundation.store.kv.shelve_store import ShelveStore
                return ShelveStore(db_path=kv_cfg.get("path", "memory_kv"))
        except Exception as exc:  
            memory_logger.error("[JiuwenMemoryProvider/sdk] kv store creation failed (%s): %s", backend, exc)
        return None

    def _create_vector_store(self) -> Any | None:
        vec_cfg = self._config.get("vector", {})
        backend = vec_cfg.get("backend", "chroma")
        try:
            from jiuwen_memory.foundation.store import create_vector_store
            return create_vector_store(backend, **{k: v for k, v in vec_cfg.items() if k != "backend"})
        except Exception as exc:  
            memory_logger.error("[JiuwenMemoryProvider/sdk] vector store creation failed (%s): %s", backend, exc)
        return None

    async def _create_db_store(self) -> Any | None:
        db_cfg = self._config.get("db", {})
        backend = db_cfg.get("backend", "sqlite")
        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            path = db_cfg.get("path", "memory.db")
            if backend == "sqlite":
                from jiuwen_memory.foundation.store.db.default_db_store import DefaultDbStore
                engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
                return DefaultDbStore(engine)
            if backend == "gaussdb":
                from jiuwen_memory.foundation.store.db.gauss_db_store import GaussDbStore
                engine = create_async_engine(f"gaussdb+async_gaussdb://{path}")
                return GaussDbStore(engine)
        except Exception as exc:  
            memory_logger.error("[JiuwenMemoryProvider/sdk] db store creation failed (%s): %s", backend, exc)
        return None

    def _create_embedding(self) -> Any | None:
        embed_cfg = self._config.get("embedding", {})
        if not embed_cfg.get("model_name"):
            return None
        try:
            from jiuwen_memory.foundation.store.base_embedding import EmbeddingConfig
            from jiuwen_memory.retrieval.embedding.api_embedding import APIEmbedding
            config = EmbeddingConfig(
                model_name=embed_cfg["model_name"],
                base_url=embed_cfg.get("base_url", ""),
                api_key=embed_cfg.get("api_key"),
            )
            return APIEmbedding(config=config)
        except Exception as exc:  
            memory_logger.error("[JiuwenMemoryProvider/sdk] embedding creation failed: %s", exc)
        return None

    async def _handle_search(self, args: dict) -> str:
        results = await self._ltm.search_user_mem(
            query=args.get("query", ""),
            num=args.get("num", DEFAULT_RECALL_USER_MEM_NUM),
            user_id=self._user_id,
            scope_id=self._scope_id,
            threshold=args.get("threshold", 0.3),
        )
        return json.dumps(
            {
                "results": [
                    {
                        "id": r.mem_info.mem_id,
                        "content": r.mem_info.content,
                        "type": r.mem_info.type.value if r.mem_info.type else "unknown",
                        "score": r.score,
                    }
                    for r in results
                ],
                "count": len(results),
            },
            ensure_ascii=False,
        )

    async def _handle_search_summary(self, args: dict) -> str:
        results = await self._ltm.search_user_history_summary(
            query=args.get("query", ""),
            num=args.get("num", DEFAULT_RECALL_HISTORY_MEM_NUM),
            user_id=self._user_id,
            scope_id=self._scope_id,
            threshold=0.3,
        )
        return json.dumps(
            {
                "results": [
                    {
                        "id": r.mem_info.mem_id,
                        "content": r.mem_info.content,
                        "score": r.score,
                    }
                    for r in results
                ],
                "count": len(results),
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Server backend — remote memory_server HTTP client
# ---------------------------------------------------------------------------


class _ServerBackend:
    """Backend that talks to a remote ``memory_server`` over HTTP."""

    def __init__(self, *, base_url: str = "http://127.0.0.1:8000", api_key: str = "", timeout: float = 30.0):
        self._base_url = (base_url or "http://127.0.0.1:8000").rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._http: Any | None = None
        self._is_initialized = False
        self._user_id = "__default__"
        self._scope_id = "__default__"
        self._session_id = "__default__"

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    def is_available(self) -> bool:
        return bool(self._base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def initialize(self, **kwargs: Any) -> None:
        self._user_id = kwargs.get("user_id", self._user_id)
        self._scope_id = kwargs.get("scope_id", self._scope_id)
        self._session_id = kwargs.get("session_id", self._session_id)
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "httpx is required for server mode. Install with `pip install httpx`."
            ) from exc

        self._http = httpx.AsyncClient(base_url=self._base_url, headers=self._headers(), timeout=self._timeout)

        # Optional connectivity check — never fatal, server may be reachable later.
        try:
            resp = await self._http.get("/health")
            if resp.status_code == 200:
                memory_logger.info("[JiuwenMemoryProvider/server] connected to %s", self._base_url)
            else:
                memory_logger.warning(
                    "[JiuwenMemoryProvider/server] health check returned %s at %s",
                    resp.status_code, self._base_url,
                )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/server] health check failed (%s): %s", self._base_url, exc)

        self._is_initialized = True

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        if not self._http or not self._is_initialized or not query:
            return ""
        user_id = kwargs.get("user_id", "__default__")
        scope_id = kwargs.get("scope_id", "__default__")
        mem_results: list = []
        summary_results: list = []
        try:
            mem_results = await self._post_search(
                "/search_memory/", query=query, num=DEFAULT_RECALL_USER_MEM_NUM,
                user_id=user_id, scope_id=scope_id, threshold=0.3,
            )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/server] prefetch search_memory failed: %s", exc)
        try:
            summary_results = await self._post_search(
                "/search_user_history_summary/", query=query, num=DEFAULT_RECALL_HISTORY_MEM_NUM,
                user_id=user_id, scope_id=scope_id, threshold=0.3,
            )
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/server] prefetch search_summary failed: %s", exc)
        return _format_prefetch(mem_results, summary_results)

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        if not self._http or not self._is_initialized:
            return
        messages: list[dict[str, str]] = []
        if user_msg:
            messages.append({"role": "user", "content": user_msg})
        if assistant_msg:
            messages.append({"role": "assistant", "content": assistant_msg})
        if not messages:
            return
        payload = {
            "messages": messages,
            "user_id": kwargs.get("user_id", "__default__"),
            "scope_id": kwargs.get("scope_id", "__default__"),
        }
        try:
            resp = await self._http.post("/add_messages/", json=payload)
            resp.raise_for_status()
        except Exception as exc:  
            memory_logger.warning("[JiuwenMemoryProvider/server] sync_turn add_messages failed: %s", exc)

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._http or not self._is_initialized:
            return json.dumps({"error": "Memory provider not initialized"})
        user_id = self._user_id
        scope_id = self._scope_id
        try:
            if tool_name == "ltm_search":
                results = await self._post_search(
                    "/search_memory/", query=args.get("query", ""),
                    num=args.get("num", DEFAULT_RECALL_USER_MEM_NUM),
                    user_id=user_id, scope_id=scope_id, threshold=args.get("threshold", 0.3),
                )
                return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
            if tool_name == "ltm_search_summary":
                results = await self._post_search(
                    "/search_user_history_summary/", query=args.get("query", ""),
                    num=args.get("num", DEFAULT_RECALL_HISTORY_MEM_NUM),
                    user_id=user_id, scope_id=scope_id, threshold=0.3,
                )
                return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:  
            return json.dumps({"error": str(exc), "results": []})

    async def shutdown(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception as exc:  
                memory_logger.debug("[JiuwenMemoryProvider/server] http client close failed: %s", exc)
        self._http = None
        self._is_initialized = False

    async def _post_search(
        self, path: str, *, query: str, num: int, user_id: str, scope_id: str, threshold: float
    ) -> list[dict]:
        """POST a search request and return the ``results`` list from the response."""
        if self._http is None:
            return []
        resp = await self._http.post(path, json={
            "query": query, "num": num, "user_id": user_id, "scope_id": scope_id, "threshold": threshold,
        })
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        return results if isinstance(results, list) else []


# ---------------------------------------------------------------------------
# Public provider — mode switch + delegation
# ---------------------------------------------------------------------------


class JiuwenMemoryProvider(MemoryProvider):
    """Unified memory provider with two switchable backends.

    Args:
        mode: ``"server"`` (default) to call a remote memory_server HTTP service,
            or ``"sdk"`` to run a local ``LongTermMemory`` engine in-process.
        base_url: memory_server URL. Server mode only.
        api_key: optional bearer token for memory_server auth. Server mode only.
        timeout: HTTP request timeout in seconds. Server mode only.
        config: SDK engine configuration dict (``kv``/``vector``/``db``/
            ``embedding``/``scope_config``). SDK mode only.
        kv_store / vector_store / db_store / embedding_model: pre-built store
            instances injected straight into the SDK engine. SDK mode only.
        scope_config / agent_memory_config: SDK engine tuning. SDK mode only.

    The two backends expose identical tools and prompt blocks; ``mode`` is fixed
    at construction and cannot be switched afterwards.
    """

    def __init__(
        self,
        *,
        mode: str = "server",
        # server-mode kwargs
        base_url: str = "http://127.0.0.1:8000",
        api_key: str = "",
        timeout: float = 30.0,
        # sdk-mode kwargs
        config: dict | None = None,
        kv_store: Any | None = None,
        vector_store: Any | None = None,
        db_store: Any | None = None,
        embedding_model: Any | None = None,
        scope_config: Any | None = None,
        agent_memory_config: Any | None = None,
    ):
        mode = (mode or "server").lower()
        if mode not in ("server", "sdk"):
            raise ValueError(f"Unsupported mode '{mode}', expected 'server' or 'sdk'.")
        self._mode = mode

        if mode == "server":
            self._backend: Any = _ServerBackend(base_url=base_url, api_key=api_key, timeout=timeout)
        else:
            self._backend = _SDKBackend(
                config=config,
                kv_store=kv_store,
                vector_store=vector_store,
                db_store=db_store,
                embedding_model=embedding_model,
                scope_config=scope_config,
                agent_memory_config=agent_memory_config,
            )

    @property
    def name(self) -> str:
        return "jiuwen"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_initialized(self) -> bool:
        return self._backend.is_initialized

    def is_available(self) -> bool:
        """Check if configured and ready. No network calls."""
        return self._backend.is_available()

    async def initialize(self, **kwargs: Any) -> None:
        await self._backend.initialize(**kwargs)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [LTM_SEARCH_SCHEMA, LTM_SEARCH_SUMMARY_SCHEMA]

    def system_prompt_block(self) -> str:
        return _SYSTEM_PROMPT_BLOCK

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return await self._backend.handle_tool_call(tool_name, args)

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        return await self._backend.prefetch(query, **kwargs)

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        await self._backend.sync_turn(user_msg, assistant_msg, **kwargs)

    async def shutdown(self) -> None:
        await self._backend.shutdown()


__all__ = ["JiuwenMemoryProvider", "LTM_SEARCH_SCHEMA", "LTM_SEARCH_SUMMARY_SCHEMA"]
