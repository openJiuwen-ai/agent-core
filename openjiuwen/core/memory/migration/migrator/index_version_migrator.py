# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.store.base_memory_index import BaseMemoryIndex
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.memory.migration.operation.operations import (
    RenameMemoryDocFieldOperation,
    TransformMemoryDocFieldOperation,
    AddMemoryDocFieldOperation,
    RemoveMemoryDocFieldOperation
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class IndexVersionMigrator:
    """
    Version migrator: Performs data transformation within the same BaseMemoryIndex instance.
    """
    
    async def try_migrate(self, index: BaseMemoryIndex, operations: list[BaseOperation]) -> bool:
        """
        Try to perform version migration.
        
        Args:
            index: BaseMemoryIndex instance
            operations: List of migration operations
            
        Returns:
            bool: Whether the migration was successful
        """
        # Get the current version
        current_version = index.get_schema_version()
        
        # Filter operations to apply (version number greater than current version)
        operations_to_apply = [
            op for op in operations
            if op.schema_version > current_version
        ]
        
        if not operations_to_apply:
            # No operations to apply
            return True
        
        # Create a backup
        backup_id = await index.create_backup()
        
        try:
            # Execute each operation in sequence
            for op in operations_to_apply:
                await self._apply_operation(index, op)
                # Update the version number after operation execution
                index.update_schema_version(op.schema_version)
            
            # Clean up the backup
            await index.cleanup_backup(backup_id)
            return True
        except Exception as e:
            # Migration failed, restore backup and rollback version
            await index.restore_backup(backup_id)
            await index.cleanup_backup(backup_id)
            index.update_schema_version(current_version)
            memory_logger.error(
                "Error during index migration: %s",
                str(e),
                event_type=LogEventType.MEMORY_INIT,
                exception=str(e)
            )
            return False
    
    async def _apply_operation(self, index: BaseMemoryIndex, operation: BaseOperation) -> None:
        """
        Apply a single migration operation.
        
        Args:
            index: BaseMemoryIndex instance
            operation: Migration operation
        """
        if isinstance(operation, RenameMemoryDocFieldOperation):
            await self._apply_rename_field(index, operation)
        elif isinstance(operation, TransformMemoryDocFieldOperation):
            await self._apply_transform_field(index, operation)
        elif isinstance(operation, AddMemoryDocFieldOperation):
            await self._apply_add_field(index, operation)
        elif isinstance(operation, RemoveMemoryDocFieldOperation):
            await self._apply_remove_field(index, operation)
        else:
            raise build_error(
                StatusCode.MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR,
                error_msg=f"Unsupported operation type: {type(operation).__name__}"
            )
    
    async def _apply_rename_field(self, index: BaseMemoryIndex, operation: RenameMemoryDocFieldOperation) -> None:
        """
        Apply field rename operation.
        """
        # Get all user-scope combinations
        scopes = await index.list_user_scopes()
        
        for user_id, scope_id in scopes:
            # Process documents in batches
            offset = 0
            batch_size = 100
            
            while True:
                documents = await index.list_memories(user_id, scope_id, offset, batch_size)
                if not documents:
                    break
                
                # Transform documents
                for doc in documents:
                    if operation.old_field_name in doc.fields:
                        doc.fields[operation.new_field_name] = doc.fields.pop(operation.old_field_name)
                
                # Update documents
                doc_ids = [doc.id for doc in documents]
                await index.delete_memories(user_id, scope_id, doc_ids)
                await index.add_memories(user_id, scope_id, documents)
                offset += batch_size

    async def _apply_transform_field(self, index: BaseMemoryIndex, operation: TransformMemoryDocFieldOperation) -> None:
        """
        Apply field value transformation operation.
        """
        # Get all user-scope combinations
        scopes = await index.list_user_scopes()

        for user_id, scope_id in scopes:
            # Process documents in batches
            offset = 0
            batch_size = 100

            while True:
                documents = await index.list_memories(user_id, scope_id, offset, batch_size)
                if not documents:
                    break

                # Transform documents
                for doc in documents:
                    if operation.field_name in doc.fields:
                        doc.fields[operation.field_name] = operation.transform_func(doc.fields[operation.field_name])

                # Update documents
                doc_ids = [doc.id for doc in documents]
                await index.delete_memories(user_id, scope_id, doc_ids)
                await index.add_memories(user_id, scope_id, documents)
                offset += batch_size

    async def _apply_add_field(self, index: BaseMemoryIndex, operation: AddMemoryDocFieldOperation) -> None:
        """
        Apply add field operation.
        """
        # Get all user-scope combinations
        scopes = await index.list_user_scopes()

        for user_id, scope_id in scopes:
            # Process documents in batches
            offset = 0
            batch_size = 100

            while True:
                documents = await index.list_memories(user_id, scope_id, offset, batch_size)
                if not documents:
                    break

                # Transform documents
                for doc in documents:
                    if operation.field_name not in doc.fields:
                        if callable(operation.default_value_or_func):
                            default_value = operation.default_value_or_func()
                        else:
                            default_value = operation.default_value_or_func
                        doc.fields[operation.field_name] = default_value

                # Update documents
                doc_ids = [doc.id for doc in documents]
                await index.delete_memories(user_id, scope_id, doc_ids)
                await index.add_memories(user_id, scope_id, documents)
                offset += batch_size

    async def _apply_remove_field(self, index: BaseMemoryIndex, operation: RemoveMemoryDocFieldOperation) -> None:
        """
        Apply remove field operation.
        """
        # Get all user-scope combinations
        scopes = await index.list_user_scopes()

        for user_id, scope_id in scopes:
            # Process documents in batches
            offset = 0
            batch_size = 100

            while True:
                documents = await index.list_memories(user_id, scope_id, offset, batch_size)
                if not documents:
                    break

                # Transform documents
                for doc in documents:
                    if operation.field_name in doc.fields:
                        del doc.fields[operation.field_name]

                # Update documents
                doc_ids = [doc.id for doc in documents]
                await index.delete_memories(user_id, scope_id, doc_ids)
                await index.add_memories(user_id, scope_id, documents)
                offset += batch_size
