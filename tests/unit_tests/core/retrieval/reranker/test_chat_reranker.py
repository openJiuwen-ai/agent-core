# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Chat Reranker model implementation test cases
"""

import json
import math
from unittest.mock import AsyncMock, Mock, patch

import pydantic
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.common.config import RerankerConfig
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.reranker.chat_reranker import ChatReranker


@pytest.fixture
def chat_reranker_config():
    """Create chat reranker model configuration with yes_no_ids"""
    return RerankerConfig(
        model_name="test-model",
        api_key="test-api-key",
        api_base="https://api.example.com/v1",
        yes_no_ids=(123, 456),
    )


@pytest.fixture
def chat_reranker_config_no_ids():
    """Create chat reranker model configuration without yes_no_ids"""
    return RerankerConfig(
        model_name="test-model",
        api_key="test-api-key",
        api_base="https://api.example.com/v1",
    )


class TestChatReranker:
    """Chat reranker model tests"""

    @staticmethod
    def test_init_with_valid_yes_no_ids(chat_reranker_config):
        """Test initialization with valid yes_no_ids"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger") as mock_logger:
            model = ChatReranker(config=chat_reranker_config)
            assert model.yes_no_ids == (123, 456)
            mock_logger.warning.assert_called_once()

    @staticmethod
    def test_init_without_yes_no_ids(chat_reranker_config_no_ids):
        """Test initialization without yes_no_ids should raise error"""
        with pytest.raises(BaseError, match='chat reranker require "yes_no_ids"'):
            ChatReranker(config=chat_reranker_config_no_ids)

    @staticmethod
    def test_init_with_invalid_yes_no_ids():
        """Test initialization with invalid yes_no_ids"""
        # Pydantic will validate at config creation time
        with pytest.raises(pydantic.ValidationError):
            RerankerConfig(
                model_name="test-model",
                api_key="test-api-key",
                api_base="https://api.example.com/v1",
                yes_no_ids=(123,),  # Only one ID, should fail
            )

    @staticmethod
    def test_init_with_non_integer_yes_no_ids():
        """Test initialization with non-integer yes_no_ids"""
        # Pydantic will validate at config creation time
        with pytest.raises(pydantic.ValidationError):
            RerankerConfig(
                model_name="test-model",
                api_key="test-api-key",
                api_base="https://api.example.com/v1",
                yes_no_ids=("yes", "no"),  # Not integers, should fail
            )

    @staticmethod
    def test_request_params_with_instruct_true(chat_reranker_config):
        """Test _request_params with instruct=True"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            params = getattr(model, "_request_params")(query="test query", documents=["doc1"], instruct=True)
            assert params["model"] == chat_reranker_config.model_name
            assert params["temperature"] == 0
            assert params["max_tokens"] == 1
            assert params["logprobs"] is True
            assert params["top_logprobs"] == 5
            assert len(params["messages"]) == 2
            assert params["messages"][0]["role"] == "system"
            assert params["messages"][1]["role"] == "user"
            assert "test query" in params["messages"][1]["content"]
            assert "<Document>" in params["messages"][1]["content"]
            assert params["logit_bias"] == {123: 5, 456: 5}

    @staticmethod
    def test_request_params_with_instruct_false(chat_reranker_config):
        """Test _request_params with instruct=False"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            params = getattr(model, "_request_params")(query="test query", documents=["doc1"], instruct=False)
            assert len(params["messages"]) == 2
            assert "test query" in params["messages"][1]["content"]

    @staticmethod
    def test_request_params_with_custom_instruct(chat_reranker_config):
        """Test _request_params with custom instruction"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            params = getattr(model, "_request_params")(
                query="test query", documents=["doc1"], instruct="Custom instruction"
            )
            assert "Custom instruction" in params["messages"][1]["content"]

    @staticmethod
    def test_request_params_with_extra_body(chat_reranker_config):
        """Test _request_params with extra_body in config"""
        config = RerankerConfig(
            model_name="test-model",
            api_key="test-api-key",
            api_base="https://api.example.com/v1",
            yes_no_ids=(123, 456),
            extra_body={"custom_param": "custom_value"},
        )
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=config)
            params = getattr(model, "_request_params")(query="test query", documents=["doc1"], instruct=True)
            assert params["custom_param"] == "custom_value"

    @staticmethod
    def test_parse_response_with_yes_token(chat_reranker_config):
        """Test _parse_response with yes token"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": math.log(0.8)},
                                        {"token": "no", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = ["doc1"]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert "doc1" in result
            assert result["doc1"] > 0.5  # Should favor yes

    @staticmethod
    def test_parse_response_with_no_token(chat_reranker_config):
        """Test _parse_response with no token"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "no", "logprob": math.log(0.8)},
                                        {"token": "yes", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = ["doc1"]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert "doc1" in result
            assert result["doc1"] < 0.5  # Should favor no

    @staticmethod
    def test_parse_response_with_case_insensitive_tokens(chat_reranker_config):
        """Test _parse_response with case-insensitive token matching"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "YES", "logprob": math.log(0.8)},
                                        {"token": "No", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = ["doc1"]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert "doc1" in result
            assert result["doc1"] > 0.5

    @staticmethod
    def test_parse_response_with_token_prefix(chat_reranker_config):
        """Test _parse_response with tokens starting with yes/no"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes,", "logprob": math.log(0.8)},
                                        {"token": "no.", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = ["doc1"]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert "doc1" in result

    @staticmethod
    def test_parse_response_without_logprobs(chat_reranker_config):
        """Test _parse_response without logprobs should raise error"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {}  # No logprobs
                ]
            }
            docs = ["doc1"]
            with pytest.raises(BaseError, match="the service does not support logprobs"):
                getattr(model, "_parse_response")(response_data, docs)

    @staticmethod
    def test_parse_response_with_empty_logprobs(chat_reranker_config):
        """Test _parse_response with empty logprobs should raise error"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {}  # Empty logprobs
                    }
                ]
            }
            docs = ["doc1"]
            with pytest.raises(BaseError, match="the service does not support logprobs"):
                getattr(model, "_parse_response")(response_data, docs)

    @staticmethod
    def test_parse_response_with_zero_total_prob(chat_reranker_config):
        """Test _parse_response with zero total probability"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "maybe", "logprob": -1000},  # Very low probability
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = ["doc1"]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert result["doc1"] == 0.0

    @staticmethod
    def test_parse_response_with_document_object(chat_reranker_config):
        """Test _parse_response with Document object"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            response_data = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": math.log(0.8)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            docs = [Document(text="Test document", id_="doc1")]
            result = getattr(model, "_parse_response")(response_data, docs)
            assert "doc1" in result

    @staticmethod
    def test_assemble_params_with_string_doc(chat_reranker_config):
        """Test _assemble_params with string document"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            query = "test query"
            docs = ["doc1"]
            headers, params = getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})
            assert "Content-Type" in headers
            assert params["model"] == chat_reranker_config.model_name
            assert "messages" in params
            assert len(params["messages"]) == 2
            assert "doc1" in params["messages"][1]["content"]

    @staticmethod
    def test_assemble_params_with_document_object(chat_reranker_config):
        """Test _assemble_params with Document object"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            query = "test query"
            docs = [Document(text="Test document", id_="doc1")]
            headers, params = getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})
            assert "messages" in params
            assert len(params["messages"]) == 2
            assert "Test document" in params["messages"][1]["content"]

    @staticmethod
    def test_assemble_params_invalid_input_not_list(chat_reranker_config):
        """Test _assemble_params with invalid input (not a list)"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            query = "test query"
            docs = "not a list"
            with pytest.raises(BaseError, match="input to chat reranker must be a list"):
                getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})

    @staticmethod
    def test_assemble_params_invalid_input_wrong_size(chat_reranker_config):
        """Test _assemble_params with invalid input (wrong size)"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            query = "test query"
            docs = ["doc1", "doc2"]  # Should be size 1
            with pytest.raises(BaseError, match="input to chat reranker must be a list.*of size 1"):
                getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})

    @staticmethod
    def test_assemble_params_invalid_input_empty_list(chat_reranker_config):
        """Test _assemble_params with invalid input (empty list)"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            query = "test query"
            docs = []
            with pytest.raises(BaseError, match="input to chat reranker must be a list.*of size 1"):
                getattr(model, "_assemble_params")(query, docs, instruct=True, kwargs={})

    @staticmethod
    @pytest.mark.asyncio
    async def test_rerank_success(chat_reranker_config):
        """Test rerank successfully"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            mock_response = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": math.log(0.8)},
                                        {"token": "no", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            with patch.object(model.client, "post", new_callable=AsyncMock) as mock_post:
                mock_response_obj = Mock()
                mock_response_obj.json.return_value = mock_response
                response_text = json.dumps(mock_response)
                mock_response_obj.text = response_text
                mock_response_obj.status_code = 200
                mock_post.return_value = mock_response_obj
                result = await model.rerank("test query", ["doc1"])
                assert "doc1" in result
                assert result["doc1"] > 0.5

    @staticmethod
    def test_rerank_sync_success(chat_reranker_config):
        """Test rerank_sync successfully"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            mock_response = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": math.log(0.8)},
                                        {"token": "no", "logprob": math.log(0.2)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            with patch.object(model.sync_client, "post") as mock_post:
                mock_response_obj = Mock()
                mock_response_obj.json.return_value = mock_response
                response_text = json.dumps(mock_response)
                mock_response_obj.text = response_text
                mock_response_obj.status_code = 200
                mock_post.return_value = mock_response_obj
                result = model.rerank_sync("test query", ["doc1"])
                assert "doc1" in result
                assert result["doc1"] > 0.5

    @staticmethod
    def test_test_compatibility_success(chat_reranker_config):
        """Test test_compatibility with successful rerank"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            mock_response = {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": math.log(0.8)},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            with patch.object(model.sync_client, "post") as mock_post:
                mock_response_obj = Mock()
                mock_response_obj.json.return_value = mock_response
                response_text = json.dumps(mock_response)
                mock_response_obj.text = response_text
                mock_response_obj.status_code = 200
                mock_post.return_value = mock_response_obj
                result = model.test_compatibility()
                assert result is True

    @staticmethod
    def test_test_compatibility_failure(chat_reranker_config):
        """Test test_compatibility with failed rerank"""
        with patch("openjiuwen.core.retrieval.reranker.chat_reranker.logger"):
            model = ChatReranker(config=chat_reranker_config)
            with patch.object(model.sync_client, "post") as mock_post:
                mock_post.side_effect = Exception("Service error")
                result = model.test_compatibility()
                assert result is False
