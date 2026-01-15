# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Auto file parser test cases
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import (
    AutoFileParser,
    register_parser,
    _PARSER_REGISTRY,
)
from openjiuwen.core.retrieval import Parser
from openjiuwen.core.common.exception.exception import JiuWenBaseException


class TestRegisterParser:
    """Parser registration decorator tests"""

    @staticmethod
    def test_register_parser_decorator():
        """Test parser registration decorator"""
        # Save original registry
        original_registry = _PARSER_REGISTRY.copy()

        try:

            class TestParser(Parser):
                async def _parse(self, file_path: str):
                    return "test content"

                def supports(self, doc: str) -> bool:
                    return True

            # Register using decorator
            decorated_parser = register_parser([".test", ".TEST"])(TestParser)

            # Verify registration succeeded
            assert ".test" in _PARSER_REGISTRY
            assert _PARSER_REGISTRY[".test"] is not None

            # Verify instance can be created
            parser_instance = _PARSER_REGISTRY[".test"]()
            assert isinstance(parser_instance, TestParser)
        finally:
            # Restore original registry
            _PARSER_REGISTRY.clear()
            _PARSER_REGISTRY.update(original_registry)

    @staticmethod
    def test_register_parser_multiple_extensions():
        """Test registering multiple extensions"""
        original_registry = _PARSER_REGISTRY.copy()

        try:

            class TestParser(Parser):
                async def _parse(self, file_path: str):
                    return "test content"

                def supports(self, doc: str) -> bool:
                    return True

            decorated_parser = register_parser([".ext1", ".ext2", ".EXT3"])(TestParser)

            # Verify all extensions are registered (converted to lowercase)
            assert ".ext1" in _PARSER_REGISTRY
            assert ".ext2" in _PARSER_REGISTRY
            assert ".ext3" in _PARSER_REGISTRY
        finally:
            _PARSER_REGISTRY.clear()
            _PARSER_REGISTRY.update(original_registry)


class TestAutoFileParser:
    """Auto file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = AutoFileParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_pdf_file(self):
        """Test parsing PDF file"""
        parser = AutoFileParser()

        # Mock PDF parsing
        with patch("openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser.pdfplumber") as mock_pdfplumber:
            mock_pdf = MagicMock()
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "PDF content"
            mock_pdf.pages = [mock_page]
            mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

            with patch("asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = "PDF content"

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    temp_path = f.name

                try:
                    documents = await parser.parse(temp_path, doc_id="doc_1")
                    assert len(documents) == 1
                    assert documents[0].metadata["file_ext"] == ".pdf"
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_file(self):
        """Test parsing JSON file"""
        parser = AutoFileParser()

        import json

        json_data = {"key": "value"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_data, f)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            assert documents[0].metadata["file_ext"] == ".json"
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_file_not_found(self):
        """Test parsing non-existent file"""
        parser = AutoFileParser()

        with pytest.raises(JiuWenBaseException, match="does not exist"):
            await parser.parse("nonexistent.txt", doc_id="doc_1")

    @pytest.mark.asyncio
    async def test_parse_unsupported_format(self):
        """Test parsing unsupported file format"""
        parser = AutoFileParser()

        # Create temporary file (unsupported format)
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            temp_path = f.name

        try:
            with pytest.raises(JiuWenBaseException, match="Unsupported format"):
                await parser.parse(temp_path, doc_id="doc_1")
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_supports_existing_file():
        """Test support check (file exists)"""
        parser = AutoFileParser()

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            temp_path = f.name

        try:
            result = parser.supports(temp_path)
            assert result is True
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_supports_nonexistent_file():
        """Test support check (file does not exist)"""
        parser = AutoFileParser()
        result = parser.supports("nonexistent.txt")
        assert result is False

    @staticmethod
    def test_supports_unsupported_format():
        """Test support check (unsupported format)"""
        parser = AutoFileParser()

        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            temp_path = f.name

        try:
            result = parser.supports(temp_path)
            assert result is False
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_register_new_parser():
        """Test dynamically registering new parser"""
        original_registry = _PARSER_REGISTRY.copy()

        try:

            class CustomParser(Parser):
                async def _parse(self, file_path: str):
                    return "custom content"

                def supports(self, doc: str) -> bool:
                    return True

            # Dynamic registration
            AutoFileParser.register_new_parser(".custom", lambda: CustomParser())

            # Verify registration succeeded
            assert ".custom" in _PARSER_REGISTRY

            # Verify it can be used
            parser = AutoFileParser()
            with tempfile.NamedTemporaryFile(suffix=".custom", delete=False) as f:
                f.write(b"test")
                temp_path = f.name

            try:
                result = parser.supports(temp_path)
                assert result is True
            finally:
                os.unlink(temp_path)
        finally:
            _PARSER_REGISTRY.clear()
            _PARSER_REGISTRY.update(original_registry)

    @staticmethod
    def test_get_supported_formats():
        """Test getting supported file formats"""
        formats = AutoFileParser.get_supported_formats()
        assert isinstance(formats, list)
        # Should contain common formats
        assert ".txt" in formats or ".pdf" in formats or ".json" in formats

    @pytest.mark.asyncio
    async def test_parse_empty_result(self):
        """Test parsing returns empty result"""
        parser = AutoFileParser()

        # Create a file that will be parsed as empty content
        with patch("openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser.aiofiles") as mock_aiofiles:
            mock_file = AsyncMock()
            mock_file.read.return_value = ""
            mock_aiofiles.open.return_value.__aenter__.return_value = mock_file

            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1")
                # Empty content should return empty list
                assert len(documents) == 0
            finally:
                os.unlink(temp_path)
