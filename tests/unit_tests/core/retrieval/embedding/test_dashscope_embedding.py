# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
DashScope embedding model implementation test cases
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import EmbeddingConfig, MultimodalDocument
from openjiuwen.core.retrieval.embedding.dashscope_embedding import DashscopeEmbedding


@pytest.fixture
def embedding_config():
    """Create embedding model configuration"""
    return EmbeddingConfig(
        model_name="test-model",
        api_key="test-api-key",
        base_url="https://dashscope.aliyuncs.com/api/v1/",
    )


@pytest.fixture
def embedding_config_no_key():
    """Create embedding model configuration without API key"""
    return EmbeddingConfig(
        model_name="test-model",
        base_url="https://dashscope.aliyuncs.com/api/v1/",
    )


class TestDashscopeEmbeddingInit:
    """DashScope embedding initialization tests"""

    @staticmethod
    def test_init_with_api_key(embedding_config):
        """Test initialization with API key"""
        model = DashscopeEmbedding(config=embedding_config)
        assert model.model_name == "test-model"
        assert model.api_key == "test-api-key"
        assert model.api_url == "https://dashscope.aliyuncs.com/api/v1/"

    @staticmethod
    def test_init_without_api_key(embedding_config_no_key):
        """Test initialization without API key"""
        model = DashscopeEmbedding(config=embedding_config_no_key)
        assert model.api_key is None

    @staticmethod
    def test_init_with_custom_params(embedding_config):
        """Test initialization with custom parameters"""
        model = DashscopeEmbedding(
            config=embedding_config,
            timeout=120,
            max_retries=5,
            max_batch_size=16,
            max_concurrent=25,
        )
        assert model.timeout == 120
        assert model.max_retries == 5
        assert model.max_batch_size == 16
        assert model.limiter is not None
        assert getattr(model.limiter, "_value") == 25

    @staticmethod
    def test_init_with_dimension_matryoshka(embedding_config):
        """Test initialization with dimension (Matryoshka) sets request params"""
        model = DashscopeEmbedding(config=embedding_config, dimension=256)
        assert model.matryoshka_dimension is True
        assert getattr(model, "_dimension") == 256
        assert getattr(model, "_request_params", {}).get("dimension") == 256

    @staticmethod
    def test_init_without_dimension(embedding_config):
        """Test initialization without dimension"""
        model = DashscopeEmbedding(config=embedding_config)
        assert model.matryoshka_dimension is False
        assert getattr(model, "_dimension") is None
        assert "dimension" not in getattr(model, "_request_params", dict(dimension=256))


class TestDashscopeEmbeddingHandleResponse:
    """Tests for _handle_dashscope_api_resp"""

    @staticmethod
    def test_handle_response_success(embedding_config):
        """Test successful response with embeddings"""
        model = DashscopeEmbedding(config=embedding_config)
        resp = Mock()
        resp.status_code = 200
        resp.output = {
            "embeddings": [
                {"index": 0, "embedding": [0.1] * 384},
                {"index": 1, "embedding": [0.2] * 384},
            ]
        }

        result = getattr(model, "_handle_dashscope_api_resp")(resp, 0)

        assert result is not None
        assert len(result) == 2
        assert len(result[0]) == 384
        assert result[0][0] == 0.1
        assert result[1][0] == 0.2

    @staticmethod
    def test_handle_response_sorts_by_index(embedding_config):
        """Test that embeddings are sorted by index"""
        model = DashscopeEmbedding(config=embedding_config)
        resp = Mock()
        resp.status_code = 200
        resp.output = {
            "embeddings": [
                {"index": 1, "embedding": [0.2] * 384},
                {"index": 0, "embedding": [0.1] * 384},
            ]
        }

        result = getattr(model, "_handle_dashscope_api_resp")(resp, 0)

        assert len(result) >= 2
        assert result[0][0] == 0.1
        assert result[1][0] == 0.2

    @staticmethod
    def test_handle_response_sets_dimension_when_none(embedding_config):
        """Test that dimension is cached from first successful response"""
        model = DashscopeEmbedding(config=embedding_config)
        assert getattr(model, "_dimension") is None
        resp = Mock()
        resp.status_code = 200
        resp.output = {"embeddings": [{"index": 0, "embedding": [0.1] * 768}]}

        getattr(model, "_handle_dashscope_api_resp")(resp, 0)

        assert getattr(model, "_dimension") == 768

    @staticmethod
    def test_handle_response_empty_embeddings_raises(embedding_config):
        """Test empty embeddings list raises error"""
        model = DashscopeEmbedding(config=embedding_config)
        resp = Mock()
        resp.status_code = 200
        resp.output = {"embeddings": []}

        with pytest.raises(BaseError, match="The embeddings field in response is empty"):
            getattr(model, "_handle_dashscope_api_resp")(resp, 0)

    @staticmethod
    def test_handle_response_no_embeddings_key_raises(embedding_config):
        """Test missing embeddings key in output raises error"""
        model = DashscopeEmbedding(config=embedding_config)
        resp = Mock()
        resp.status_code = 200
        resp.output = {"other_key": "value"}

        with pytest.raises(BaseError, match="No embeddings in response"):
            getattr(model, "_handle_dashscope_api_resp")(resp, 0)

    @staticmethod
    def test_handle_response_non_200_on_last_attempt_raises(embedding_config):
        """Test non-200 status on last retry attempt raises"""
        model = DashscopeEmbedding(config=embedding_config, max_retries=2)
        resp = Mock()
        resp.status_code = 500
        resp.code = "InternalError"
        resp.message = "Server error"

        with pytest.raises(BaseError, match="Failed to get embedding after 2 attempts"):
            getattr(model, "_handle_dashscope_api_resp")(resp, 1)

    @staticmethod
    def test_handle_response_non_200_not_last_attempt_falls_through_to_output(embedding_config):
        """Test non-200 status before last attempt still parses output (no early return None)."""
        model = DashscopeEmbedding(config=embedding_config, max_retries=3)
        resp = Mock()
        resp.status_code = 500
        resp.code = "InternalError"
        resp.message = "Server error"
        resp.output = {}  # No embeddings key -> raises

        with pytest.raises(BaseError, match="No embeddings in response"):
            getattr(model, "_handle_dashscope_api_resp")(resp, 0)


