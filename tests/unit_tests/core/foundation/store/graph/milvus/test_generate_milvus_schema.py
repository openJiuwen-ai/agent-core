# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for generate_milvus_schema module."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.graph.constants import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
)
from openjiuwen.core.foundation.store.graph.database_config import (
    GraphStoreIndexConfig,
    GraphStoreStorageConfig,
)
from openjiuwen.core.foundation.store.graph.milvus.generate_milvus_schema import (
    generate_schema_and_index,
)
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO


def _make_storage_config():
    return GraphStoreStorageConfig()


def _make_embed_config():
    return GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="cosine")


class TestGenerateSchemaAndIndex:
    """Tests for generate_schema_and_index."""

    @staticmethod
    def test_entity_collection_schema_has_expected_fields():
        """ENTITY_COLLECTION schema includes name, name_embedding, relations, episodes."""
        mock_client = MagicMock()
        mock_schema = MagicMock()
        mock_index_params = MagicMock()
        mock_client.create_schema.return_value = mock_schema
        mock_client.prepare_index_params.return_value = mock_index_params

        schema, index_params = generate_schema_and_index(
            mock_client,
            collection=ENTITY_COLLECTION,
            storage_config=_make_storage_config(),
            embed_config=_make_embed_config(),
            dim=64,
        )

        assert schema is mock_schema
        assert index_params is mock_index_params
        # Common + entity-specific: uuid, created_at, user_id, obj_type, language, metadata,
        # name, name_embedding, attributes, relations, episodes, content, content_embedding, content_bm25
        assert mock_schema.add_field.call_count >= 10
        add_field_calls = [c[0][0] for c in mock_schema.add_field.call_args_list]
        assert "name" in add_field_calls
        assert "name_embedding" in add_field_calls
        assert "relations" in add_field_calls
        assert "episodes" in add_field_calls
        assert mock_index_params.add_index.call_count >= 2  # name_embedding, content_embedding, content_bm25

    @staticmethod
    def test_relation_collection_schema_has_expected_fields():
        """RELATION_COLLECTION schema includes valid_since, valid_until, lhs, rhs."""
        mock_client = MagicMock()
        mock_schema = MagicMock()
        mock_index_params = MagicMock()
        mock_client.create_schema.return_value = mock_schema
        mock_client.prepare_index_params.return_value = mock_index_params

        schema, index_params = generate_schema_and_index(
            mock_client,
            collection=RELATION_COLLECTION,
            storage_config=_make_storage_config(),
            embed_config=_make_embed_config(),
            dim=64,
        )

        add_field_calls = [c[0][0] for c in mock_schema.add_field.call_args_list]
        assert "valid_since" in add_field_calls
        assert "valid_until" in add_field_calls
        assert "lhs" in add_field_calls
        assert "rhs" in add_field_calls

    @staticmethod
    def test_episode_collection_schema_has_expected_fields():
        """EPISODE_COLLECTION schema includes valid_since, entities."""
        mock_client = MagicMock()
        mock_schema = MagicMock()
        mock_index_params = MagicMock()
        mock_client.create_schema.return_value = mock_schema
        mock_client.prepare_index_params.return_value = mock_index_params

        generate_schema_and_index(
            mock_client,
            collection=EPISODE_COLLECTION,
            storage_config=_make_storage_config(),
            embed_config=_make_embed_config(),
            dim=64,
        )

        add_field_calls = [c[0][0] for c in mock_schema.add_field.call_args_list]
        assert "valid_since" in add_field_calls
        assert "entities" in add_field_calls

    @staticmethod
    def test_unknown_collection_raises_not_implemented_error():
        """Unknown collection name raises error."""
        mock_client = MagicMock()
        with pytest.raises(BaseError, match="not supported, collection=UNKNOWN_COLLECTION"):
            generate_schema_and_index(
                mock_client,
                collection="UNKNOWN_COLLECTION",
                storage_config=_make_storage_config(),
                embed_config=_make_embed_config(),
                dim=64,
            )

    @staticmethod
    def test_metric_type_dot_maps_to_ip():
        """distance_metric 'dot' is mapped to IP for Milvus."""
        mock_client = MagicMock()
        mock_schema = MagicMock()
        mock_index_params = MagicMock()
        mock_client.create_schema.return_value = mock_schema
        mock_client.prepare_index_params.return_value = mock_index_params
        embed_config = GraphStoreIndexConfig(
            index_type=MilvusAUTO(),
            distance_metric="dot",
        )

        generate_schema_and_index(
            mock_client,
            collection=ENTITY_COLLECTION,
            storage_config=_make_storage_config(),
            embed_config=embed_config,
            dim=64,
        )

        all_kwargs = [c[1] for c in mock_index_params.add_index.call_args_list if len(c) > 1]
        assert any(kw.get("metric_type") == "IP" for kw in all_kwargs)
