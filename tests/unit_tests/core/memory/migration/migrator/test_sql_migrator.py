# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.memory.manage.mem_model.db_model import create_tables
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.migration.migrator.sql_migrator import SQLMigrator
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
from openjiuwen.core.memory.migration.operation.operations import (
    AddColumnOperation,
    RenameColumnOperation,
    UpdateColumnTypeOperation,
)


@pytest.fixture(name="test_store")
def store_fixture():
    utc_now = datetime.now(timezone.utc)
    time_str = utc_now.strftime("%Y%m%d%H%M%S")
    uuid_str = uuid.uuid4().hex[:6]
    path = Path(f"./test_sql_migrator_{time_str}_{uuid_str}.db").resolve()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    db_store = DefaultDbStore(engine)
    asyncio.run(create_tables(db_store))
    sql_store = SqlDbStore(db_store)

    yield sql_store

    asyncio.run(engine.dispose())
    if path.exists():
        path.unlink()


async def clean():
    engine = create_async_engine('mysql+aiomysql://root:root@localhost:3306/test_db')
    async with engine.begin() as conn:
        await conn.execute(text('DROP TABLE IF EXISTS user_message'))
        await conn.execute(text('DROP TABLE IF EXISTS scope_user_mapping'))
        await conn.execute(text('DROP TABLE IF EXISTS memory_meta'))
    await engine.dispose()


@pytest.fixture(name="mysql_store")
def mysql_store_fixture():
    pytest.skip("Skipping MySQL tests")
    engine = create_async_engine("mysql+aiomysql://root:root@localhost:3306/test_db")
    asyncio.run(clean())
    db_store = DefaultDbStore(engine)
    asyncio.run(create_tables(db_store))
    sql_store = SqlDbStore(db_store)

    yield sql_store

    asyncio.run(engine.dispose())


