# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from datetime import datetime, timezone
from typing import Any, List, Tuple

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import SummaryUnit, BaseMemoryUnit, MemoryType
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class SummaryManager(BaseMemoryManager):

    def __init__(self,
                 memory_index: BaseMemoryIndex,
                 crypto_key: bytes = None):
        self.memory_index = memory_index
        self.crypto_key = crypto_key
        self.mem_type = MemoryType.SUMMARY.value

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime:
        if not ts:
            return datetime.now(timezone.utc).astimezone()
        for fmt in ("%Y-%m-%d %H-%M-%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            pass
        return datetime.now(timezone.utc).astimezone()

    def _convert_to_memory_docs(self, memories: dict[str, list[BaseMemoryUnit]]) -> List[MemoryDoc]:
        memory_docs = []
        for mem_type, memory_list in memories.items():
            if mem_type != self.mem_type:
                continue
            for mem_unit in memory_list:
                if not isinstance(mem_unit, SummaryUnit):
                    continue
                plaintext_content = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self.crypto_key,
                    ciphertext=mem_unit.summary
                ) if self.crypto_key else mem_unit.summary

                memory_doc = MemoryDoc(
                    id=mem_unit.mem_id,
                    text=plaintext_content,
                    type=mem_type,
                    timestamp=self._parse_timestamp(mem_unit.timestamp),
                    fields={
                        "source_id": mem_unit.message_mem_id,
                        "metadata": {}
                    }
                )
                memory_docs.append(memory_doc)
        return memory_docs

    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Tuple[str, Model] | None = None, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot add memories",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        memory_docs = self._convert_to_memory_docs(memories)
        if not memory_docs:
            memory_logger.warning(
                "No valid summary docs to add",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        await self.memory_index.add_memories(user_id, scope_id, memory_docs)

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot update memory",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        memory_doc = await self.memory_index.get_by_id(user_id, scope_id, mem_id)
        if not memory_doc:
            return False
        updated_doc = MemoryDoc(
            id=mem_id,
            text=new_memory,
            type=self.mem_type,
            timestamp=datetime.now(timezone.utc).astimezone(),
            fields=memory_doc.fields
        )
        await self.memory_index.delete_memories(user_id, scope_id, [mem_id])
        await self.memory_index.add_memories(user_id, scope_id, [updated_doc])
        return True

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot delete memory",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        await self.memory_index.delete_memories(user_id, scope_id, [mem_id])
        return True

    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot delete by user_id",
                event_type=LogEventType.MEMORY_STORE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        await self.memory_index.delete_by_user_and_scope(user_id, scope_id)
        return True

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot get memory",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return None
        memory_doc = await self.memory_index.get_by_id(user_id, scope_id, mem_id)
        if not memory_doc:
            return None

        encrypted_content = BaseMemoryManager.encrypt_memory_if_needed(
            key=self.crypto_key,
            plaintext=memory_doc.text
        ) if self.crypto_key else memory_doc.text

        return {
            "id": memory_doc.id,
            "mem": encrypted_content,
            "mem_type": memory_doc.type,
            "timestamp": memory_doc.timestamp,
            "source_id": memory_doc.fields.get("source_id"),
            "metadata": memory_doc.fields.get("metadata")
        }

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot search memories",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        search_results = await self.memory_index.search(
            user_id=user_id,
            scope_id=scope_id,
            query=query,
            mem_type=self.mem_type,
            top_k=top_k
        )

        result = []
        for memory_doc, score in search_results:
            encrypted_content = BaseMemoryManager.encrypt_memory_if_needed(
                key=self.crypto_key,
                plaintext=memory_doc.text
            ) if self.crypto_key else memory_doc.text

            result.append({
                "id": memory_doc.id,
                "mem": encrypted_content,
                "mem_type": memory_doc.type,
                "timestamp": memory_doc.timestamp,
                "score": score,
                "source_id": memory_doc.fields.get("source_id"),
                "metadata": memory_doc.fields.get("metadata")
            })
        return result

    async def list_user_summary(self, user_id: str, scope_id: str) -> list[dict[str, Any]]:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot list user summary",
                event_type=LogEventType.MEMORY_RETRIEVE,
                memory_type=self.mem_type,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        offset = 0
        batch_size = 100
        all_memories = []
        while True:
            batch = await self.memory_index.list_memories(user_id, scope_id, offset, batch_size)
            if not batch:
                break
            all_memories.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size

        summary_memories = [m for m in all_memories if m.type == self.mem_type]

        result = []
        for memory_doc in summary_memories:
            encrypted_content = BaseMemoryManager.encrypt_memory_if_needed(
                key=self.crypto_key,
                plaintext=memory_doc.text
            ) if self.crypto_key else memory_doc.text

            result.append({
                "id": memory_doc.id,
                "mem": encrypted_content,
                "mem_type": memory_doc.type,
                "timestamp": memory_doc.timestamp,
                "source_id": memory_doc.fields.get("source_id"),
                "metadata": memory_doc.fields.get("metadata")
            })

        result.sort(key=lambda x: x['timestamp'], reverse=True)
        return result
