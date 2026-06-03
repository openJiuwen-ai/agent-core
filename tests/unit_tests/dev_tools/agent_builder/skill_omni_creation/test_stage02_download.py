# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import hashlib
import io
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import stage_02_download as s2


def _make_png(width: int = 200, height: int = 200) -> bytes:
    """Create a minimal valid PNG image in memory."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _img_block(url: str = "https://example.com/img.png") -> dict:
    return {"type": "image", "url": url, "alt": "", "source": "main", "path": None}


def _text_block(text: str = "Some text") -> dict:
    return {"type": "text", "text": text, "source": "main"}


def _heading_block(text: str = "Heading") -> dict:
    return {"type": "heading", "level": 2, "text": text, "source": "main"}


def _mock_response(data: bytes, mime: str = "image/png", status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.headers = {"content-type": mime}
    chunk_size = 8192
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]
    resp.iter_content = MagicMock(return_value=iter(chunks))
    return resp


@pytest.mark.unit
class TestFetchOne:
    @staticmethod
    def test_returns_data_and_mime_on_success():
        png = _make_png()
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(png, "image/png")):
            url, data, mime = s2.fetch_one("https://example.com/img.png")
        assert data == png
        assert mime == "image/png"

    @staticmethod
    def test_returns_none_on_unsupported_mime():
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(b"data", "image/tiff")):
            url, data, mime = s2.fetch_one("https://example.com/img.tiff")
        assert data is None
        assert mime is None

    @staticmethod
    def test_returns_none_on_network_exception():
        with patch.object(s2._fetch_session, "get", side_effect=Exception("timeout")):
            url, data, mime = s2.fetch_one("https://example.com/img.png")
        assert data is None
        assert mime is None

    @staticmethod
    def test_returns_none_when_image_too_small():
        tiny_png = _make_png(width=10, height=10)
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(tiny_png, "image/png")):
            url, data, mime = s2.fetch_one("https://example.com/tiny.png")
        assert data is None

    @staticmethod
    def test_returns_none_when_data_exceeds_max_bytes():
        # Simulate large response by patching MAX_IMAGE_BYTES to 1
        png = _make_png()
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(png, "image/png")):
            with patch.object(s2.common, "MAX_IMAGE_BYTES", 1):
                url, data, mime = s2.fetch_one("https://example.com/huge.png")
        assert data is None

    @staticmethod
    def test_strips_mime_parameters():
        png = _make_png()
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(png, "image/png; charset=utf-8")):
            url, data, mime = s2.fetch_one("https://example.com/img.png")
        assert mime == "image/png"

    @staticmethod
    def test_returns_correct_url():
        target = "https://example.com/specific.png"
        png = _make_png()
        with patch.object(s2._fetch_session, "get", return_value=_mock_response(png)):
            url, _, _ = s2.fetch_one(target)
        assert url == target


@pytest.mark.unit
class TestDownloadImageBlocks:
    @staticmethod
    def _patch_fetch(url_to_result: dict):
        """Return a fetch_one mock that maps url -> (url, data, mime)."""
        def _fake_fetch(url):
            result = url_to_result.get(url, (url, None, None))
            return result
        return patch("stage_02_download.fetch_one", side_effect=_fake_fetch)

    def test_keeps_successfully_downloaded_image_blocks(self):
        png = _make_png()
        url = "https://example.com/img.png"
        blocks = [_img_block(url)]
        with self._patch_fetch({url: (url, png, "image/png")}):
            new_blocks, fetched = s2.download_image_blocks(blocks)
        assert len([b for b in new_blocks if b["type"] == "image"]) == 1
        assert url in fetched

    def test_removes_failed_image_blocks(self):
        url = "https://example.com/broken.png"
        blocks = [_img_block(url)]
        with self._patch_fetch({url: (url, None, None)}):
            new_blocks, fetched = s2.download_image_blocks(blocks)
        assert all(b["type"] != "image" for b in new_blocks)
        assert url not in fetched

    def test_deduplicates_by_content_hash(self):
        png = _make_png()
        url1 = "https://example.com/img1.png"
        url2 = "https://example.com/img2.png"
        blocks = [_img_block(url1), _img_block(url2)]
        with self._patch_fetch({url1: (url1, png, "image/png"), url2: (url2, png, "image/png")}):
            new_blocks, fetched = s2.download_image_blocks(blocks)
        image_blocks = [b for b in new_blocks if b["type"] == "image"]
        assert len(image_blocks) == 1

    def test_keeps_distinct_images_by_content(self):
        png1 = _make_png(200, 200)
        png2 = _make_png(300, 300)
        url1 = "https://example.com/img1.png"
        url2 = "https://example.com/img2.png"
        blocks = [_img_block(url1), _img_block(url2)]
        with self._patch_fetch({url1: (url1, png1, "image/png"), url2: (url2, png2, "image/png")}):
            new_blocks, fetched = s2.download_image_blocks(blocks)
        assert len([b for b in new_blocks if b["type"] == "image"]) == 2

    def test_preserves_non_image_blocks(self):
        blocks = [_text_block("Keep me"), _heading_block("Also keep"), _img_block()]
        url = blocks[2]["url"]
        with self._patch_fetch({url: (url, None, None)}):
            new_blocks, _ = s2.download_image_blocks(blocks)
        types = [b["type"] for b in new_blocks]
        assert "text" in types
        assert "heading" in types

    def test_preserves_dom_order_of_surviving_blocks(self):
        png = _make_png()
        url = "https://example.com/img.png"
        blocks = [_heading_block("H"), _img_block(url), _text_block("T" * 20)]
        with self._patch_fetch({url: (url, png, "image/png")}):
            new_blocks, _ = s2.download_image_blocks(blocks)
        types = [b["type"] for b in new_blocks]
        assert types == ["heading", "image", "text"]

    def test_returns_empty_fetched_when_all_fail(self):
        blocks = [_img_block("https://example.com/a.png"), _img_block("https://example.com/b.png")]
        with self._patch_fetch({}):
            _, fetched = s2.download_image_blocks(blocks)
        assert fetched == {}

    def test_handles_empty_blocks_list(self):
        new_blocks, fetched = s2.download_image_blocks([])
        assert new_blocks == []
        assert fetched == {}
