# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Any

from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.user_profile_manager import UserProfileManager
from openjiuwen.core.memory.manage.variable_manager import VariableManager
from openjiuwen.core.memory.mem_unit.memory_unit import MemoryType
from openjiuwen.core.memory.store.user_mem_store import UserMemStore


class SearchManager:
    user_mem_manager_list = [MemoryType.USER_PROFILE.value]
    all_mem_manager_list = [item.value for item in MemoryType]

    def __init__(self,
                 managers: dict[str, BaseMemoryManager],
                 user_mem_store: UserMemStore,
                 crypto_key: bytes):
        self.managers = managers
        self.mem_store = user_mem_store
        self.crypto_key = crypto_key

    async def search(self, user_id: str, group_id: str, query: str, top_k: int = 5, threshold: float = 0.3,
                     search_type: Optional[str] = None, **kwargs) -> list[dict[str, Any]] | None:
        # search_type is illegal
        if search_type is not None and search_type not in self.all_mem_manager_list:
            raise ValueError(f"{search_type} is not a valid search type")
        # search_type is valid, but the corresponding manager has not been initialized
        if search_type and not self.managers.get(search_type):
            raise ValueError(f"{search_type} memory manager not inited")
        result = []
        # search_type not specified, traverse available managers
        if search_type is None:
            for mem_type, manager in self.managers.items():
                if mem_type in self.user_mem_manager_list:
                    res = await manager.search(user_id=user_id, group_id=group_id, query=query, top_k=top_k, **kwargs)
                    if res is not None:
                        result.extend(res)
        # call the manager corresponding to search_type
        else:
            res = await self.managers[search_type].search(user_id=user_id, group_id=group_id, query=query, top_k=top_k,
                                                          **kwargs)
            if res:
                result = res
        # sort and truncate multiple search_type results based on score
        if len(result) > top_k:
            result.sort(key=lambda item: item["score"], reverse=True)
        return [item for item in result if item["score"] >= threshold][:top_k]

    async def list_user_mem(self, user_id: str, group_id: str, nums: int, pages: int) -> list[dict[str, Any]] | None:
        list_res = await self.mem_store.get_in_range(user_id, group_id, nums * (pages - 1), nums * pages)
        if not list_res:
            return list_res
        for item in list_res:
            item["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=item["mem"])
            item["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                 ciphertext=item["context_summary"])
        return list_res

    async def list_user_profile(self, user_id: str, group_id: str, profile_type: Optional[str] = None) -> list[dict]:
        if MemoryType.USER_PROFILE.value not in self.managers:
            raise ValueError(f"{MemoryType.USER_PROFILE.value} memory manager not inited")
        if not isinstance(self.managers[MemoryType.USER_PROFILE.value], UserProfileManager):
            raise ValueError(f"{MemoryType.USER_PROFILE.value} manager class is not UserProfileManager")
        return await self.managers[MemoryType.USER_PROFILE.value].list_user_profile(user_id=user_id,
                                                                                    group_id=group_id,
                                                                                    profile_type=profile_type)

    async def get_user_variable(self, user_id: str, group_id: str, var_name: str) -> str | None:
        if MemoryType.VARIABLE.value not in self.managers:
            raise ValueError(f"{MemoryType.VARIABLE.value} memory manager not inited")
        if not isinstance(self.managers[MemoryType.VARIABLE.value], VariableManager):
            raise ValueError(f"{MemoryType.VARIABLE.value} manager class is not VariableManager")
        res = await self.managers[MemoryType.VARIABLE.value].query_variable(user_id=user_id,
                                                                            group_id=group_id, name=var_name)
        if res is None:
            return None
        return res[var_name]

    async def get_all_user_variable(self, user_id: str, group_id: str) -> dict[str, Any]:
        if MemoryType.VARIABLE.value not in self.managers:
            raise ValueError(f"{MemoryType.VARIABLE.value} memory manager not inited")
        if not isinstance(self.managers[MemoryType.VARIABLE.value], VariableManager):
            raise ValueError(f"{MemoryType.VARIABLE.value} manager class is not VariableManager")
        return await self.managers[MemoryType.VARIABLE.value].query_variable(user_id=user_id, group_id=group_id)
