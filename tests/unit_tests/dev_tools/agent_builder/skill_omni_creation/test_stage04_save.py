# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pathlib import Path

import pytest

import stage_04_save as s4


def _img_block(url: str, source: str = "main") -> dict:
    return {"type": "image", "url": url, "alt": "screenshot", "source": source, "path": None}


def _text_block(text: str = "Some text content") -> dict:
    return {"type": "text", "text": text, "source": "main"}


def _heading_block(text: str = "Section") -> dict:
    return {"type": "heading", "level": 2, "text": text, "source": "main"}


@pytest.mark.unit
class TestSaveImageBlocks:
    @staticmethod
    def test_writes_image_to_disk(tmp_path):
        url = "https://example.com/img.png"
        data = b"\x89PNG\r\n" + b"x" * 100
        fetched = {url: (data, "image/png")}
        blocks = [_img_block(url)]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)

        saved_files = list(tmp_path.iterdir())
        assert len(saved_files) == 1

    @staticmethod
    def test_sets_path_field_in_block(tmp_path):
        url = "https://example.com/img.jpg"
        fetched = {url: (b"jpegdata", "image/jpeg")}
        blocks = [_img_block(url)]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        image_blocks = [b for b in result if b["type"] == "image"]

        assert len(image_blocks) == 1
        assert image_blocks[0]["path"] is not None
        assert image_blocks[0]["path"].parent == tmp_path

    @staticmethod
    def test_sequential_filename_numbering(tmp_path):
        urls = [f"https://example.com/img{i}.png" for i in range(3)]
        fetched = {url: (b"data" * 100, "image/png") for url in urls}
        blocks = [_img_block(url) for url in urls]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        names = sorted(f.name for f in tmp_path.iterdir())

        assert names[0].startswith("img_00")
        assert names[1].startswith("img_01")
        assert names[2].startswith("img_02")

    @staticmethod
    def test_uses_correct_extension_from_url(tmp_path):
        url = "https://example.com/photo.jpg"
        fetched = {url: (b"data", "image/jpeg")}
        blocks = [_img_block(url)]

        s4.save_image_blocks(blocks, fetched, tmp_path)
        saved = list(tmp_path.iterdir())

        assert saved[0].suffix == ".jpg"

    @staticmethod
    def test_falls_back_to_mime_extension_for_unknown_ext(tmp_path):
        url = "https://example.com/photo.bmp"
        fetched = {url: (b"data", "image/png")}
        blocks = [_img_block(url)]

        s4.save_image_blocks(blocks, fetched, tmp_path)
        saved = list(tmp_path.iterdir())

        assert saved[0].suffix == ".png"

    @staticmethod
    def test_preserves_non_image_blocks(tmp_path):
        url = "https://example.com/img.png"
        fetched = {url: (b"data" * 50, "image/png")}
        blocks = [_text_block(), _heading_block(), _img_block(url)]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        types = [b["type"] for b in result]

        assert "text" in types
        assert "heading" in types

    @staticmethod
    def test_skips_image_block_not_in_fetched(tmp_path):
        url = "https://example.com/missing.png"
        fetched = {}
        blocks = [_img_block(url)]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        image_blocks = [b for b in result if b["type"] == "image"]

        assert image_blocks == []
        assert list(tmp_path.iterdir()) == []

    @staticmethod
    def test_creates_img_dir_if_not_exists(tmp_path):
        img_dir = tmp_path / "deep" / "nested" / "references"
        url = "https://example.com/img.png"
        fetched = {url: (b"data" * 50, "image/png")}
        blocks = [_img_block(url)]

        s4.save_image_blocks(blocks, fetched, img_dir)

        assert img_dir.exists()

    @staticmethod
    def test_preserves_dom_order_in_result(tmp_path):
        url = "https://example.com/img.png"
        fetched = {url: (b"data" * 50, "image/png")}
        blocks = [_heading_block("H"), _img_block(url), _text_block()]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        types = [b["type"] for b in result]

        assert types == ["heading", "image", "text"]

    @staticmethod
    def test_handles_empty_blocks(tmp_path):
        result = s4.save_image_blocks([], {}, tmp_path)
        assert result == []

    @staticmethod
    def test_all_original_fields_preserved_in_output(tmp_path):
        url = "https://example.com/img.png"
        fetched = {url: (b"data" * 50, "image/png")}
        block = {**_img_block(url), "alt": "custom alt", "source": "subpage"}
        blocks = [block]

        result = s4.save_image_blocks(blocks, fetched, tmp_path)
        image_blocks = [b for b in result if b["type"] == "image"]

        assert image_blocks[0]["alt"] == "custom alt"
        assert image_blocks[0]["source"] == "subpage"
        assert image_blocks[0]["url"] == url
