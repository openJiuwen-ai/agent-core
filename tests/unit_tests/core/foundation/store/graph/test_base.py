# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph store factory (base.py)."""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.graph.base import GraphStoreFactory
from openjiuwen.core.foundation.store.graph.base_graph_store import GraphStore
from openjiuwen.core.foundation.store.graph.config import GraphConfig


class TestGraphStoreFactoryCannotInstantiate:
    """GraphStoreFactory must not be instantiated."""

    @staticmethod
    def test_instantiate_raises_runtime_error():
        """GraphStoreFactory() raises RuntimeError."""
        with pytest.raises(BaseError, match="must not be instantiated"):
            GraphStoreFactory()


class TestRegisterBackend:
    """Tests for register_backend."""

    @staticmethod
    def test_empty_name_raises_error():
        """Empty name raises error."""
        with pytest.raises(BaseError, match="empty value"):
            GraphStoreFactory.register_backend("", MagicMock())

    @staticmethod
    def test_duplicate_name_without_force_raises_error():
        """Duplicate name without force=True raises error."""
        mock_backend = MagicMock(spec=GraphStore, __name__="Mock Name")
        GraphStoreFactory.register_backend("test_dup", mock_backend)
        try:
            with pytest.raises(BaseError, match="exists, name="):
                GraphStoreFactory.register_backend("test_dup", mock_backend)
        finally:
            if "test_dup" in GraphStoreFactory.class_map:
                del GraphStoreFactory.class_map["test_dup"]

    @staticmethod
    def test_duplicate_name_with_force_overwrites():
        """Duplicate name with force=True overwrites."""
        mock1 = MagicMock(spec=GraphStore)
        mock2 = MagicMock(spec=GraphStore)
        GraphStoreFactory.register_backend("test_force", mock1)
        GraphStoreFactory.register_backend("test_force", mock2, force=True)
        try:
            assert GraphStoreFactory.class_map["test_force"] is mock2
        finally:
            if "test_force" in GraphStoreFactory.class_map:
                del GraphStoreFactory.class_map["test_force"]

    @staticmethod
    def test_backend_not_graphstore_raises_when_force_false():
        """Backend not implementing GraphStore: raise error when force=False."""
        with pytest.raises(BaseError, match="did not implement GraphStore"):
            GraphStoreFactory.register_backend("bad", object(), force=False)

    @staticmethod
    def test_backend_not_graphstore_force_true_logs_and_registers():
        """Backend not GraphStore with force=True: log and register."""
        with patch("openjiuwen.core.foundation.store.graph.base.logger") as logger:
            GraphStoreFactory.register_backend("bad_force", object(), force=True)
            try:
                assert "bad_force" in GraphStoreFactory.class_map
                logger.warning.assert_called()
            finally:
                if "bad_force" in GraphStoreFactory.class_map:
                    del GraphStoreFactory.class_map["bad_force"]

    @staticmethod
    def test_happy_path_register_and_retrieve():
        """Happy path: register and retrieve from class_map."""
        mock_backend = MagicMock(spec=GraphStore)
        GraphStoreFactory.register_backend("happy_backend", mock_backend)
        try:
            assert GraphStoreFactory.class_map["happy_backend"] is mock_backend
        finally:
            del GraphStoreFactory.class_map["happy_backend"]


class TestFromConfig:
    """Tests for from_config."""

    @staticmethod
    def test_unknown_backend_raises_error():
        """Unknown backend (not in class_map and not 'milvus') raises error."""
        config = MagicMock(spec=GraphConfig)
        config.backend = "unknown_backend_xyz"
        with pytest.raises(BaseError, match="please register it first"):
            GraphStoreFactory.from_config(config)

    @staticmethod
    def test_known_backend_returns_from_config():
        """Known backend: returns backend_cls.from_config(config, **kwargs)."""
        mock_instance = MagicMock()
        mock_cls = MagicMock(spec=GraphStore)
        mock_cls.from_config.return_value = mock_instance
        config = MagicMock(spec=GraphConfig)
        config.backend = "known_test"
        GraphStoreFactory.register_backend("known_test", mock_cls)
        try:
            result = GraphStoreFactory.from_config(config, foo="bar")
            assert result is mock_instance
            mock_cls.from_config.assert_called_once_with(config, foo="bar")
        finally:
            if "known_test" in GraphStoreFactory.class_map:
                del GraphStoreFactory.class_map["known_test"]

    @staticmethod
    def test_milvus_backend_registers_and_retries():
        """backend_name='milvus': patch register_milvus_support and avoid testing milvus internals."""
        # Ensure milvus module is loaded so our patch is not overwritten on import
        import openjiuwen.core.foundation.store.graph.milvus as milvus_mod  # noqa: F401

        saved_milvus = GraphStoreFactory.class_map.pop("milvus", None)
        config = MagicMock(spec=GraphConfig)
        config.backend = "milvus"
        config.uri = "http://localhost:19530"
        mock_store = MagicMock(spec=GraphStore)
        mock_cls = MagicMock(spec=GraphStore)
        mock_cls.from_config.return_value = mock_store
        try:
            with patch.object(milvus_mod, "register_milvus_support") as register:

                def register_milvus():
                    GraphStoreFactory.class_map["milvus"] = mock_cls

                register.side_effect = register_milvus
                result = GraphStoreFactory.from_config(config)
                register.assert_called_once()
                assert result is mock_store
        finally:
            GraphStoreFactory.class_map.pop("milvus", None)
            if saved_milvus is not None:
                GraphStoreFactory.class_map["milvus"] = saved_milvus
