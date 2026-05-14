# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex, MemoryDoc
from openjiuwen.core.memory.migration.migrator.index_version_migrator import IndexVersionMigrator
from openjiuwen.core.memory.migration.operation.operations import (
    RenameMemoryDocFieldOperation,
    TransformMemoryDocFieldOperation,
    AddMemoryDocFieldOperation,
    RemoveMemoryDocFieldOperation
)
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata


class TestIndexVersionMigrator(unittest.IsolatedAsyncioTestCase):
    """
    Test cases for IndexVersionMigrator
    """
    
    async def asyncSetUp(self):
        """
        Set up test environment
        """
        # Create a mock BaseMemoryIndex
        self.mock_index = Mock(spec=BaseMemoryIndex)
        self.mock_index.get_schema_version.return_value = 0
        self.mock_index.update_schema_version = Mock()
        
        # Create test documents
        self.test_docs = [
            MemoryDoc(
                id="doc1",
                text="Test document 1",
                type="fragment",
                timestamp=datetime.fromtimestamp(1234567890.0, tz=timezone.utc),
                fields={"memory_text": "Content 1", "field1": "value1"}
            ),
            MemoryDoc(
                id="doc2",
                text="Test document 2",
                type="fragment",
                timestamp=datetime.fromtimestamp(1234567891.0, tz=timezone.utc),
                fields={"memory_text": "Content 2", "field1": "value2"}
            )
        ]
        
        # Set up mock methods
        self.mock_index.list_user_scopes.return_value = [("user1", "scope1")]
        self.mock_index.list_memories.side_effect = self.mock_list_memories
        self.mock_index.delete_memories = AsyncMock()
        self.mock_index.add_memories = AsyncMock()
        self.mock_index.create_backup.return_value = "backup123"
        self.mock_index.restore_backup = AsyncMock()
        self.mock_index.cleanup_backup = AsyncMock()
    
    def mock_list_memories(self, user_id, scope_id, offset, limit):
        """Mock method for list_memories"""
        return self.test_docs[offset: offset + limit]
    
    async def test_try_migrate_no_operations(self):
        """
        Test try_migrate with no operations to apply
        """
        migrator = IndexVersionMigrator()
        result = await migrator.try_migrate(self.mock_index, [])
        
        self.assertTrue(result)
        self.mock_index.get_schema_version.assert_called_once()
        self.mock_index.create_backup.assert_not_called()
    
    async def test_try_migrate_with_operations(self):
        """
        Test try_migrate with operations to apply
        """
        # Create a rename operation
        rename_operation = RenameMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=1, description="Rename memory_text to text"),
            old_field_name="memory_text",
            new_field_name="text"
        )
        
        migrator = IndexVersionMigrator()
        result = await migrator.try_migrate(self.mock_index, [rename_operation])
        
        self.assertTrue(result)
        self.mock_index.get_schema_version.assert_called_once()
        self.mock_index.create_backup.assert_called_once()
        self.mock_index.list_user_scopes.assert_called_once()
        # 验证list_memories被调用两次：第一次获取数据，第二次检查是否有更多数据
        self.assertEqual(self.mock_index.list_memories.call_count, 2)
        self.mock_index.list_memories.assert_any_call("user1", "scope1", 0, 100)
        self.mock_index.list_memories.assert_any_call("user1", "scope1", 100, 100)
        self.mock_index.add_memories.assert_called_once()
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.update_schema_version.assert_called_once_with(1)
        self.mock_index.cleanup_backup.assert_called_once_with("backup123")

    async def test_apply_rename_field(self):
        """
        Test applying RenameMemoryDocFieldOperation
        """
        # Create a rename operation
        rename_operation = RenameMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=1, description="Rename memory_text to text"),
            old_field_name="memory_text",
            new_field_name="text"
        )

        migrator = IndexVersionMigrator()
        await migrator.try_migrate(self.mock_index, [rename_operation])

        # Verify the operation was applied correctly
        self.mock_index.list_user_scopes.assert_called_once()
        # 验证list_memories被调用两次：第一次获取数据，第二次检查是否有更多数据
        self.assertEqual(self.mock_index.list_memories.call_count, 2)
        self.mock_index.list_memories.assert_any_call("user1", "scope1", 0, 100)
        self.mock_index.list_memories.assert_any_call("user1", "scope1", 100, 100)
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.add_memories.assert_called_once()

        # Check that the field was renamed in the passed documents
        passed_docs = self.mock_index.add_memories.call_args[0][2]
        for doc in passed_docs:
            self.assertIn("text", doc.fields)
            self.assertNotIn("memory_text", doc.fields)
            self.assertEqual(doc.fields["text"], f"Content {doc.id[-1]}")
    
    async def test_apply_transform_field(self):
        """
        Test applying TransformMemoryDocFieldOperation
        """
        # Create a transform operation
        def transform_func(value):
            return value.upper()
        
        transform_operation = TransformMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=2, description="Uppercase field1"),
            field_name="field1",
            transform_func=transform_func
        )
        
        migrator = IndexVersionMigrator()
        await migrator.try_migrate(self.mock_index, [transform_operation])
        
        # Verify the operation was applied correctly
        self.mock_index.list_user_scopes.assert_called_once()
        self.assertEqual(self.mock_index.list_memories.call_count, 2)
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.add_memories.assert_called_once()

        # Check that the field was transformed in the passed documents
        passed_docs = self.mock_index.add_memories.call_args[0][2]
        for doc in passed_docs:
            self.assertEqual(doc.fields["field1"], f"VALUE{doc.id[-1]}")
    
    async def test_apply_add_field(self):
        """
        Test applying AddMemoryDocFieldOperation
        """
        # Create an add field operation
        add_operation = AddMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=3, description="Add new_field"),
            field_name="new_field",
            default_value_or_func="default_value"
        )
        
        migrator = IndexVersionMigrator()
        await migrator.try_migrate(self.mock_index, [add_operation])
        
        # Verify the operation was applied correctly
        self.mock_index.list_user_scopes.assert_called_once()
        self.assertEqual(self.mock_index.list_memories.call_count, 2)
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.add_memories.assert_called_once()

        # Check that the field was added in the passed documents
        passed_docs = self.mock_index.add_memories.call_args[0][2]
        for doc in passed_docs:
            self.assertEqual(doc.fields["new_field"], "default_value")
    
    async def test_apply_remove_field(self):
        """
        Test applying RemoveMemoryDocFieldOperation
        """
        # Create a remove field operation
        remove_operation = RemoveMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=4, description="Remove field1"),
            field_name="field1"
        )
        
        migrator = IndexVersionMigrator()
        await migrator.try_migrate(self.mock_index, [remove_operation])
        
        # Verify the operation was applied correctly
        self.mock_index.list_user_scopes.assert_called_once()
        self.assertEqual(self.mock_index.list_memories.call_count, 2)
        self.mock_index.delete_memories.assert_called_once()
        self.mock_index.add_memories.assert_called_once()

        # Check that the field was removed in the passed documents
        passed_docs = self.mock_index.add_memories.call_args[0][2]
        for doc in passed_docs:
            self.assertNotIn("field1", doc.fields)
    
    async def test_migration_failure_rollback(self):
        """
        Test that migration failure triggers rollback
        """
        # Create an operation that will fail
        rename_operation = RenameMemoryDocFieldOperation(
            metadata=OperationMetadata(schema_version=1, description="Rename memory_text to text"),
            old_field_name="memory_text",
            new_field_name="text"
        )
        
        # Make add_memories raise an exception
        self.mock_index.add_memories.side_effect = Exception("Migration failed")
        
        migrator = IndexVersionMigrator()
        
        # try_migrate should return False on failure now instead of raising exception
        result = await migrator.try_migrate(self.mock_index, [rename_operation])
        self.assertFalse(result)
        
        # Verify rollback was called
        self.mock_index.create_backup.assert_called_once()
        self.mock_index.list_user_scopes.assert_called_once()
        # 在失败情况下，list_memories可能只被调用1�?
        self.assertIn(self.mock_index.list_memories.call_count, [1, 2])
        self.mock_index.restore_backup.assert_called_once_with("backup123")
        self.mock_index.cleanup_backup.assert_called_once_with("backup123")


if __name__ == "__main__":
    unittest.main()
