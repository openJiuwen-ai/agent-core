# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Word file parser test cases
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.retrieval import WordParser


class TestWordParser:
    """Word file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = WordParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_docx_success(self):
        """Test parsing DOCX file successfully"""
        parser = WordParser()

        # Mock docx.Document
        mock_doc = MagicMock()
        mock_element1 = MagicMock()
        mock_element1.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        mock_element1.text = "Paragraph 1"
        mock_element2 = MagicMock()
        mock_element2.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        mock_element2.text = "Paragraph 2"

        mock_doc.element.body = [mock_element1, mock_element2]

        with patch("asyncio.to_thread") as mock_to_thread:
            # First call returns Document instance
            # Second call returns element list
            mock_to_thread.side_effect = [
                mock_doc,
                [mock_element1, mock_element2],
            ]

            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1")
                assert len(documents) == 1
                assert documents[0].id_ == "doc_1"
                assert "Paragraph 1" in documents[0].text
                assert "Paragraph 2" in documents[0].text
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_docx_empty_document(self):
        """Test parsing empty document"""
        parser = WordParser()

        mock_doc = MagicMock()
        mock_doc.element.body = []

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = [
                mock_doc,
                [],
            ]

            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1")
                # Empty document should return empty list
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_docx_file_not_found(self):
        """Test parsing non-existent file"""
        parser = WordParser()
        documents = await parser.parse("nonexistent.docx", doc_id="doc_1")
        # Should return empty list (exception is caught)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_docx_with_exception(self):
        """Test exception during parsing"""
        parser = WordParser()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = Exception("DOCX parsing error")

            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1")
                # Should return empty list (exception is caught)
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
