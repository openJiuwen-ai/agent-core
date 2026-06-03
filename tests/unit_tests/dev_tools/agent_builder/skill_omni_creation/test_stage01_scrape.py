# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

import pytest
from bs4 import BeautifulSoup

import stage_01_scrape as s1


@pytest.mark.unit
class TestIsUtilityUrl:
    @staticmethod
    def test_login_path_is_utility():
        assert s1.is_utility_url("https://example.com/login") is True

    @staticmethod
    def test_normal_article_is_not_utility():
        assert s1.is_utility_url("https://support.google.com/docs/answer/12345") is False

    @staticmethod
    def test_subpath_of_utility_is_utility():
        assert s1.is_utility_url("https://example.com/terms/service") is True

    @staticmethod
    def test_empty_url_does_not_raise():
        assert s1.is_utility_url("") is False


@pytest.mark.unit
class TestIsContentImg:
    @staticmethod
    def test_img_with_src_is_content():
        soup = BeautifulSoup('<img src="https://example.com/photo.png">', "html.parser")
        assert s1.is_content_img(soup.find("img")) is True

    @staticmethod
    def test_img_with_data_src_is_content():
        soup = BeautifulSoup('<img data-src="https://example.com/photo.png">', "html.parser")
        assert s1.is_content_img(soup.find("img")) is True

    @staticmethod
    def test_img_without_src_is_not_content():
        soup = BeautifulSoup("<img alt='no src'>", "html.parser")
        assert s1.is_content_img(soup.find("img")) is False

    @staticmethod
    def test_data_uri_img_is_not_content():
        soup = BeautifulSoup('<img src="data:image/png;base64,abc">', "html.parser")
        assert s1.is_content_img(soup.find("img")) is False

    @staticmethod
    def test_svg_img_is_not_content():
        soup = BeautifulSoup('<img src="icon.svg">', "html.parser")
        assert s1.is_content_img(soup.find("img")) is False


@pytest.mark.unit
class TestBestImgUrl:
    @staticmethod
    def test_picks_last_srcset_entry():
        html = '<img srcset="img-small.png 320w, img-medium.png 640w, img-large.png 1024w" src="img-small.png">'
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        result = s1._best_img_url(img, "https://example.com/page")
        assert "img-large.png" in result

    @staticmethod
    def test_falls_back_to_src_when_no_srcset():
        html = '<img src="photo.png">'
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        result = s1._best_img_url(img, "https://example.com/page")
        assert "photo.png" in result

    @staticmethod
    def test_resolves_relative_src():
        html = '<img src="/images/photo.png">'
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        result = s1._best_img_url(img, "https://example.com/page")
        assert result == "https://example.com/images/photo.png"

    @staticmethod
    def test_skips_data_uri_in_src():
        html = '<img src="data:image/png;base64,abc" data-src="https://example.com/real.png">'
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        result = s1._best_img_url(img, "https://example.com/page")
        assert "real.png" in result

    @staticmethod
    def test_returns_empty_when_no_valid_url():
        html = '<img alt="no src">'
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        assert s1._best_img_url(img, "https://example.com/page") == ""


