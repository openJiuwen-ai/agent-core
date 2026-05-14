# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Any, List, Optional

from pydantic import BaseModel, Field
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit, FragmentMemoryUnit, MemoryType
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class FragmentMemoryStoreParams(BaseModel):
    mem_type: str = Field(default=MemoryType.USER_PROFILE.value)
    user_id: Optional[str] = None
    scope_id: Optional[str] = None
    content: Optional[str] = None
    source_id: Optional[str] = None


FRAGMENT_MEMORY_TYPE = [MemoryType.USER_PROFILE.value, MemoryType.SEMANTIC_MEMORY.value,
                        MemoryType.EPISODIC_MEMORY.value]


class FragmentMemoryManager(BaseMemoryManager):
    UPDATE_CHECK_OLD_MEMORY_NUM = 5
    UPDATE_CHECK_OLD_MEMORY_RELEVANCE_THRESHOLD = 0.75

    def __init__(self,
                 memory_index: BaseMemoryIndex,
                 data_id_generator: Optional[DataIdManager] = None,
                 crypto_key: bytes = None):
        self.memory_index = memory_index
        self.date_user_profile_id = data_id_generator
        self.crypto_key = crypto_key
        self.mem_type = "fragment"

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

    @staticmethod
    def _process_conflict_info(conflict_info: list[dict], input_memory_ids_map: dict[int, str]) -> list[dict]:
        process_conflict_info = []
        for conflict in conflict_info:
            conf_id = int(conflict['id'])
            conf_mem = conflict['text']
            conf_event = conflict['event']
            if conf_id == 0:
                process_conflict_info.append({
                    "id": '-1',
                    "text": conf_mem,
                    "event": conf_event
                })
                continue
            map_id = input_memory_ids_map[conf_id]
            process_conflict_info.append({
                "id": map_id,
                "text": conf_mem,
                "event": conf_event
            })
        return process_conflict_info

    def _convert_to_memory_docs(self, memories: dict[str, list[BaseMemoryUnit]]) -> List[MemoryDoc]:
        memory_docs = []
        for mem_type, memory_list in memories.items():
            if mem_type not in FRAGMENT_MEMORY_TYPE:
                continue
            for mem_unit in memory_list:
                if not isinstance(mem_unit, FragmentMemoryUnit):
                    continue
                plaintext_content = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self.crypto_key,
                    ciphertext=mem_unit.content
                ) if self.crypto_key else mem_unit.content

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
                      llm: Model | None = None, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot add memories",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        memory_docs = self._convert_to_memory_docs(memories)
        if not memory_docs:
            memory_logger.warning(
                "No valid memory docs to add",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        await self.memory_index.add_memories(user_id, scope_id, memory_docs)

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs) -> bool:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot update memory",
                event_type=LogEventType.MEMORY_STORE,
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
            type=memory_doc.type,
            timestamp=datetime.now(timezone.utc).astimezone(),
            fields=memory_doc.fields
        )
        await self.memory_index.delete_memories(user_id, scope_id, [mem_id])
        await self.memory_index.add_memories(user_id, scope_id, [updated_doc])
        return True

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot search memories",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        mem_type = kwargs.get("mem_type", None)
        if not mem_type:
            mem_types = FRAGMENT_MEMORY_TYPE
        else:
            mem_types = [mem_type]

        result = []
        for mt in mem_types:
            search_results = await self.memory_index.search(
                user_id=user_id,
                scope_id=scope_id,
                query=query,
                mem_type=mt,
                top_k=top_k
            )
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

        result.sort(key=lambda x: x["score"], reverse=True)
        return result[:top_k]

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot get memory",
                event_type=LogEventType.MEMORY_RETRIEVE,
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

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot delete memory",
                event_type=LogEventType.MEMORY_STORE,
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
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        await self.memory_index.delete_by_user_and_scope(user_id, scope_id)
        return True

    async def list_fragment_memories(self, user_id: str, scope_id: str,
                                mem_type: Optional[MemoryType] = None) -> list[dict[str, Any]]:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot list fragment memories",
                event_type=LogEventType.MEMORY_RETRIEVE,
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

        if mem_type:
            if mem_type.value not in FRAGMENT_MEMORY_TYPE:
                memory_logger.error(
                    f"{mem_type.value} is not a valid memory type",
                    memory_type=mem_type,
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=user_id,
                    scope_id=scope_id
                )
                return []
            filtered_memories = [m for m in all_memories if m.type == mem_type.value]
        else:
            filtered_memories = [m for m in all_memories if m.type in FRAGMENT_MEMORY_TYPE]

        result = []
        for memory_doc in filtered_memories:
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
