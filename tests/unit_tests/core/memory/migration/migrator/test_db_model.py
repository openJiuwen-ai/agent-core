# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.memory.manage.mem_model.db_model import create_tables
from openjiuwen.core.memory.migration.migration_plan import sql_registry
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import AddColumnOperation


@pytest.fixture(name="test_store")
def store_fixture():
    """Create a temporary test database"""
    utc_now = datetime.now(timezone.utc)
    time_str = utc_now.strftime("%Y%m%d%H%M%S")
    uuid_str = uuid.uuid4().hex[:6]
    path = Path(f"./test_db_model_{time_str}_{uuid_str}.db").resolve()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    db_store = DefaultDbStore(engine)
    sql_store = SqlDbStore(db_store)

    yield sql_store

    asyncio.run(engine.dispose())
    if path.exists():
        path.unlink()


@pytest.fixture(name="clean_store")
def clean_store_fixture():
    """Create a clean test database without any tables"""
    utc_now = datetime.now(timezone.utc)
    time_str = utc_now.strftime("%Y%m%d%H%M%S")
    uuid_str = uuid.uuid4().hex[:6]
    path = Path(f"./test_db_model_clean_{time_str}_{uuid_str}.db").resolve()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    db_store = DefaultDbStore(engine)

    yield db_store

    asyncio.run(engine.dispose())
    if path.exists():
        path.unlink()


class TestCreateTablesSchemaVersion:
    """Test cases for create_tables schema version functionality"""

    @pytest.mark.asyncio
    async def test_create_tables_creates_all_tables(self, clean_store):
        """Test that create_tables creates all required tables"""
        engine = clean_store.get_async_engine()
        
        # Check that no tables exist initially
        async with engine.begin() as conn:
            def check_tables(sync_conn):
                inspector = inspect(sync_conn)
                tables = inspector.get_table_names()
                return tables
            
            initial_tables = await conn.run_sync(check_tables)
            assert len(initial_tables) == 0
        
        # Create tables
        await create_tables(clean_store)
        
        # Check that all tables were created
        async with engine.begin() as conn:
            def check_tables(sync_conn):
                inspector = inspect(sync_conn)
                tables = inspector.get_table_names()
                return tables
            
            created_tables = await conn.run_sync(check_tables)
            
            assert "memory_meta" in created_tables
            assert "user_message" in created_tables
            assert "scope_user_mapping" in created_tables

    @pytest.mark.asyncio
    async def test_create_tables_with_registered_operations(self, clean_store):
        """Test that create_tables uses correct schema_version from registry"""
        # Register some operations with specific versions
        sql_registry.register(
            "user_messages",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=5, description="Test operation"),
                table="user_message",
                column_name="test_col",
                column_type="String"
            )
        )
        
        sql_registry.register(
            "scope_user_mapping",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=3, description="Test operation"),
                table="scope_user_mapping",
                column_name="test_col",
                column_type="String"
            )
        )
        
        engine = clean_store.get_async_engine()
        
        # Create tables
        await create_tables(clean_store)
        
        # Check that correct schema versions were written
        async with engine.begin() as conn:
            def check_memory_meta(sync_conn):
                result = sync_conn.execute(text("SELECT * FROM memory_meta"))
                rows = result.fetchall()
                return {row[0]: row[1] for row in rows}
            
            meta_dict = await conn.run_sync(check_memory_meta)
            
            assert "user_message" in meta_dict
            assert "scope_user_mapping" in meta_dict
            assert int(meta_dict["user_message"]) == 5
            assert int(meta_dict["scope_user_mapping"]) == 3

    @pytest.mark.asyncio
    async def test_create_tables_does_not_overwrite_existing_meta(self, clean_store):
        """Test that create_tables does not overwrite existing memory_meta entries"""
        engine = clean_store.get_async_engine()
        
        # Create tables first to get the memory_meta table
        await create_tables(clean_store)
        
        # Insert a custom schema version
        async with engine.begin() as conn:
            def insert_old_meta(sync_conn):
                sync_conn.execute(text("DELETE FROM memory_meta"))
                sync_conn.execute(
                    text("INSERT INTO memory_meta (table_name, schema_version) VALUES ('user_message', '1')")
                )
                sync_conn.execute(
                    text("INSERT INTO memory_meta (table_name, schema_version) VALUES ('scope_user_mapping', '2')")
                )
            await conn.run_sync(insert_old_meta)
        
        # Register a higher version operation
        sql_registry.register(
            "user_messages",
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=10, description="Test operation"),
                table="user_message",
                column_name="test_col",
                column_type="String"
            )
        )
        
        # Call create_tables again - this should not overwrite existing schema versions
        await create_tables(clean_store)
        
        # Verify that original schema versions are preserved
        async with engine.begin() as conn:
            def check_memory_meta(sync_conn):
                result = sync_conn.execute(text("SELECT * FROM memory_meta"))
                rows = result.fetchall()
                return {row[0]: row[1] for row in rows}
            
            meta_dict = await conn.run_sync(check_memory_meta)
            
            assert "user_message" in meta_dict
            assert "scope_user_mapping" in meta_dict
            assert int(meta_dict["user_message"]) == 1
            assert int(meta_dict["scope_user_mapping"]) == 2

    @pytest.mark.asyncio
    async def test_create_tables_with_empty_registry(self, clean_store):
        """Test create_tables when registry has no operations"""
        # Clear registry
        sql_registry.clear()
        
        engine = clean_store.get_async_engine()
        
        # Create tables
        await create_tables(clean_store)
        
        # Check that tables were created but no schema versions were written (since registry is empty)
        async with engine.begin() as conn:
            def check_memory_meta(sync_conn):
                result = sync_conn.execute(text("SELECT * FROM memory_meta"))
                rows = result.fetchall()
                return rows
            
            meta_rows = await conn.run_sync(check_memory_meta)
            assert len(meta_rows) == 0


# Make sure we import SqlDbStore
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