class TestSQLMigrator:
    """Test cases for SQLMigrator class"""

    @pytest.mark.asyncio
    async def test_try_migrate_empty_operations(self, test_store):
        """Test migration with empty operations list"""
        migrator = SQLMigrator(test_store)
        result = await migrator.try_migrate([])
        assert result is True

    @pytest.mark.asyncio
    async def test_add_column_operation_user_message(self, test_store):
        """Test adding a column to user_message table"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add new_column to user_message"),
                table="user_message",
                column_name="new_column",
                column_type="String",
                nullable=True,
                default=None
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_column(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return "new_column" in column_names
            
            column_exists = await conn.run_sync(check_column)
            assert column_exists is True

    @pytest.mark.asyncio
    async def test_add_column_operation_scope_user_mapping(self, test_store):
        """Test adding a column to scope_user_mapping table"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add new_column to scope_user_mapping"),
                table="scope_user_mapping",
                column_name="new_column",
                column_type="Integer",
                nullable=False,
                default=0
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_column(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("scope_user_mapping")
                column_names = [col['name'] for col in columns]
                return "new_column" in column_names
            
            column_exists = await conn.run_sync(check_column)
            assert column_exists is True

    @pytest.mark.asyncio
    async def test_add_column_unsupported_table(self, test_store):
        """Test adding column to unsupported table should raise ValueError"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column to unsupported table"),
                table="unsupported_table",
                column_name="new_column",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_rename_column_operation(self, test_store):
        """Test renaming a column in user_message table"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add old_name column"),
                table="user_message",
                column_name="old_name",
                column_type="String"
            ),
            RenameColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Rename old_name to new_name"),
                table="user_message",
                old_column_name="old_name",
                new_column_name="new_name"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return "old_name" not in column_names and "new_name" in column_names
            
            columns_correct = await conn.run_sync(check_columns)
            assert columns_correct is True

    @pytest.mark.asyncio
    async def test_rename_column_unsupported_table(self, test_store):
        """Test renaming column in unsupported table should fail"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            RenameColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Rename column in unsupported table"),
                table="unsupported_table",
                old_column_name="old_name",
                new_column_name="new_name"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_update_column_type_operation_sqlite(self, test_store):
        """Test updating column type in SQLite"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with String type"),
                table="user_message",
                column_name="test_column",
                column_type="String"
            ),
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=2, description="Update column to Text type"),
                table="user_message",
                column_name="test_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_column_type(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                for col in columns:
                    if col['name'] == "test_column":
                        return "text" in str(col['type']).lower()
                return False
            
            column_type_correct = await conn.run_sync(check_column_type)
            assert column_type_correct is True

    @pytest.mark.asyncio
    async def test_update_column_type_unsupported_table(self, test_store):
        """Test updating column type in unsupported table should fail"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=1, description="Update column in unsupported table"),
                table="unsupported_table",
                column_name="test_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_version_control(self, test_store):
        """Test that only operations with higher schema version are executed"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column v1"),
                table="user_message",
                column_name="column_v1",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add column v2"),
                table="user_message",
                column_name="column_v2",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=3, description="Add column v3"),
                table="user_message",
                column_name="column_v3",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "column_v1" in column_names
            assert "column_v2" in column_names
            assert "column_v3" in column_names
        
        operations_v4 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=4, description="Add column v4"),
                table="user_message",
                column_name="column_v4",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v4)
        assert result is True

    @pytest.mark.asyncio
    async def test_multiple_operations_in_single_migration(self, test_store):
        """Test multiple operations in a single migration"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add col1"),
                table="user_message",
                column_name="col1",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add col2"),
                table="user_message",
                column_name="col2",
                column_type="Integer"
            ),
            RenameColumnOperation(
                metadata=OperationMetadata(schema_version=3, description="Rename col1 to col1_renamed"),
                table="user_message",
                old_column_name="col1",
                new_column_name="col1_renamed"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "col1_renamed" in column_names
            assert "col2" in column_names
            assert "col1" not in column_names

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_string(self):
        """Test get_sqlalchemy_type for String type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("String")
        assert isinstance(result, String)
        
        result = migrator.get_sqlalchemy_type("VARCHAR")
        assert isinstance(result, String)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_string_with_length(self):
        """Test get_sqlalchemy_type for String type with length"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("String(100)")
        assert isinstance(result, String)
        
        result = migrator.get_sqlalchemy_type("VARCHAR(255)")
        assert isinstance(result, String)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_integer(self):
        """Test get_sqlalchemy_type for Integer type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("Integer")
        assert isinstance(result, Integer)
        
        result = migrator.get_sqlalchemy_type("INT")
        assert isinstance(result, Integer)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_datetime(self):
        """Test get_sqlalchemy_type for DateTime type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("DateTime")
        assert isinstance(result, DateTime)
        
        result = migrator.get_sqlalchemy_type("DATETIME")
        assert isinstance(result, DateTime)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_boolean(self):
        """Test get_sqlalchemy_type for Boolean type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("Boolean")
        assert isinstance(result, Boolean)
        
        result = migrator.get_sqlalchemy_type("BOOL")
        assert isinstance(result, Boolean)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_text(self):
        """Test get_sqlalchemy_type for Text type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("Text")
        assert isinstance(result, Text)
        
        result = migrator.get_sqlalchemy_type("TEXT")
        assert isinstance(result, Text)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_float(self):
        """Test get_sqlalchemy_type for Float type"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("Float")
        assert isinstance(result, Float)
        
        result = migrator.get_sqlalchemy_type("FLOAT")
        assert isinstance(result, Float)

    @pytest.mark.asyncio
    async def test_get_sqlalchemy_type_unknown(self):
        """Test get_sqlalchemy_type for unknown type (should default to Text)"""
        mock_store = MagicMock()
        mock_store.db_store = MagicMock()
        mock_store.db_store.get_async_engine = MagicMock()
        
        migrator = SQLMigrator(mock_store)
        
        result = migrator.get_sqlalchemy_type("UnknownType")
        assert isinstance(result, Text)

    @pytest.mark.asyncio
    async def test_batch_migrate(self, test_store):
        """Test batch migration of multiple tables"""
        migrator = SQLMigrator(test_store)
        
        migrations = [
            {
                "table_name": "user_message",
                "operations": [
                    AddColumnOperation(
                        metadata=OperationMetadata(schema_version=1, description="Add batch_col1"),
                        table="user_message",
                        column_name="batch_col1",
                        column_type="String"
                    )
                ]
            },
            {
                "table_name": "scope_user_mapping",
                "operations": [
                    AddColumnOperation(
                        metadata=OperationMetadata(schema_version=1, description="Add batch_col2"),
                        table="scope_user_mapping",
                        column_name="batch_col2",
                        column_type="Integer"
                    )
                ]
            }
        ]
        
        results = await migrator.batch_migrate(migrations)
        
        assert "user_message" in results
        assert "scope_user_mapping" in results
        assert results["user_message"] is True
        assert results["scope_user_mapping"] is True

    @pytest.mark.asyncio
    async def test_update_column_type_nonexistent_column(self, test_store):
        """Test updating type of non-existent column should fail"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=1, description="Update non-existent column type"),
                table="user_message",
                column_name="nonexistent_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_add_column_with_default_value(self, test_store):
        """Test adding column with default value"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with default"),
                table="user_message",
                column_name="default_col",
                column_type="Integer",
                nullable=False,
                default=42
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True

    @pytest.mark.asyncio
    async def test_migration_idempotency(self, test_store):
        """Test that migration is idempotent (can be run multiple times)"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add idempotent_col"),
                table="user_message",
                column_name="idempotent_col",
                column_type="String"
            )
        ]
        
        result1 = await migrator.try_migrate(operations)
        assert result1 is True
        
        result2 = await migrator.try_migrate(operations)
        assert result2 is True

    @pytest.mark.asyncio
    async def test_migrate_with_data_preservation(self, test_store):
        """Test that data is preserved during column type change"""
        migrator = SQLMigrator(test_store)
        
        await test_store.write("user_message", {
            "message_id": "test_msg_1",
            "user_id": "user1",
            "scope_id": "scope1",
            "content": "test content"
        })
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add test_col"),
                table="user_message",
                column_name="test_col",
                column_type="String"
            ),
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=2, description="Update test_col to Text"),
                table="user_message",
                column_name="test_col",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        row = await test_store.condition_get("user_message", {"message_id": ["test_msg_1"]})
        assert row is not None
        assert len(row) > 0
        assert row[0]["message_id"] == "test_msg_1"
        assert row[0]["content"] == "test content"

    @pytest.mark.asyncio
    async def test_empty_operations_list(self, test_store):
        """Test migration with completely empty operations list"""
        migrator = SQLMigrator(test_store)
        result = await migrator.try_migrate([])
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_migrate_with_empty_operations(self, test_store):
        """Test batch migrate with empty operations"""
        migrator = SQLMigrator(test_store)
        
        migrations = [
            {
                "table_name": "user_message",
                "operations": []
            }
        ]
        
        results = await migrator.batch_migrate(migrations)
        assert "user_message" in results
        assert results["user_message"] is True

    @pytest.mark.asyncio
    async def test_add_column_text_type(self, test_store):
        """Test adding Text type column"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add Text column"),
                table="user_message",
                column_name="text_col",
                column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_column(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                for col in columns:
                    if col['name'] == "text_col":
                        return "text" in str(col['type']).lower()
                return False
            
            is_text = await conn.run_sync(check_column)
            assert is_text is True

    @pytest.mark.asyncio
    async def test_update_column_type_sqlite_data_preservation(self, test_store):
        """Test updating column type in SQLite with data preservation"""
        migrator = SQLMigrator(test_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with String type"),
                table="user_message",
                column_name="test_column",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            current_time = datetime.now(timezone.utc)
            await conn.execute(
                text(
                    "INSERT INTO user_message "
                    "(message_id, user_id, session_id, scope_id, role, content, timestamp, test_column) "
                    "VALUES ('test_id', 'user1', 'session1', 'scope1', 'user', 'test message', :timestamp, 'test_data')"
                ),
                {"timestamp": current_time}
            )
            await conn.commit()
        
        update_operations = [
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=2, description="Update column to Text type"),
                table="user_message",
                column_name="test_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(update_operations)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            result = await conn.execute(text("SELECT test_column FROM user_message WHERE message_id = 'test_id'"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 'test_data'

    @pytest.mark.asyncio
    async def test_skip_lower_version_operations(self, test_store):
        """Test that lower version operations are skipped"""
        migrator = SQLMigrator(test_store)
        
        operations_v1 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column v1"),
                table="user_message",
                column_name="column_v1",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v1)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "column_v1" in column_names
        
        operations_v2 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add column v2"),
                table="user_message",
                column_name="column_v2",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v2)
        assert result is True
        
        async with test_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "column_v2" in column_names
        
        result = await migrator.try_migrate(operations_v1)
        assert result is True


