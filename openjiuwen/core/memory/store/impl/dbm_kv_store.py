#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import dbm
import re
import time
import json
import asyncio
from typing import List
from contextlib import asynccontextmanager
import portalocker
from openjiuwen.core.memory.store.base_kv_store import BaseKVStore
from openjiuwen.core.common.logging import logger


class DbmKVStore(BaseKVStore):
    """
    KV store based on dbm
    """
    def __init__(self, filename: str):
        self.filename = filename
        self._async_lock = asyncio.Lock()
        self._lock_file_path = filename + ".lock"

    @asynccontextmanager
    async def lock(self):
        await self._async_lock.acquire()
        try:
            lock_file = await asyncio.to_thread(open, self._lock_file_path, "w")
            try:
                await asyncio.to_thread(portalocker.lock, lock_file, portalocker.LOCK_EX)
                yield
            finally:
                await asyncio.to_thread(portalocker.unlock, lock_file)
                await asyncio.to_thread(lock_file.close)
        finally:
            self._async_lock.release()

    async def set(self, key: str, value: str):
        with dbm.open(self.filename, "c") as db:
            db[key.encode()] = value.encode()

    async def exclusive_set(self, key: str, val: str, expiry: int | None = None) -> bool:
        async with self.lock():
            def sync_op():
                try:
                    with dbm.open(self.filename, "c") as db:
                        key_b = key.encode()
                        now = time.time()
                        existing = db.get(key_b)
                        if existing:
                            try:
                                data = json.loads(existing.decode())
                                expire_at = data.get("expiry")
                                if expire_at is None or expire_at > now:
                                    return False
                            except json.JSONDecodeError:
                                return False
                        expire_at = now + expiry if expiry else None
                        db[key_b] = json.dumps({"value": val, "expiry": expire_at}).encode()
                        return True
                except Exception as e:
                    logger.error(f"[exclusive_set] DBM error: {e}")
                    return False
            return await asyncio.to_thread(sync_op)

    async def get(self, key: str) -> str | None:
        with dbm.open(self.filename, "c") as db:
            v = db.get(key)
            if v is None:
                return None
            return v.decode("utf-8")

    async def exists(self, key: str) -> bool:
        with dbm.open(self.filename, "c") as db:
            return key.encode() in db

    async def delete(self, key: str):
        with dbm.open(self.filename, "c") as db:
            key_b = key.encode()
            if key_b in db:
                del db[key_b]

    async def get_by_prefix(self, prefix: str) -> dict[str, str]:
        regex_str = re.escape(prefix) + ".*"
        pat = re.compile(regex_str)
        result = {}
        with dbm.open(self.filename, "c") as db:
            for key_b in db.keys():
                k = key_b.decode()
                if pat.search(k):
                    result[k] = db[key_b].decode()
        return result

    async def delete_by_prefix(self, prefix: str):
        regex_str = re.escape(prefix) + ".*"
        pat = re.compile(regex_str)
        delete_keys = []
        with dbm.open(self.filename, "c") as db:
            for key_b in db.keys():
                k = key_b.decode()
                if pat.search(k):
                    delete_keys.append(key_b)
            if delete_keys:
                for key_b in delete_keys:
                    del db[key_b]

    async def mget(self, keys: List[str]) -> List[str | None]:
        result = []
        for k in keys:
            v = await self.get(k)
            result.append(v if v else None)
        return result
