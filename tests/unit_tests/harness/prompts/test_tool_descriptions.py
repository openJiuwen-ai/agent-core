# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for per-tool bilingual descriptions and registry lookups."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.prompts.sections.tools import get_tool_description
from openjiuwen.harness.prompts.sections.tools.audio import (
    AUDIO_METADATA_DESCRIPTION,
    AUDIO_QUESTION_ANSWERING_DESCRIPTION,
    AUDIO_TRANSCRIPTION_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.bash import (
    DESCRIPTION as BASH_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.code import (
    DESCRIPTION as CODE_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.filesystem import (
    EDIT_FILE_DESCRIPTION,
    GLOB_DESCRIPTION,
    GREP_DESCRIPTION,
    LIST_DIR_DESCRIPTION,
    READ_FILE_DESCRIPTION,
    WRITE_FILE_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.list_skill import (
    DESCRIPTION as LIST_SKILL_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.todo import (
    TODO_CREATE_DESCRIPTION,
    TODO_LIST_DESCRIPTION,
    TODO_MODIFY_DESCRIPTION,
)
from openjiuwen.harness.prompts.sections.tools.vision import (
    IMAGE_OCR_DESCRIPTION,
    VISUAL_QUESTION_ANSWERING_DESCRIPTION,
)
from openjiuwen.harness.tools.audio import (
    AudioMetadataTool,
    AudioQuestionAnsweringTool,
    AudioTranscriptionTool,
)
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import ReadFileTool
from openjiuwen.harness.tools.bash import BashTool
from openjiuwen.harness.tools.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
)


class TestBilingualDescriptions:
    """Each description dict must have both cn and en keys."""

    @staticmethod
    def test_core_descriptions():
        descriptions = [
            BASH_DESCRIPTION,
            CODE_DESCRIPTION,
            LIST_SKILL_DESCRIPTION,
            READ_FILE_DESCRIPTION,
            WRITE_FILE_DESCRIPTION,
            EDIT_FILE_DESCRIPTION,
            GLOB_DESCRIPTION,
            LIST_DIR_DESCRIPTION,
            GREP_DESCRIPTION,
            TODO_CREATE_DESCRIPTION,
            TODO_LIST_DESCRIPTION,
            TODO_MODIFY_DESCRIPTION,
            IMAGE_OCR_DESCRIPTION,
            VISUAL_QUESTION_ANSWERING_DESCRIPTION,
            AUDIO_TRANSCRIPTION_DESCRIPTION,
            AUDIO_QUESTION_ANSWERING_DESCRIPTION,
            AUDIO_METADATA_DESCRIPTION,
        ]
        for description in descriptions:
            assert description["cn"].strip()
            assert description["en"].strip()


class TestGetToolDescription:
    @staticmethod
    def test_known_tool_cn():
        assert get_tool_description("bash", "cn") == BASH_DESCRIPTION["cn"]

    @staticmethod
    def test_known_tool_en():
        assert get_tool_description("bash", "en") == BASH_DESCRIPTION["en"]

    @staticmethod
    def test_unknown_tool_raises():
        with pytest.raises(KeyError):
            get_tool_description("nonexistent", "cn")

    @staticmethod
    def test_all_registered_tools():
        names = [
            "bash",
            "code",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "list_files",
            "grep",
            "list_skill",
            "todo_create",
            "todo_list",
            "todo_modify",
            "image_ocr",
            "visual_question_answering",
            "audio_transcription",
            "audio_question_answering",
            "audio_metadata",
        ]
        for name in names:
            assert get_tool_description(name, "cn")
            assert get_tool_description(name, "en")


class TestToolClassesUseBilingualDescriptions:
    """Verify tool classes pick descriptions from the centralized registry."""

    @staticmethod
    def test_existing_tools_en():
        assert BashTool(MagicMock(), language="en").card.description == (
            BASH_DESCRIPTION["en"]
        )
        assert CodeTool(MagicMock(), language="en").card.description == (
            CODE_DESCRIPTION["en"]
        )
        assert ReadFileTool(MagicMock(), language="en").card.description == (
            READ_FILE_DESCRIPTION["en"]
        )

    @staticmethod
    def test_vision_tools_en():
        image_ocr_tool = ImageOCRTool(language="en")
        vqa_tool = VisualQuestionAnsweringTool(language="en")
        assert image_ocr_tool.card.description == IMAGE_OCR_DESCRIPTION["en"]
        assert (
            vqa_tool.card.description
            == VISUAL_QUESTION_ANSWERING_DESCRIPTION["en"]
        )

    @staticmethod
    def test_audio_tools_en():
        transcription_tool = AudioTranscriptionTool(language="en")
        qa_tool = AudioQuestionAnsweringTool(language="en")
        metadata_tool = AudioMetadataTool(language="en")
        assert (
            transcription_tool.card.description
            == AUDIO_TRANSCRIPTION_DESCRIPTION["en"]
        )
        assert (
            qa_tool.card.description
            == AUDIO_QUESTION_ANSWERING_DESCRIPTION["en"]
        )
        assert metadata_tool.card.description == AUDIO_METADATA_DESCRIPTION["en"]
