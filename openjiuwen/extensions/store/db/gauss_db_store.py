# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy.ext.asyncio import AsyncEngine

from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
import openjiuwen.extensions.store.db.gauss_dialect


class GaussDbStore(BaseDbStore):
    """GaussDB database store implementation.

    This class wraps an AsyncEngine for GaussDB database operations.
    Importing this module triggers gauss_dialect registration via import_dbapi.
    """

    def __init__(self, async_conn: AsyncEngine):
        """Initialize GaussDbStore with an AsyncEngine.

        Args:
            async_conn: The asynchronous SQLAlchemy engine instance.
        """
        self.async_conn = async_conn

    def get_async_engine(self) -> AsyncEngine:
        """Return the stored AsyncEngine instance.

        Returns:
            AsyncEngine: The asynchronous SQLAlchemy engine instance.
        """
        return self.async_conn
