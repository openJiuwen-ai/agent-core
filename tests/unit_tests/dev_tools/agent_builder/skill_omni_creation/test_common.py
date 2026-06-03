# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pathlib

import pytest

import common as common


@pytest.mark.unit
class TestSlugify:
    @staticmethod
    def test_lowercases_input():
        assert common.slugify("Hello World") == "hello_world"

    @staticmethod
    def test_replaces_spaces_with_underscore():
        assert common.slugify("foo bar baz") == "foo_bar_baz"

    @staticmethod
    def test_strips_special_characters():
        assert common.slugify("hello! world?") == "hello_world"

    @staticmethod
    def test_collapses_multiple_separators():
        assert common.slugify("foo--bar__baz") == "foo_bar_baz"

    @staticmethod
    def test_truncates_to_80_chars():
        long_text = "a" * 100
        assert len(common.slugify(long_text)) == 80

    @staticmethod
    def test_strips_leading_trailing_whitespace():
        assert common.slugify("  hello  ") == "hello"


@pytest.mark.unit
class TestStripJsonFence:
    @staticmethod
    def test_strips_json_fence():
        text = '```json\n{"key": "value"}\n```'
        assert common.strip_json_fence(text) == '{"key": "value"}'

    @staticmethod
    def test_strips_plain_fence():
        text = '```\n{"key": "value"}\n```'
        assert common.strip_json_fence(text) == '{"key": "value"}'

    @staticmethod
    def test_no_fence_unchanged():
        text = '{"key": "value"}'
        assert common.strip_json_fence(text) == text

    @staticmethod
    def test_strips_surrounding_whitespace():
        text = '  ```json\n{"key": "value"}\n```  '
        assert common.strip_json_fence(text) == '{"key": "value"}'


@pytest.mark.unit
class TestImageExt:
    @staticmethod
    def test_extracts_extension_from_url():
        assert common.image_ext("https://example.com/img/photo.jpg", "image/png") == ".jpg"

    @staticmethod
    def test_falls_back_to_mime_when_ext_unsupported():
        assert common.image_ext("https://example.com/img/photo.bmp", "image/png") == ".png"

    @staticmethod
    def test_falls_back_to_png_when_mime_unknown():
        assert common.image_ext("https://example.com/img/photo.bmp", "image/tiff") == ".png"

    @staticmethod
    def test_webp_extension_kept():
        assert common.image_ext("https://example.com/img/photo.webp", "image/jpeg") == ".webp"


@pytest.mark.unit
class TestBlocksWithPaths:
    @staticmethod
    def test_as_str_converts_path_to_string():
        blocks = [{"type": "image", "path": pathlib.Path("/some/path.png"), "alt": "x"}]
        result = common.blocks_with_paths_as_str(blocks)
        assert isinstance(result[0]["path"], str)
        assert result[0]["path"] == "/some/path.png"

    @staticmethod
    def test_as_path_converts_string_to_path():
        blocks = [{"type": "image", "path": "/some/path.png", "alt": "x"}]
        result = common.blocks_with_paths_as_path(blocks)
        assert isinstance(result[0]["path"], pathlib.Path)

    @staticmethod
    def test_as_str_leaves_non_image_blocks_unchanged():
        blocks = [{"type": "text", "text": "hello"}]
        result = common.blocks_with_paths_as_str(blocks)
        assert result[0] == {"type": "text", "text": "hello"}

    @staticmethod
    def test_as_str_skips_image_block_without_path():
        blocks = [{"type": "image", "path": None, "alt": "x"}]
        result = common.blocks_with_paths_as_str(blocks)
        assert result[0]["path"] is None

    @staticmethod
    def test_as_str_does_not_mutate_original():
        original = [{"type": "image", "path": pathlib.Path("/some/path.png")}]
        common.blocks_with_paths_as_str(original)
        assert isinstance(original[0]["path"], pathlib.Path)

    @staticmethod
    def test_heading_block_passes_through_unchanged():
        blocks = [{"type": "heading", "level": 2, "text": "Windows 11", "source": "main"}]
        result = common.blocks_with_paths_as_str(blocks)
        assert result[0] == blocks[0]


@pytest.mark.unit
class TestStripHallucinatedImages:
    @staticmethod
    def test_keeps_valid_image_reference():
        md = "![screenshot](references/img_00.jpg)"
        valid = {"references/img_00.jpg"}
        assert common.strip_hallucinated_images(md, valid) == md

    @staticmethod
    def test_removes_invalid_image_reference():
        md = "step one\n\n![fake](references/nonexistent.png)\n\nstep two"
        result = common.strip_hallucinated_images(md, set())
        assert "nonexistent" not in result
        assert "step one" in result
        assert "step two" in result

    @staticmethod
    def test_keeps_multiple_valid_references():
        md = "![a](references/img_00.jpg)\n\n![b](references/img_01.jpg)"
        valid = {"references/img_00.jpg", "references/img_01.jpg"}
        result = common.strip_hallucinated_images(md, valid)
        assert "img_00" in result
        assert "img_01" in result

    @staticmethod
    def test_removes_button_name_hallucination():
        md = "Click ![Plus](Plus) to add an item."
        result = common.strip_hallucinated_images(md, set())
        assert "![Plus](Plus)" not in result

    @staticmethod
    def test_empty_valid_set_removes_all_images():
        md = "step\n\n![x](references/img_00.jpg)\n\nstep2"
        result = common.strip_hallucinated_images(md, set())
        assert "![ " not in result
        assert "img_00" not in result

    @staticmethod
    def test_no_images_in_md_returns_unchanged():
        md = "just text, no images"
        assert common.strip_hallucinated_images(md, set()) == md
