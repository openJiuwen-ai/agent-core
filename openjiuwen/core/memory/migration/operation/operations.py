# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from openjiuwen.core.foundation.store import BaseKVStore
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation


UpdateKVCallable = Callable[[BaseKVStore], Awaitable[None]]


# ==================== SQL Operations ====================
@dataclass
class AddColumnOperation(BaseOperation):
    """
    Add a new column to a table.

    Attributes:
        table (str): Name of the target table.
        column_name (str): Name of the column to add.
        column_type (str): Data type of the new column.
        nullable (bool): Whether the column can contain NULL values. Defaults to True.
        default (Any, optional): Default value for the column. Defaults to None.
    """
    table: str
    column_name: str
    column_type: str
    nullable: bool = True
    default: Any = None


@dataclass
class RenameColumnOperation(BaseOperation):
    """
    Rename a column in a table.

    Attributes:
        table (str): Name of the target table.
        old_column_name (str): Original name of the column.
        new_column_name (str): New name of the column.
    """
    table: str
    old_column_name: str
    new_column_name: str


@dataclass
class UpdateColumnTypeOperation(BaseOperation):
    """
    Update the data type of existing column.

    Attributes:
        table (str): Name of the target table.
        column_name (str): Name of the column to modify.
        new_column_type (str): New data type for the column.
    """
    table: str
    column_name: str
    new_column_type: str


# ==================== Vector Operations ====================
@dataclass
class AddScalarFieldOperation(BaseOperation):
    """
    Add a scalar field to a vector data type.

    Attributes:
        data_type (str): Name of the vector data type.
        field_name (str): Name of the scalar field to add.
        field_type (str): Data type of the field (e.g., "int", "float", "string").
        default_value (Any, optional): Default value for the field. Defaults to None.
    """
    data_type: str
    field_name: str
    field_type: str
    default_value: Any = None


@dataclass
class RenameScalarFieldOperation(BaseOperation):
    """
    Rename a scalar field in a vector data type.

    Attributes:
        data_type (str): Name of the vector data type.
        old_field_name (str): Original name of the scalar field.
        new_field_name (str): New name of the scalar field.
    """
    data_type: str
    old_field_name: str
    new_field_name: str


@dataclass
class UpdateScalarFieldTypeOperation(BaseOperation):
    """
    Update the data type of scalar field in a vector data type.

    Attributes:
        data_type (str): Name of the vector data type.
        field_name (str): Name of the scalar field to modify.
        new_field_type (str): New data type for the field.
    """
    data_type: str
    field_name: str
    new_field_type: str


@dataclass
class UpdateEmbeddingDimensionOperation(BaseOperation):
    """
    Update the embedding dimension of a vector data type.

    Attributes:
        data_type (str): Name of the vector data type.
        field_name (str): Name of the embedding field (e.g., "embedding").
        new_dimension (int): New dimension of the embedding vectors.
        recompute_embedding_func (Callable[[Any], Any]): Callback function that recomputes
            the embedding for each vector. Called by the Adapter.
        batch_size (int): Batch size to use when recomputing embeddings (used by the Adapter). Defaults to 1000.
    """
    data_type: str
    field_name: str
    new_dimension: int
    recompute_embedding_func: Callable[[Any], Any] = None
    batch_size: int = 1000


# ==================== KV Operations ====================
@dataclass
class UpdateKVOperation(BaseOperation):
    """
    Update a key-value pair via a provided callable.

    Attributes:
        update_func (UpdateKVCallable): Callable that performs the key-value update.
    """
    update_func: UpdateKVCallable


# ==================== Message Operations ====================
MessageUpdateCallable = Callable[[Any], Awaitable[None]]


@dataclass
class UpdateMessageOperation(BaseOperation):
    """
    Update message store data via a provided callable.

    Attributes:
        update_func (MessageUpdateCallable): Callable that performs the message store update.
    """
    update_func: MessageUpdateCallable


# ==================== Index Version Migration Operations ====================
@dataclass
class RenameMemoryDocFieldOperation(BaseOperation):
    """
    Rename a field in MemoryDoc objects.

    Attributes:
        old_field_name (str): Original name of the field to rename.
        new_field_name (str): New name for the field.
    """
    old_field_name: str
    new_field_name: str


@dataclass
class TransformMemoryDocFieldOperation(BaseOperation):
    """
    Transform the value of a field in MemoryDoc objects.

    Attributes:
        field_name (str): Name of the field to transform.
        transform_func (Callable[[Any], Any]): Function to apply to the field value.
    """
    field_name: str
    transform_func: Callable[[Any], Any]


@dataclass
class AddMemoryDocFieldOperation(BaseOperation):
    """
    Add a new field to MemoryDoc objects.

    Attributes:
        field_name (str): Name of the field to add.
        default_value_or_func (Any | Callable[[], Any]): Default value or function to generate the default.
    """
    field_name: str
    default_value_or_func: Any | Callable[[], Any]


@dataclass
class RemoveMemoryDocFieldOperation(BaseOperation):
    """
    Remove a field from MemoryDoc objects.

    Attributes:
        field_name (str): Name of the field to remove.
    """
    field_name: str
