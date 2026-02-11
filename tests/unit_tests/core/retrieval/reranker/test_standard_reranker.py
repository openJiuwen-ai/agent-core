# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Standard Reranker model implementation test cases
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.common.config import RerankerConfig
from openjiuwen.core.retrieval.common.document import Document, MultimodalDocument
from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker


@pytest.fixture
def reranker_config():
    """Create reranker model configuration"""
    return RerankerConfig(
        model="test-model",  # Use alias "model" instead of "model_name"
        api_key="test-api-key",
        api_base="https://api.example.com/v1",
    )


@pytest.fixture
def reranker_config_no_key():
    """Create reranker model configuration without API key"""
    return RerankerConfig(
        model="test-model",  # Use alias "model" instead of "model_name"
        api_base="https://api.example.com/v1",
    )


class TestStandardReranker:
    """Standard reranker model tests"""

    @staticmethod
    def test_init_with_api_key(reranker_config):
        """Test initialization with API key"""
        model = StandardReranker(config=reranker_config)
        assert model.model_name == reranker_config.model_name
        assert model.api_key == "test-api-key"
        assert model.api_url == "https://api.example.com/v1"

    @staticmethod
    def test_init_without_api_key(reranker_config_no_key):
        """Test initialization without API key"""
        model = StandardReranker(config=reranker_config_no_key)
        # api_key defaults to empty string in RerankerConfig
        assert model.api_key == ""

    @staticmethod
    def test_init_with_extra_headers(reranker_config):
        """Test initialization with extra headers"""
        extra_headers = {"X-Custom-Header": "custom-value"}
        model = StandardReranker(config=reranker_config, extra_headers=extra_headers)
        actual_header = getattr(model, "_headers", {})
        assert "X-Custom-Header" in actual_header
        assert actual_header.get("X-Custom-Header") == "custom-value"

    @staticmethod
    def test_init_with_custom_params(reranker_config):
        """Test initialization with custom parameters"""
        model = StandardReranker(
            config=reranker_config,
            timeout=120,
            max_retries=5,
            retry_wait=0.5,
        )
        assert model.timeout == reranker_config.timeout
        assert model.max_retries == 5

    @staticmethod
    def test_init_api_url_with_trailing_slash():
        """Test initialization with API URL containing trailing slash"""
        config = RerankerConfig(
            model_name="test-model",
            api_key="test-api-key",
            api_base="https://api.example.com/v1/",
        )
        model = StandardReranker(config=config)
        assert model.api_url == "https://api.example.com/v1"

    @staticmethod
    def test_init_api_url_with_endpoint():
        """Test initialization with API URL containing endpoint"""
        config = RerankerConfig(
            model_name="test-model",
            api_key="test-api-key",
            api_base="https://api.example.com/v1/rerank",
        )
        model = StandardReranker(config=config)
        assert model.api_url == "https://api.example.com/v1"

    @staticmethod
    def test_request_headers(reranker_config):
        """Test _request_headers method"""
        model = StandardReranker(config=reranker_config)
        headers = getattr(model, "_request_headers")()
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-api-key"

    @staticmethod
    def test_request_headers_no_api_key(reranker_config_no_key):
        """Test _request_headers method without API key"""
        model = StandardReranker(config=reranker_config_no_key)
        headers = getattr(model, "_request_headers")()
        assert "Content-Type" in headers
        assert "Authorization" not in headers

    @staticmethod
    def test_request_params_with_instruct_true(reranker_config):
        """Test _request_params with instruct=True"""
        model = StandardReranker(config=reranker_config)
        params = getattr(model, "_request_params")(query="test query", documents=["doc1", "doc2"], instruct=True)
        assert params["model"] == reranker_config.model_name
        assert params["return_documents"] is False
        assert "<Instruct>" in params["query"]
        assert "<Query>" in params["query"]
        assert "test query" in params["query"]

    @staticmethod
    def test_request_params_with_instruct_false(reranker_config):
        """Test _request_params with instruct=False"""
        model = StandardReranker(config=reranker_config)
        params = getattr(model, "_request_params")(query="test query", documents=["doc1", "doc2"], instruct=False)
        assert params["model"] == "test-model"
        assert params["query"] == "test query"

    @staticmethod
    def test_request_params_with_custom_instruct(reranker_config):
        """Test _request_params with custom instruction"""
        model = StandardReranker(config=reranker_config)
        params = getattr(model, "_request_params")(
            query="test query", documents=["doc1", "doc2"], instruct="Custom instruction"
        )
        assert params["model"] == reranker_config.model_name
        assert "Custom instruction" in params["query"]
        assert "test query" in params["query"]

    @staticmethod
    def test_request_params_with_extra_body(reranker_config):
        """Test _request_params with extra_body in config"""
        config = RerankerConfig(
            model_name="test-model",
            api_key="test-api-key",
            api_base="https://api.example.com/v1",
            extra_body={"custom_param": "custom_value"},
        )
        model = StandardReranker(config=config)
        params = getattr(model, "_request_params")(query="test query", documents=["doc1"], instruct=True)
        assert params["custom_param"] == "custom_value"

    @staticmethod
    def test_parse_response_with_string_docs(reranker_config):
        """Test _parse_response with string documents"""
        model = StandardReranker(config=reranker_config)
        response_data = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.7},
                ]
            }
        }
        docs = ["doc1", "doc2"]
        result = getattr(model, "_parse_response")(response_data, docs)
        assert result["doc1"] == 0.9
        assert result["doc2"] == 0.7

    @staticmethod
    def test_parse_response_with_document_objects(reranker_config):
        """Test _parse_response with Document objects"""
        model = StandardReranker(config=reranker_config)
        response_data = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.7},
                ]
            }
        }
        docs = [
            Document(text="First document", id_="doc1"),
            Document(text="Second document", id_="doc2"),
        ]
        result = getattr(model, "_parse_response")(response_data, docs)
        assert result["doc1"] == 0.9
        assert result["doc2"] == 0.7

    @staticmethod
    def test_parse_response_without_output_key(reranker_config):
        """Test _parse_response without output key (direct results)"""
        model = StandardReranker(config=reranker_config)
        response_data = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.7},
            ]
        }
        docs = ["doc1", "doc2"]
        result = getattr(model, "_parse_response")(response_data, docs)
        assert result["doc1"] == 0.9
        assert result["doc2"] == 0.7

    @staticmethod
    def test_parse_response_missing_index(reranker_config):
        """Test _parse_response with missing index in results"""
        model = StandardReranker(config=reranker_config)
        response_data = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"relevance_score": 0.7},  # Missing index
                ]
            }
        }
        docs = ["doc1", "doc2"]
        # Missing index will cause KeyError when accessing rank_result["index"]
        with pytest.raises(KeyError):
            getattr(model, "_parse_response")(response_data, docs)

    @staticmethod
    def test_assemble_params_with_string_docs(reranker_config):
        """Test _assemble_params with string documents"""
        model = StandardReranker(config=reranker_config)
        query = "test query"
        docs = ["doc1", "doc2"]
        headers, params = getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})
        assert "Content-Type" in headers
        assert params["model"] == reranker_config.model_name
        assert params["documents"] == ["doc1", "doc2"]
        assert params["top_n"] == 2

    @staticmethod
    def test_assemble_params_with_document_objects(reranker_config):
        """Test _assemble_params with Document objects"""
        model = StandardReranker(config=reranker_config)
        query = "test query"
        docs = [
            Document(text="First document", id_="doc1"),
            Document(text="Second document", id_="doc2"),
        ]
        headers, params = getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})
        assert params["documents"] == ["First document", "Second document"]

    @staticmethod
    def test_assemble_params_invalid_input(reranker_config):
        """Test _assemble_params with invalid input"""
        model = StandardReranker(config=reranker_config)
        query = "test query"
        docs = [123, 456]  # Invalid type
        with pytest.raises(BaseError, match="input to reranker must be either list"):
            getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})

    @staticmethod
    def test_assemble_params_with_multimodal_document(reranker_config):
        """Test _assemble_params with MultimodalDocument (should warn)"""
        model = StandardReranker(config=reranker_config)
        query = "test query"
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        doc.text = "Hello world"  # MultimodalDocument.text is not updated by add_field, need to set explicitly
        with patch("openjiuwen.core.retrieval.reranker.standard_reranker.logger") as mock_logger:
            _, params = getattr(model, "_assemble_params")(query, [doc], instruct=True, kwargs={})
            mock_logger.warning.assert_called_once()
            assert params["documents"] == ["Hello world"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_success(reranker_config):
        """Test rerank successfully"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.7},
                ]
            }
        }
        with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = await model.rerank("test query", ["doc1", "doc2"])
            assert result["doc1"] == 0.9
            assert result["doc2"] == 0.7

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_documents(reranker_config):
        """Test rerank with Document objects"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            docs = [Document(text="Test document", id_="doc1")]
            result = await model.rerank("test query", docs)
            assert result["doc1"] == 0.9

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_instruct_false(reranker_config):
        """Test rerank with instruct=False"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = await model.rerank("test query", ["doc1"], instruct=False)
            assert result["doc1"] == 0.9
            call_kwargs = mock_post.call_args[1]
            params = call_kwargs["json"]
            assert params["query"] == "test query"

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_with_custom_instruct(reranker_config):
        """Test rerank with custom instruction"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = await model.rerank("test query", ["doc1"], instruct="Custom instruction")
            assert result["doc1"] == 0.9
            call_kwargs = mock_post.call_args[1]
            params = call_kwargs["json"]
            assert "Custom instruction" in params["query"]

    @staticmethod
    def test_rerank_sync_success(reranker_config):
        """Test rerank_sync successfully"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.7},
                ]
            }
        }
        with patch.object(model.sync_client, "post") as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = model.rerank_sync("test query", ["doc1", "doc2"])
            assert result["doc1"] == 0.9
            assert result["doc2"] == 0.7

    @staticmethod
    def test_rerank_sync_with_documents(reranker_config):
        """Test rerank_sync with Document objects"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.sync_client, "post") as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            docs = [Document(text="Test document", id_="doc1")]
            result = model.rerank_sync("test query", docs)
            assert result["doc1"] == 0.9

    @staticmethod
    def test_rerank_sync_with_instruct_false(reranker_config):
        """Test rerank_sync with instruct=False"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.sync_client, "post") as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = model.rerank_sync("test query", ["doc1"], instruct=False)
            assert result["doc1"] == 0.9
            call_kwargs = mock_post.call_args[1]
            params = call_kwargs["json"]
            assert params["query"] == "test query"

    @staticmethod
    def test_rerank_sync_with_custom_instruct(reranker_config):
        """Test rerank_sync with custom instruction"""
        model = StandardReranker(config=reranker_config)
        mock_response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        with patch.object(model.sync_client, "post") as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_response
            response_text = json.dumps(mock_response)
            mock_response_obj.text = response_text
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj
            result = model.rerank_sync("test query", ["doc1"], instruct="Custom instruction")
            assert result["doc1"] == 0.9
            call_kwargs = mock_post.call_args[1]
            params = call_kwargs["json"]
            assert "Custom instruction" in params["query"]
