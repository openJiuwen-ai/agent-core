# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Triple extractor test cases
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor import TripleExtractor


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client"""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_completion():
    """Create mock completion object"""
    completion = MagicMock()
    completion.content = json.dumps(
        {
            "triples": [
                ["Alice", "knows", "Bob"],
                ["Bob", "works_at", "Company"],
            ]
        }
    )
    return completion


class TestTripleExtractor:
    """Triple extractor tests"""

    @classmethod
    def test_init(cls, mock_llm_client):
        """Test initialization"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            temperature=0.0,
            max_concurrent=10,
        )
        assert extractor.llm_client == mock_llm_client
        assert extractor.model_name == "test-model"

    @classmethod
    def test_init_with_defaults(cls, mock_llm_client):
        """Test initialization with default values"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
        )

    @pytest.mark.asyncio
    async def test_extract_multiple_chunks(self, mock_llm_client, mock_completion):
        """Test extracting multiple chunks"""
        mock_llm_client.invoke = AsyncMock(return_value=mock_completion)

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            max_concurrent=2,
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
            TextChunk(id_="2", text="Charlie knows David", doc_id="doc_1"),
        ]
        triples = await extractor.extract(chunks)
        # Should extract triples for each chunk
        assert mock_llm_client.invoke.call_count == 2

    @pytest.mark.asyncio
    async def test_extract_with_exception(self, mock_llm_client):
        """Test exception during extraction"""
        mock_llm_client.ainvoke = AsyncMock(side_effect=Exception("LLM error"))

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
        ]
        # Should raise exception when extraction fails
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code

    @pytest.mark.asyncio
    async def test_extract_invalid_json(self, mock_llm_client):
        """Test invalid JSON response"""
        mock_completion = MagicMock()
        mock_completion.content = "Invalid JSON response"
        mock_llm_client.ainvoke = AsyncMock(return_value=mock_completion)

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
        ]
        # Should raise exception when JSON parsing fails
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code

    @pytest.mark.asyncio
    async def test_extract_empty_chunks(self, mock_llm_client):
        """Test extracting empty chunk list"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
        )
        triples = await extractor.extract([])
        assert len(triples) == 0
        mock_llm_client.ainvoke.assert_not_called()