@pytest.mark.unit
class TestBuildTabpanelLabels:
    @staticmethod
    def test_builds_label_from_aria_controls():
        html = (
            "<main>"
            '<div role="tab" aria-controls="panel1">Windows 11</div>'
            '<div role="tabpanel" id="panel1"><p>Content</p></div>'
            "</main>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = s1._build_tabpanel_labels(soup.find("main"))
        assert result == {"panel1": "Windows 11"}

    @staticmethod
    def test_fallback_to_aria_labelledby():
        html = (
            "<main>"
            '<div role="tab" id="tab1">Windows 10</div>'
            '<div role="tabpanel" id="panel2" aria-labelledby="tab1"><p>Content</p></div>'
            "</main>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = s1._build_tabpanel_labels(soup.find("main"))
        assert result == {"panel2": "Windows 10"}

    @staticmethod
    def test_multiple_tabs():
        html = (
            "<main>"
            '<div role="tab" aria-controls="p1">Tab A</div>'
            '<div role="tab" aria-controls="p2">Tab B</div>'
            '<div role="tabpanel" id="p1"></div>'
            '<div role="tabpanel" id="p2"></div>'
            "</main>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = s1._build_tabpanel_labels(soup.find("main"))
        assert result == {"p1": "Tab A", "p2": "Tab B"}

    @staticmethod
    def test_returns_empty_for_no_tabs():
        soup = BeautifulSoup("<main><p>No tabs here</p></main>", "html.parser")
        result = s1._build_tabpanel_labels(soup.find("main"))
        assert result == {}


@pytest.mark.unit
class TestTabpanelInfo:
    @staticmethod
    def test_detects_element_inside_tabpanel():
        html = (
            "<main>"
            '<div role="tab" aria-controls="p1">Windows 11</div>'
            '<div role="tabpanel" id="p1"><img src="x.png"></div>'
            "</main>"
        )
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main")
        labels = s1._build_tabpanel_labels(main)
        img = soup.find("img")
        panel_id, tab_label = s1._tabpanel_info(img, main, labels)
        assert panel_id == "p1"
        assert tab_label == "Windows 11"

    @staticmethod
    def test_returns_empty_for_element_outside_tabpanel():
        html = "<main><p id='para'>Paragraph</p></main>"
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main")
        para = soup.find("p")
        panel_id, tab_label = s1._tabpanel_info(para, main, {})
        assert panel_id == ""
        assert tab_label == ""


@pytest.mark.unit
class TestBuildBlocks:
    @staticmethod
    def test_produces_heading_blocks():
        html = "<html><body><main><h2>Step One</h2><p>Do this thing first indeed.</p></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        headings = [b for b in blocks if b["type"] == "heading"]
        assert any(b["text"] == "Step One" for b in headings)

    @staticmethod
    def test_produces_text_blocks():
        html = "<html><body><main><p>This is a long enough paragraph to be included in output.</p></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        texts = [b for b in blocks if b["type"] == "text"]
        assert any("long enough paragraph" in b["text"] for b in texts)

    @staticmethod
    def test_skips_short_text():
        html = "<html><body><main><p>Short</p></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        assert not any(b.get("text") == "Short" for b in blocks)

    @staticmethod
    def test_produces_image_blocks():
        html = '<html><body><main><img src="https://example.com/photo.png" alt="screenshot"></main></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        images = [b for b in blocks if b["type"] == "image"]
        assert len(images) == 1
        assert "photo.png" in images[0]["url"]
        assert images[0]["alt"] == "screenshot"
        assert images[0]["path"] is None

    @staticmethod
    def test_image_block_has_source_field():
        html = '<html><body><main><img src="https://example.com/img.png" alt="x"></main></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "subpage")
        images = [b for b in blocks if b["type"] == "image"]
        assert images[0]["source"] == "subpage"

    @staticmethod
    def test_deduplicates_repeated_headings():
        html = "<html><body><main><h2>Duplicate</h2><h2>Duplicate</h2></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        heading_texts = [b["text"] for b in blocks if b["type"] == "heading"]
        assert heading_texts.count("Duplicate") == 1

    @staticmethod
    def test_preserves_dom_order():
        html = (
            "<html><body><main>"
            "<h2>Heading</h2>"
            "<p>Text block that is long enough to be included.</p>"
            '<img src="https://example.com/img.png" alt="step">'
            "</main></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        types = [b["type"] for b in blocks]
        assert types.index("heading") < types.index("text") < types.index("image")

    @staticmethod
    def test_injects_tab_label_as_heading():
        html = (
            "<html><body><main>"
            '<div role="tab" aria-controls="p1">Windows 11</div>'
            '<div role="tabpanel" id="p1"><p>Some content that is long enough.</p></div>'
            "</main></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        headings = [b for b in blocks if b["type"] == "heading"]
        assert any(b["text"] == "Windows 11" for b in headings)

    @staticmethod
    def test_skips_noise_tabpanel_images():
        html = (
            "<html><body><main>"
            '<div role="tab" aria-controls="community-panel">Community</div>'
            '<div role="tabpanel" id="community-panel">'
            '<img src="https://example.com/community.png" alt="community">'
            "</div>"
            "</main></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        image_urls = [b.get("url", "") for b in blocks if b["type"] == "image"]
        assert "https://example.com/community.png" not in image_urls

    @staticmethod
    def test_heading_level_is_correct():
        html = "<html><body><main><h3>Sub heading</h3></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        headings = [b for b in blocks if b["type"] == "heading"]
        assert headings[0]["level"] == 3

    @staticmethod
    def test_truncates_long_text_to_400_chars():
        long_text = "a" * 500
        html = f"<html><body><main><p>{long_text}</p></main></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = s1.build_blocks(soup, "https://example.com/page", "main")
        texts = [b for b in blocks if b["type"] == "text"]
        assert all(len(b["text"]) <= 400 for b in texts)


@pytest.mark.unit
class TestParsePageHtml:
    @staticmethod
    def test_returns_list_of_blocks():
        html = "<html><body><main><h2>Step</h2></main></body></html>"
        result = s1.parse_page_html(html, "https://example.com/page", "main")
        assert isinstance(result, list)

    @staticmethod
    def test_strips_cookie_banner_by_id():
        html = (
            "<html><body>"
            '<div id="onetrust-consent-sdk">Cookie notice</div>'
            "<main><h2>Real content</h2></main>"
            "</body></html>"
        )
        blocks = s1.parse_page_html(html, "https://example.com/page", "main")
        texts = [b.get("text", "") for b in blocks]
        assert not any("Cookie notice" in t for t in texts)

    @staticmethod
    def test_includes_main_content_headings():
        html = "<html><body><main><h2>How to Share Files</h2></main></body></html>"
        blocks = s1.parse_page_html(html, "https://example.com/page", "main")
        headings = [b for b in blocks if b["type"] == "heading"]
        assert any("How to Share Files" in b["text"] for b in headings)


@pytest.mark.unit
class TestDetectVideoUrls:
    @staticmethod
    def test_detects_youtube_embed():
        html = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>'
        result = s1.detect_video_urls_from_html(html)
        assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in result

    @staticmethod
    def test_detects_vimeo_embed():
        html = '<iframe src="https://player.vimeo.com/video/123456789"></iframe>'
        result = s1.detect_video_urls_from_html(html)
        assert "https://vimeo.com/123456789" in result

    @staticmethod
    def test_deduplicates_same_video():
        html = (
            '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
            '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        )
        result = s1.detect_video_urls_from_html(html)
        assert result.count("https://www.youtube.com/watch?v=abc123") == 1

    @staticmethod
    def test_returns_empty_for_no_videos():
        assert s1.detect_video_urls_from_html("<p>No videos here</p>") == []


@pytest.mark.unit
class TestScrapeOnePage(IsolatedAsyncioTestCase):
    async def test_returns_html_on_success(self):
        page = MagicMock()
        page.goto = AsyncMock(return_value=MagicMock(status=200))
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])
        page.eval_on_selector_all = AsyncMock(return_value=[])
        page.query_selector = AsyncMock(return_value=None)
        page.content = AsyncMock(return_value="<html><body><h1>Hello</h1></body></html>")

        html, video_urls, subpage_links = await s1.scrape_one_page(page, "https://example.com")

        assert "<html>" in html

    async def test_returns_empty_on_http_error(self):
        page = MagicMock()
        page.goto = AsyncMock(return_value=MagicMock(status=404))
        page.wait_for_timeout = AsyncMock()

        html, video_urls, subpage_links = await s1.scrape_one_page(page, "https://example.com/missing")

        assert html == ""

    async def test_returns_empty_on_exception(self):
        page = MagicMock()
        page.goto = AsyncMock(side_effect=Exception("network error"))
        page.wait_for_timeout = AsyncMock()

        html, video_urls, subpage_links = await s1.scrape_one_page(page, "https://example.com")

        assert html == ""
