# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for DefaultDbStore."""

from unittest.mock import MagicMock

from sqlalchemy.ext.asyncio import AsyncEngine

from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore


class TestDefaultDbStore:
    """Tests for DefaultDbStore class."""

    @classmethod
    def test_init_with_async_engine(cls):
        """Test initialization with an AsyncEngine."""
        mock_engine = MagicMock(spec=AsyncEngine)
        store = DefaultDbStore(async_conn=mock_engine)

        assert store.async_conn is mock_engine

    @classmethod
    def test_get_async_engine(cls):
        """Test get_async_engine returns the stored AsyncEngine."""
        mock_engine = MagicMock(spec=AsyncEngine)
        store = DefaultDbStore(async_conn=mock_engine)

        result = store.get_async_engine()

        assert result is mock_engine
        assert isinstance(result, AsyncEngine)

    @classmethod
    def test_get_async_engine_returns_same_instance(cls):
        """Test that get_async_engine returns the same instance each time."""
        mock_engine = MagicMock(spec=AsyncEngine)
        store = DefaultDbStore(async_conn=mock_engine)

        result1 = store.get_async_engine()
        result2 = store.get_async_engine()

        assert result1 is result2
