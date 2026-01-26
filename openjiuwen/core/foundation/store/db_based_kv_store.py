# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import time
from typing import Dict, List, Optional

from sqlalchemy import (
    Column,
    String,
    delete,
    select,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import (
    async_sessionmaker, AsyncEngine, AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore


class Base(DeclarativeBase):
    pass


class KVStoreTable(Base):
    __tablename__ = "kv_store"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class DbBasedKVStore(BaseKVStore):
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.async_session = async_sessionmaker(
            self.engine, expire_on_commit=False,
            class_=AsyncSession
        )
        self.table_created = False

    async def set(self, key: str, value: str):
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            async with session.begin():
                stmt = (
                    insert(KVStoreTable)
                    .values(key=key, value=value)
                    .on_conflict_do_update(
                        index_elements=["key"],
                        set_={"value": value}
                    )
                )
                await session.execute(stmt)

    async def exclusive_set(
        self, key: str, value: str, expiry: Optional[int] = None
    ) -> bool:
        await self._create_table_if_not_exist()
        now = time.time()
        async with self.async_session() as session:
            async with session.begin():
                stmt = select(KVStoreTable).where(
                    KVStoreTable.key == key
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is not None:
                    try:
                        data = json.loads(row.value)
                        old_expire = data.get("expire")
                        if old_expire is None or old_expire > now:
                            return False
                    except json.JSONDecodeError:
                        return False
                expire_at = now + expiry if expiry else None
                val = json.dumps({"value": value, "expiry": expire_at})
                stmt = (
                    insert(KVStoreTable)
                    .values(key=key, value=val)
                    .on_conflict_do_update(
                        index_elements=["key"],
                        set_={"value": val}
                    )
                )
                await session.execute(stmt)
                return True

    async def get(self, key: str) -> str | None:
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            stmt = select(KVStoreTable).where(KVStoreTable.key == key)
            rec = (await session.execute(stmt)).scalar_one_or_none()
            if rec is None:
                return None
            return rec.value

    async def exists(self, key: str) -> bool:
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            stmt = select(KVStoreTable).where(KVStoreTable.key == key)
            rec = (await session.execute(stmt)).scalar_one_or_none()
            return rec is not None

    async def delete(self, key: str):
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(KVStoreTable).where(KVStoreTable.key == key)
                )

    async def get_by_prefix(self, prefix: str) -> dict[str, str]:
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            stmt = select(KVStoreTable).where(
                KVStoreTable.key.startswith(prefix)
            )
            rows = (await session.execute(stmt)).scalars().all()
            result: Dict[str, str] = {}
            for rec in rows:
                result[rec.key] = rec.value
            return result

    async def delete_by_prefix(self, prefix: str):
        await self._create_table_if_not_exist()
        async with self.async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(KVStoreTable).where(
                        KVStoreTable.key.startswith(prefix)
                    )
                )

    async def mget(self, keys: List[str]) -> List[str | None]:
        await self._create_table_if_not_exist()
        if not keys:
            return []
        async with self.async_session() as session:
            stmt = select(KVStoreTable).where(KVStoreTable.key.in_(keys))
            rows = (await session.execute(stmt)).scalars().all()
            lookup: Dict[str, str] = {}
            for rec in rows:
                lookup[rec.key] = rec.value
            return [lookup.get(k) for k in keys]

    async def _create_table_if_not_exist(self) -> None:
        if self.table_created:
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.table_created = True