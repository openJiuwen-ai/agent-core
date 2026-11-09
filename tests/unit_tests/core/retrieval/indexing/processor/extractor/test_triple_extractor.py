# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Triple extractor test cases
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor import TripleExtractor


class _TestableTripleExtractor(TripleExtractor):
    def parse_triples_for_test(self, content, doc_id, chunk_id):
        return self._parse_triples(content, doc_id, chunk_id)


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
    
    @classmethod
    def test_init_with_validation(cls, mock_llm_client):
        """Test initialization with validation"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            temperature=0.0,
            max_concurrent=10,
            validate=True
        )
        assert getattr(extractor, "validate", False) is True

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
        mock_llm_client.invoke = AsyncMock(side_effect=Exception("429 too many requests"))

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
        assert "429 too many requests" in (exc_info.value.message or "")

    @pytest.mark.asyncio
    async def test_extract_invalid_json(self, mock_llm_client):
        """Test invalid JSON response"""
        mock_completion = MagicMock()
        mock_completion.content = "Invalid JSON response"
        mock_llm_client.invoke = AsyncMock(return_value=mock_completion)

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
        assert "parsed" in (exc_info.value.message or "").lower()

    @pytest.mark.asyncio
    async def test_extract_empty_chunks(self, mock_llm_client):
        """Test extracting empty chunk list"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
        )
        triples = await extractor.extract([])
        assert len(triples) == 0
        mock_llm_client.invoke.assert_not_called()

    @staticmethod
    def test_parse_triples_json_array(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test('[["a", "b", "c"]]', "d1", "c1")
        assert ok and len(triples) == 1
        assert triples[0].subject == "a" and triples[0].predicate == "b" and triples[0].object == "c"

    @staticmethod
    def test_parse_triples_extra_fields_ignored(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test('[["a", "b", "c", "ignored", 99]]', "d1", "c1")
        assert ok and triples[0].object == "c"

    @staticmethod
    def test_parse_triples_wrapped_dict(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test(
            '{"triples": [["x", "y", "z"]]}', "d1", "c1"
        )
        assert ok and len(triples) == 1 and triples[0].subject == "x"

    @staticmethod
    def test_parse_triples_prompt_shape(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test(
            '{"named_entities": ["Alice", "Bob"], "triples": [["Alice", "knows", "Bob"]]}',
            "d1",
            "c1",
        )
        assert ok and len(triples) == 1
        assert triples[0].subject == "Alice"

    @staticmethod
    def test_parse_triples_missing_triples_key_fails(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test('{"named_entities": ["Alice", "Bob"]}', "d1", "c1")
        assert not ok and triples == []

    @staticmethod
    def test_parse_triples_invalid_items_ignored(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test(
            '{"triples": [["a", "b", "c"], ["x"], {"bad": 1}, ["y", ["nested"], "z"]]}',
            "d1",
            "c1",
        )
        assert ok and len(triples) == 1
        assert triples[0].subject == "a"

    @staticmethod
    def test_parse_triples_all_invalid_fails(mock_llm_client):
        ex = _TestableTripleExtractor(llm_client=mock_llm_client, model_name="m")
        triples, ok = ex.parse_triples_for_test('{"triples": [["x"], {"bad": 1}]}', "d1", "c1")
        assert not ok and triples == []

    @pytest.mark.asyncio
    async def test_extract_multiple_chunks_with_validation(self, mock_llm_client, mock_completion):
        mock_llm_client.invoke = AsyncMock(return_value=mock_completion)

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            max_concurrent=2,
            validate=True
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
            TextChunk(id_="2", text="Charlie knows David", doc_id="doc_1"),
        ]
        triples = await extractor.extract(chunks)
        
        assert mock_llm_client.invoke.call_count == 4

    @pytest.mark.asyncio
    async def test_extract_with_validation_and_exception(self, mock_llm_client):
        """Test exception during extraction with validation enabled"""
        mock_llm_client.invoke = AsyncMock(side_effect=Exception("429 too many requests"))

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            validate=True,
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
        ]
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code
        assert "429 too many requests" in (exc_info.value.message or "")

    @pytest.mark.asyncio
    async def test_extract_invalid_json_with_validation(self, mock_llm_client):
        """Test invalid JSON response with validation enabled"""
        mock_completion = MagicMock()
        mock_completion.content = "Invalid JSON response"
        mock_llm_client.invoke = AsyncMock(return_value=mock_completion)

        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            validate=True,
        )
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
        ]
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code
        assert "parsed" in (exc_info.value.message or "").lower()

    @pytest.mark.asyncio
    async def test_extract_empty_chunks_with_validation(self, mock_llm_client):
        """Test extracting empty chunk list with validation enabled"""
        extractor = TripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            validate=True,
        )
        triples = await extractor.extract([])
        assert len(triples) == 0
        mock_llm_client.invoke.assert_not_called()

class TestTripleExtractorForwardsModelName:
    """issue #2460: model_name 必须转交给 LLM 调用，否则形参只是摆设"""

    @pytest.mark.asyncio
    async def test_extract_forwards_model_name(self, mock_llm_client, mock_completion):
        mock_llm_client.invoke = AsyncMock(return_value=mock_completion)
        extractor = TripleExtractor(llm_client=mock_llm_client, model_name="chosen-model", temperature=0.3)

        await extractor.extract([TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1")])

        assert mock_llm_client.invoke.await_count == 1
        kwargs = mock_llm_client.invoke.await_args.kwargs
        assert kwargs["model"] == "chosen-model"
        # 原有参数不能被挤掉
        assert kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_validate_forwards_model_name(self, mock_llm_client):
        """校验阶段同样是一次 LLM 调用，也必须带上同一个模型名"""
        extraction = MagicMock()
        extraction.content = json.dumps({"triples": [["Alice", "knows", "Bob"]]})
        # 校验阶段复用同一个解析器，因此也返回 triples 形状
        validation = MagicMock()
        validation.content = json.dumps({"triples": [["Alice", "knows", "Bob"]]})

        calls = []

        async def _side_effect(messages, **kwargs):
            calls.append(kwargs)
            return extraction if len(calls) == 1 else validation

        mock_llm_client.invoke = AsyncMock(side_effect=_side_effect)
        extractor = TripleExtractor(
            llm_client=mock_llm_client, model_name="chosen-model", validate=True
        )

        await extractor.extract([TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1")])

        assert len(calls) == 2
        assert all(c.get("model") == "chosen-model" for c in calls)
