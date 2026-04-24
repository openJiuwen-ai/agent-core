# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenJiuwen Memory Provider — based on LongTermMemory."""

import json
from typing import Any

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, BaseMessage
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
from openjiuwen.core.foundation.store.base_embedding import Embedding, EmbeddingConfig
from openjiuwen.core.memory.config.config import (
    AgentMemoryConfig,
    MemoryEngineConfig,
    MemoryScopeConfig,
)
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.external.provider import MemoryProvider


DEFAULT_RECALL_USER_MEM_NUM = 5
DEFAULT_RECALL_HISTORY_MEM_NUM = 3

LTM_SEARCH_SCHEMA = {
    "name": "ltm_search",
    "description": "在长期记忆中搜索相关信息。搜索范围包括用户用户画像、情景记忆和语义记忆。",
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


class OpenJiuwenMemoryProvider(MemoryProvider):
    """External memory provider based on openjiuwen LongTermMemory."""

    _DEFAULT_KV_BACKEND = "memory"
    _DEFAULT_VECTOR_BACKEND = "chroma"
    _DEFAULT_DB_BACKEND = "sqlite"

    def __init__(
        self,
        config: dict | None = None,
        *,
        kv_store: BaseKVStore | None = None,
        vector_store: BaseVectorStore | None = None,
        db_store: BaseDbStore | None = None,
        embedding_model: Embedding | None = None,
        engine_config: MemoryEngineConfig | None = None,
        scope_config: MemoryScopeConfig | None = None,
        agent_memory_config: AgentMemoryConfig | None = None,
    ):
        self._config = config or {}
        self._kv_store = kv_store
        self._vector_store = vector_store
        self._db_store = db_store
        self._embedding_model = embedding_model
        self._engine_config = engine_config
        self._scope_config = scope_config or self._parse_scope_config()
        self._agent_memory_config = agent_memory_config or AgentMemoryConfig()
        self._ltm: LongTermMemory | None = None
        self._is_initialized = False
        self._user_id = "__default__"
        self._scope_id = "__default__"
        self._session_id = "__default__"

    @property
    def name(self) -> str:
        return "openjiuwen"

    def is_available(self) -> bool:
        if self._kv_store and self._vector_store and self._db_store:
            return True
        return bool(self._config.get("embedding", {}).get("model_name"))

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self, **kwargs) -> None:
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
            logger.error("[OpenJiuwenMemoryProvider] Store creation failed")
            return

        self._ltm = LongTermMemory()

        if self._ltm.kv_store is None:
            await self._ltm.register_store(
                kv_store=self._kv_store,
                vector_store=self._vector_store,
                db_store=self._db_store,
                embedding_model=self._embedding_model,
            )

        if self._scope_config and self._scope_id != "__default__":
            await self._ltm.set_scope_config(self._scope_id, self._scope_config)

        self._is_initialized = True

    def system_prompt_block(self) -> str:
        return (
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

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [LTM_SEARCH_SCHEMA, LTM_SEARCH_SUMMARY_SCHEMA]

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._ltm or not self._is_initialized:
            return json.dumps({"error": "Memory provider not initialized"})
        try:
            if tool_name == "ltm_search":
                return await self._handle_search(args)
            elif tool_name == "ltm_search_summary":
                return await self._handle_search_summary(args)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            return json.dumps({"error": str(e), "results": []})

    async def prefetch(self, query: str, **kwargs) -> str:
        if not self._ltm or not self._is_initialized:
            return ""
        user_id = kwargs.get("user_id", self._user_id)
        scope_id = kwargs.get("scope_id", self._scope_id)
        parts = []
        try:
            mem_results = await self._ltm.search_user_mem(
                query=query,
                num=DEFAULT_RECALL_USER_MEM_NUM,
                user_id=user_id,
                scope_id=scope_id,
                threshold=0.3,
            )
            if mem_results:
                parts.append("## Related Memories")
                for r in mem_results:
                    type_label = r.mem_info.type.value if r.mem_info.type else "unknown"
                    parts.append(f"- [{type_label}] {r.mem_info.content} (score: {r.score:.2f})")
        except Exception as e:
            logger.warning(f"prefetch search_user_mem failed: {e}")
        try:
            summary_results = await self._ltm.search_user_history_summary(
                query=query,
                num=DEFAULT_RECALL_HISTORY_MEM_NUM,
                user_id=user_id,
                scope_id=scope_id,
                threshold=0.3,
            )
            if summary_results:
                parts.append("\n## Related History Summaries")
                for r in summary_results:
                    parts.append(f"- {r.mem_info.content} (score: {r.score:.2f})")
        except Exception as e:
            logger.warning(f"prefetch search_user_history_summary failed: {e}")
        return "\n".join(parts) if parts else ""

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        if not self._ltm or not self._is_initialized:
            return
        user_id = kwargs.get("user_id", self._user_id)
        scope_id = kwargs.get("scope_id", self._scope_id)
        session_id = kwargs.get("session_id", self._session_id)
        messages: list[BaseMessage] = []
        if user_msg:
            messages.append(UserMessage(content=user_msg))
        if assistant_msg:
            messages.append(AssistantMessage(content=assistant_msg))
        if not messages:
            return
        try:
            await self._ltm.add_messages(
                messages,
                self._agent_memory_config,
                user_id=user_id,
                scope_id=scope_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"sync_turn add_messages failed: {e}")

    async def shutdown(self) -> None:
        self._is_initialized = False

    def _parse_scope_config(self) -> MemoryScopeConfig | None:
        scope_cfg = self._config.get("scope_config", {})
        if not scope_cfg:
            return None
        try:
            return MemoryScopeConfig(**scope_cfg)
        except Exception as e:
            logger.warning(f"[OpenJiuwenMemoryProvider] Failed to parse scope_config: {e}")
            return None

    def _create_kv_store(self) -> BaseKVStore | None:
        kv_cfg = self._config.get("kv", {})
        backend = kv_cfg.get("backend", self._DEFAULT_KV_BACKEND)
        try:
            if backend == "memory":
                from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
                return InMemoryKVStore()
            elif backend == "sqlite":
                from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
                from sqlalchemy.ext.asyncio import create_async_engine
                path = kv_cfg.get("path", "memory_kv.db")
                engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
                return DbBasedKVStore(engine)
            elif backend == "shelve":
                from openjiuwen.core.foundation.store.kv.shelve_store import ShelveStore
                return ShelveStore(db_path=kv_cfg.get("path", "memory_kv"))
        except Exception as e:
            logger.error(f"[OpenJiuwenMemoryProvider] KV store creation failed ({backend}): {e}")
        return None

    def _create_vector_store(self) -> BaseVectorStore | None:
        vec_cfg = self._config.get("vector", {})
        backend = vec_cfg.get("backend", self._DEFAULT_VECTOR_BACKEND)
        try:
            from openjiuwen.core.foundation.store import create_vector_store
            return create_vector_store(
                backend, **{k: v for k, v in vec_cfg.items() if k != "backend"}
            )
        except Exception as e:
            logger.error(f"[OpenJiuwenMemoryProvider] Vector store creation failed ({backend}): {e}")
        return None

    async def _create_db_store(self) -> BaseDbStore | None:
        db_cfg = self._config.get("db", {})
        backend = db_cfg.get("backend", self._DEFAULT_DB_BACKEND)
        try:
            if backend == "sqlite":
                from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
                from sqlalchemy.ext.asyncio import create_async_engine
                path = db_cfg.get("path", "memory.db")
                engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
                return DefaultDbStore(engine)
        except Exception as e:
            logger.error(f"[OpenJiuwenMemoryProvider] DB store creation failed ({backend}): {e}")
        return None

    def _create_embedding(self) -> Embedding | None:
        embed_cfg = self._config.get("embedding", {})
        if not embed_cfg.get("model_name"):
            return None
        try:
            from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
            config = EmbeddingConfig(
                model_name=embed_cfg["model_name"],
                base_url=embed_cfg.get("base_url", ""),
                api_key=embed_cfg.get("api_key"),
            )
            return APIEmbedding(config=config)
        except Exception as e:
            logger.error(f"[OpenJiuwenMemoryProvider] Embedding creation failed: {e}")
        return None

    async def _handle_search(self, args: dict) -> str:
        results = await self._ltm.search_user_mem(
            query=args.get("query", ""),
            num=args.get("num", 5),
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
            num=args.get("num", 3),
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
