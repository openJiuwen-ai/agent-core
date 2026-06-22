# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import Any
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.common.kv_prefix_registry import kv_prefix_registry
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore


class ScopeUserMappingManager:
    def __init__(self,
                 sql_db_store: SqlDbStore):
        self.sql_db = sql_db_store
        self.meta_table = "scope_user_mapping"

    async def add(self, user_id: str, scope_id: str, **kwargs):
        data = {
            'user_id': user_id or '',
            'scope_id': scope_id or '',
        }
        exists = await self.sql_db.exist(
            table=self.meta_table,
            conditions={
                "user_id": data["user_id"],
                "scope_id": data["scope_id"],
            },
        )
        if exists:
            return
        await self.sql_db.write(self.meta_table, data)

    async def delete_by_scope_id(self, scope_id: str) -> bool:
        return await self.sql_db.delete(
            table=self.meta_table,
            conditions={"scope_id": scope_id}
        )

    async def get_by_scope_id(self, scope_id: str) -> list[dict[str, Any]] | None:
        results = await self.sql_db.condition_get(
            table=self.meta_table,
            conditions={"scope_id": [scope_id]},
            columns=None
        )
        return results if results else None


_SCOPE_USER_MAPPING_PREFIX = "scope_user_mapping"


class KvScopeUserMappingManager:
    def __init__(self, kv_store: BaseKVStore):
        self._kv_store = kv_store

    async def add(self, user_id: str, scope_id: str, **kwargs):
        key = f"{_SCOPE_USER_MAPPING_PREFIX}/{scope_id}/{user_id}"
        exists = await self._kv_store.exists(key)
        if exists:
            return
        value = json.dumps({"user_id": user_id or '', "scope_id": scope_id or ''})
        await self._kv_store.set(key, value)

    async def delete_by_scope_id(self, scope_id: str) -> bool:
        prefix = f"{_SCOPE_USER_MAPPING_PREFIX}/{scope_id}/"
        await self._kv_store.delete_by_prefix(prefix)
        return True

    async def get_by_scope_id(self, scope_id: str) -> list[dict[str, Any]] | None:
        prefix = f"{_SCOPE_USER_MAPPING_PREFIX}/{scope_id}/"
        result = await self._kv_store.get_by_prefix(prefix)
        if not result:
            return None
        output = []
        for value in result.values():
            if isinstance(value, str):
                output.append(json.loads(value))
            else:
                output.append(json.loads(value.decode()))
        return output


kv_prefix_registry.register_current(_SCOPE_USER_MAPPING_PREFIX)
