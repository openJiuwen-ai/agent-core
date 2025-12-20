#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.mem_unit.memory_unit import BaseMemoryUnit
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.store.user_mem_store import UserMemStore


class WriteManager:
    def __init__(self, managers: dict[str, BaseMemoryManager], mem_store: UserMemStore):
        self.managers = managers
        self.mem_store = mem_store

    async def add_mem(self, mem_units: list[BaseMemoryUnit]):
        for mem_unit in mem_units:
            mem_type = mem_unit.mem_type.value
            if mem_type in self.managers:
                await self.managers[mem_type].add(mem_unit)
            else:
                logger.warning(f"Unsupported memory type: {mem_type}")

    async def update_mem_by_id(self, user_id: str, group_id: str, mem_id: str, memory: str):
        mem_type = await self.__get_mem_type_from_store(user_id, group_id, mem_id)
        if mem_type is None:
            logger.warning(f"Skipping this update due to failure in getting memory type, mem_id:{mem_id}, "
                           f"user_id:{user_id}, group_id:{group_id}")
            return
        await self.managers[mem_type].update(user_id, group_id, mem_id, memory)

    async def delete_mem_by_id(self, user_id: str, group_id: str, mem_id: str):
        mem_type = await self.__get_mem_type_from_store(user_id, group_id, mem_id)
        if mem_type is None:
            logger.warning(f"Skipping this deletion due to failure in getting memory type, mem_id:{mem_id}, "
                           f"user_id:{user_id}, group_id:{group_id}")
            return
        await self.managers[mem_type].delete(user_id, group_id, mem_id)

    async def delete_mem_by_user_id(self, user_id: str, group_id: str):
        for manager in self.managers:
            await self.managers[manager].delete_by_user_id(user_id=user_id, group_id=group_id)

    async def __get_mem_type_from_store(self, user_id: str, group_id: str, mem_id: str) -> str | None:
        data = None
        try:
            data = await self.mem_store.get(user_id=user_id, group_id=group_id, mem_id=mem_id)
        except Exception as e:
            logger.error(f"Failed to get memory: {e}")
            return None
        if data is None:
            logger.warning(f"Nonexistent memory, mem_id:{mem_id}, user_id:{user_id}, group_id:{group_id}")
            return None
        if "mem_type" not in data:
            logger.warning(f"The mem_type field doesn't exist, mem_id:{mem_id}, user_id:{user_id}, group_id:{group_id}")
            return None
        mem_type = data['mem_type']
        if mem_type not in self.managers:
            logger.warning(f"Unsupported mem_type:{mem_type}, mem_id:{mem_id}, user_id:{user_id}, group_id:{group_id}")
            return None
        return mem_type