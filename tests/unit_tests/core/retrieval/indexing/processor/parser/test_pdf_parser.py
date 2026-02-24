# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
PDF file parser test cases
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval import PDFParser


class TestPDFParser:
    """PDF file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = PDFParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_pdf_success(self):
        """Test parsing PDF file successfully"""
        parser = PDFParser()

        # Mock pdfplumber
        mock_pdf = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 content"
        mock_pdf.pages = [mock_page1, mock_page2]

        with patch("pdfplumber.open") as mock_open, patch("asyncio.to_thread") as mock_to_thread:
            mock_open.return_value.__enter__.return_value = mock_pdf
            mock_to_thread.return_value = "Page 1 content\nPage 2 content"

            # Create temporary file path
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1", llm_client=None)
                assert len(documents) == 1
                assert documents[0].id_ == "doc_1"
                assert "Page 1 content" in documents[0].text
                assert "Page 2 content" in documents[0].text
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_pdf_empty_pages(self):
        """Test parsing PDF with empty pages"""
        parser = PDFParser()

        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open, patch("asyncio.to_thread") as mock_to_thread:
            mock_open.return_value.__enter__.return_value = mock_pdf
            mock_to_thread.return_value = ""

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1", llm_client=None)
                # Empty pages should return empty list
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_pdf_with_none_text(self):
        """Test parsing page that returns None"""
        parser = PDFParser()

        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open, patch("asyncio.to_thread") as mock_to_thread:
            mock_open.return_value.__enter__.return_value = mock_pdf
            mock_to_thread.return_value = ""

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1", llm_client=None)
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_pdf_file_not_found(self):
        """Test parsing non-existent file"""
        parser = PDFParser()
        documents = await parser.parse("nonexistent.pdf", doc_id="doc_1", llm_client=None)
        # Should return empty list (exception is caught)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_pdf_with_exception(self):
        """Test exception during parsing"""
        parser = PDFParser()

        with patch("pdfplumber.open") as mock_open:
            mock_open.side_effect = Exception("PDF parsing error")

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1", llm_client=None)
                # Should return empty list (exception is caught)
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_pdf_multiple_pages(self):
        """Test parsing multi-page PDF"""
        parser = PDFParser()

        mock_pdf = MagicMock()
        mock_pages = []
        for i in range(5):
            mock_page = MagicMock()
            mock_page.extract_text.return_value = f"Page {i + 1} content"
            mock_pages.append(mock_page)
        mock_pdf.pages = mock_pages

        with patch("pdfplumber.open") as mock_open, patch("asyncio.to_thread") as mock_to_thread:
            mock_open.return_value.__enter__.return_value = mock_pdf
            mock_to_thread.return_value = "\n".join([f"Page {i + 1} content" for i in range(5)])

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1", llm_client=None)
                assert len(documents) == 1
                # Verify all page content is present
                for i in range(5):
                    assert f"Page {i + 1} content" in documents[0].text
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_pdf_with_image_captions(self):
        """Test parsing PDF that has images and uses ImageCaptioner"""
        parser = PDFParser()

        # create a mock pdf with one page (text optional)
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        # Mock page.images with all required attributes per pdfplumber spec
        mock_page.images = [
            {
                "x0": 0,
                "x1": 100,
                "y0": 0,
                "y1": 100,
                "top": 0,
                "bottom": 100,
                "width": 100,
                "height": 100,
                "name": "img1",
                "page_number": 1,
                "object_type": "image",
            }
        ]
        mock_pdf.pages = [mock_page]
        with patch("pdfplumber.open") as mock_open, patch("asyncio.to_thread") as mock_to_thread:
            mock_open.return_value.__enter__.return_value = mock_pdf
            mock_to_thread.return_value = ""

            # patch ImageCaptioner so caption_images returns expected captions
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.caption_images = AsyncMock(return_value=["An example caption"])
                mock_captioner_cls.return_value = mock_inst

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    temp_path = f.name
                try:
                    # pass a fake llm_client (could be a MagicMock) so ImageCaptioner is constructed
                    mock_llm_client = AsyncMock()
                    mock_llm_client.model_config.model_name = "gpt-4o"
                    documents = await parser.parse(
                        temp_path, doc_id="doc_capt", llm_client=mock_llm_client
                    )
                    assert len(documents) == 1
                    assert "An example caption" in documents[0].text
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
