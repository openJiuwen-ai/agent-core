# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for extraction base (MultilingualBaseModel)"""

from typing import List
from unittest.mock import patch

from pydantic import Field

from openjiuwen.core.memory.graph.extraction.base import (
    MULTILINGUAL_DESCRIPTION,
    MultilingualBaseModel,
)
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityExtraction


class _SampleModel(MultilingualBaseModel):
    """Minimal model for testing"""

    name: str = Field(description="{{[test_name]}}")
    count: int = Field(default=0, description="{{[test_count]}}")


class TestMultilingualBaseModelSchema:
    """Tests for multilingual_model_json_schema"""

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {}, "en": {}}, clear=False)
    def test_schema_returns_dict_with_properties():
        """multilingual_model_json_schema returns dict with properties and optional $defs"""
        schema = _SampleModel.multilingual_model_json_schema("cn")
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "count" in schema["properties"]
        assert schema["properties"]["name"].get("type") == "string"
        assert schema["properties"]["count"].get("type") == "integer"

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {"{{[test_name]}}": "名称", "{{[test_count]}}": "数量"}}, clear=False)
    def test_schema_replaces_descriptions_from_lookup():
        """Description values are replaced using MULTILINGUAL_DESCRIPTION[language]"""
        schema = _SampleModel.multilingual_model_json_schema("cn")
        assert schema["properties"]["name"].get("description") == "名称"
        assert schema["properties"]["count"].get("description") == "数量"

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {}}, clear=False)
    def test_schema_strict_sets_additional_properties_false():
        """When strict=True, additionalProperties is False in schema and $defs"""
        schema = _SampleModel.multilingual_model_json_schema("cn", strict=True)
        assert schema.get("additionalProperties") is False


class TestReadableSchema:
    """Tests for readable_schema"""

    @staticmethod
    @patch.dict(
        MULTILINGUAL_DESCRIPTION,
        {"cn": {"{{[test_name]}}": "名称", "{{[test_count]}}": "数量"}},
        clear=False,
    )
    def test_readable_schema_returns_tuple_of_str_and_dict():
        """readable_schema returns (format_string, refs_dict)"""
        out_str, refs = _SampleModel.readable_schema("cn")
        assert isinstance(out_str, str)
        assert isinstance(refs, dict)
        assert "name" in out_str
        assert "count" in out_str

    @staticmethod
    @patch.dict(
        MULTILINGUAL_DESCRIPTION,
        {"cn": {"{{[ent_ext_list]}}": "列表", "{{[ent_def_name]}}": "名称", "{{[ent_def_type]}}": "类型"}},
        clear=False,
    )
    def test_readable_schema_with_defs_includes_refs():
        """readable_schema with $defs returns non-empty refs and replaces $ref"""
        out_str, refs = EntityExtraction.readable_schema("cn")
        assert isinstance(out_str, str)
        assert "extracted_entities" in out_str
        assert isinstance(refs, dict)
        assert "EntityDeclaration" in refs


class TestResponseFormat:
    """Tests for response_format"""

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {}}, clear=False)
    def test_response_format_has_json_schema_type_and_name():
        """response_format returns dict with type json_schema and model name"""
        fmt = _SampleModel.response_format("cn")
        assert fmt["type"] == "json_schema"
        assert "json_schema" in fmt
        assert fmt["json_schema"]["name"] == "_SampleModel"
        assert fmt["json_schema"].get("strict") is True


class TestRecursiveReplace:
    """Tests for _recursive_replace"""

    @staticmethod
    def test_recursive_replace_replaces_key_in_dict():
        """_recursive_replace replaces from_key with lookup value"""
        data = {"description": "a", "nested": {"description": "b"}}
        lookup = {"a": "A", "b": "B"}
        getattr(MultilingualBaseModel, "_recursive_replace")(data, lookup, "description", "description")
        assert data["description"] == "A"
        assert data["nested"]["description"] == "B"

    @staticmethod
    def test_recursive_replace_missing_key_unchanged():
        """Keys not in lookup are left as-is (or key itself when get returns default)"""
        data = {"description": "unknown"}
        lookup = {}
        getattr(MultilingualBaseModel, "_recursive_replace")(data, lookup, "description", "description")
        assert data["description"] == "unknown"

    @staticmethod
    def test_recursive_replace_lists_traversed():
        """Nested lists are traversed"""
        data = [{"description": "x"}]
        lookup = {"x": "X"}
        getattr(MultilingualBaseModel, "_recursive_replace")(data, lookup, "description", "description")
        assert data[0]["description"] == "X"


class TestToJsonTypes:
    """Tests for _to_json_types"""

    @staticmethod
    def test_simple_type_returns_name():
        """Simple type returns __name__"""
        assert getattr(MultilingualBaseModel, "_to_json_types")(str) == "str"
        assert getattr(MultilingualBaseModel, "_to_json_types")(int) == "int"

    @staticmethod
    def test_list_type_returns_origin_and_args():
        """list[T] returns list with arg name"""
        assert "list" in getattr(MultilingualBaseModel, "_to_json_types")(list[str])
        assert "str" in getattr(MultilingualBaseModel, "_to_json_types")(list[str])

    @staticmethod
    def test_origin_without_args_returns_origin_name():
        """Type with origin but no args returns origin name (e.g. typing.List)"""
        result = getattr(MultilingualBaseModel, "_to_json_types")(List)
        assert result == "list"