@pytest.mark.skip(reason="Skipping MySQL tests")
class TestSQLMigratorMySQL:
    """Test cases for SQLMigrator class with MySQL"""

    @pytest.mark.asyncio
    async def test_try_migrate_empty_operations(self, mysql_store):
        """Test migration with empty operations list"""
        migrator = SQLMigrator(mysql_store)
        result = await migrator.try_migrate([])
        assert result is True

    @pytest.mark.asyncio
    async def test_add_column_operation_user_message(self, mysql_store):
        """Test adding a column to user_message table"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add mysql_new_column to user_message"),
                table="user_message",
                column_name="mysql_new_column",
                column_type="String",
                nullable=True,
                default=None
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_column(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return "mysql_new_column" in column_names
            
            column_exists = await conn.run_sync(check_column)
            assert column_exists is True

    @pytest.mark.asyncio
    async def test_add_column_operation_scope_user_mapping(self, mysql_store):
        """Test adding a column to scope_user_mapping table"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add mysql_new_column to scope_user_mapping"),
                table="scope_user_mapping",
                column_name="mysql_new_column",
                column_type="Integer",
                nullable=False,
                default=0
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_column(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("scope_user_mapping")
                column_names = [col['name'] for col in columns]
                return "mysql_new_column" in column_names
            
            column_exists = await conn.run_sync(check_column)
            assert column_exists is True

    @pytest.mark.asyncio
    async def test_rename_column_operation(self, mysql_store):
        """Test renaming a column in user_message table"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add old_name column"),
                table="user_message",
                column_name="old_name",
                column_type="String"
            ),
            RenameColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Rename old_name to new_name"),
                table="user_message",
                old_column_name="old_name",
                new_column_name="new_name"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return "old_name" not in column_names and "new_name" in column_names
            
            columns_correct = await conn.run_sync(check_columns)
            assert columns_correct is True

    @pytest.mark.asyncio
    async def test_update_column_type_operation_mysql(self, mysql_store):
        """Test updating column type in MySQL"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with String type"),
                table="user_message",
                column_name="test_column",
                column_type="String"
            ),
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=2, description="Update column to Text type"),
                table="user_message",
                column_name="test_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_column_type(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                for col in columns:
                    if col['name'] == "test_column":
                        return "text" in str(col['type']).lower()
                return False
            
            column_type_correct = await conn.run_sync(check_column_type)
            assert column_type_correct is True

    @pytest.mark.asyncio
    async def test_version_control(self, mysql_store):
        """Test that only operations with higher schema version are executed"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column v1"),
                table="user_message",
                column_name="column_v1",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add column v2"),
                table="user_message",
                column_name="column_v2",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=3, description="Add column v3"),
                table="user_message",
                column_name="column_v3",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "column_v1" in column_names
            assert "column_v2" in column_names
            assert "column_v3" in column_names
        
        operations_v4 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=4, description="Add column v4"),
                table="user_message",
                column_name="column_v4",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v4)
        assert result is True

    @pytest.mark.asyncio
    async def test_multiple_operations_in_single_migration(self, mysql_store):
        """Test multiple operations in a single migration"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add col1"),
                table="user_message",
                column_name="col1",
                column_type="String"
            ),
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add col2"),
                table="user_message",
                column_name="col2",
                column_type="Integer"
            ),
            RenameColumnOperation(
                metadata=OperationMetadata(schema_version=3, description="Rename col1 to col1_renamed"),
                table="user_message",
                old_column_name="col1",
                new_column_name="col1_renamed"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "col1_renamed" in column_names
            assert "col2" in column_names
            assert "col1" not in column_names

    @pytest.mark.asyncio
    async def test_batch_migrate(self, mysql_store):
        """Test batch migration of multiple tables"""
        migrator = SQLMigrator(mysql_store)
        
        migrations = [
            {
                "table_name": "user_message",
                "operations": [
                    AddColumnOperation(
                        metadata=OperationMetadata(schema_version=1, description="Add batch_col1"),
                        table="user_message",
                        column_name="batch_col1",
                        column_type="String"
                    )
                ]
            },
            {
                "table_name": "scope_user_mapping",
                "operations": [
                    AddColumnOperation(
                        metadata=OperationMetadata(schema_version=1, description="Add batch_col2"),
                        table="scope_user_mapping",
                        column_name="batch_col2",
                        column_type="Integer"
                    )
                ]
            }
        ]
        
        results = await migrator.batch_migrate(migrations)
        
        assert "user_message" in results
        assert "scope_user_mapping" in results
        assert results["user_message"] is True
        assert results["scope_user_mapping"] is True

    @pytest.mark.asyncio
    async def test_add_column_with_default_value(self, mysql_store):
        """Test adding column with default value"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with default"),
                table="user_message",
                column_name="default_col",
                column_type="Integer",
                nullable=False,
                default=42
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True

    @pytest.mark.asyncio
    async def test_migration_idempotency(self, mysql_store):
        """Test that migration is idempotent (can be run multiple times)"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add idempotent_col"),
                table="user_message",
                column_name="idempotent_col",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        result = await migrator.try_migrate(operations)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_column_type_mysql_data_preservation(self, mysql_store):
        """Test updating column type in MySQL with data preservation"""
        migrator = SQLMigrator(mysql_store)
        
        operations = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add column with String type"),
                table="user_message",
                column_name="mysql_test_column",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            current_time = datetime.now(timezone.utc)
            await conn.execute(
                text(
                    "INSERT INTO user_message "
                    "(message_id, user_id, session_id, scope_id, role, content, timestamp, mysql_test_column) "
                    "VALUES ('test_id', 'user1', 'session1', 'scope1', 'user', 'test message', :timestamp, 'test_data')"
                ),
                {"timestamp": current_time}
            )
            await conn.commit()
        
        update_operations = [
            UpdateColumnTypeOperation(
                metadata=OperationMetadata(schema_version=2, description="Update column to Text type"),
                table="user_message",
                column_name="mysql_test_column",
                new_column_type="Text"
            )
        ]
        
        result = await migrator.try_migrate(update_operations)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            result = await conn.execute(
                text("SELECT mysql_test_column FROM user_message WHERE message_id = 'test_id'")
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == 'test_data'

    @pytest.mark.asyncio
    async def test_skip_lower_version_operations(self, mysql_store):
        """Test that lower version operations are skipped"""
        migrator = SQLMigrator(mysql_store)
        
        operations_v1 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=1, description="Add mysql column v1"),
                table="user_message",
                column_name="mysql_column_v1",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v1)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "mysql_column_v1" in column_names
        
        operations_v2 = [
            AddColumnOperation(
                metadata=OperationMetadata(schema_version=2, description="Add mysql column v2"),
                table="user_message",
                column_name="mysql_column_v2",
                column_type="String"
            )
        ]
        
        result = await migrator.try_migrate(operations_v2)
        assert result is True
        
        async with mysql_store.db_store.get_async_engine().begin() as conn:
            def check_columns(sync_conn):
                inspector = inspect(sync_conn)
                columns = inspector.get_columns("user_message")
                column_names = [col['name'] for col in columns]
                return column_names
            
            column_names = await conn.run_sync(check_columns)
            assert "mysql_column_v2" in column_names
        
        result = await migrator.try_migrate(operations_v1)
        assert result is True
