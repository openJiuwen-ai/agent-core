# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from unittest.mock import MagicMock

import pytest

import stage_03_filter as s3


def _make_client(response_json) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps(response_json)
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def _img_block(url: str = "https://example.com/img.png", source: str = "main") -> dict:
    return {"type": "image", "url": url, "alt": "", "source": source, "path": None}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text, "source": "main"}


def _heading_block(text: str, level: int = 2) -> dict:
    return {"type": "heading", "level": level, "text": text, "source": "main"}


@pytest.mark.unit
class TestGetImageContext:
    @staticmethod
    def test_finds_heading_before_image():
        blocks = [
            _heading_block("Quick access"),
            _img_block(),
        ]
        heading, _, _ = s3.get_image_context(blocks, 1)
        assert heading == "Quick access"

    @staticmethod
    def test_finds_text_before_image():
        blocks = [
            _text_block("Click the Save button to proceed."),
            _img_block(),
        ]
        _, text_before, _ = s3.get_image_context(blocks, 1)
        assert "Save" in text_before

    @staticmethod
    def test_finds_text_after_image():
        blocks = [
            _img_block(),
            _text_block("The dialog will close."),
        ]
        _, _, text_after = s3.get_image_context(blocks, 0)
        assert "dialog" in text_after

    @staticmethod
    def test_returns_empty_strings_when_no_context():
        blocks = [_img_block()]
        heading, text_before, text_after = s3.get_image_context(blocks, 0)
        assert heading == ""
        assert text_before == ""
        assert text_after == ""

    @staticmethod
    def test_heading_search_stops_at_first_found():
        blocks = [
            _heading_block("Far Heading"),
            _text_block("Some text in between that is long enough."),
            _heading_block("Close Heading"),
            _img_block(),
        ]
        heading, _, _ = s3.get_image_context(blocks, 3)
        assert heading == "Close Heading"

    @staticmethod
    def test_text_after_skips_non_text_blocks():
        blocks = [
            _img_block(),
            _heading_block("Next Section"),
            _text_block("First real text after image."),
        ]
        _, _, text_after = s3.get_image_context(blocks, 0)
        assert "First real text" in text_after

    @staticmethod
    def test_both_heading_and_text_found_before():
        blocks = [
            _heading_block("Windows 11"),
            _text_block("Right-click the folder to proceed."),
            _img_block(),
        ]
        heading, text_before, _ = s3.get_image_context(blocks, 2)
        assert heading == "Windows 11"
        assert "Right-click" in text_before


@pytest.mark.unit
class TestFilterBatch:
    @staticmethod
    def test_returns_keep_flags_from_llm():
        client = _make_client(["KEEP", "SKIP", "KEEP"])
        items = [
            (_img_block(f"https://example.com/img{i}.png"), "heading", "before", "after")
            for i in range(3)
        ]
        images = [(b"data", "image/png")] * 3
        result = s3.filter_batch(client, items, images, "How to use Chrome")
        assert result == [True, False, True]

    @staticmethod
    def test_case_insensitive_keep():
        client = _make_client(["keep", "KEEP"])
        items = [
            (_img_block(f"https://example.com/img{i}.png"), "", "", "")
            for i in range(2)
        ]
        images = [(b"data", "image/png")] * 2
        result = s3.filter_batch(client, items, images, "Tutorial")
        assert result == [True, True]

    @staticmethod
    def test_falls_back_to_all_keep_on_llm_error():
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API error")
        items = [(_img_block(), "", "", ""), (_img_block(), "", "", "")]
        images = [(b"data", "image/png")] * 2
        result = s3.filter_batch(client, items, images, "Tutorial")
        assert result == [True, True]

    @staticmethod
    def test_falls_back_to_all_keep_on_invalid_json():
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "not valid json"
        client.chat.completions.create.return_value = MagicMock(choices=[choice])
        items = [(_img_block(), "", "", "")]
        images = [(b"data", "image/png")]
        result = s3.filter_batch(client, items, images, "Tutorial")
        assert result == [True]

    @staticmethod
    def test_includes_subpage_context_label():
        client = _make_client(["KEEP"])
        block = _img_block(source="subpage")
        items = [(block, "", "", "")]
        images = [(b"data", "image/png")]
        s3.filter_batch(client, items, images, "Tutorial")
        call_args = client.chat.completions.create.call_args
        user_content = call_args.kwargs["messages"][1]["content"]
        subpage_found = any(
            "subpage" in part.get("text", "")
            for part in user_content
            if isinstance(part, dict)
        )
        assert subpage_found

    @staticmethod
    def test_includes_heading_context():
        client = _make_client(["KEEP"])
        items = [(_img_block(), "Quick access", "Click the folder", "")]
        images = [(b"data", "image/png")]
        s3.filter_batch(client, items, images, "File Explorer")
        call_args = client.chat.completions.create.call_args
        user_content = call_args.kwargs["messages"][1]["content"]
        heading_found = any(
            "Quick access" in part.get("text", "")
            for part in user_content
            if isinstance(part, dict)
        )
        assert heading_found
