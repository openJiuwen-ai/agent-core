# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for bilingual tool input_params builders and registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.deepagents.prompts.sections.tools import get_tool_input_params
from openjiuwen.deepagents.prompts.sections.tools.audio import (
    get_audio_metadata_input_params,
    get_audio_question_answering_input_params,
    get_audio_transcription_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.bash import (
    get_bash_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.code import (
    get_code_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.filesystem import (
    get_edit_file_input_params,
    get_glob_input_params,
    get_grep_input_params,
    get_list_dir_input_params,
    get_read_file_input_params,
    get_write_file_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.list_skill import (
    get_list_skill_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.todo import (
    get_todo_create_input_params,
    get_todo_list_input_params,
    get_todo_modify_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.vision import (
    get_image_ocr_input_params,
    get_visual_question_answering_input_params,
)
from openjiuwen.deepagents.tools.audio import (
    AudioMetadataTool,
    AudioQuestionAnsweringTool,
    AudioTranscriptionTool,
)
from openjiuwen.deepagents.tools.code import CodeTool
from openjiuwen.deepagents.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.deepagents.tools.list_skill import ListSkillTool
from openjiuwen.deepagents.tools.shell import BashTool
from openjiuwen.deepagents.tools.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
)


def _assert_valid_schema(schema: dict) -> None:
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "required" in schema


def _assert_bilingual_descriptions_differ(builder_fn) -> None:
    cn = builder_fn("cn")
    en = builder_fn("en")
    _assert_valid_schema(cn)
    _assert_valid_schema(en)
    if cn["properties"]:
        any_differ = False
        for key in cn["properties"]:
            cn_desc = cn["properties"][key].get("description", "")
            en_desc = en["properties"][key].get("description", "")
            assert cn_desc
            assert en_desc
            if cn_desc != en_desc:
                any_differ = True
        assert any_differ


class TestBuilderInputParams:
    @staticmethod
    def test_core_builders():
        builders = [
            get_bash_input_params,
            get_code_input_params,
            get_read_file_input_params,
            get_write_file_input_params,
            get_edit_file_input_params,
            get_glob_input_params,
            get_list_dir_input_params,
            get_grep_input_params,
            get_list_skill_input_params,
            get_todo_create_input_params,
            get_todo_list_input_params,
            get_todo_modify_input_params,
            get_image_ocr_input_params,
            get_visual_question_answering_input_params,
            get_audio_transcription_input_params,
            get_audio_question_answering_input_params,
            get_audio_metadata_input_params,
        ]
        for builder in builders:
            _assert_bilingual_descriptions_differ(builder)

    @staticmethod
    def test_expected_required_fields():
        assert get_bash_input_params("cn")["required"] == ["command"]
        assert get_code_input_params("cn")["required"] == ["code"]
        assert get_read_file_input_params("cn")["required"] == ["file_path"]
        assert set(get_write_file_input_params("cn")["required"]) == {
            "file_path",
            "content",
        }
        assert get_todo_create_input_params("cn")["required"] == ["tasks"]
        assert get_image_ocr_input_params("cn")["required"] == [
            "image_path_or_url"
        ]
        assert get_visual_question_answering_input_params("cn")["required"] == [
            "image_path_or_url",
            "question",
        ]
        assert get_audio_transcription_input_params("cn")["required"] == [
            "audio_path_or_url"
        ]
        assert get_audio_question_answering_input_params("cn")["required"] == [
            "audio_path_or_url",
            "question",
        ]
        assert get_audio_metadata_input_params("cn")["required"] == [
            "audio_path_or_url"
        ]


class TestGetToolInputParams:
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
            schema = get_tool_input_params(name, "cn")
            assert schema.get("type") == "object"

    @staticmethod
    def test_unknown_tool_raises():
        with pytest.raises(KeyError):
            get_tool_input_params("nonexistent", "cn")

    @staticmethod
    def test_registry_matches_direct_builder():
        assert get_tool_input_params("bash", "cn") == get_bash_input_params("cn")
        assert get_tool_input_params("image_ocr", "en") == (
            get_image_ocr_input_params("en")
        )
        assert get_tool_input_params("audio_metadata", "en") == (
            get_audio_metadata_input_params("en")
        )


class TestToolClassInputParams:
    @staticmethod
    def test_existing_tools_use_builders():
        bash_tool = BashTool(MagicMock(), language="en")
        code_tool = CodeTool(MagicMock(), language="en")
        assert bash_tool.card.input_params == get_bash_input_params("en")
        assert code_tool.card.input_params == get_code_input_params("en")

        filesystem_builders = [
            (ReadFileTool, get_read_file_input_params),
            (WriteFileTool, get_write_file_input_params),
            (EditFileTool, get_edit_file_input_params),
            (GlobTool, get_glob_input_params),
            (ListDirTool, get_list_dir_input_params),
            (GrepTool, get_grep_input_params),
        ]
        for tool_cls, builder_fn in filesystem_builders:
            tool = tool_cls(MagicMock(), language="en")
            assert tool.card.input_params == builder_fn("en")

        list_skill_tool = ListSkillTool(get_skills=lambda: [], language="en")
        assert list_skill_tool.card.input_params == get_list_skill_input_params(
            "en"
        )

    @staticmethod
    def test_vision_tools_use_builders():
        builders = [
            (ImageOCRTool, get_image_ocr_input_params),
            (
                VisualQuestionAnsweringTool,
                get_visual_question_answering_input_params,
            ),
        ]
        for tool_cls, builder_fn in builders:
            tool = tool_cls(language="en")
            assert tool.card.input_params == builder_fn("en")

    @staticmethod
    def test_audio_tools_use_builders():
        builders = [
            (AudioTranscriptionTool, get_audio_transcription_input_params),
            (
                AudioQuestionAnsweringTool,
                get_audio_question_answering_input_params,
            ),
            (AudioMetadataTool, get_audio_metadata_input_params),
        ]
        for tool_cls, builder_fn in builders:
            tool = tool_cls(language="en")
            assert tool.card.input_params == builder_fn("en")
