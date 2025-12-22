# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Ollama embedding model implementation test cases
"""
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from openjiuwen.core.retrieval.embedding.ollama_embedding import OllamaEmbedding
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.common.exception.exception import JiuWenBaseException


@pytest.fixture
def ollama_config():
    """Create Ollama embedding model configuration"""
    return EmbeddingConfig(
        model_name="nomic-embed-text",
        base_url="http://localhost:11434",
    )


class TestOllamaEmbedding:
    """Ollama embedding model tests"""

    @classmethod
    def test_init_success(cls, ollama_config):
        """Test successful initialization"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            assert model.model_name == "nomic-embed-text"
            assert model.base_url == "http://localhost:11434"
            assert model.embed_url == "http://localhost:11434/api/embed"

    @classmethod
    def test_init_model_not_found(cls, ollama_config):
        """Test model not found"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "other-model"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            with pytest.raises(JiuWenBaseException, match="not found in available models"):
                OllamaEmbedding(config=ollama_config)

    @classmethod
    def test_init_connection_error(cls, ollama_config):
        """Test connection error"""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Connection refused"
            )

            with pytest.raises(JiuWenBaseException, match="Could not connect to Ollama"):
                OllamaEmbedding(config=ollama_config)

    @classmethod
    def test_init_with_extra_headers(cls, ollama_config):
        """Test initialization with extra headers"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            extra_headers = {"X-Custom-Header": "custom-value"}
            model = OllamaEmbedding(config=ollama_config, extra_headers=extra_headers)

    @classmethod
    def test_init_with_custom_params(cls, ollama_config):
        """Test initialization with custom parameters"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(
                config=ollama_config, timeout=120, max_retries=5
            )
            assert model.timeout == 120
            assert model.max_retries == 5

    @classmethod
    def test_init_base_url_with_trailing_slash(cls, ollama_config):
        """Test base_url with trailing slash"""
        ollama_config.base_url = "http://localhost:11434/"
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            assert model.base_url == "http://localhost:11434"
            assert model.embed_url == "http://localhost:11434/api/embed"

    @pytest.mark.asyncio
    async def test_embed_query_success(self, ollama_config):
        """Test embedding query text successfully"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Response for embedding request
            mock_embed_response = Mock()
            mock_embed_response.json.return_value = {
                "embeddings": [[0.1] * 768]
            }
            mock_embed_response.raise_for_status = Mock()
            mock_to_thread.return_value = mock_embed_response

            model = OllamaEmbedding(config=ollama_config)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 768
            assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_embed_query_empty_text(self, ollama_config):
        """Test embedding empty text"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            with pytest.raises(JiuWenBaseException, match="Empty text provided"):
                await model.embed_query("   ")

    @pytest.mark.asyncio
    async def test_embed_query_retry_on_failure(self, ollama_config):
        """Test retry on request failure"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Embedding request: first attempt fails, second succeeds
            mock_failure_response = Mock()
            mock_failure_response.raise_for_status = Mock(
                side_effect=requests.exceptions.RequestException("Connection error")
            )

            mock_success_response = Mock()
            mock_success_response.json.return_value = {
                "embeddings": [[0.1] * 768]
            }
            mock_success_response.raise_for_status = Mock()

            mock_to_thread.side_effect = [
                mock_failure_response,
                mock_success_response,
            ]

            model = OllamaEmbedding(config=ollama_config, max_retries=3)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 768
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_query_max_retries_exceeded(self, ollama_config):
        """Test exceeding maximum retry count"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Embedding request always fails
            mock_failure_response = Mock()
            mock_failure_response.raise_for_status = Mock(
                side_effect=requests.exceptions.RequestException("Connection error")
            )
            mock_to_thread.return_value = mock_failure_response

            model = OllamaEmbedding(config=ollama_config, max_retries=2)
            with pytest.raises(JiuWenBaseException, match="Failed to get embedding"):
                await model.embed_query("test query")
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_query_invalid_response(self, ollama_config):
        """Test invalid response format"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Embedding request returns invalid format
            mock_response = Mock()
            mock_response.json.return_value = {"invalid": "format"}
            mock_response.raise_for_status = Mock()
            mock_to_thread.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            with pytest.raises(JiuWenBaseException, match="No embeddings in response"):
                await model.embed_query("test query")

    @pytest.mark.asyncio
    async def test_embed_documents_success(self, ollama_config):
        """Test embedding document list successfully"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Response for embedding request
            mock_embed_response = Mock()
            mock_embed_response.json.return_value = {
                "embeddings": [[0.1] * 768, [0.2] * 768, [0.3] * 768]
            }
            mock_embed_response.raise_for_status = Mock()
            mock_to_thread.return_value = mock_embed_response

            model = OllamaEmbedding(config=ollama_config)
            texts = ["text 1", "text 2", "text 3"]
            embeddings = await model.embed_documents(texts)
            assert len(embeddings) == 3
            assert all(len(emb) == 768 for emb in embeddings)

    @pytest.mark.asyncio
    async def test_embed_documents_with_batching(self, ollama_config):
        """Test batch embedding documents"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Response for embedding request
            mock_embed_response = Mock()
            mock_embed_response.json.return_value = {
                "embeddings": [[0.1] * 768, [0.2] * 768]
            }
            mock_embed_response.raise_for_status = Mock()
            mock_to_thread.return_value = mock_embed_response

            model = OllamaEmbedding(config=ollama_config)
            texts = ["text 1", "text 2", "text 3", "text 4"]
            embeddings = await model.embed_documents(texts, batch_size=2)
            # Should process in 2 batches
            assert mock_to_thread.call_count == 2
            assert len(embeddings) == 4

    @pytest.mark.asyncio
    async def test_embed_documents_empty_list(self, ollama_config):
        """Test embedding empty list"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            with pytest.raises(JiuWenBaseException, match="Empty texts list provided"):
                await model.embed_documents([])

    @pytest.mark.asyncio
    async def test_embed_documents_with_empty_texts(self, ollama_config):
        """Test list containing empty texts"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            with pytest.raises(JiuWenBaseException, match="chunks are empty"):
                await model.embed_documents(["text 1", "   ", "text 2"])

    @pytest.mark.asyncio
    async def test_embed_documents_all_empty(self, ollama_config):
        """Test all texts are empty"""
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            model = OllamaEmbedding(config=ollama_config)
            with pytest.raises(JiuWenBaseException):
                await model.embed_documents(["   ", "  ", ""])

    @classmethod
    def test_dimension_property(cls, ollama_config):
        """Test dimension property"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.get_event_loop"
        ) as mock_get_loop, patch(
            "asyncio.to_thread"
        ) as mock_to_thread:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Response when getting dimension
            mock_loop = Mock()
            mock_loop.is_running.return_value = False
            mock_get_loop.return_value = mock_loop

            mock_embed_response = Mock()
            mock_embed_response.json.return_value = {"embeddings": [[0.1] * 768]}
            mock_embed_response.raise_for_status = Mock()
            mock_to_thread.return_value = mock_embed_response

            model = OllamaEmbedding(config=ollama_config)
            dimension = model.dimension
            assert dimension == 768

    @classmethod
    def test_dimension_property_with_running_loop(cls, ollama_config):
        """Test dimension property (event loop is running)"""
        with patch("requests.get") as mock_get, patch(
            "asyncio.get_event_loop"
        ) as mock_get_loop:
            # Model check during initialization
            mock_init_response = Mock()
            mock_init_response.json.return_value = {
                "models": [{"name": "nomic-embed-text"}]
            }
            mock_init_response.raise_for_status = Mock()
            mock_get.return_value = mock_init_response

            # Event loop is running
            mock_loop = Mock()
            mock_loop.is_running.return_value = True
            mock_get_loop.return_value = mock_loop

            model = OllamaEmbedding(config=ollama_config)
            # Should use default value 768
            dimension = model.dimension
            assert dimension == 768
