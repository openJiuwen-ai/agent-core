# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WeChat Official Account Article Parser

Fetches and parses WeChat article pages from URLs (e.g. https://mp.weixin.qq.com/s/...)
into Document objects for indexing. Uses BeautifulSoup for HTML parsing.
"""

from typing import List, Optional

from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser import (
    _parse_html,
    _extract_title,
    _get_text_from_soup,
    _is_wechat_article_url,
    WebPageParser,
)


def _extract_js_content(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Get the div with id='js_content' (article body)."""
    div = soup.find("div", id="js_content")
    return div


class WeChatArticleParser(WebPageParser):
    """
    Parser for WeChat official account article URLs.
    Use AutoParser or parse_urls with AutoLinkParser for auto-dispatch by URL.
    """

    @staticmethod
    def _validate_url(url: str):
        url = (url or "").strip()
        if not _is_wechat_article_url(url):
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Not a WeChat article URL: {url!r}",
            )

    @classmethod
    async def _parse_html(
        cls,
        html: str,
        doc_id: str = "",
        source: Optional[str] = None,
    ) -> List[Document]:
        """
        Fetch a web page URL and parse it into one or more Document objects.

        Args:
            html: Web page content string
            doc_id: Optional document ID; defaults to URL or generated UUID.
            source: HTML source (URL or file path).

        Returns:
            List of Document instances (typically one).

        Raises:
            build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
        """
        soup = _parse_html(html)
        title = _extract_title(soup)
        content_node = _extract_js_content(soup)
        if not content_node:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Could not find article content (js_content) in page (source: {source})",
            )
        text = _get_text_from_soup(content_node)
        if not text:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Article content is empty after parsing (source: {source})",
            )

        return [
            Document(
                id_=doc_id,
                text=text,
                metadata={
                    "title": title or "(无标题)",
                    "source_type": "wechat_article",
                },
            )
        ]

    def supports(self, doc: str) -> bool:
        """Return True if doc is a WeChat article URL."""
        return _is_wechat_article_url(doc)


parse_wechat_article_url = WeChatArticleParser.parse_url
