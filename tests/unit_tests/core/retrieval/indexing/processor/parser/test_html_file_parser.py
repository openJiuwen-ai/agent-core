# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""
HTML file parser unit tests.
"""

import builtins
import os
import tempfile
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
import openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser as html_file_parser

HTMLFileParser = html_file_parser.HTMLFileParser
MAIN_CONTENT_SELECTORS = html_file_parser.MAIN_CONTENT_SELECTORS
DEFAULT_TIMEOUT = html_file_parser.DEFAULT_TIMEOUT
DEFAULT_USER_AGENT = html_file_parser.DEFAULT_USER_AGENT


def _long_text(min_chars: int) -> str:
    """Build a string of at least min_chars (for selector length thresholds)."""
    if min_chars <= 0:
        return ""
    return "w" * min_chars


def _minimal_html(
    inner: str,
    *,
    head_extra: str = "",
    title: str = "DocTitle",
) -> str:
    return f"""<!DOCTYPE html><html><head>
    <meta charset="utf-8"/>
    {head_extra}
    <title>{title}</title>
    </head><body>{inner}</body></html>"""


class TestModuleConstants:
    """Module-level patterns and defaults (used by tooling / future URL handling)."""

    @staticmethod
    def test_default_user_agent_and_timeout_are_defined():
        assert isinstance(DEFAULT_USER_AGENT, str) and len(DEFAULT_USER_AGENT) > 10
        assert DEFAULT_TIMEOUT == 30.0

    @staticmethod
    def test_main_content_selectors_is_non_empty_ordered_list():
        assert isinstance(MAIN_CONTENT_SELECTORS, list)
        assert "article" in MAIN_CONTENT_SELECTORS
        assert "main" in MAIN_CONTENT_SELECTORS


class TestParseHtmlBackend:
    """_parse_html(): BeautifulSoup backend selection."""

    @staticmethod
    def test_parse_html_uses_lxml_when_available():
        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        soup = html_file_parser._parse_html(html)
        assert soup.find("article") is not None

    @staticmethod
    def test_parse_html_falls_back_when_lxml_import_fails():
        html = _minimal_html(f"<main><p>{_long_text(120)}</p></main>")
        real_import = builtins.__import__

        def import_no_lxml(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "lxml":
                raise ImportError("forced for test")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=import_no_lxml):
            soup = html_file_parser._parse_html(html)
        assert soup.find("main") is not None


class TestExtractTitle:
    """_extract_title()."""

    @staticmethod
    def test_og_title_preferred_over_title_tag():
        html = _minimal_html(
            "<article>x</article>",
            head_extra='<meta property="og:title" content="  OG Title  "/>',
            title="TitleTag",
        )
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == "OG Title"

    @staticmethod
    def test_og_title_whitespace_only_returns_empty_string():
        html = _minimal_html(
            f"<article>{_long_text(120)}</article>",
            head_extra='<meta property="og:title" content="   "/>',
            title="IgnoredBecauseOgWins",
        )
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == ""

    @staticmethod
    def test_missing_og_content_attr_falls_back_to_title_tag():
        # Whitespace-only content="" still hits the og branch and yields ""; omit content to fall through.
        html = _minimal_html(
            f"<article>{_long_text(120)}</article>",
            head_extra='<meta property="og:title"/>',
            title="RealTitle",
        )
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == "RealTitle"

    @staticmethod
    def test_missing_og_uses_title_tag():
        html = _minimal_html(f"<article>{_long_text(120)}</article>", title="OnlyTitle")
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == "OnlyTitle"

    @staticmethod
    def test_title_tag_with_nested_markup_uses_string_repr_under_html_parser():
        # html.parser may expose markup in title_tag.string for nested tags; cover the title branch.
        html = _minimal_html(
            f"<article>{_long_text(120)}</article>",
            title="",
        ).replace("<title></title>", "<title><b>Nested</b></title>")
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == "<b>Nested</b>"

    @staticmethod
    def test_no_title_returns_empty_string():
        html = f"<html><body><article>{_long_text(120)}</article></body></html>"
        soup = html_file_parser._parse_html(html)
        assert html_file_parser._extract_title(soup) == ""


class TestTextLength:
    """_text_length(): script/style excluded."""

    @staticmethod
    def test_excludes_script_and_style_text():
        filler = _long_text(150)
        html = f"<div>{filler}<script>alert('ignore this long script text')</script><style>.x{{}}</style></div>"
        soup = BeautifulSoup(html, "html.parser")
        div = soup.find("div")
        length = html_file_parser._text_length(div)
        assert length >= 150
        assert "ignore" not in div.get_text()


class TestGetTextFromSoup:
    """_get_text_from_soup()."""

    @staticmethod
    def test_none_returns_empty():
        assert html_file_parser._get_text_from_soup(None) == ""

    @staticmethod
    def test_removes_script_and_style_and_normalizes_whitespace():
        html = """<div>Line  one\t \n\n\n  Line  two<script>bad</script><style>x</style></div>"""
        soup = BeautifulSoup(html, "html.parser")
        text = html_file_parser._get_text_from_soup(soup.find("div"))
        assert "bad" not in text
        assert "Line one" in text
        assert "Line two" in text


class TestFindMainContent:
    """_find_main_content() selector order and fallbacks."""

    @pytest.mark.parametrize(
        "tag_open,tag_close",
        [
            ("<article>", "</article>"),
            ("<main>", "</main>"),
            ('<div role="main">', "</div>"),
            ('<div class="article-body">', "</div>"),
            ('<div class="post-content">', "</div>"),
            ('<div class="content">', "</div>"),
            ('<div class="entry-content">', "</div>"),
            ('<div class="post-body">', "</div>"),
            ('<div id="content">', "</div>"),
            ('<div class="main-content">', "</div>"),
        ],
    )
    def test_selector_matches_when_text_long_enough(self, tag_open, tag_close):
        inner = f"{tag_open}<p>{_long_text(120)}</p>{tag_close}"
        html = _minimal_html(inner)
        soup = html_file_parser._parse_html(html)
        node = html_file_parser._find_main_content(soup)
        assert node is not None
        assert html_file_parser._text_length(node) > 100

    def test_skips_selector_when_text_too_short_then_uses_later_or_body(self):
        short_article = f"<article>{_long_text(50)}</article>"
        big_div = f'<div class="content"><p>{_long_text(120)}</p></div>'
        html = _minimal_html(short_article + big_div)
        soup = html_file_parser._parse_html(html)
        node = html_file_parser._find_main_content(soup)
        assert node is not None
        assert node.get("class") == ["content"]

    def test_fallback_large_div_over_200_chars(self):
        inner = f"<div><p>{_long_text(250)}</p></div>"
        html = _minimal_html(inner)
        soup = html_file_parser._parse_html(html)
        node = html_file_parser._find_main_content(soup)
        assert node is not None
        assert node.name == "div"

    def test_fallback_section_over_200_chars(self):
        inner = f"<section><p>{_long_text(250)}</p></section>"
        html = _minimal_html(inner)
        soup = html_file_parser._parse_html(html)
        node = html_file_parser._find_main_content(soup)
        assert node is not None
        assert node.name == "section"

    def test_returns_body_when_no_structural_match_but_body_has_text(self):
        inner = f"<p>{_long_text(80)}</p>"
        html = _minimal_html(inner)
        soup = html_file_parser._parse_html(html)
        node = html_file_parser._find_main_content(soup)
        assert node is not None
        assert node.name == "body"

    def test_returns_none_when_no_body(self):
        soup = BeautifulSoup("", "html.parser")
        assert soup.find("body") is None
        assert html_file_parser._find_main_content(soup) is None


class TestHTMLFileParserParseHtml:
    """HTMLFileParser._parse_html() classmethod."""

    @pytest.mark.asyncio
    async def test_success_single_document_metadata(self):
        body = _long_text(120)
        html = _minimal_html(
            f"<article><p>{body}</p></article>",
            head_extra='<meta property="og:title" content="MetaTitle"/>',
        )
        docs = await HTMLFileParser._parse_html(html, doc_id="id-1", source="/tmp/x.html")
        assert len(docs) == 1
        assert docs[0].id_ == "id-1"
        assert docs[0].metadata["title"] == "MetaTitle"
        assert docs[0].metadata["source_type"] == "web_page"
        assert _long_text(120)[:20] in docs[0].text

    @pytest.mark.asyncio
    async def test_placeholder_title_when_missing(self):
        html = _minimal_html(
            f"<article><p>{_long_text(120)}</p></article>",
            title="",
        )
        html = html.replace("<title></title>", "")
        docs = await HTMLFileParser._parse_html(html, doc_id="d")
        assert docs[0].metadata["title"] == "(无标题)"

    @pytest.mark.asyncio
    async def test_raises_when_no_main_content_node(self):
        html = ""
        with pytest.raises(BaseError) as exc_info:
            await HTMLFileParser._parse_html(html, source="empty.html")
        assert exc_info.value.status == StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR
        assert "Could not find main content" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_when_extracted_text_too_short(self):
        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        with patch.object(html_file_parser, "_get_text_from_soup", return_value="short"):
            with pytest.raises(BaseError) as exc_info:
                await HTMLFileParser._parse_html(html, source="x.html")
        assert exc_info.value.status == StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR
        assert "too short or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_when_text_empty_string(self):
        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        with patch.object(html_file_parser, "_get_text_from_soup", return_value=""):
            with pytest.raises(BaseError) as exc_info:
                await HTMLFileParser._parse_html(html, source="x.html")
        assert exc_info.value.status == StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR


class TestHTMLFileParserParseFile:
    """HTMLFileParser.parse() with real files."""

    @pytest.mark.asyncio
    async def test_parse_html_file_success(self):
        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        parser = HTMLFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            docs = await parser.parse(path, doc_id="my-id")
            assert len(docs) == 1
            assert docs[0].id_ == "my-id"
            assert "DocTitle" in docs[0].metadata.get("title", "") or docs[0].metadata.get("title")
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_htm_extension(self):
        html = _minimal_html(f"<main><p>{_long_text(120)}</p></main>")
        parser = HTMLFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".htm", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            docs = await parser.parse(path)
            assert len(docs) == 1
            assert docs[0].id_ == path
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_doc_id_defaults_to_path(self):
        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        parser = HTMLFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".HTML", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            docs = await parser.parse(path, doc_id="")
            assert docs[0].id_ == path
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_empty_file_raises(self):
        parser = HTMLFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            path = f.name
        try:
            with pytest.raises(BaseError) as exc_info:
                await parser.parse(path)
            assert exc_info.value.status == StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR
            assert "empty" in str(exc_info.value).lower() or "Could not read" in str(exc_info.value)
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_nonexistent_file_raises(self):
        parser = HTMLFileParser()
        with pytest.raises(BaseError) as exc_info:
            await parser.parse("/nonexistent/path/does-not-exist-12345.html")
        assert exc_info.value.status == StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR

    @pytest.mark.asyncio
    async def test_parse_utf8_content(self):
        html = _minimal_html(f"<article><p>日本語 {_long_text(120)}</p></article>")
        parser = HTMLFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            docs = await parser.parse(path)
            assert "日本語" in docs[0].text
        finally:
            os.unlink(path)


class TestHTMLFileParserInit:
    @staticmethod
    def test_init_accepts_kwargs():
        assert HTMLFileParser(foo=1) is not None


class TestAutoFileParserHtmlIntegration:
    """End-to-end: AutoFileParser dispatches .html to HTMLFileParser when registry is loaded."""

    @pytest.mark.asyncio
    async def test_auto_file_parser_parses_html_path(self):
        from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser

        html = _minimal_html(f"<article><p>{_long_text(120)}</p></article>")
        parser = AutoFileParser()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            docs = await parser.parse(path, doc_id="auto-1")
            assert len(docs) == 1
            assert docs[0].metadata.get("doc_id") == "auto-1"
            assert docs[0].metadata.get("file_ext") == ".html"
            assert _long_text(40) in docs[0].text
        finally:
            os.unlink(path)
