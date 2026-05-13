# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.memory.migration.operation.operation_registry import OperationRegistry


sql_registry = OperationRegistry()
vector_registry = OperationRegistry()
kv_registry = OperationRegistry()
message_registry = OperationRegistry()

"""
# SQL Example
from openjiuwen.core.memory.migration.operation.operations import AddColumnOperation
from openjiuwen.core.memory.migration.operation.base_operation import OperationMetadata
sql_registry.register(
    "user_messages",
    AddColumnOperation(
        metadata=OperationMetadata(schema_version=1, description="user_message add test column"),
        table="user_messages",
        column_name="test",
        column_type="INT",
        nullable=True,
        default=0
    )
)

# Vector Example
from openjiuwen.core.memory.migration.operation.operations import RenameScalarFieldOperation
vector_registry.register(
    "vector_summary",
    RenameScalarFieldOperation(
        metadata=OperationMetadata(schema_version=1, description="Rename 'message_id' to 'msg_id' in vector_summary"),
        data_type="vector_summary",
        old_field_name="message_id",
        new_field_name="msg_id"
    )
)

# KV Example
from openjiuwen.core.memory.migration.operation.operations import UpdateKVOperation
async def update_user_settings_kv(kv_store):
    # Example: Update user settings by adding a new field
    user_settings = await kv_store.get("user_settings")
    if user_settings and "theme" not in user_settings:
        user_settings["theme"] = "light"
        await kv_store.set("user_settings", user_settings)

# Use the same entity_key in for kv
kv_registry.register(
    "kv_global",
    UpdateKVOperation(
        metadata=OperationMetadata(schema_version=1, description="Update user settings by adding theme field"),
        update_func=update_user_settings_kv
    )
)

# Message Example
from openjiuwen.core.memory.migration.operation.operations import UpdateMessageOperation

async def add_role_prefix(message_store):
    # Example: Update all messages by prefixing role with 'user:' if role is 'user'
    # Uses only BaseMessageStore public API, works with any store implementation
    messages = await message_store.get_messages(
        message_filter={},
        limit=10000,
        order_by="timestamp",
        order_direction="asc",
    )
    for base_msg, metadata in messages:
        if metadata.message_type == "user" and not base_msg.role.startswith("user:"):
            await message_store.update_message(
                metadata.message_id,
                base_msg.content,
            )

message_registry.register(
    "message_global",
    UpdateMessageOperation(
        metadata=OperationMetadata(schema_version=1, description="Add role prefix to user messages"),
        update_func=add_role_prefix
    )
)

"""