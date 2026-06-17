# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Triple extractor test cases
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.indexing.processor.extractor.ontology_triple_extractor import OntologyTripleExtractor


class _TestableOntologyTripleExtractor(OntologyTripleExtractor):
    def parse_triples_for_test(self, content, doc_id, chunk_id):
        return self._parse_triples(content, doc_id, chunk_id)


@pytest.fixture
def entities_completion():
    completion = MagicMock()
    completion.content = json.dumps({
        "entities": [
            {"uri": "Aristotle", "label": "Aristotle", "class": "E21_Person"},
            {"uri": "Stagira",   "label": "Stagira",   "class": "E53_Place"},
        ]
    })
    return completion


@pytest.fixture
def triples_completion():
    completion = MagicMock()
    completion.content = json.dumps({
        "triples": [
            ["Aristotle", "rdf:type", "E21_Person"],
            ["Aristotle", "P74_has_current_or_former_residence", "Stagira"],
        ]
    })
    return completion


@pytest.fixture
def mock_llm_client(entities_completion, triples_completion):
    """
    LLM mock that routes by prompt content:
    - entity-extraction calls  → entities_completion
    - triple-extraction calls  → triples_completion
    """
    client = AsyncMock()

    async def _side_effect(messages, **kwargs):
        prompt_text = str(messages).lower()
        if '"entities"' in prompt_text:
            return entities_completion
        return triples_completion

    client.invoke = AsyncMock(side_effect=_side_effect)
    return client


@pytest.fixture(autouse=True)
def mock_ontology_file_system():
    """Globally mock file system checks and open() for all tests."""
    mock_ontology_nt = (
        "<http://example.org/ontology/E21_Person> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2002/07/owl#Class> .\n"
        "<http://example.org/ontology/P74_has_current_or_former_residence> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2002/07/owl#ObjectProperty> .\n"
    )

    # Apply all the patches, explicitly adding os.path.isfile
    with patch("builtins.open", mock_open(read_data=mock_ontology_nt)), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.isfile", return_value=True), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True):
        
        yield


