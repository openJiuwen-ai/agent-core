# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import time
from typing import Optional, List, Dict

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore


class InMemoryKVStore(BaseKVStore):
    def __init__(self):
        self._store: dict[str, tuple[str, Optional[int]]] = {}  # (value, expiry_timestamp or None)
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: str):
        """Set a key-value pair."""
        async with self._lock:
            self._store[key] = (value, None)

    async def get(self, key: str) -> str | None:
        """Get value by key. Returns None if key doesn't exist or is expired."""
        async with self._lock:
            return await self._get_without_lock(key)

    async def delete(self, key: str):
        """Delete a key."""
        async with self._lock:
            if key in self._store:
                del self._store[key]

    async def exclusive_set(self, key: str, value: str, expiry: int | None = None) -> bool:
        """
        Set the key only if it does NOT already exist (even if expired).
        However, if the key exists but is expired, we ALLOW setting it.
        """
        async with self._lock:
            current_time = time.time()
            if key in self._store:
                _, expiry_ts = self._store[key]
                if expiry_ts is not None and current_time > expiry_ts:
                    # Expired: allow to overwrite
                    pass
                else:
                    # Not expired: reject
                    return False

            # Either not present or expired → set it
            expiry_ts = int(current_time + expiry) if expiry is not None else None
            self._store[key] = (value, expiry_ts)
            return True

    async def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return await self.get(key) is not None

    async def get_by_prefix(self, prefix: str) -> Dict[str, str]:
        async with self._lock:
            return {
                k: await self._get_without_lock(k)
                for k in self._store.keys()
                if k.startswith(prefix)
            }

    async def delete_by_prefix(self, prefix: str) -> None:
        async with self._lock:
            to_del = [k for k in self._store if k.startswith(prefix)]
            for k in to_del:
                del self._store[k]

    async def mget(self, keys: List[str]) -> List[Optional[str]]:
        async with self._lock:
            return [await self._get_without_lock(k) for k in keys]

    async def _get_without_lock(self, key: str) -> str | None:
        if key not in self._store:
            return None
        value, expiry_ts = self._store[key]
        if expiry_ts is not None and time.time() > expiry_ts:
            # Note: we do NOT auto-delete expired keys to allow re-set later
            return None
        return value
