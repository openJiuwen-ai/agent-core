# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Any, Optional

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import (
    BaseMemoryUnit,
    FragmentMemoryUnit,
    MemoryType,
    OperationType,
)
from openjiuwen.core.memory.manage.update.mem_update_checker import (
    MemUpdateChecker,
    MemoryStatus,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


FRAGMENT_MEMORY_TYPE = [
    MemoryType.USER_PROFILE.value,
    MemoryType.SEMANTIC_MEMORY.value,
    MemoryType.EPISODIC_MEMORY.value,
]


def _remove_update_entries_from_process_result(delete_memory_id_set: set[str],
                                                process_result_dict: dict[str, FragmentMemoryUnit]):
    for mem_id in delete_memory_id_set:
        if (mem_id in process_result_dict and
                process_result_dict[mem_id].operation_type == OperationType.UPDATE.value):
            process_result_dict.pop(mem_id)


def _append_mem_unit_list_to_dict(mem_unit_dict: dict[str, FragmentMemoryUnit],
                                   mem_unit_list: list[FragmentMemoryUnit]):
    for mem_unit in mem_unit_list:
        if mem_unit.mem_id in mem_unit_dict:
            memory_logger.warning(
                "mem duplicate, old will be overwrite",
                event_type=LogEventType.MEMORY_STORE,
                memory_id=mem_unit.mem_id,
            )
        mem_unit_dict[mem_unit.mem_id] = mem_unit


class FragmentMemoryManager(BaseMemoryManager):
    UPDATE_CHECK_OLD_MEMORY_NUM = 5
    UPDATE_CHECK_OLD_MEMORY_RELEVANCE_THRESHOLD = 0.75

    def __init__(self,
                 memory_index: BaseMemoryIndex,
                 crypto_key: bytes = None):
        self.memory_index = memory_index
        self.crypto_key = crypto_key
        self.mem_type = "fragment"

    @staticmethod
    def _parse_timestamp(ts) -> datetime:
        if isinstance(ts, datetime):
            return ts
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

    def _convert_to_memory_doc(self, mem_unit: FragmentMemoryUnit) -> MemoryDoc:
        return MemoryDoc(
            id=mem_unit.mem_id,
            text=mem_unit.content,
            type=mem_unit.mem_type.value,
            timestamp=self._parse_timestamp(mem_unit.timestamp) if
            mem_unit.timestamp else datetime.now(timezone.utc).astimezone(),
            fields={
                "source_id": mem_unit.message_mem_id,
            }
        )

    def _doc_to_dict(self, doc: MemoryDoc, score: float = 0.0) -> dict[str, Any]:
        encrypted_content = BaseMemoryManager.encrypt_memory_if_needed(
            key=self.crypto_key,
            plaintext=doc.text
        ) if self.crypto_key else doc.text
        return {
            "id": doc.id,
            "mem": encrypted_content,
            "mem_type": doc.type,
            "timestamp": doc.timestamp,
            "score": score,
            "source_id": doc.fields.get("source_id") if doc.fields else None,
        }

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

        delete_memory_id_set = set()
        process_result_dict: dict[str, FragmentMemoryUnit] = {}
        add_memory_unit_list = []

        # Step 1: Prepare new memories dictionary for checker
        new_mem_units = await self._get_new_mem_units_and_update_memories(
            user_id, scope_id, memories, delete_memory_id_set, process_result_dict)
        new_mem_content = {mem_id: mem_unit.content for mem_id, mem_unit in new_mem_units.items()}

        if not new_mem_units:
            if delete_memory_id_set:
                await self.memory_index.delete_memories(user_id, scope_id, [*delete_memory_id_set])
                _remove_update_entries_from_process_result(delete_memory_id_set, process_result_dict)
            return list(process_result_dict.values())

        # Step 2: Query existing memories for context using search
        old_memories: dict[str, str] = await self._get_related_old_memories(new_mem_content, user_id, scope_id)

        # If no existing memories found, and only has one new memory, skip check and write directly
        if not old_memories and len(new_mem_content) == 1:
            if delete_memory_id_set:
                await self.memory_index.delete_memories(user_id, scope_id, [*delete_memory_id_set])
                _remove_update_entries_from_process_result(delete_memory_id_set, process_result_dict)
            add_memory_unit_list = list(new_mem_units.values())
            add_docs = [self._convert_to_memory_doc(mem_unit) for mem_unit in add_memory_unit_list]
            await self.memory_index.add_memories(user_id, scope_id, add_docs)
            _append_mem_unit_list_to_dict(process_result_dict, add_memory_unit_list)
            return list(process_result_dict.values())

        # Step 3: Use MemChecker to analyze for redundancy/conflicts
        checker = MemUpdateChecker()
        action_items = await checker.check(
            new_memories=new_mem_content,
            old_memories=old_memories,
            base_chat_model=llm,
        )
        memory_logger.info(
            "Memory check completed, got %s action items",
            len(action_items),
            event_type=LogEventType.MEMORY_PROCESS,
            metadata={"action_count": len(action_items)},
        )

        # Step 4: Execute add/delete operations based on action items
        for action_item in action_items:
            if action_item.status == MemoryStatus.ADD:
                if new_mem_units.get(action_item.id):
                    add_memory_unit_list.append(new_mem_units.get(action_item.id))
            elif action_item.status == MemoryStatus.DELETE:
                delete_memory_id_set.add(action_item.id)

        if delete_memory_id_set:
            await self.memory_index.delete_memories(user_id, scope_id, [*delete_memory_id_set])
            _remove_update_entries_from_process_result(delete_memory_id_set, process_result_dict)
        if add_memory_unit_list:
            add_docs = [self._convert_to_memory_doc(mem_unit) for mem_unit in add_memory_unit_list]
            await self.memory_index.add_memories(user_id, scope_id, add_docs)
            _append_mem_unit_list_to_dict(process_result_dict, add_memory_unit_list)
        return list(process_result_dict.values())

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs) -> bool:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot update memory",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        old_doc = await self.memory_index.get_by_id(user_id, scope_id, mem_id)
        if not old_doc:
            return False
        updated_doc = MemoryDoc(
            id=mem_id,
            text=new_memory,
            type=old_doc.type,
            timestamp=datetime.now(timezone.utc).astimezone(),
            fields=old_doc.fields
        )
        await self.memory_index.update_memories(user_id, scope_id, [updated_doc])
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
        mem_types = kwargs.get("mem_types", None)
        result = []
        search_results = await self.memory_index.search(
            user_id=user_id,
            scope_id=scope_id,
            query=query,
            mem_types=mem_types if mem_types else FRAGMENT_MEMORY_TYPE,
            top_k=top_k
        )
        for memory_doc, score in search_results:
            result.append(self._doc_to_dict(memory_doc, score))
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
        return self._doc_to_dict(memory_doc)

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot delete memory",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        doc = await self.memory_index.get_by_id(user_id, scope_id, mem_id)
        if doc is None:
            memory_logger.error(
                "Delete memory failed, memory not found",
                event_type=LogEventType.MEMORY_STORE,
                memory_id=[mem_id],
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

    async def list_fragment_memories(self, user_id: str, scope_id: str, offset, 
                            batch_size, mem_type: Optional[MemoryType] = None) -> list[dict[str, Any]]:
        if not self.memory_index:
            memory_logger.warning(
                "memory_index is not initialized, cannot list memories",
                event_type=LogEventType.MEMORY_RETRIEVE,
                user_id=user_id,
                scope_id=scope_id
            )
            return []
        all_memories = []
        if mem_type:
            if mem_type.value not in FRAGMENT_MEMORY_TYPE:
                memory_logger.error(
                    "%s is not a valid memory type",
                    mem_type.value,
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=user_id,
                    scope_id=scope_id
                )
                return []
            all_memories = await self.memory_index.list_memories(user_id, scope_id, offset,
                                                                 batch_size, [mem_type.value])
        else:
            all_memories = await self.memory_index.list_memories(user_id, scope_id, offset,
                                                                 batch_size, FRAGMENT_MEMORY_TYPE)

        if not all_memories:
            return []

        result = [self._doc_to_dict(doc) for doc in all_memories]
        result.sort(key=lambda x: (x['mem'], str(x.get('timestamp') or '')), reverse=True)
        return result

    async def _get_new_mem_units_and_update_memories(
            self,
            user_id: str,
            scope_id: str,
            memories: dict[str, list[BaseMemoryUnit]],
            delete_memory_id_set: set,
            process_result_dict: dict[str, FragmentMemoryUnit],
    ) -> dict[str, FragmentMemoryUnit]:
        new_mem_units: dict[str, FragmentMemoryUnit] = {}
        update_mem_units: dict[str, FragmentMemoryUnit] = {}
        for mem_type, memory_list in memories.items():
            if mem_type not in FRAGMENT_MEMORY_TYPE:
                continue
            for mem_unit in memory_list:
                if not isinstance(mem_unit, FragmentMemoryUnit):
                    memory_logger.warning(
                        "mem_unit is not a FragmentMemoryUnit",
                        event_type=LogEventType.MEMORY_STORE,
                        memory_type=mem_type,
                        user_id=user_id,
                        scope_id=scope_id
                    )
                    continue

                mem_content = mem_unit.content
                mem_id = mem_unit.mem_id
                operation_type = mem_unit.operation_type
                if operation_type == OperationType.UPDATE and mem_content:
                    if mem_id in update_mem_units:
                        memory_logger.warning(
                            "update memory duplicate, old will be overwrite",
                            event_type=LogEventType.MEMORY_STORE,
                            memory_id=mem_id,
                        )
                    update_mem_units[mem_id] = mem_unit
                elif operation_type == OperationType.DELETE:
                    delete_memory_id_set.add(mem_id)
                    process_result_dict[mem_id] = mem_unit
                elif mem_content:
                    new_mem_units[mem_id] = mem_unit

        if update_mem_units:
            update_docs = [self._convert_to_memory_doc(mem_unit) for mem_unit in update_mem_units.values()]
            await self.memory_index.update_memories(user_id, scope_id, update_docs)
            process_result_dict.update(update_mem_units)

        return new_mem_units

    async def _get_related_old_memories(
            self,
            new_mem_content: dict[str, str],
            user_id: str,
            scope_id: str,
    ) -> dict[str, str]:
        old_memories: dict[str, str] = {}
        old_mem_ids = set()
        for _, new_mem in new_mem_content.items():
            search_results = await self.search(
                user_id=user_id,
                scope_id=scope_id,
                query=new_mem,
                top_k=self.UPDATE_CHECK_OLD_MEMORY_NUM,
            )
            if search_results:
                for result in search_results:
                    result_id = result.get("id", "")
                    result_score = result.get("score", 0)
                    result_content = result.get("mem", "")
                    if (result_id and result_score > self.UPDATE_CHECK_OLD_MEMORY_RELEVANCE_THRESHOLD and
                            result_id not in old_mem_ids):
                        old_memories[result_id] = result_content
                        old_mem_ids.add(result_id)
        return old_memories

    async def _add_memory_to_store(
            self,
            user_id: str,
            scope_id: str,
            memory: FragmentMemoryUnit,
    ):
        if not user_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type.value,
                error_msg="user_id is required",
            )
        if not scope_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type.value,
                error_msg="scope_id is required",
            )
        if not memory.content:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type=memory.mem_type.value,
                error_msg="content is required",
            )

        memory_logger.debug(
            "Add memory",
            memory_type=memory.mem_type.value,
            event_type=LogEventType.MEMORY_STORE,
            user_id=user_id,
            scope_id=scope_id
        )
        memory_doc = self._convert_to_memory_doc(memory)
        await self.memory_index.add_memories(user_id, scope_id, [memory_doc])
