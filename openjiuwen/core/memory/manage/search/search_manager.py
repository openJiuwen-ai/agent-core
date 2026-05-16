# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Optional, Any

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.index.summary_manager import SummaryManager
from openjiuwen.core.memory.manage.index.fragment_memory_manager import FragmentMemoryManager, FRAGMENT_MEMORY_TYPE
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class SearchParams(BaseModel):
    user_id: str
    scope_id: str
    query: str
    top_k: int = Field(default=5, description="返回的最大结果数")
    threshold: float = Field(default=0.3, description="匹配阈值")
    search_type: Optional[list[str]] = Field(default=None, description="搜索类型")


class SearchManager:
    all_mem_manager_list = [item.value for item in MemoryType]

    def __init__(self,
                 managers: dict[str, BaseMemoryManager],
                 crypto_key: bytes,
                 memory_index: BaseMemoryIndex):
        self.managers = managers
        self.crypto_key = crypto_key
        self.memory_index = memory_index

    async def search(self, params: SearchParams, **kwargs) -> list[dict[str, Any]] | None:
        user_id = params.user_id
        scope_id = params.scope_id
        query = params.query
        top_k = params.top_k
        threshold = params.threshold
        search_type = params.search_type
        kwargs['mem_types'] = search_type
        # search_type is illegal
        if search_type is not None:
            for st in search_type:
                if st not in self.all_mem_manager_list:
                    raise build_error(
                        StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                        memory_type=st,
                        error_msg=f"{st} is not a valid search type",
                    )
        # search_type is valid, but the corresponding manager has not been initialized
        used_types = {}
        if search_type is not None:
            for st in search_type:
                if st and not self.managers.get(st):
                    raise build_error(
                        StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                        memory_type=st,
                        error_msg=f"{st} memory manager not inited",
                    )
                manager = self.managers[st]
                if manager not in used_types:
                    used_types[manager] = []
                used_types[manager].append(st)
        result = []
        # search_type not specified, traverse available managers
        if search_type is None:
            for manager in set(self.managers.values()):
                res = await manager.search(user_id=user_id, scope_id=scope_id, query=query, top_k=top_k, **kwargs)
                if res is not None:
                    result.extend(res)
        # call the manager corresponding to search_type
        else:
            for manager, types in used_types.items():
                kwargs['mem_types'] = types
                res = await manager.search(user_id=user_id, scope_id=scope_id,
                                                      query=query, top_k=top_k, **kwargs)
                if res:
                    result.extend(res)
        
        # sort and truncate multiple search_type results based on score
        if len(result) > top_k:
            result.sort(key=lambda item: item["score"], reverse=True)
        return [item for item in result if item["score"] >= threshold][:top_k]

    async def list_user_mem(
        self,
        user_id: str,
        scope_id: str,
        nums: int,
        pages: int,
        mem_type: str = None
    ) -> list[dict[str, Any]] | None:
        result = []
        start = nums * (pages - 1)
        if not self.memory_index:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="search_memory",
                error_msg=f"memory index not inited",
            )
        if mem_type:
            result = await self.memory_index.list_memories(user_id, scope_id, start, nums, [mem_type])
        else:
            result = await self.memory_index.list_memories(user_id, scope_id, start, nums, [])
        result = [{
            "id": res.id,
            "user_id": user_id,
            "scope_id": scope_id,
            "mem": res.text,
            "mem_type": res.type,
            "timestamp": res.timestamp,
            **res.fields,
        } for res in result]
        return result

    async def list_user_profile(self, user_id: str, scope_id: str) -> list[dict]:
        if any(item not in self.managers for item in FRAGMENT_MEMORY_TYPE):
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="fragment_memory",
                error_msg=f"fragment memory manager not inited",
            )
        if not isinstance(self.managers[MemoryType.USER_PROFILE.value], FragmentMemoryManager):
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type="fragment_memory",
                error_msg=f"fragment memory manager class is not FragmentMemoryManager",
            )
        return await self.managers[MemoryType.USER_PROFILE.value].list_fragment_memories(
            user_id=user_id,
            scope_id=scope_id
        )

    async def list_user_summary(self, user_id: str, scope_id: str) -> list[dict]:
        if MemoryType.SUMMARY.value not in self.managers:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.SUMMARY.value,
                error_msg=f"{MemoryType.SUMMARY.value} memory manager not inited",
            )
        if not isinstance(self.managers[MemoryType.SUMMARY.value], SummaryManager):
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.SUMMARY.value,
                error_msg=f"{MemoryType.SUMMARY.value} manager class is not SummaryManager",
            )
        return await self.managers[MemoryType.SUMMARY.value].list_user_summary(user_id=user_id,
                                                                                    scope_id=scope_id)

    async def get_user_variable(self, user_id: str, scope_id: str, var_name: str) -> str | None:
        if MemoryType.VARIABLE.value not in self.managers:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.VARIABLE.value,
                error_msg=f"{MemoryType.VARIABLE.value} memory manager not inited",
            )
        if not isinstance(self.managers[MemoryType.VARIABLE.value], VariableManager):
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.VARIABLE.value,
                error_msg=f"{MemoryType.VARIABLE.value} manager class is not VariableManager",
            )
        res = await self.managers[MemoryType.VARIABLE.value].query_variable(user_id=user_id,
                                                                            scope_id=scope_id, name=var_name)
        if res is None:
            return None
        return res[var_name] if var_name in res else None

    async def get_all_user_variable(self, user_id: str, scope_id: str) -> dict[str, Any]:
        if MemoryType.VARIABLE.value not in self.managers:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.VARIABLE.value,
                error_msg=f"{MemoryType.VARIABLE.value} memory manager not inited",
            )
        if not isinstance(self.managers[MemoryType.VARIABLE.value], VariableManager):
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                memory_type=MemoryType.VARIABLE.value,
                error_msg=f"{MemoryType.VARIABLE.value} manager class is not VariableManager",
            )
        return await self.managers[MemoryType.VARIABLE.value].query_variable(user_id=user_id, scope_id=scope_id)
