# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Dict, List

from openjiuwen.core.foundation.store.base_message_store import BaseMessageStore
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.memory.migration.operation.operations import (
    UpdateMessageOperation,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


MESSAGE_ENTITY_KEY = "message_global"

_BACKUP_PAGE_SIZE = 1000


class MessageMigrator:
    def __init__(self, message_store: BaseMessageStore):
        self.message_store = message_store

    async def try_migrate(self, entity_key: str, operations: List[BaseOperation]) -> bool:
        """
        Try to migrate message data to target version by applying operations.

        Args:
            entity_key: Entity key to validate (must be MESSAGE_ENTITY_KEY)
            operations: List of migration operations to execute

        Returns:
            bool: True if migration succeeded or not needed, False otherwise
        """
        if entity_key != MESSAGE_ENTITY_KEY:
            memory_logger.error(
                f"Unsupported entity_key: '{entity_key}'. Expected: '{MESSAGE_ENTITY_KEY}'",
                event_type=LogEventType.MEMORY_INIT,
            )
            return False
        if not operations:
            return True

        if not self._validate_operations_order(operations):
            memory_logger.error(
                "Operations are not in ascending order by schema_version",
                event_type=LogEventType.MEMORY_INIT,
            )
            return False

        current_version = await self.message_store.get_schema_version()
        last_operation_version = operations[-1].schema_version
        if current_version is not None and current_version >= last_operation_version:
            memory_logger.info(
                f"Current version {current_version} is already >= "
                f"last operation version {last_operation_version}, no migration needed",
                event_type=LogEventType.MEMORY_INIT,
            )
            return True

        pending_operations = [
            op for op in operations
            if current_version is None or op.schema_version > current_version
        ]
        if not pending_operations:
            return True
        memory_logger.info(
            f"Found {len(pending_operations)} pending operations to execute",
            event_type=LogEventType.MEMORY_INIT,
        )

        backup_data = None
        try:
            backup_data = await self._create_backup()

            executed_operations: list[UpdateMessageOperation] = []
            last_version = current_version
            for idx, operation in enumerate(pending_operations, 1):
                memory_logger.info(
                    f"Executing operation {idx}/{len(pending_operations)}: "
                    f"{operation.__class__.__name__} (schema_version={operation.schema_version})",
                    event_type=LogEventType.MEMORY_INIT,
                )
                await self._execute_operation(operation)
                executed_operations.append(operation)
                last_version = operation.schema_version
                memory_logger.info(
                    f"Successfully executed operation {operation.__class__.__name__} "
                    f"with schema_version {operation.schema_version}",
                    event_type=LogEventType.MEMORY_INIT,
                )

            if last_version != current_version:
                await self.message_store.set_schema_version(last_version)
                memory_logger.info(
                    f"Message schema version updated from {current_version} to {last_version}",
                    event_type=LogEventType.MEMORY_INIT,
                )

            return True

        except Exception as e:
            memory_logger.error(
                f"Error during message migration: {str(e)}",
                event_type=LogEventType.MEMORY_INIT,
                exception=str(e),
            )
            if backup_data is not None:
                await self._restore_from_backup(backup_data, current_version)
            return False

    async def _execute_operation(self, operation: BaseOperation) -> None:
        """
        Execute a single migration operation.

        Args:
            operation: The operation to execute

        Raises:
            BaseError: If operation type is not supported
        """
        if isinstance(operation, UpdateMessageOperation):
            await operation.update_func(self.message_store)
        else:
            raise build_error(
                StatusCode.MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR,
                error_msg=f"Unsupported operation type: {operation.__class__.__name__}",
            )

    # ==================== Backup / Restore ====================

    async def _create_backup(self) -> list[dict]:
        """
        Create a backup of all messages before migration.

        Returns:
            list[dict]: Serialized message records for restoration on failure.
        """
        backup_data: list[dict] = []
        message_filter: Dict[str, Any] = {}
        messages = await self.message_store.get_messages(
            message_filter=message_filter,
            limit=_BACKUP_PAGE_SIZE,
            order_by="timestamp",
            order_direction="asc",
        )

        for base_msg, metadata in messages:
            backup_data.append({
                "message": {
                    "content": base_msg.content,
                    "role": base_msg.role,
                },
                "metadata": metadata.model_dump(mode="json"),
            })

        memory_logger.info(
            f"Created message backup with {len(backup_data)} records",
            event_type=LogEventType.MEMORY_INIT,
        )
        return backup_data

    async def _restore_from_backup(
        self, backup_data: list[dict], pre_migration_version: int | None
    ) -> None:
        """
        Restore messages from backup after migration failure.

        Deletes all current messages, re-inserts backup data,
        then resets the schema version.

        Args:
            backup_data: Serialized message records from _create_backup.
            pre_migration_version: Version before migration started.
        """
        try:
            message_filter: Dict[str, Any] = {}
            await self.message_store.delete_messages(message_filter)

            if backup_data:
                from openjiuwen.core.foundation.llm.schema.message import BaseMessage as BaseMsg
                from datetime import datetime

                for record in backup_data:
                    msg_data = record["message"]
                    meta_data = record["metadata"]
                    base_msg = BaseMsg(content=msg_data["content"], role=msg_data.get("role", ""))
                    add_req: Dict[str, Any] = {
                        'message': base_msg,
                        'user_id': meta_data.get("user_id", ""),
                        'scope_id': meta_data.get("scope_id", ""),
                        'session_id': meta_data.get("session_id", ""),
                        'timestamp': datetime.fromisoformat(meta_data["timestamp"])\
                            if meta_data.get("timestamp") else None,
                    }
                    await self.message_store.add_message(add_req)

            memory_logger.info(
                f"Successfully restored {len(backup_data)} messages from backup",
                event_type=LogEventType.MEMORY_INIT,
            )

        except Exception as e:
            memory_logger.error(
                f"Failed to restore messages from backup: {str(e)}",
                event_type=LogEventType.MEMORY_INIT,
                exception=str(e),
            )

        try:
            if pre_migration_version is not None:
                await self.message_store.set_schema_version(pre_migration_version)
                memory_logger.info(
                    f"Reset schema version to pre-migration value: {pre_migration_version}",
                    event_type=LogEventType.MEMORY_INIT,
                )
        except Exception as e:
            memory_logger.error(
                f"Failed to reset schema version to {pre_migration_version}: {str(e)}",
                event_type=LogEventType.MEMORY_INIT,
                exception=str(e),
            )

    @staticmethod
    def _validate_operations_order(operations: List[BaseOperation]) -> bool:
        """
        Validate that operations are in ascending order by schema_version.
        """
        for i in range(len(operations) - 1):
            if operations[i].schema_version >= operations[i + 1].schema_version:
                return False
        return True
