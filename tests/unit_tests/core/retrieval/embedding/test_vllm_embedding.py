# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
VLLM embedding model implementation test cases
"""

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import EmbeddingConfig, MultimodalDocument, VLLMEmbedding
from tests.unit_tests.core.retrieval.common.test_multimodal_document import all_media


@pytest.fixture
def embedding_config():
    """Create embedding model configuration"""
    return EmbeddingConfig(
        model_name="test-model",
        api_key="test-api-key",
        base_url="https://api.example.com/v1/embeddings",
    )


class TestVLLMEmbedding:
    """VLLM embedding model tests"""

    @staticmethod
    def test_parse_multimodal_input_default_instruction():
        """Test parse_multimodal_input with default instruction"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        kwargs = {}

        result = VLLMEmbedding.parse_multimodal_input(doc, kwargs)

        assert result == kwargs
        assert "instruction" not in kwargs
        assert "extra_body" in kwargs
        assert kwargs.get("extra_body", {}).get("messages") == [
            {"role": "system", "content": [{"type": "text", "text": "Represent the user's input."}]},
            {"role": "user", "content": doc.content},
        ]

    @staticmethod
    def test_parse_multimodal_input_custom_instruction():
        """Test parse_multimodal_input with custom instruction"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        kwargs = {"instruction": "Custom instruction text"}

        result = VLLMEmbedding.parse_multimodal_input(doc, kwargs)

        assert result == kwargs
        assert "instruction" not in kwargs
        assert "extra_body" in kwargs
        assert kwargs.get("extra_body", {}).get("messages") == [
            {"role": "system", "content": [{"type": "text", "text": "Custom instruction text"}]},
            {"role": "user", "content": doc.content},
        ]

    @staticmethod
    def test_parse_multimodal_input_none_instruction():
        """Test parse_multimodal_input with None instruction"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        kwargs = {"instruction": None}

        result = VLLMEmbedding.parse_multimodal_input(doc, kwargs)

        assert result == kwargs
        assert "instruction" not in kwargs
        assert "extra_body" in kwargs
        assert kwargs.get("extra_body", {}).get("messages") == [
            {"role": "user", "content": doc.content},
        ]

    @staticmethod
    def test_parse_multimodal_input_preserves_other_kwargs():
        """Test parse_multimodal_input preserves other kwargs"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        kwargs = {"other_param": "value", "another": 123}

        _ = VLLMEmbedding.parse_multimodal_input(doc, kwargs)

        assert kwargs["other_param"] == "value"
        assert kwargs["another"] == 123
        assert "extra_body" in kwargs

    @staticmethod
    def test_parse_multimodal_input_with_multimodal_content():
        """Test parse_multimodal_input with multiple content types"""
        media = all_media()
        doc = MultimodalDocument()
        doc.add_field("text", "Description")
        doc.add_field("image", data=media["image"])
        doc.add_field("audio", data=media["audio"])

        kwargs = {}
        _ = VLLMEmbedding.parse_multimodal_input(doc, kwargs)

        assert "extra_body" in kwargs
        assert len(kwargs.get("extra_body", {}).get("messages")) == 2
        user_message = kwargs.get("extra_body", {}).get("messages")[1]
        assert user_message["role"] == "user"
        assert len(user_message["content"]) == 3

    @pytest.mark.asyncio
    async def test_embed_multimodal_success(self, embedding_config):
        """Test embed_multimodal with valid MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = VLLMEmbedding(timeout=1, config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings", AsyncMock(return_value=[mock_embedding]))

        embedding = await model.embed_multimodal(doc)

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)
        getattr(model, "_get_embeddings").assert_called_once()
        call_kwargs = getattr(model, "_get_embeddings").call_args[1]
        assert "extra_body" in call_kwargs
        assert "messages" in call_kwargs["extra_body"]
        assert call_kwargs["extra_body"]["messages"][1].get("content") == doc.content

    @pytest.mark.asyncio
    async def test_embed_multimodal_invalid_input(self, embedding_config):
        """Test embed_multimodal with invalid input (not MultimodalDocument)"""
        model = VLLMEmbedding(timeout=1, config=embedding_config)

        with pytest.raises(BaseError, match="input provided for multimodal embedding is not a MultimodalDocument"):
            await model.embed_multimodal("not a document")

    @pytest.mark.asyncio
    async def test_embed_multimodal_with_instruction(self, embedding_config):
        """Test embed_multimodal with custom instruction"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = VLLMEmbedding(timeout=1, config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings", AsyncMock(return_value=[mock_embedding]))

        await model.embed_multimodal(doc, instruction="Custom instruction")

        call_kwargs = getattr(model, "_get_embeddings").call_args[1]
        assert call_kwargs["extra_body"]["messages"][0]["content"][0]["text"] == "Custom instruction"

    @staticmethod
    def test_embed_multimodal_sync_success(embedding_config):
        """Test embed_multimodal_sync with valid MultimodalDocument"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = VLLMEmbedding(timeout=1, config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings_sync", Mock(return_value=[mock_embedding]))

        embedding = model.embed_multimodal_sync(doc)

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)
        getattr(model, "_get_embeddings_sync").assert_called_once()
        call_kwargs = getattr(model, "_get_embeddings_sync").call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["messages"][1]["content"] == doc.content

    @staticmethod
    def test_embed_multimodal_sync_invalid_input(embedding_config):
        """Test embed_multimodal_sync with invalid input (not MultimodalDocument)"""
        model = VLLMEmbedding(timeout=1, config=embedding_config)

        with pytest.raises(BaseError, match="input provided for multimodal embedding is not a MultimodalDocument"):
            model.embed_multimodal_sync("not a document")

    @staticmethod
    def test_embed_multimodal_sync_with_instruction(embedding_config):
        """Test embed_multimodal_sync with custom instruction"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")

        model = VLLMEmbedding(timeout=1, config=embedding_config)
        mock_embedding = [0.1] * 384
        setattr(model, "_get_embeddings_sync", Mock(return_value=[mock_embedding]))

        model.embed_multimodal_sync(doc, instruction="Custom instruction")

        call_kwargs = getattr(model, "_get_embeddings_sync").call_args[1]
        assert call_kwargs["extra_body"]["messages"][0]["content"][0]["text"] == "Custom instruction"