class TestDashscopeEmbeddingMultimodal:
    """Tests for embed_multimodal and embed_multimodal_sync"""

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_multimodal_success(embedding_config):
        """Test embed_multimodal with valid MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = DashscopeEmbedding(config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings", AsyncMock(return_value=[mock_embedding]))

        embedding = await model.embed_multimodal(doc)

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)
        getattr(model, "_get_embeddings").assert_called_once()
        call_args = getattr(model, "_get_embeddings").call_args
        assert len(call_args[0]) > 0
        assert call_args[0][0] == [doc.dashscope_input]

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_multimodal_invalid_input(embedding_config):
        """Test embed_multimodal with invalid input (not MultimodalDocument)"""
        model = DashscopeEmbedding(config=embedding_config)

        with pytest.raises(BaseError, match="input provided for multimodal embedding is not a MultimodalDocument"):
            await model.embed_multimodal("not a document")

    @staticmethod
    def test_embed_multimodal_sync_success(embedding_config):
        """Test embed_multimodal_sync with valid MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = DashscopeEmbedding(config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings_sync", Mock(return_value=[mock_embedding]))

        embedding = model.embed_multimodal_sync(doc)

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)
        getattr(model, "_get_embeddings_sync").assert_called_once()
        call_args = getattr(model, "_get_embeddings_sync").call_args
        assert len(call_args[0]) > 0
        assert call_args[0][0] == [doc.dashscope_input]

    @staticmethod
    def test_embed_multimodal_sync_invalid_input(embedding_config):
        """Test embed_multimodal_sync with invalid input (not MultimodalDocument)"""
        model = DashscopeEmbedding(config=embedding_config)

        with pytest.raises(BaseError, match="input provided for multimodal embedding is not a MultimodalDocument"):
            model.embed_multimodal_sync("not a document")


