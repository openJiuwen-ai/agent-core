# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class WriteManager:
    def __init__(self, managers: dict[str, BaseMemoryManager], memory_index: BaseMemoryIndex):
        self.managers = managers
        self.memory_index = memory_index

    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Model | None, **kwargs) -> None:
        if not memories:
            memory_logger.debug("No memory units to add", event_type=LogEventType.MEMORY_STORE)
            return

        for manager in set(self.managers.values()):
            try:
                await manager.add_memories(
                    user_id=user_id, scope_id=scope_id, memories=memories, llm=llm, **kwargs)
            except Exception as e:
                memory_logger.error(
                    "Failed to add mem",
                    exception=str(e),
                    memory_type=manager.mem_type,
                    event_type=LogEventType.MEMORY_STORE,
                )
                raise e

    async def update_mem_by_id(self, user_id: str, scope_id: str, mem_id: str, memory: str, **kwargs):
        mem_type = await self.__get_mem_type_from_index(user_id, scope_id, mem_id)
        if mem_type is None:
            memory_logger.warning(
                "Skipping this update due to failure in getting memory type",
                memory_type=mem_type,
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id,
            )
            return
        await self.managers[mem_type].update(user_id=user_id, scope_id=scope_id, mem_id=mem_id,
                                             new_memory=memory, **kwargs)

    async def delete_mem_by_id(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        mem_type = await self.__get_mem_type_from_index(user_id, scope_id, mem_id)
        if mem_type is None:
            memory_logger.warning(
                "Skipping this deletion due to failure in getting memory type",
                memory_type=mem_type,
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return
        await self.managers[mem_type].delete(user_id=user_id, scope_id=scope_id,
                                            mem_id=mem_id, **kwargs)

    async def delete_mem_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        for manager in set(self.managers.values()):
            await manager.delete_by_user_id(user_id=user_id, scope_id=scope_id, **kwargs)

    async def __get_mem_type_from_index(self, user_id: str, scope_id: str, mem_id: str) -> str | None:
        memory_doc = await self.memory_index.get_by_id(user_id, scope_id, mem_id)
        if memory_doc and memory_doc.type:
            mem_type = memory_doc.type
            if mem_type in self.managers:
                return mem_type
            memory_logger.warning(
                "Unsupported mem_type",
                memory_id=[mem_id],
                memory_type=mem_type,
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )

        memory_logger.warning(
            "Nonexistent memory or memory type",
            memory_id=[mem_id],
            event_type=LogEventType.MEMORY_STORE,
            user_id=user_id,
            scope_id=scope_id,
        )
        return None
