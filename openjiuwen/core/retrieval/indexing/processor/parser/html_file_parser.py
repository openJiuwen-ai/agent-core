# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0

# Selectors to try for main content (in order)
MAIN_CONTENT_SELECTORS = [
    "article",
    "main",
    '[role="main"]',
    ".article-body",
    ".post-content",
    ".content",
    ".entry-content",
    ".post-body",
    "#content",
    ".main-content",
]


def _parse_html(html: str) -> BeautifulSoup:
    try:
        import lxml  # noqa: F401

        return BeautifulSoup(html, "lxml")
    except ImportError:
        return BeautifulSoup(html, "html.parser")


def _extract_title(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", property="og:title")
    if meta and meta.get("content"):
        return (meta["content"] or "").strip()
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()
    return ""


def _find_main_content(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Find main content node by trying common selectors."""
    for sel in MAIN_CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and _text_length(node) > 100:
            return node
    # Fallback: largest block with substantial text
    body = soup.find("body")
    if body:
        for tag in body.find_all(["article", "main", "div", "section"]):
            if _text_length(tag) > 200:
                return tag
    return body


def _text_length(node: BeautifulSoup) -> int:
    """Approximate main-text length (exclude script/style) without mutating the tree."""
    total = 0
    for elem in node.find_all(string=True):
        if elem.parent and elem.parent.name not in ("script", "style"):
            total += len(elem.strip())
    return total


def _get_text_from_soup(soup: Optional[BeautifulSoup]) -> str:
    if not soup:
        return ""
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


@register_parser([".htm", ".HTM", ".html", ".HTML"])
class HTMLFileParser(TxtMdParser):
    """Local file parser for HTML format"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

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
        content_node = _find_main_content(soup)
        if not content_node:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Could not find main content in HTML (source={source})",
            )
        text = _get_text_from_soup(content_node)
        if not text or len(text) < 50:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Article content too short or empty after parsing html (source={source})",
            )

        return [
            Document(
                id_=doc_id,
                text=text,
                metadata={
                    "title": title or "(无标题)",
                    "source_type": "web_page",
                },
            )
        ]

    async def parse(self, doc: str, doc_id: str = "", **kwargs) -> List[Document]:
        html = await super()._parse(doc, **kwargs)
        if not html:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Could not read HTML file or file is empty (source={doc})",
            )
        return await self._parse_html(html, doc_id=doc_id or doc, source=doc)