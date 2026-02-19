# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
Auto link parser test cases
"""

import re
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser import (
    AutoLinkParser,
    HTTP_URL_PATTERN,
)


class TestAutoLinkParser:
    """AutoLinkParser tests"""

    @staticmethod
    def test_supports_wechat_url():
        """supports() is True for WeChat article URL"""
        parser = AutoLinkParser()
        assert parser.supports("https://mp.weixin.qq.com/s/abc123") is True
        assert parser.supports("http://mp.weixin.qq.com/s/xyz") is True

    @staticmethod
    def test_supports_generic_http_url():
        """supports() is True for generic http(s) URL"""
        parser = AutoLinkParser()
        assert parser.supports("https://example.com/article") is True
        assert parser.supports("http://blog.google/foo") is True

    @staticmethod
    def test_supports_non_http_false():
        """supports() is False for non-http input"""
        parser = AutoLinkParser()
        assert parser.supports("ftp://example.com") is False
        assert parser.supports("/local/path/file.txt") is False
        assert parser.supports("not-a-url") is False

    @staticmethod
    def test_supports_empty_or_none():
        """supports() is False for empty or None"""
        parser = AutoLinkParser()
        assert parser.supports("") is False
        assert parser.supports("   ") is False

    @pytest.mark.asyncio
    async def test_parse_delegates_to_first_matching_route(self):
        """parse() delegates to first matching route and returns its result"""
        doc = Document(id_="d1", text="parsed", metadata={})
        mock_parser = AsyncMock()
        mock_parser.parse = AsyncMock(return_value=[doc])
        routes = [
            (re.compile(r"^https?://wechat\.com/"), mock_parser),
            (HTTP_URL_PATTERN, AsyncMock(return_value=[])),
        ]
        parser = AutoLinkParser(routes=routes)
        result = await parser.parse("https://wechat.com/article", doc_id="id1")
        assert result == [doc]
        mock_parser.parse.assert_awaited_once_with("https://wechat.com/article", doc_id="id1")

    @pytest.mark.asyncio
    async def test_parse_second_route_when_first_no_match(self):
        """parse() uses second route when first does not match"""
        doc = Document(id_="d2", text="web", metadata={})
        web_parser = AsyncMock()
        web_parser.parse = AsyncMock(return_value=[doc])
        routes = [
            (re.compile(r"^https?://wechat\.com/"), AsyncMock(return_value=[])),
            (HTTP_URL_PATTERN, web_parser),
        ]
        parser = AutoLinkParser(routes=routes)
        result = await parser.parse("https://example.com/page", doc_id="id2")
        assert result == [doc]
        web_parser.parse.assert_awaited_once_with("https://example.com/page", doc_id="id2")

    @pytest.mark.asyncio
    async def test_parse_no_match_returns_empty(self):
        """parse() returns [] when no route matches"""
        routes = [(re.compile(r"^https?://only\.this/"), AsyncMock(return_value=[]))]
        parser = AutoLinkParser(routes=routes)
        result = await parser.parse("https://other.com/page", doc_id="id3")
        assert result == []

    @staticmethod
    def test_custom_callable_route():
        """supports() and routing work with callable pattern"""

        def only_odd_length(url: str) -> bool:
            return bool(url and len(url) % 2 == 1)

        # Use a non-callable placeholder so the pattern is unambiguously the callable
        class _FakeParser:
            pass
        routes = [(only_odd_length, _FakeParser())]
        parser = AutoLinkParser(routes=routes)
        # len("http://a.b")==10 (even) -> False; len("http://a.bc")==11 (odd) -> True
        assert len("http://a.b") == 10 and len("http://a.bc") == 11
        assert parser.supports("http://a.b") is False
        assert parser.supports("http://a.bc") is True
