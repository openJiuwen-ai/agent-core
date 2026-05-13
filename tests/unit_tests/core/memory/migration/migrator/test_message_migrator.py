# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.store.base_message_store import (
    BaseMessageStore,
)
from openjiuwen.core.memory.migration.migrator.message_migrator import (
    MessageMigrator,
    MESSAGE_ENTITY_KEY,
)
from openjiuwen.core.memory.migration.operation.base_operation import (
    BaseOperation,
    OperationMetadata,
)
from openjiuwen.core.memory.migration.operation.operations import (
    UpdateMessageOperation,
)


def make_update_op(version: int, update_func=None):
    if update_func is None:
        update_func = AsyncMock()
    return UpdateMessageOperation(
        metadata=OperationMetadata(schema_version=version, description=f"v{version}"),
        update_func=update_func,
    )


def make_fake_op(version: int):
    op = MagicMock(spec=BaseOperation)
    op.schema_version = version
    op.__class__ = type("FakeOperation", (BaseOperation,), {})
    return op


@pytest.fixture(name="mock_message_store")
def mock_message_store_fixture():
    store = AsyncMock(spec=BaseMessageStore)
    store.count_messages = AsyncMock(return_value=0)
    store.get_schema_version = AsyncMock(return_value=None)
    store.set_schema_version = AsyncMock()
    store.get_messages = AsyncMock(return_value=[])
    store.delete_messages = AsyncMock()
    store.add_message = AsyncMock(return_value="msg_1")
    return store


@pytest.fixture(name="migrator")
def migrator_fixture(mock_message_store):
    return MessageMigrator(mock_message_store)