class TestDashscopeEmbeddingDocuments:
    """Tests for embed_documents and embed_documents_sync"""

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_documents_success(embedding_config):
        """Test embed_documents with list of texts"""
        model = DashscopeEmbedding(config=embedding_config)
        setattr(
            model,
            "_get_embeddings",
            AsyncMock(return_value=[[0.1] * 384, [0.2] * 384, [0.3] * 384]),
        )

        texts = ["text 1", "text 2", "text 3"]
        embeddings = await model.embed_documents(texts)

        assert len(embeddings) == 3
        assert all(len(emb) == 384 for emb in embeddings)

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_documents_with_multimodal_docs(embedding_config):
        """Test embed_documents converts MultimodalDocument to dashscope_input"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello")
        model = DashscopeEmbedding(config=embedding_config)
        setattr(model, "_get_embeddings", AsyncMock(return_value=[[0.1] * 384]))

        await model.embed_documents([doc])

        call_args = getattr(model, "_get_embeddings").call_args
        # Batch is list of one: [doc.dashscope_input]
        assert len(call_args[0]) > 0
        assert call_args[0][0] == [doc.dashscope_input]

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_documents_mixed_str_and_multimodal_docs(embedding_config):
        """Test embed_documents with list containing both str and MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello")
        model = DashscopeEmbedding(config=embedding_config)
        setattr(
            model,
            "_get_embeddings",
            AsyncMock(return_value=[[0.1] * 384, [0.2] * 384, [0.3] * 384]),
        )

        mixed = ["plain text", doc, "another string"]
        embeddings = await model.embed_documents(mixed)

        assert len(embeddings) == 3
        # Batch passed to _get_embeddings: str unchanged, doc -> dashscope_input
        call_args = getattr(model, "_get_embeddings").call_args
        assert len(call_args[0]) > 0
        batch = call_args[0][0]
        assert len(batch) >= 3
        assert batch[0] == "plain text"
        assert batch[1] == doc.dashscope_input
        assert batch[2] == "another string"

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_documents_respects_max_batch_size(embedding_config):
        """Test embed_documents respects max_batch_size"""
        model = DashscopeEmbedding(config=embedding_config, max_batch_size=2)
        mock_get = AsyncMock(side_effect=[[[0.1] * 384, [0.2] * 384], [[0.3] * 384]])
        setattr(model, "_get_embeddings", mock_get)

        texts = ["a", "b", "c"]
        embeddings = await model.embed_documents(texts)

        assert len(embeddings) == 3
        assert mock_get.call_count == 2  # batches: [a,b] and [c]

    @pytest.mark.asyncio
    @staticmethod
    async def test_embed_documents_empty_list_raises(embedding_config):
        """Test embed_documents with empty list raises"""
        model = DashscopeEmbedding(config=embedding_config)
        with pytest.raises(BaseError, match="Empty texts list provided"):
            await model.embed_documents([])

    @staticmethod
    def test_embed_documents_sync_success(embedding_config):
        """Test embed_documents_sync with list of texts"""
        model = DashscopeEmbedding(config=embedding_config)
        setattr(
            model,
            "_get_embeddings_sync",
            Mock(return_value=[[0.1] * 384, [0.2] * 384]),
        )

        texts = ["text 1", "text 2"]
        embeddings = model.embed_documents_sync(texts)

        assert len(embeddings) == 2
        assert all(len(emb) == 384 for emb in embeddings)

    @staticmethod
    def test_embed_documents_sync_mixed_str_and_multimodal_docs(embedding_config):
        """Test embed_documents_sync with list containing both str and MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello")
        model = DashscopeEmbedding(config=embedding_config)
        setattr(
            model,
            "_get_embeddings_sync",
            Mock(return_value=[[0.1] * 384, [0.2] * 384]),
        )

        mixed = ["plain text", doc]
        embeddings = model.embed_documents_sync(mixed)

        assert len(embeddings) == 2
        call_args = getattr(model, "_get_embeddings_sync").call_args
        assert len(call_args[0]) > 0
        batch = call_args[0][0]
        assert len(batch) >= 2
        assert batch[0] == "plain text"
        assert batch[1] == doc.dashscope_input

    @staticmethod
    def test_embed_documents_sync_empty_list_raises(embedding_config):
        """Test embed_documents_sync with empty list raises"""
        model = DashscopeEmbedding(config=embedding_config)
        with pytest.raises(BaseError, match="Empty texts list provided"):
            model.embed_documents_sync([])


class TestDashscopeEmbeddingGetEmbeddings:
    """Tests for _get_embeddings and _get_embeddings_sync (with mocked dashscope)"""

    @pytest.mark.asyncio
    @staticmethod
    async def test_get_embeddings_async_success(embedding_config):
        """Test _get_embeddings returns embeddings from dashscope API"""
        model = DashscopeEmbedding(config=embedding_config)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.output = {"embeddings": [{"index": 0, "embedding": [0.1] * 384}]}

        with patch("openjiuwen.core.retrieval.embedding.dashscope_embedding.dashscope") as mock_dashscope:
            mock_dashscope.AioMultiModalEmbedding.call = AsyncMock(return_value=mock_resp)

            result = await getattr(model, "_get_embeddings")("hello", session=Mock())

        assert len(result) == 1
        assert len(result[0]) == 384

    @staticmethod
    def test_get_embeddings_sync_success(embedding_config):
        """Test _get_embeddings_sync returns embeddings from dashscope API"""
        model = DashscopeEmbedding(config=embedding_config)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.output = {"embeddings": [{"index": 0, "embedding": [0.1] * 384}]}

        with patch("openjiuwen.core.retrieval.embedding.dashscope_embedding.dashscope") as mock_dashscope:
            mock_dashscope.MultiModalEmbedding.call = Mock(return_value=mock_resp)

            result = getattr(model, "_get_embeddings_sync")("hello")

        assert len(result) == 1
        assert len(result[0]) == 384

    @pytest.mark.asyncio
    @staticmethod
    async def test_get_embeddings_async_invalid_response_raises(embedding_config):
        """Test _get_embeddings raises when response has empty embeddings (no retry, exception propagates)"""
        model = DashscopeEmbedding(config=embedding_config, max_retries=3)
        empty_resp = Mock()
        empty_resp.status_code = 200
        empty_resp.output = {"embeddings": []}

        with patch("openjiuwen.core.retrieval.embedding.dashscope_embedding.dashscope") as mock_dashscope:
            mock_dashscope.AioMultiModalEmbedding.call = AsyncMock(return_value=empty_resp)

            with pytest.raises(BaseError, match="The embeddings field in response is empty"):
                await getattr(model, "_get_embeddings")("hello", session=Mock())

        assert mock_dashscope.AioMultiModalEmbedding.call.call_count == 1
