# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncEngine


class BaseDbStore(ABC):
    """
    Abstract base class for raw DB access (provides access to a SQLAlchemy AsyncEngine).

    **Plugin authoring**: Stable public API. Same rules as
    :class:`openjiuwen.core.foundation.store.base_kv_store.BaseKVStore` —
    used via direct import, no factory.

    See :class:`openjiuwen.core.foundation.store.base_vector_store.BaseVectorStore`
    for the plugin contract and compatibility policy.
    """

    @abstractmethod
    def get_async_engine(self) -> AsyncEngine:
        """
        Return the asynchronous SQLAlchemy engine，allowing callers to perform async database operations
        such as issuing raw SQL statements or using SQLAlchemy's asyncio extension.

        Returns:
            AsyncEngine: The asynchronous SQLAlchemy engine instance.
        """
        pass
