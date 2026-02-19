# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
Web page parser test cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser import WebPageParser


class TestWebPageParser:
    """WebPageParser tests"""

    @staticmethod
    def test_supports_http_url_not_wechat():
        """supports() is True for generic http(s) URL"""
        parser = WebPageParser()
        assert parser.supports("https://example.com/article") is True
        assert parser.supports("http://blog.google/foo") is True

    @staticmethod
    def test_supports_wechat_url_false():
        """supports() is False for WeChat article URL"""
        parser = WebPageParser()
        assert parser.supports("https://mp.weixin.qq.com/s/abc") is False

    @staticmethod
    def test_supports_non_url_false():
        """supports() is False for non-URL"""
        parser = WebPageParser()
        assert parser.supports("") is False
        assert parser.supports("not-a-url") is False

    @pytest.mark.asyncio
    async def test_parse_returns_document_with_metadata(self):
        """parse() returns one Document with source_url, title, source_type"""
        body_text = "This is the main article content. " * 10  # > 50 chars and enough for selectors
        html = f"""<!DOCTYPE html><html><head>
        <meta property="og:title" content="Test Page Title"/>
        <title>Fallback</title>
        </head><body><article><p>{body_text}</p></article></body></html>"""
        url = "https://example.com/page"
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=html)
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_session.close = AsyncMock(return_value=None)

        with patch(
            "openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            parser = WebPageParser()
            docs = await parser.parse(url, doc_id="doc_1")

        assert len(docs) == 1
        assert docs[0].id_ == "doc_1"
        assert docs[0].metadata.get("source_url") == url
        assert docs[0].metadata.get("title") == "Test Page Title"
        assert docs[0].metadata.get("source_type") == "web_page"
        assert "main article content" in docs[0].text

    @pytest.mark.asyncio
    async def test_parse_wechat_url_raises(self):
        """parse() with WeChat URL raises"""
        parser = WebPageParser()
        with pytest.raises(BaseError, match="Use WeChatArticleParser"):
            await parser.parse("https://mp.weixin.qq.com/s/abc", doc_id="id1")
