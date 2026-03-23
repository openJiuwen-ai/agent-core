# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
WeChat article parser test cases
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser import (
    WeChatArticleParser,
    _is_wechat_article_url,
)


class TestWeChatArticleParser:
    """WeChatArticleParser tests"""

    @staticmethod
    def test_supports_wechat_url():
        """supports() is True for WeChat article URL"""
        parser = WeChatArticleParser()
        assert parser.supports("https://mp.weixin.qq.com/s/abc123") is True
        assert parser.supports("http://mp.weixin.qq.com/s/xyz") is True

    @staticmethod
    def test_supports_non_wechat_url():
        """supports() is False for non-WeChat URL"""
        parser = WeChatArticleParser()
        assert parser.supports("https://example.com/page") is False
        assert parser.supports("") is False

    @staticmethod
    def test_is_wechat_article_url():
        """_is_wechat_article_url matches mp.weixin.qq.com/s/..."""
        assert _is_wechat_article_url("https://mp.weixin.qq.com/s/abc") is True
        assert _is_wechat_article_url("https://other.com") is False

    @pytest.mark.asyncio
    async def test_parse_returns_document_with_metadata(self):
        """parse() returns one Document with source_url, title, source_type"""
        html = """<!DOCTYPE html><html><head>
        <meta property="og:title" content="Test WeChat Title"/>
        <title>Fallback Title</title>
        </head><body><div id="js_content"><p>Article body text here.</p></div></body></html>"""
        url = "https://mp.weixin.qq.com/s/abc123"

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url_arg, headers=None, **kwargs):
                assert url_arg == url
                response = MagicMock()
                response.raise_for_status = MagicMock()
                response.text = html
                return response

        with patch(
            "openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser.httpx.AsyncClient",
            FakeAsyncClient,
        ):
            parser = WeChatArticleParser()
            docs = await parser.parse(url, doc_id="doc_1")

        assert len(docs) == 1
        assert docs[0].id_ == "doc_1"
        assert docs[0].metadata.get("source_url") == url
        assert docs[0].metadata.get("title") == "Test WeChat Title"
        assert docs[0].metadata.get("source_type") == "wechat_article"
        assert "Article body text here" in docs[0].text

    @pytest.mark.asyncio
    async def test_parse_not_wechat_url_raises(self):
        """parse() with non-WeChat URL raises"""
        parser = WeChatArticleParser()
        with pytest.raises(BaseError, match="Not a WeChat article URL"):
            await parser.parse("https://example.com/page", doc_id="id1")
