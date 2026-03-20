# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for bilingual tool input_params builders and registry."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.deepagents.tools.shell import BashTool
from openjiuwen.deepagents.tools.code import CodeTool
from openjiuwen.deepagents.tools.filesystem import (
    ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, ListDirTool, GrepTool,
)
from openjiuwen.deepagents.tools.list_skill import ListSkillTool
from openjiuwen.deepagents.tools.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
)

from openjiuwen.deepagents.prompts.sections.tools import get_tool_input_params
from openjiuwen.deepagents.prompts.sections.tools.bash import get_bash_input_params
from openjiuwen.deepagents.prompts.sections.tools.code import get_code_input_params
from openjiuwen.deepagents.prompts.sections.tools.filesystem import (
    get_read_file_input_params,
    get_write_file_input_params,
    get_edit_file_input_params,
    get_glob_input_params,
    get_list_dir_input_params,
    get_grep_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.list_skill import get_list_skill_input_params
from openjiuwen.deepagents.prompts.sections.tools.todo import (
    get_todo_create_input_params,
    get_todo_list_input_params,
    get_todo_modify_input_params,
)
from openjiuwen.deepagents.prompts.sections.tools.vision import (
    get_image_ocr_input_params,
    get_visual_question_answering_input_params,
)


def _assert_valid_schema(schema: dict):
    """Assert that a schema dict looks like a valid JSON Schema object."""
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "required" in schema


def _assert_bilingual_descriptions_differ(builder_fn):
    """Assert CN and EN descriptions are present and different for at least one property."""
    cn = builder_fn("cn")
    en = builder_fn("en")
    _assert_valid_schema(cn)
    _assert_valid_schema(en)
    # At least one property description should differ between cn and en
    if cn["properties"]:
        any_differ = False
        for key in cn["properties"]:
            cn_desc = cn["properties"][key].get("description", "")
            en_desc = en["properties"][key].get("description", "")
            assert cn_desc, f"Missing cn description for {key}"
            assert en_desc, f"Missing en description for {key}"
            if cn_desc != en_desc:
                any_differ = True
        assert any_differ, "CN and EN descriptions should differ for at least one property"


# ---------------------------------------------------------------------------
# Individual builder tests
# ---------------------------------------------------------------------------
class TestBashInputParams:
    @staticmethod
    def test_valid_schema():
        schema = get_bash_input_params("cn")
        _assert_valid_schema(schema)
        assert "command" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema["required"] == ["command"]

    @staticmethod
    def test_bilingual():
        _assert_bilingual_descriptions_differ(get_bash_input_params)


class TestCodeInputParams:
    @staticmethod
    def test_valid_schema():
        schema = get_code_input_params("cn")
        _assert_valid_schema(schema)
        assert "code" in schema["properties"]
        assert "language" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema["required"] == ["code"]

    @staticmethod
    def test_bilingual():
        _assert_bilingual_descriptions_differ(get_code_input_params)


class TestFilesystemInputParams:
    @staticmethod
    def test_read_file():
        schema = get_read_file_input_params("cn")
        _assert_valid_schema(schema)
        assert "file_path" in schema["properties"]
        assert schema["required"] == ["file_path"]
        _assert_bilingual_descriptions_differ(get_read_file_input_params)

    @staticmethod
    def test_write_file():
        schema = get_write_file_input_params("cn")
        _assert_valid_schema(schema)
        assert set(schema["required"]) == {"file_path", "content"}
        _assert_bilingual_descriptions_differ(get_write_file_input_params)

    @staticmethod
    def test_edit_file():
        schema = get_edit_file_input_params("cn")
        _assert_valid_schema(schema)
        assert "old_string" in schema["properties"]
        assert "new_string" in schema["properties"]
        assert "replace_all" in schema["properties"]
        _assert_bilingual_descriptions_differ(get_edit_file_input_params)

    @staticmethod
    def test_glob():
        schema = get_glob_input_params("cn")
        _assert_valid_schema(schema)
        assert "pattern" in schema["properties"]
        assert schema["required"] == ["pattern"]
        _assert_bilingual_descriptions_differ(get_glob_input_params)

    @staticmethod
    def test_list_dir():
        schema = get_list_dir_input_params("cn")
        _assert_valid_schema(schema)
        assert "path" in schema["properties"]
        assert "show_hidden" in schema["properties"]
        _assert_bilingual_descriptions_differ(get_list_dir_input_params)

    @staticmethod
    def test_grep():
        schema = get_grep_input_params("cn")
        _assert_valid_schema(schema)
        assert set(schema["required"]) == {"pattern", "path"}
        _assert_bilingual_descriptions_differ(get_grep_input_params)


class TestListSkillInputParams:
    @staticmethod
    def test_valid_schema():
        schema = get_list_skill_input_params("cn")
        _assert_valid_schema(schema)
        assert "query" in schema["properties"]
        assert schema["required"] == []

    @staticmethod
    def test_bilingual():
        _assert_bilingual_descriptions_differ(get_list_skill_input_params)


class TestTodoInputParams:
    @staticmethod
    def test_todo_create():
        schema = get_todo_create_input_params("cn")
        _assert_valid_schema(schema)
        assert "tasks" in schema["properties"]
        assert schema["required"] == ["tasks"]
        _assert_bilingual_descriptions_differ(get_todo_create_input_params)

    @staticmethod
    def test_todo_list():
        schema = get_todo_list_input_params("cn")
        _assert_valid_schema(schema)
        assert schema["properties"] == {}

    @staticmethod
    def test_todo_modify():
        schema = get_todo_modify_input_params("cn")
        _assert_valid_schema(schema)
        assert "action" in schema["properties"]
        assert "ids" in schema["properties"]
        assert "todos" in schema["properties"]
        assert "todo_data" in schema["properties"]
        assert schema["required"] == ["action"]

    @staticmethod
    def test_todo_modify_bilingual():
        _assert_bilingual_descriptions_differ(get_todo_modify_input_params)

    @staticmethod
    def test_todo_modify_nested_schema_bilingual():
        """Nested todo item properties inside todos and todo_data should be bilingual."""
        cn = get_todo_modify_input_params("cn")
        en = get_todo_modify_input_params("en")
        # Check todos.items.properties
        cn_item = cn["properties"]["todos"]["items"]["properties"]
        en_item = en["properties"]["todos"]["items"]["properties"]
        assert cn_item["id"]["description"] != en_item["id"]["description"]
        assert cn_item["content"]["description"] != en_item["content"]["description"]
        assert cn_item["status"]["description"] != en_item["status"]["description"]
        # Check todo_data.items[1].items.properties (nested insert list)
        cn_nested = cn["properties"]["todo_data"]["items"][1]["items"]["properties"]
        en_nested = en["properties"]["todo_data"]["items"][1]["items"]["properties"]
        assert cn_nested["id"]["description"] != en_nested["id"]["description"]
        assert cn_nested["content"]["description"] != en_nested["content"]["description"]


class TestVisionInputParams:
    @staticmethod
    def test_image_ocr():
        schema = get_image_ocr_input_params("cn")
        _assert_valid_schema(schema)
        assert "image_path_or_url" in schema["properties"]
        assert "prompt" in schema["properties"]
        assert schema["required"] == ["image_path_or_url"]
        _assert_bilingual_descriptions_differ(get_image_ocr_input_params)

    @staticmethod
    def test_visual_question_answering():
        schema = get_visual_question_answering_input_params("cn")
        _assert_valid_schema(schema)
        assert "image_path_or_url" in schema["properties"]
        assert "question" in schema["properties"]
        assert "include_ocr" in schema["properties"]
        assert "ocr_prompt" in schema["properties"]
        assert schema["required"] == ["image_path_or_url", "question"]
        _assert_bilingual_descriptions_differ(
            get_visual_question_answering_input_params
        )


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------
class TestGetToolInputParams:
    @staticmethod
    def test_all_registered_tools():
        names = [
            "bash", "code", "read_file", "write_file", "edit_file",
            "glob", "list_files", "grep", "list_skill",
            "todo_write", "todo_read", "todo_modify",
            "image_ocr", "visual_question_answering",
        ]
        for name in names:
            schema = get_tool_input_params(name, "cn")
            assert schema.get("type") == "object", f"Invalid schema for {name}"

    @staticmethod
    def test_unknown_tool_raises():
        with pytest.raises(KeyError):
            get_tool_input_params("nonexistent", "cn")

    @staticmethod
    def test_registry_matches_direct_builder():
        assert get_tool_input_params("bash", "cn") == get_bash_input_params("cn")
        assert get_tool_input_params("bash", "en") == get_bash_input_params("en")
        assert get_tool_input_params("todo_modify", "cn") == get_todo_modify_input_params("cn")
        assert get_tool_input_params("image_ocr", "en") == get_image_ocr_input_params("en")


# ---------------------------------------------------------------------------
# Tool class integration tests
# ---------------------------------------------------------------------------
class TestToolClassInputParams:
    @staticmethod
    def test_bash_tool_uses_builder():
        for lang in ("cn", "en"):
            tool = BashTool(MagicMock(), language=lang)
            assert tool.card.input_params == get_bash_input_params(lang)

    @staticmethod
    def test_code_tool_uses_builder():
        for lang in ("cn", "en"):
            tool = CodeTool(MagicMock(), language=lang)
            assert tool.card.input_params == get_code_input_params(lang)

    @staticmethod
    def test_filesystem_tools_use_builders():
        builders = [
            (ReadFileTool, get_read_file_input_params),
            (WriteFileTool, get_write_file_input_params),
            (EditFileTool, get_edit_file_input_params),
            (GlobTool, get_glob_input_params),
            (ListDirTool, get_list_dir_input_params),
            (GrepTool, get_grep_input_params),
        ]
        for tool_cls, builder_fn in builders:
            for lang in ("cn", "en"):
                tool = tool_cls(MagicMock(), language=lang)
                assert tool.card.input_params == builder_fn(lang), \
                    f"{tool_cls.__name__} lang={lang} mismatch"

    @staticmethod
    def test_list_skill_tool_uses_builder():
        for lang in ("cn", "en"):
            tool = ListSkillTool(get_skills=lambda: [], language=lang)
            assert tool.card.input_params == get_list_skill_input_params(lang)

    @staticmethod
    def test_todo_tools_use_builders():
        from openjiuwen.deepagents.tools.todo import (
            create_todo_create_tool, create_todo_list_tool, create_todo_modify_tool,
        )
        builders = [
            (create_todo_create_tool, get_todo_create_input_params),
            (create_todo_list_tool, get_todo_list_input_params),
            (create_todo_modify_tool, get_todo_modify_input_params),
        ]
        for factory_fn, builder_fn in builders:
            for lang in ("cn", "en"):
                tool = factory_fn(MagicMock(), language=lang)
                assert tool.card.input_params == builder_fn(lang), \
                    f"{factory_fn.__name__} lang={lang} mismatch"

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
            for lang in ("cn", "en"):
                tool = tool_cls(language=lang)
                assert tool.card.input_params == builder_fn(lang)
