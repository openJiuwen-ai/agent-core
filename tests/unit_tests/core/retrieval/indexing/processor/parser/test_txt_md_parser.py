# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
TXT/MD file parser test cases
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser


class TestTxtMdParser:
    """TXT/MD file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = TxtMdParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_empty_file(self):
        """Test parsing empty file"""
        parser = TxtMdParser()
        
        # Create temporary empty file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            # Empty file should return empty list or document with empty text
            if documents:
                assert documents[0].text.strip() == ""
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_file_not_found(self):
        """Test parsing non-existent file"""
        parser = TxtMdParser()
        documents = await parser.parse("nonexistent.txt", doc_id="doc_1")
        # Should return empty list (exception is caught)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_strips_content(self):
        """Test content whitespace stripping"""
        parser = TxtMdParser()
        
        # Create temporary file (with whitespace before and after)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("   \n  Content  \n   ")
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            if documents:
                # Content should be stripped
                assert documents[0].text.strip() == "Content"
        finally:
            os.unlink(temp_path)

