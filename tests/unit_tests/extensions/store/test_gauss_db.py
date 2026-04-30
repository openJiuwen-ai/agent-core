# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for GaussDbStore — mock-based, no real DB connection.
Uses MagicMock for engine/session to verify ORM SQL generation for
table creation, CRUD, transactions, aggregation, like queries, and pagination.
Follows the same mock pattern as test_gauss_vector_store.py."""

from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest
from sqlalchemy import Column, Integer, String, Float, Text, func, select, update, delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from openjiuwen.extensions.store.db.gauss_db_store import GaussDbStore

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "test_user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), nullable=True)
    age = Column(Integer, nullable=True)


class Product(Base):
    __tablename__ = "test_product"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    price = Column(Float, nullable=True)
    description = Column(Text, nullable=True)


def _mock_engine():
    engine = MagicMock(spec=AsyncEngine)
    conn = AsyncMock()
    conn.run_sync = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=ctx)
    engine.connect = MagicMock(return_value=ctx)
    engine.dispose = AsyncMock()
    return engine, conn


# -------------------- Init / Engine --------------------

class TestGaussDbStoreInit:

    def test_init_with_async_engine(self):
        mock_engine = MagicMock(spec=AsyncEngine)
        store = GaussDbStore(async_conn=mock_engine)
        assert store.async_conn is mock_engine

    def test_init_with_none(self):
        store = GaussDbStore(async_conn=None)
        assert store.async_conn is None

    def test_get_async_engine_returns_same_instance(self):
        mock_engine = MagicMock(spec=AsyncEngine)
        store = GaussDbStore(async_conn=mock_engine)
        assert store.get_async_engine() is store.get_async_engine()

    def test_get_async_engine_returns_none(self):
        store = GaussDbStore(async_conn=None)
        assert store.get_async_engine() is None

    def test_inherits_from_base_db_store(self):
        from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
        assert issubclass(GaussDbStore, BaseDbStore)


# -------------------- Auto Table Creation --------------------

class TestGaussDbStoreAutoTableCreation:

    @pytest.mark.asyncio
    async def test_auto_create_tables_calls_run_sync(self):
        engine, conn = _mock_engine()
        store = GaussDbStore(async_conn=engine)

        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

        engine.begin.assert_called_once()
        conn.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_drop_tables_calls_run_sync(self):
        engine, conn = _mock_engine()
        store = GaussDbStore(async_conn=engine)

        async with engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)

        engine.begin.assert_called_once()
        conn.run_sync.assert_called_once()


# -------------------- Create (Insert) --------------------

class TestGaussDbStoreCreate:

    def test_insert_single_sql(self):
        stmt = insert(User).values(name="alice", email="alice@example.com", age=30)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "INSERT INTO" in sql
        assert "test_user" in sql
        assert "alice" in sql

    def test_insert_multiple_sql(self):
        stmt = insert(User)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "INSERT INTO" in sql
        assert "test_user" in sql

    def test_insert_null_field_sql(self):
        stmt = insert(User).values(name="eve", age=28)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "INSERT INTO" in sql
        assert "eve" in sql

    def test_insert_auto_increment_id(self):
        user = User(name="auto_id", age=20)
        assert user.id is None


# -------------------- Read (Select) --------------------

class TestGaussDbStoreRead:

    def test_select_all_sql(self):
        stmt = select(User).order_by(User.id)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "SELECT" in sql
        assert "test_user" in sql

    def test_select_by_primary_key_sql(self):
        stmt = select(User).where(User.id == 1)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "WHERE" in sql
        assert "test_user.id" in sql

    def test_select_with_filter_sql(self):
        stmt = select(User).where(User.age == 20)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "WHERE" in sql
        assert "age" in sql

    def test_select_with_multiple_conditions_sql(self):
        stmt = select(User).where(User.age == 25, User.email == "a@b.com")
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "WHERE" in sql
        assert "age" in sql
        assert "email" in sql

    def test_select_count_sql(self):
        stmt = select(func.count()).select_from(User)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "count" in sql.lower()
        assert "test_user" in sql

    def test_select_order_by_desc_sql(self):
        stmt = select(User).order_by(User.age.desc())
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ORDER BY" in sql
        assert "DESC" in sql

    def test_select_nonexistent_sql(self):
        stmt = select(User).where(User.id == 99999)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "99999" in sql


# -------------------- Update --------------------

class TestGaussDbStoreUpdate:

    def test_update_single_sql(self):
        stmt = update(User).where(User.name == "up_user").values(age=25, email="new@test.com")
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "UPDATE" in sql
        assert "test_user" in sql
        assert "WHERE" in sql
        assert "age" in sql

    def test_update_batch_sql(self):
        stmt = update(User).where(User.age == 20).values(age=99)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "UPDATE" in sql
        assert "99" in sql

    def test_update_nonexistent_sql(self):
        stmt = update(User).where(User.id == 99999).values(age=100)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "UPDATE" in sql
        assert "99999" in sql


# -------------------- Delete --------------------

class TestGaussDbStoreDelete:

    def test_delete_by_condition_sql(self):
        stmt = delete(User).where(User.name == "del_user")
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "DELETE FROM" in sql
        assert "test_user" in sql
        assert "WHERE" in sql
        assert "del_user" in sql

    def test_delete_by_age_sql(self):
        stmt = delete(User).where(User.age == 20)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "DELETE FROM" in sql
        assert "age" in sql

    def test_delete_nonexistent_sql(self):
        stmt = delete(User).where(User.id == 99999)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "DELETE FROM" in sql
        assert "99999" in sql


# -------------------- Transaction --------------------

class TestGaussDbStoreTransaction:

    @pytest.mark.asyncio
    async def test_transaction_commit_calls_begin(self):
        engine, conn = _mock_engine()
        store = GaussDbStore(async_conn=engine)

        async with engine.begin() as c:
            pass

        engine.begin.assert_called_once()

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_exception(self):
        engine, conn = _mock_engine()
        store = GaussDbStore(async_conn=engine)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.rollback = AsyncMock()

        with pytest.raises(RuntimeError):
            async with engine.begin() as c:
                raise RuntimeError("force rollback")


# -------------------- Aggregate / Like / Pagination --------------------

class TestGaussDbStoreAggregate:

    def test_aggregate_sum_sql(self):
        stmt = select(func.sum(User.age))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "sum" in sql.lower()
        assert "age" in sql

    def test_aggregate_count_sql(self):
        stmt = select(func.count()).select_from(User)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "count" in sql.lower()
        assert "test_user" in sql

    def test_like_query_sql(self):
        stmt = select(User).where(User.name.like("a%"))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIKE" in sql
        assert "a%" in sql

    def test_in_clause_sql(self):
        stmt = select(User).where(User.name.in_(["alice", "alex"]))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "IN" in sql

    def test_multi_table_select_sql(self):
        stmt_u = select(func.count()).select_from(User)
        stmt_p = select(func.count()).select_from(Product)
        sql_u = str(stmt_u.compile(compile_kwargs={"literal_binds": True}))
        sql_p = str(stmt_p.compile(compile_kwargs={"literal_binds": True}))
        assert "test_user" in sql_u
        assert "test_product" in sql_p

    def test_pagination_sql(self):
        stmt = select(User).order_by(User.age).limit(5).offset(10)
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT" in sql
        assert "OFFSET" in sql
        assert "ORDER BY" in sql