class TestOntologyTripleExtractor:
    """Triple extractor tests"""

    @classmethod
    def test_init(cls, mock_llm_client):
        """Test initialization"""
        extractor = OntologyTripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            ontology_name="test-ontology",
            ontology_path="test/path.nt",
            constrain_ontology=True,
            temperature=0.0,
            max_concurrent=10,
        )
        assert extractor.llm_client == mock_llm_client
        assert extractor.model_name == "test-model"
        assert extractor.ontology_name == "test-ontology"


    @classmethod
    def test_init_with_defaults(cls, mock_llm_client):
        """Test initialization with default values"""
        extractor = OntologyTripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            ontology_name="test-ontology",
        )

    @pytest.mark.asyncio
    async def test_extract_multiple_chunks(self, mock_llm_client):
        chunks = [
            TextChunk(id_="1", text="Aristotle is a Person", doc_id="doc_1"),
            TextChunk(id_="2", text="Aristotle lives in Stagira", doc_id="doc_1"),
        ]
        extractor = OntologyTripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            ontology_name="test-ontology",
            ontology_path="test/path.nt",
            constrain_ontology=True,
            max_concurrent=2,
        )
        triples = await extractor.extract(chunks)
        assert mock_llm_client.invoke.call_count == 4


    @pytest.mark.asyncio
    async def test_extract_with_exception(self, mock_llm_client):
        """Test exception during extraction"""
        mock_llm_client.invoke = AsyncMock(side_effect=Exception("429 too many requests"))

        extractor = OntologyTripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            ontology_name="test-ontology",
            ontology_path="test/path.nt",
            constrain_ontology=True,
        )
        chunks = [
            TextChunk(id_="1", text="Aristotle is a Person", doc_id="doc_1"),
        ]
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code
        assert "429 too many requests" in (exc_info.value.message or "")


    @pytest.mark.asyncio
    async def test_extract_invalid_json(self, mock_llm_client, entities_completion):
        """Test invalid JSON in the triple-extraction response"""
        invalid_completion = MagicMock()
        invalid_completion.content = "Invalid JSON response"

        async def _side_effect(messages, **kwargs):
            prompt_text = str(messages).lower()
            if '"entities"' in prompt_text:
                return entities_completion
            return invalid_completion

        mock_llm_client.invoke = AsyncMock(side_effect=_side_effect)

        extractor = OntologyTripleExtractor(
            llm_client=mock_llm_client,
            model_name="test-model",
            ontology_name="test-ontology",
            ontology_path="test/path.nt",
            constrain_ontology=True,
        )
        chunks = [
            TextChunk(id_="1", text="Aristotle is a Person", doc_id="doc_1"),
        ]
        with pytest.raises(BaseError) as exc_info:
            await extractor.extract(chunks)
        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.code
        assert "relation extraction failed" in (exc_info.value.message or "").lower()
    

    @classmethod
    def test_init_ontology_load_failure_raises_invalid(cls, mock_llm_client):
        """Store.load raises → RETRIEVAL_KB_ONTOLOGY_INVALID is surfaced from __init__."""
        with patch("builtins.open", mock_open(read_data=b"")), \
            patch("os.path.exists", return_value=True), \
            patch("os.path.isfile", return_value=True), \
            patch(
                "openjiuwen.core.retrieval.indexing.processor.extractor"
                ".ontology_triple_extractor.Store"
            ) as MockStore:

            MockStore.return_value.load.side_effect = Exception("Corrupt NT file")

            with pytest.raises(BaseError) as exc_info:
                OntologyTripleExtractor(
                    llm_client=mock_llm_client,
                    model_name="test-model",
                    ontology_name="test-ontology",
                    ontology_path="test/path.nt",
                )

        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_ONTOLOGY_INVALID.code
        assert "Corrupt NT file" in (exc_info.value.message or "")


    @classmethod
    def test_init_ontology_query_failure_raises_invalid(cls, mock_llm_client):
        """Store.query raises after load succeeds → RETRIEVAL_KB_ONTOLOGY_INVALID is surfaced."""
        with patch("builtins.open", mock_open(read_data=b"")), \
            patch("os.path.exists", return_value=True), \
            patch("os.path.isfile", return_value=True), \
            patch(
                "openjiuwen.core.retrieval.indexing.processor.extractor"
                ".ontology_triple_extractor.Store"
            ) as MockStore:

            MockStore.return_value.load.return_value = None  # load succeeds
            MockStore.return_value.query.side_effect = Exception("SPARQL engine failure")

            with pytest.raises(BaseError) as exc_info:
                OntologyTripleExtractor(
                    llm_client=mock_llm_client,
                    model_name="test-model",
                    ontology_name="test-ontology",
                    ontology_path="test/path.nt",
                )

        assert exc_info.value.code == StatusCode.RETRIEVAL_KB_ONTOLOGY_INVALID.code
        assert "SPARQL engine failure" in (exc_info.value.message or "")

    @staticmethod
    def test_parse_triples_json_array(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test('[["a", "b", "c"]]', "d1", "c1")
        assert ok and len(triples) == 1
        assert triples[0].subject == "a" and triples[0].predicate == "b" and triples[0].object == "c"

    @staticmethod
    def test_parse_triples_extra_fields_ignored(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test('[["a", "b", "c", "ignored", 99]]', "d1", "c1")
        assert ok and triples[0].object == "c"

    @staticmethod
    def test_parse_triples_wrapped_dict(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test(
            '{"triples": [["x", "y", "z"]]}', "d1", "c1"
        )
        assert ok and len(triples) == 1 and triples[0].subject == "x"

    @staticmethod
    def test_parse_triples_prompt_shape(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test(
            '{"named_entities": ["Alice", "Bob"], "triples": [["Alice", "knows", "Bob"]]}',
            "d1",
            "c1",
        )
        assert ok and len(triples) == 1
        assert triples[0].subject == "Alice"

    @staticmethod
    def test_parse_triples_missing_triples_key_fails(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test('{"named_entities": ["Alice", "Bob"]}', "d1", "c1")
        print(ok, triples)
        assert not ok and triples == []

    @staticmethod
    def test_parse_triples_invalid_items_ignored(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test(
            '{"triples": [["a", "b", "c"], ["x"], {"bad": 1}, ["y", ["nested"], "z"]]}',
            "d1",
            "c1",
        )
        assert ok and len(triples) == 1
        assert triples[0].subject == "a"

    @staticmethod
    def test_parse_triples_all_invalid_fails(mock_llm_client):
        ex = _TestableOntologyTripleExtractor(llm_client=mock_llm_client, model_name="m", ontology_name="o")
        triples, ok = ex.parse_triples_for_test('{"triples": [["x"], {"bad": 1}]}', "d1", "c1")
        assert not ok and triples == []
