# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Document parser abstract base class test cases
"""

from typing import Any, Optional

import pytest
from openjiuwen.core.retrieval import Parser
from openjiuwen.core.foundation.llm.model import Model


class ConcreteParser(Parser):
    """Concrete parser implementation for testing abstract base class"""

    async def _parse(self, file_path: str, llm_client: Optional[Model] = None) -> str:
        return f"Content from {file_path}"

    def supports(self, doc: str) -> bool:
        return doc.endswith(".test")


class TestParser:
    """Document parser abstract base class tests"""

    @pytest.mark.asyncio
    async def test_parse_success(self):
        """Test parsing successfully"""
        parser = ConcreteParser()
        documents = await parser.parse("test.txt", doc_id="doc_1", llm_client=None)
        assert len(documents) == 1
        assert documents[0].id_ == "doc_1"
        assert "Content from test.txt" in documents[0].text

    @pytest.mark.asyncio
    async def test_parse_empty_content(self):
        """Test parsing empty content"""

        class EmptyParser(Parser):
            async def _parse(
                self, file_path: str, llm_client: Optional[Model] = None
            ) -> Optional[str]:
                return None

            def supports(self, doc: str) -> bool:
                return True

        parser = EmptyParser()
        documents = await parser.parse("test.txt", llm_client=None)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_with_kwargs(self):
        """Test passing extra parameters during parsing"""
        parser = ConcreteParser()
        documents = await parser.parse(
            "test.txt", doc_id="doc_1", file_name="test", llm_client=None
        )
        assert len(documents) == 1

    @pytest.mark.asyncio
    async def test_lazy_parse(self):
        """Test lazy loading parsing"""
        parser = ConcreteParser()
        docs = []
        async for doc in parser.lazy_parse("test.txt", doc_id="doc_1"):
            docs.append(doc)
        assert len(docs) == 1
        assert docs[0].id_ == "doc_1"

    @pytest.mark.asyncio
    async def test_process(self):
        """Test processing (implements Processor interface)"""
        parser = ConcreteParser()
        result = await parser.process("test.txt", doc_id="doc_1")
        assert len(result) == 1

    @staticmethod
    def test_supports():
        """Test support check"""
        parser = ConcreteParser()
        assert parser.supports("file.test") is True
        assert parser.supports("file.txt") is False
