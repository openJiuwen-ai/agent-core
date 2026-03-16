# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph store config."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.database_config import (
    GraphStoreIndexConfig,
)
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO


def _minimal_embed_config():
    """Helper to create minimal GraphStoreIndexConfig for GraphConfig."""
    return GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="cosine")


class TestGraphConfigDefaults:
    """Test GraphConfig default field values."""

    @staticmethod
    def test_defaults():
        """Defaults: name='', token='', backend='milvus', timeout=15.0, max_concurrent=10, embed_dim=512, etc."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(uri="/tmp/graph_db", db_embed_config=_minimal_embed_config())
        assert cfg.name == ""
        assert cfg.token == ""
        assert cfg.backend == "milvus"
        assert cfg.timeout == 15.0
        assert cfg.max_concurrent == 10
        assert cfg.embed_dim == 512
        assert cfg.embed_batch_size == 10
        assert cfg.embedding_model is None
        assert cfg.request_max_retries == 5


class TestGraphConfigCheckExtras:
    """Test check_extras validator."""

    @staticmethod
    def test_valid_dict_with_string_keys_passes():
        """Valid dict with string keys passes."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(
                uri="/tmp/graph_db",
                extras={"alias": "default"},
                db_embed_config=_minimal_embed_config(),
            )
        assert cfg.extras == {"alias": "default"}

    @staticmethod
    def test_non_dict_extras_raises():
        """Non-dict extras raises."""
        with pytest.raises((ValidationError, Exception)):
            GraphConfig(
                uri="/tmp/graph_db",
                extras="not_a_dict",
                db_embed_config=_minimal_embed_config(),
            )

    @staticmethod
    def test_dict_with_non_string_keys_raises():
        """Dict with non-string keys raises."""
        with pytest.raises((ValidationError, Exception)):
            GraphConfig(
                uri="/tmp/graph_db",
                extras={1: "value"},
                db_embed_config=_minimal_embed_config(),
            )


class TestGraphConfigCheckValidity:
    """Test check_validity (uri) model validator."""

    @staticmethod
    def test_file_path_uri_creates_parent_dir():
        """File path URI (no ://): creates parent dir if needed (mock os.makedirs)."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs") as makedirs:
            GraphConfig(
                uri="/some/path/to/db",
                db_embed_config=_minimal_embed_config(),
            )
            makedirs.assert_called_once_with("/some/path/to", exist_ok=True)

    @staticmethod
    def test_file_path_uri_makedirs_failure_no_raise():
        """Invalid/missing parent: makedirs OSError does not raise; config still returned."""
        # Patch os.makedirs so only the graph db path fails; other paths (e.g. logger) use real makedirs
        real_makedirs = os.makedirs

        def makedirs_side_effect(path, *args, **kwargs):
            if "nonexistent" in str(path):
                raise OSError("Permission denied")
            return real_makedirs(path, *args, **kwargs)

        with patch("os.makedirs", side_effect=makedirs_side_effect):
            cfg = GraphConfig(
                uri="/nonexistent/path/db",
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.uri == "/nonexistent/path/db"

    @staticmethod
    def test_network_uri_success_no_raise():
        """Network URI: socket.create_connection succeeds, no raise."""
        with patch("openjiuwen.core.foundation.store.graph.config.socket.create_connection") as create_conn:

            def _return_none(*args, **kwargs):
                return None

            setattr(create_conn.return_value, "__enter__", _return_none)
            setattr(create_conn.return_value, "__exit__", _return_none)
            cfg = GraphConfig(
                uri="http://localhost:19530",
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.uri == "http://localhost:19530"

    @staticmethod
    def test_network_uri_failure_logs_error():
        """Network URI: connection failure logs error, still returns self (no raise)."""
        with patch("openjiuwen.core.foundation.store.graph.config.socket.create_connection") as create_conn:
            create_conn.side_effect = OSError("Connection refused")
            with patch("openjiuwen.core.foundation.store.graph.config.store_logger") as logger:
                cfg = GraphConfig(
                    uri="http://badhost:19530",
                    db_embed_config=_minimal_embed_config(),
                )
                logger.error.assert_called()
                assert cfg.uri == "http://badhost:19530"


class TestGraphConfigFieldConstraints:
    """Test field constraints."""

    @staticmethod
    def test_timeout_gt_zero():
        """timeout > 0."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(
                uri="/tmp/db",
                timeout=1.0,
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.timeout == 1.0
        with pytest.raises(ValidationError):
            GraphConfig(
                uri="/tmp/db",
                timeout=0,
                db_embed_config=_minimal_embed_config(),
            )

    @staticmethod
    def test_max_concurrent_ge_zero():
        """max_concurrent >= 0."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(
                uri="/tmp/db",
                max_concurrent=0,
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.max_concurrent == 0
        with pytest.raises(ValidationError):
            GraphConfig(
                uri="/tmp/db",
                max_concurrent=-1,
                db_embed_config=_minimal_embed_config(),
            )

    @staticmethod
    def test_embed_dim_ge_32():
        """embed_dim >= 32."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(
                uri="/tmp/db",
                embed_dim=32,
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.embed_dim == 32
        with pytest.raises(ValidationError):
            GraphConfig(
                uri="/tmp/db",
                embed_dim=31,
                db_embed_config=_minimal_embed_config(),
            )

    @staticmethod
    def test_embed_batch_size_ge_one():
        """embed_batch_size >= 1."""
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            cfg = GraphConfig(
                uri="/tmp/db",
                embed_batch_size=1,
                db_embed_config=_minimal_embed_config(),
            )
            assert cfg.embed_batch_size == 1
        with pytest.raises(ValidationError):
            GraphConfig(
                uri="/tmp/db",
                embed_batch_size=0,
                db_embed_config=_minimal_embed_config(),
            )
