# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import stage_05_generate as s5


SKILL_DIR = Path("/skills/001_my_skill")


def _img_block(filename: str, alt: str = "screenshot", source: str = "main") -> dict:
    return {
        "type": "image",
        "path": SKILL_DIR / "references" / filename,
        "alt": alt,
        "source": source,
    }


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text, "source": "main"}


def _heading_block(text: str, level: int = 2) -> dict:
    return {"type": "heading", "level": level, "text": text, "source": "main"}


@pytest.mark.unit
class TestBlocksForLlm:
    @staticmethod
    def test_converts_image_path_to_relative():
        blocks = [_img_block("img_00.png")]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert result[0]["path"] == "references/img_00.png"

    @staticmethod
    def test_skips_image_blocks_without_path():
        blocks = [{"type": "image", "path": None, "alt": "x", "source": "main"}]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert result == []

    @staticmethod
    def test_strips_url_field_from_image():
        blocks = [{"type": "image", "url": "https://example.com/img.png", "path": SKILL_DIR / "references" / "img_00.png", "alt": "x", "source": "main"}]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert "url" not in result[0]

    @staticmethod
    def test_passes_text_block_through():
        blocks = [_text_block("Click the button.")]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert result[0]["text"] == "Click the button."

    @staticmethod
    def test_strips_path_field_from_text_block():
        blocks = [{"type": "text", "text": "hello", "source": "main", "path": None}]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert "path" not in result[0]

    @staticmethod
    def test_passes_heading_block_through():
        blocks = [_heading_block("Windows 11")]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert result[0]["type"] == "heading"
        assert result[0]["text"] == "Windows 11"
        assert result[0]["level"] == 2

    @staticmethod
    def test_preserves_order_of_mixed_blocks():
        blocks = [
            _heading_block("Step"),
            _text_block("Do this."),
            _img_block("img_00.png"),
        ]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert [b["type"] for b in result] == ["heading", "text", "image"]

    @staticmethod
    def test_fallback_to_filename_when_path_not_relative_to_skill_dir():
        blocks = [{"type": "image", "path": Path("/other/dir/img_00.png"), "alt": "x", "source": "main"}]
        result = s5.blocks_for_llm(blocks, SKILL_DIR)
        assert result[0]["path"] == "img_00.png"


@pytest.mark.unit
class TestCallSkillAgent:
    @staticmethod
    def _make_client(response_text: str) -> MagicMock:
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = response_text
        client.chat.completions.create.return_value = MagicMock(choices=[choice])
        return client

    def test_calls_llm_and_returns_stripped_content(self):
        client = self._make_client("  ## My Skill\n1. Step one  ")
        blocks_llm = [_text_block("Click here.")]
        result = s5.call_skill_agent(client, "My Skill", blocks_llm)
        assert result == "## My Skill\n1. Step one"
        client.chat.completions.create.assert_called_once()

    def test_passes_title_in_user_message(self):
        client = self._make_client("output")
        s5.call_skill_agent(client, "File Explorer in Windows", [])
        call_args = client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert "File Explorer in Windows" in user_msg

    def test_passes_blocks_json_in_user_message(self):
        client = self._make_client("output")
        blocks_llm = [{"type": "heading", "level": 2, "text": "Windows 11", "source": "main"}]
        s5.call_skill_agent(client, "Title", blocks_llm)
        call_args = client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert "Windows 11" in user_msg
        assert "BLOCKS" in user_msg

    def test_uses_skill_prompt_as_system_message(self):
        import common as common
        client = self._make_client("output")
        s5.call_skill_agent(client, "Title", [])
        call_args = client.chat.completions.create.call_args
        system_msg = call_args.kwargs["messages"][0]["content"]
        assert system_msg == common.SKILL_PROMPT


@pytest.mark.unit
class TestAppendReferenceFiles:
    @staticmethod
    def test_appends_section_when_images_present():
        blocks_llm = [{"type": "image", "path": "references/img_00.jpg", "alt": "screenshot", "source": "main"}]
        md = "---\nname: skill\n---\n\n# Skill\n\n## Steps\n\n1. Step one."
        result = s5.append_reference_files(md, blocks_llm)
        assert "## Reference Files" in result
        assert "references/img_00.jpg" in result

    @staticmethod
    def test_uses_alt_as_description():
        blocks_llm = [{"type": "image", "path": "references/img_00.jpg", "alt": "Click save button", "source": "main"}]
        result = s5.append_reference_files("# Skill", blocks_llm)
        assert "Click save button" in result

    @staticmethod
    def test_uses_screenshot_fallback_when_no_alt():
        blocks_llm = [{"type": "image", "path": "references/img_00.jpg", "alt": "", "source": "main"}]
        result = s5.append_reference_files("# Skill", blocks_llm)
        assert "screenshot" in result

    @staticmethod
    def test_does_not_append_when_no_image_blocks():
        blocks_llm = [{"type": "text", "text": "hello", "source": "main"}]
        md = "# Skill\n\n1. Step one."
        result = s5.append_reference_files(md, blocks_llm)
        assert "## Reference Files" not in result
        assert result == md

    @staticmethod
    def test_does_not_append_when_images_have_no_path():
        blocks_llm = [{"type": "image", "path": None, "alt": "x", "source": "main"}]
        md = "# Skill"
        result = s5.append_reference_files(md, blocks_llm)
        assert "## Reference Files" not in result

    @staticmethod
    def test_lists_all_images():
        blocks_llm = [
            {"type": "image", "path": "references/img_00.jpg", "alt": "first", "source": "main"},
            {"type": "image", "path": "references/img_01.jpg", "alt": "second", "source": "main"},
        ]
        result = s5.append_reference_files("# Skill", blocks_llm)
        assert "img_00.jpg" in result
        assert "img_01.jpg" in result

    @staticmethod
    def test_appended_section_comes_after_skill_content():
        blocks_llm = [{"type": "image", "path": "references/img_00.jpg", "alt": "x", "source": "main"}]
        md = "# Skill\n\n1. Step one."
        result = s5.append_reference_files(md, blocks_llm)
        steps_pos = result.index("Step one")
        ref_pos = result.index("Reference Files")
        assert steps_pos < ref_pos
