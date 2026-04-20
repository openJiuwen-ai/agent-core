# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Dashscope Reranker model implementation test cases
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.common.config import RerankerConfig
from openjiuwen.core.retrieval.common.document import Document, MultimodalDocument
from openjiuwen.core.retrieval.reranker.dashscope_reranker import DashscopeReranker


@pytest.fixture
def dashscope_reranker_config():
    return RerankerConfig(
        model="qwen3-rerank",
        api_key="test-api-key",
        api_base="https://dashscope.aliyuncs.com/api/v1",
    )


class TestDashscopeReranker:
    @staticmethod
    def test_endpoint_constant():
        assert DashscopeReranker.end_point == "/services/rerank/text-rerank/text-rerank"

    @staticmethod
    def test_init_strips_dashscope_endpoint_from_api_base():
        base = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        config = RerankerConfig(
            model_name="qwen3-rerank",
            api_key="test-api-key",
            api_base=base,
        )
        model = DashscopeReranker(config=config)
        assert model.api_url == "https://dashscope.aliyuncs.com/api/v1"

    @staticmethod
    def test_request_params_shape_without_string_instruct(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        params = getattr(model, "_request_params")(
            query="q1",
            documents=["a", "b"],
            top_n=2,
            instruct=True,
        )
        assert params["model"] == "qwen3-rerank"
        assert params["input"] == {"query": "q1", "documents": ["a", "b"]}
        assert params["parameters"]["return_documents"] is False
        assert params["parameters"]["top_n"] == 2
        assert "instruct" not in params["parameters"]

    @staticmethod
    def test_request_params_adds_instruct_when_string(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        params = getattr(model, "_request_params")(
            query="q1",
            documents=["x"],
            top_n=1,
            instruct="Rank by relevance",
        )
        assert params["parameters"]["instruct"] == "Rank by relevance"

    @staticmethod
    def test_request_params_top_n_defaults_to_document_count(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        params = getattr(model, "_request_params")(query="q", documents=["a", "b", "c"], instruct=False)
        assert params["parameters"]["top_n"] == 3

    @staticmethod
    def test_assemble_params_string_documents(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        headers, params = getattr(model, "_assemble_params")("test query", ["d1", "d2"], instruct=False, kwargs={})
        assert "Content-Type" in headers
        assert params["input"]["query"] == "test query"
        assert params["input"]["documents"] == ["d1", "d2"]
        assert params["parameters"]["top_n"] == 2

    @staticmethod
    def test_assemble_params_document_objects(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        docs = [
            Document(text="First", id_="id1"),
            Document(text="Second", id_="id2"),
        ]
        _, params = getattr(model, "_assemble_params")("q", docs, instruct=False, kwargs={})
        assert params["input"]["documents"] == ["First", "Second"]

    @staticmethod
    def test_assemble_params_multimodal_query(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        query_doc = MultimodalDocument().add_field("text", "query text")
        _, params = getattr(model, "_assemble_params")(query_doc, ["doc"], instruct=False, kwargs={})
        assert params["input"]["query"] == {"text": "query text"}

    @staticmethod
    def test_assemble_params_mixed_multimodal_documents_wraps_plain_text(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        mm = MultimodalDocument().add_field("text", "mm body")
        _, params = getattr(model, "_assemble_params")("q", [mm, "plain"], instruct=False, kwargs={})
        assert params["input"]["documents"] == [{"text": "mm body"}, {"text": "plain"}]

    @staticmethod
    def test_assemble_params_invalid_document_types(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        with pytest.raises(BaseError, match="input to reranker must be either list"):
            getattr(model, "_assemble_params")("q", [123, 456], instruct=False, kwargs={})

    @staticmethod
    def test_assemble_params_merges_extra_kwargs_into_parameters(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        _, params = getattr(model, "_assemble_params")("q", ["a"], instruct=False, kwargs={"custom": 1})
        assert params["parameters"]["custom"] == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_posts_to_dashscope_endpoint(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        mock_payload = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.5},
                ]
            }
        }
        with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_payload
            mock_response_obj.text = json.dumps(mock_payload)
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj

            result = await model.rerank("question", ["doc1", "doc2"], instruct=False)

            assert result == {"doc1": 0.9, "doc2": 0.5}
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["url"] == DashscopeReranker.end_point
            body = call_kwargs["json"]
            assert body["input"]["query"] == "question"
            assert body["input"]["documents"] == ["doc1", "doc2"]
            assert body["parameters"]["return_documents"] is False

    @staticmethod
    def test_rerank_sync_success(dashscope_reranker_config):
        model = DashscopeReranker(config=dashscope_reranker_config)
        mock_payload = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.88},
                ]
            }
        }
        with patch.object(model.sync_client, "post") as mock_post:
            mock_response_obj = Mock()
            mock_response_obj.json.return_value = mock_payload
            mock_response_obj.text = json.dumps(mock_payload)
            mock_response_obj.status_code = 200
            mock_post.return_value = mock_response_obj

            result = model.rerank_sync("q", ["only"], instruct=False)
            assert result == {"only": 0.88}
            assert mock_post.call_args[1]["url"] == DashscopeReranker.end_point
