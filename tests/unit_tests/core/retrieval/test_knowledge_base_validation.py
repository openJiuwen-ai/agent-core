# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Knowledge base configuration validation test cases

Tests for the configuration compatibility checks between VectorStore and IndexManager.
"""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase


class ConcreteKnowledgeBase(KnowledgeBase):
    """Concrete knowledge base implementation for testing abstract base class"""

    async def parse_files(self, file_paths, **kwargs):
        return []

    async def add_documents(self, documents, **kwargs):
        return [doc.id_ for doc in documents]

    async def retrieve(self, query, config=None, **kwargs):
        return []

    async def delete_documents(self, doc_ids, **kwargs):
        return True

    async def update_documents(self, documents, **kwargs):
        return [doc.id_ for doc in documents]

    async def get_statistics(self):
        return {"kb_id": self.config.kb_id}


class TestKnowledgeBaseConfigurationValidation:
    """Test configuration validation between VectorStore and IndexManager"""

    @staticmethod
    def _create_compatible_mocks():
        """Helper to create compatible vector_store and index_manager mocks"""
        mock_vector_store = MagicMock()
        mock_index_manager = MagicMock()
        attrs = [
            "database_name",
            "distance_metric",
            "index_type",
            "text_field",
            "vector_field",
            "sparse_vector_field",
            "metadata_field",
            "doc_id_field",
        ]
        for attr in attrs:
            setattr(mock_vector_store, attr, f"test_{attr}")
            setattr(mock_index_manager, attr, f"test_{attr}")
        return mock_vector_store, mock_index_manager

    @staticmethod
    def test_validation_passes_when_all_attributes_match():
        """Test that validation passes when all attributes match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()

        # Should not raise exception
        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
            index_manager=mock_index_manager,
        )
        assert kb.vector_store == mock_vector_store
        assert kb.index_manager == mock_index_manager

    @staticmethod
    def test_validation_skipped_when_vector_store_is_none():
        """Test that validation is skipped when vector_store is None"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_index_manager = MagicMock()
        # Set attributes on index_manager
        for attr in ["database_name", "distance_metric", "index_type"]:
            setattr(mock_index_manager, attr, "test_value")

        # Should not raise exception even though index_manager has attributes
        kb = ConcreteKnowledgeBase(
            config=config,
            index_manager=mock_index_manager,
        )
        assert kb.vector_store is None
        assert kb.index_manager == mock_index_manager

    @staticmethod
    def test_validation_skipped_when_index_manager_is_none():
        """Test that validation is skipped when index_manager is None"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = MagicMock()
        # Set attributes on vector_store
        for attr in ["database_name", "distance_metric", "index_type"]:
            setattr(mock_vector_store, attr, "test_value")

        # Should not raise exception even though vector_store has attributes
        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
        )
        assert kb.vector_store == mock_vector_store
        assert kb.index_manager is None

    @staticmethod
    def test_validation_runs_when_setting_vector_store_after_index_manager():
        """Test that validation runs when setting vector_store after index_manager"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()

        kb = ConcreteKnowledgeBase(config=config, index_manager=mock_index_manager)
        # Setting vector_store should trigger validation
        kb.vector_store = mock_vector_store
        assert kb.vector_store == mock_vector_store

    @staticmethod
    def test_validation_runs_when_setting_index_manager_after_vector_store():
        """Test that validation runs when setting index_manager after vector_store"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()

        kb = ConcreteKnowledgeBase(config=config, vector_store=mock_vector_store)
        # Setting index_manager should trigger validation
        kb.index_manager = mock_index_manager
        assert kb.index_manager == mock_index_manager

    @staticmethod
    def test_validation_fails_on_mismatch_database_name():
        """Test that validation fails when database_name doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.database_name = "db1"
        mock_index_manager.database_name = "db2"

        with pytest.raises(BaseError, match="incompatible database_name configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_distance_metric():
        """Test that validation fails when distance_metric doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.distance_metric = "cosine"
        mock_index_manager.distance_metric = "euclidean"

        with pytest.raises(BaseError, match="incompatible distance_metric configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_text_field():
        """Test that validation fails when text_field doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.text_field = "text"
        mock_index_manager.text_field = "content"

        with pytest.raises(BaseError, match="incompatible text_field configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_vector_field():
        """Test that validation fails when vector_field doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.vector_field = "embedding"
        mock_index_manager.vector_field = "vector"

        with pytest.raises(BaseError, match="incompatible vector_field configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_sparse_vector_field():
        """Test that validation fails when sparse_vector_field doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.sparse_vector_field = "sparse_embedding"
        mock_index_manager.sparse_vector_field = "bm25_vector"

        with pytest.raises(BaseError, match="incompatible sparse_vector_field configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_metadata_field():
        """Test that validation fails when metadata_field doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.metadata_field = "meta"
        mock_index_manager.metadata_field = "metadata"

        with pytest.raises(BaseError, match="incompatible metadata_field configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_fails_on_mismatch_doc_id_field():
        """Test that validation fails when doc_id_field doesn't match"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.doc_id_field = "document_id"
        mock_index_manager.doc_id_field = "doc_id"

        with pytest.raises(BaseError, match="incompatible doc_id_field configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )

    @staticmethod
    def test_validation_error_message_includes_type_names():
        """Test that error message includes the type names of vector_store and index_manager"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        mock_vector_store.database_name = "db1"
        mock_index_manager.database_name = "db2"

        with pytest.raises(BaseError) as exc_info:
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )
        error_msg = str(exc_info.value)
        assert "MagicMock" in error_msg or "Vector Store" in error_msg
        assert "Index manager" in error_msg or "index_manager" in error_msg
        assert "db1" in error_msg
        assert "db2" in error_msg

    @staticmethod
    def test_validation_passes_when_attributes_are_none():
        """Test that validation passes when attributes are None (both None)"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store = MagicMock()
        mock_index_manager = MagicMock()
        # Explicitly set all attributes to None - both having None should be considered matching
        attrs = [
            "database_name",
            "distance_metric",
            "index_type",
            "text_field",
            "vector_field",
            "sparse_vector_field",
            "metadata_field",
            "doc_id_field",
        ]
        for attr in attrs:
            setattr(mock_vector_store, attr, None)
            setattr(mock_index_manager, attr, None)

        kb = ConcreteKnowledgeBase(
            config=config,
            vector_store=mock_vector_store,
            index_manager=mock_index_manager,
        )
        assert kb.vector_store == mock_vector_store
        assert kb.index_manager == mock_index_manager

    @staticmethod
    def test_validation_fails_on_first_mismatch():
        """Test that validation fails on the first mismatched attribute"""
        config = KnowledgeBaseConfig(kb_id="test_kb")
        mock_vector_store, mock_index_manager = TestKnowledgeBaseConfigurationValidation._create_compatible_mocks()
        # Set multiple mismatches
        mock_vector_store.database_name = "db1"
        mock_index_manager.database_name = "db2"
        mock_vector_store.distance_metric = "metric1"
        mock_index_manager.distance_metric = "metric2"

        with pytest.raises(BaseError, match="incompatible database_name configs"):
            ConcreteKnowledgeBase(
                config=config,
                vector_store=mock_vector_store,
                index_manager=mock_index_manager,
            )
