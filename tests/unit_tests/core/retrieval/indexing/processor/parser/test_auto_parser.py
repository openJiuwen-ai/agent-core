# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
Auto parser (top-level router) test cases
"""

import os
import tempfile
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.auto_parser import AutoParser


class TestAutoParser:
    """AutoParser tests"""

    @staticmethod
    def test_supports_http_url():
        """supports() is True for http(s) URL (delegates to link parser)"""
        parser = AutoParser()
        assert parser.supports("https://mp.weixin.qq.com/s/abc") is True
        assert parser.supports("https://example.com/page") is True

    @staticmethod
    def test_supports_file_path():
        """supports() is True for existing file with registered extension"""
        parser = AutoParser()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            assert parser.supports(path) is True
        finally:
            os.unlink(path)

    @staticmethod
    def test_supports_non_url_non_file():
        """supports() is False for non-URL and non-existing path"""
        parser = AutoParser()
        assert parser.supports("not-a-url") is False
        assert parser.supports("/nonexistent/path.txt") is False

    @pytest.mark.asyncio
    async def test_parse_url_delegates_to_link_parser(self):
        """parse() with URL calls link_parser.parse()"""

        def _supports_http(d):
            return d.startswith("http")

        def _supports_nothing(_d):
            return False

        doc = Document(id_="link_doc", text="from link", metadata={})
        link_parser = AsyncMock()
        link_parser.parse = AsyncMock(return_value=[doc])
        link_parser.supports = _supports_http
        file_parser = AsyncMock()
        file_parser.supports = _supports_nothing
        parser = AutoParser(link_parser=link_parser, file_parser=file_parser)
        result = await parser.parse("https://example.com/page", doc_id="id1")
        assert result == [doc]
        link_parser.parse.assert_awaited_once_with("https://example.com/page", doc_id="id1")
        file_parser.parse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parse_file_delegates_to_file_parser(self):
        """parse() with file path calls file_parser.parse()"""

        def _supports_nothing(_d):
            return False

        def _supports_txt_file(d):
            return os.path.exists(d) and d.endswith(".txt")

        doc = Document(id_="file_doc", text="from file", metadata={})
        link_parser = AsyncMock()
        link_parser.supports = _supports_nothing
        file_parser = AsyncMock()
        file_parser.parse = AsyncMock(return_value=[doc])
        file_parser.supports = _supports_txt_file
        parser = AutoParser(link_parser=link_parser, file_parser=file_parser)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            result = await parser.parse(path, doc_id="id2")
            assert result == [doc]
            file_parser.parse.assert_awaited_once_with(path, doc_id="id2")
            link_parser.parse.assert_not_awaited()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_unsupported_returns_empty(self):
        """parse() returns [] when neither link nor file parser supports"""

        def _supports_nothing(_d):
            return False

        link_parser = AsyncMock()
        link_parser.supports = _supports_nothing
        file_parser = AsyncMock()
        file_parser.supports = _supports_nothing
        parser = AutoParser(link_parser=link_parser, file_parser=file_parser)
        result = await parser.parse("https://example.com", doc_id="id3")
        assert result == []
        result = await parser.parse("/nonexistent.xyz", doc_id="id4")
        assert result == []