class TestMessageMigrator:

    @pytest.mark.asyncio
    async def test_try_migrate_empty_operations(self, migrator):
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [])
        assert result is True

    @pytest.mark.asyncio
    async def test_try_migrate_invalid_entity_key(self, migrator):
        update_func = AsyncMock()
        result = await migrator.try_migrate("invalid_key", [make_update_op(1, update_func)])
        assert result is False

    @pytest.mark.asyncio
    async def test_operations_not_ascending_order(self, migrator):
        operations = [make_update_op(2), make_update_op(1)]
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_operations_with_equal_version(self, migrator):
        operations = [make_update_op(1), make_update_op(1)]
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, operations)
        assert result is False

    @pytest.mark.asyncio
    async def test_fresh_store_executes_all(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        update_func1 = AsyncMock()
        update_func2 = AsyncMock()
        op1 = make_update_op(1, update_func1)
        op2 = make_update_op(2, update_func2)

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op1, op2])

        assert result is True
        update_func1.assert_awaited_once_with(mock_message_store)
        update_func2.assert_awaited_once_with(mock_message_store)
        mock_message_store.set_schema_version.assert_called_with(2)

    @pytest.mark.asyncio
    async def test_skip_already_applied_operations(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=2)

        update_func2 = AsyncMock()
        update_func3 = AsyncMock()
        op2 = make_update_op(2, update_func2)
        op3 = make_update_op(3, update_func3)

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op2, op3])

        assert result is True
        update_func2.assert_not_awaited()
        update_func3.assert_awaited_once_with(mock_message_store)
        mock_message_store.set_schema_version.assert_called_with(3)

    @pytest.mark.asyncio
    async def test_no_pending_operations(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=3)

        result = await migrator.try_migrate(
            MESSAGE_ENTITY_KEY,
            [make_update_op(1), make_update_op(2), make_update_op(3)],
        )

        assert result is True
        mock_message_store.set_schema_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_version_stored_as_int(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=1)

        update_func = AsyncMock()
        op2 = make_update_op(2, update_func)
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op2])

        assert result is True
        update_func.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_operation_error_returns_false(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        failing_func = AsyncMock(side_effect=RuntimeError("boom"))
        failing_op = make_update_op(1, failing_func)

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [failing_op])

        assert result is False

    @pytest.mark.asyncio
    async def test_idempotent_migration(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=1)

        update_func = AsyncMock()
        op1 = make_update_op(1, update_func)
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op1])

        assert result is True
        update_func.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_version_update_failure_propagates(self):
        mock_message_store = AsyncMock()
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)
        mock_message_store.set_schema_version = AsyncMock(side_effect=RuntimeError("write failed"))

        migrator = MessageMigrator(mock_message_store)
        update_func = AsyncMock()
        op = make_update_op(1, update_func)
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op])
        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_operations_sequential(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        func1 = AsyncMock()
        func2 = AsyncMock()
        func3 = AsyncMock()
        ops = [
            make_update_op(1, func1),
            make_update_op(2, func2),
            make_update_op(3, func3),
        ]

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, ops)

        assert result is True
        func1.assert_awaited_once_with(mock_message_store)
        func2.assert_awaited_once_with(mock_message_store)
        func3.assert_awaited_once_with(mock_message_store)
        mock_message_store.set_schema_version.assert_called_with(3)

    @pytest.mark.asyncio
    async def test_partial_migration_on_failure(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        func1 = AsyncMock()
        func2 = AsyncMock(side_effect=RuntimeError("failure at v2"))
        func3 = AsyncMock()
        ops = [
            make_update_op(1, func1),
            make_update_op(2, func2),
            make_update_op(3, func3),
        ]

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, ops)

        assert result is False
        func1.assert_awaited_once()
        func3.assert_not_awaited()
        mock_message_store.set_schema_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_operation_type_returns_false(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        result = await migrator.try_migrate(
            MESSAGE_ENTITY_KEY,
            [make_fake_op(1)],
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_single_operation(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)
        mock_message_store.count_messages = AsyncMock(return_value=5)

        update_func = AsyncMock()
        op = make_update_op(1, update_func)
        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [op])

        assert result is True
        update_func.assert_awaited_once()

    # ==================== Backup / Restore Tests ====================

    @pytest.mark.asyncio
    async def test_backup_created_before_migration(self, migrator, mock_message_store):
        mock_message_store.get_schema_version = AsyncMock(return_value=None)

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(1)])

        assert result is True
        mock_message_store.get_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_restore_on_failure(self, migrator, mock_message_store):
        """When migration fails, messages should be restored from backup."""
        mock_message_store.get_schema_version = AsyncMock(return_value=None)

        failing_func = AsyncMock(side_effect=RuntimeError("migration failed"))
        from openjiuwen.core.foundation.llm.schema.message import BaseMessage
        from openjiuwen.core.foundation.store.base_message_store import MessageMetadata
        from datetime import datetime, timezone

        mock_msg = BaseMessage(content="hello", role="user")
        mock_meta = MessageMetadata(
            message_id="msg_1", user_id="u1", scope_id="s1",
            session_id="sess1", timestamp=datetime.now(timezone.utc),
            message_type="user",
        )
        mock_message_store.get_messages = AsyncMock(return_value=[(mock_msg, mock_meta)])

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(1, failing_func)])

        assert result is False
        mock_message_store.delete_messages.assert_awaited_once()
        mock_message_store.add_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_restore_on_success(self, migrator, mock_message_store):
        """No restore should happen when migration succeeds."""
        mock_message_store.get_schema_version = AsyncMock(return_value=None)

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(1)])

        assert result is True
        mock_message_store.delete_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restore_resets_version(self, migrator, mock_message_store):
        """After restore, schema version should be reset to pre-migration value."""
        mock_message_store.get_schema_version = AsyncMock(return_value=2)

        failing_func = AsyncMock(side_effect=RuntimeError("v3 failed"))
        mock_message_store.get_messages = AsyncMock(return_value=[])

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(3, failing_func)])

        assert result is False
        mock_message_store.set_schema_version.assert_called_once_with(2)

    @pytest.mark.asyncio
    async def test_restore_continues_on_restore_error(self, migrator, mock_message_store):
        """Restore errors should be logged but not raise."""
        mock_message_store.get_schema_version = AsyncMock(return_value=None)

        failing_func = AsyncMock(side_effect=RuntimeError("migration failed"))
        mock_message_store.delete_messages = AsyncMock(side_effect=RuntimeError("delete failed"))
        mock_message_store.get_messages = AsyncMock(return_value=[])

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(1, failing_func)])

        assert result is False
        mock_message_store.delete_messages.assert_awaited()

    # ==================== Version Reset Tests ====================

    @pytest.mark.asyncio
    async def test_rollback_resets_version_to_pre_migration_value(
        self, migrator, mock_message_store
    ):
        """When migration from version 2 fails, version should be reset to 2."""
        mock_message_store.get_schema_version = AsyncMock(return_value=2)

        failing_func = AsyncMock(side_effect=RuntimeError("v3 failed"))
        mock_message_store.get_messages = AsyncMock(return_value=[])

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(3, failing_func)])

        assert result is False

    @pytest.mark.asyncio
    async def test_no_version_reset_on_first_op_failure_no_backup_data(
        self, migrator, mock_message_store
    ):
        """When first op fails and no data to restore, version reset is skipped."""
        mock_message_store.get_schema_version = AsyncMock(return_value=None)

        failing_func = AsyncMock(side_effect=RuntimeError("v1 failed"))
        mock_message_store.get_messages = AsyncMock(return_value=[])

        result = await migrator.try_migrate(MESSAGE_ENTITY_KEY, [make_update_op(1, failing_func)])

        assert result is False
        mock_message_store.delete_messages.assert_awaited()
