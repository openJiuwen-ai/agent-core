# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Extractor abstract base class test cases
"""
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple


class ConcreteExtractor(Extractor):
    """Concrete extractor implementation for testing abstract base class"""

    async def extract(self, chunks, **kwargs):
        triples = []
        for chunk in chunks:
            # Simple extraction logic: extract some triples from text
            if "knows" in chunk.text:
                triples.append(Triple(
                    subject="Alice",
                    predicate="knows",
                    object="Bob",
                    metadata={"doc_id": chunk.doc_id},
                ))
        return triples


class TestExtractor:
    """Extractor abstract base class tests"""

    @pytest.mark.asyncio
    async def test_extract(self):
        """Test extraction"""
        extractor = ConcreteExtractor()
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
            TextChunk(id_="2", text="Some other text", doc_id="doc_1"),
        ]
        triples = await extractor.extract(chunks)
        assert len(triples) == 1
        assert triples[0].subject == "Alice"
        assert triples[0].predicate == "knows"
        assert triples[0].object == "Bob"

    @pytest.mark.asyncio
    async def test_process(self):
        """Test processing (implements Processor interface)"""
        extractor = ConcreteExtractor()
        chunks = [
            TextChunk(id_="1", text="Alice knows Bob", doc_id="doc_1"),
        ]
        triples = await extractor.process(chunks)
        assert len(triples) == 1

    @staticmethod
    def test_cannot_instantiate_abstract_class():
        """Test cannot directly instantiate abstract class"""
        with pytest.raises(TypeError):
            Extractor()

