# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import time
from typing import Dict, List, Optional

from openjiuwen.core.memory.store.base_kv_store import BaseKVStore


class MockKVStore(BaseKVStore):

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _get(self, key: str) -> Optional[str]:
        val = self._data.get(key, None)
        if val is None:
            return None
        return val

    async def set(self, key: str, value: str) -> None:
        async with self._lock:
            self._data[key] = value

    async def exclusive_set(
        self, key: str, value: str, expiry: Optional[int] = None
    ) -> bool:
        now = time.time()
        expire_at = int(time.time() + expiry) if expiry else None
        async with self._lock:
            if self._get(key) is not None:
                try:
                    data = json.loads(self._get(key))
                    old_expire = data.get("expire")
                    if old_expire is None or old_expire > now:
                        return False
                except json.JSONDecodeError:
                    return False
            self._data[key] = json.dumps({"value": value, "expiry": expire_at})
            return True

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            return self._get(key)

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def get_by_prefix(self, prefix: str) -> Dict[str, str]:
        async with self._lock:
            return {
                k: v
                for k, v in self._data.items()
                if k.startswith(prefix)
            }

    async def delete_by_prefix(self, prefix: str) -> None:
        async with self._lock:
            to_del = [k for k in self._data if k.startswith(prefix)]
            for k in to_del:
                self._data.pop(k, None)

    async def mget(self, keys: List[str]) -> List[Optional[str]]:
        async with self._lock:
            return [self._get(k) for k in keys]
