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


def _object_nodes_with_wrong_additional_properties(schema: object, path: str = "$") -> list[str]:
    """Return paths where a structured object (type=object with dict properties) lacks additionalProperties=False.

    Matches ``multilingual_model_json_schema(..., strict=True)``: only nodes with a ``properties`` mapping
    are forced to ``additionalProperties: False``; free-form ``dict`` fields stay as emitted by Pydantic.
    """
    bad: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
            if schema.get("additionalProperties") is not False:
                bad.append(f"{path}: additionalProperties={schema.get('additionalProperties')!r}")
        for key, val in schema.items():
            bad.extend(_object_nodes_with_wrong_additional_properties(val, f"{path}.{key}"))
    elif isinstance(schema, list):
        for i, item in enumerate(schema):
            bad.extend(_object_nodes_with_wrong_additional_properties(item, f"{path}[{i}]"))
    return bad


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
        """When strict=True, root object with properties gets additionalProperties False."""
        schema = _SampleModel.multilingual_model_json_schema("cn", strict=True)
        assert schema.get("additionalProperties") is False
        assert isinstance(schema.get("properties"), dict)


class _StrictInner(MultilingualBaseModel):
    value: str = Field(description="{{[strict_inner_val]}}")


class _StrictNestedRoot(MultilingualBaseModel):
    items: list[_StrictInner] = Field(description="{{[strict_inner_list]}}")


class _StrictWithDict(MultilingualBaseModel):
    summary: str = Field(description="{{[strict_summary]}}")
    attributes: dict = Field(description="{{[strict_attrs]}}")


class _StrictOuterWithNestedDict(MultilingualBaseModel):
    """Outer wrapper so dict field lives under a $ref'd nested model, not only at root."""

    payload: _StrictWithDict = Field(description="{{[strict_outer_payload]}}")


class TestStrictSchemaNestedAdditionalProperties:
    """strict=True BFS: structured objects (with properties dict) get additionalProperties=False."""

    _NESTED_PATCH = {
        "cn": {
            "{{[strict_inner_val]}}": "inner",
            "{{[strict_inner_list]}}": "list",
        }
    }

    _DICT_PATCH = {
        "cn": {
            "{{[strict_summary]}}": "summary",
            "{{[strict_attrs]}}": "attrs",
        }
    }

    _OUTER_NESTED_DICT_PATCH = {
        "cn": {
            "{{[strict_summary]}}": "summary",
            "{{[strict_attrs]}}": "attrs",
            "{{[strict_outer_payload]}}": "payload",
        }
    }

    _ENTITY_EXTRACTION_PATCH = {
        "cn": {
            "{{[ent_ext_list]}}": "entities",
            "{{[ent_def_name]}}": "name",
            "{{[ent_def_type]}}": "type id",
        }
    }

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, _NESTED_PATCH, clear=False)
    def test_strict_nested_list_model_all_objects_additional_properties_false():
        """Root plus $defs models (list items) are all type=object with additionalProperties False."""
        schema = _StrictNestedRoot.multilingual_model_json_schema("cn", strict=True)
        assert "$defs" in schema
        bad = _object_nodes_with_wrong_additional_properties(schema)
        assert bad == [], bad

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, _DICT_PATCH, clear=False)
    def test_strict_inline_dict_field_free_form_unchanged():
        """Bare ``dict`` fields have no fixed keys: Pydantic keeps additionalProperties True; strict BFS skips."""
        schema = _StrictWithDict.multilingual_model_json_schema("cn", strict=True)
        attrs = schema["properties"]["attributes"]
        assert attrs.get("type") == "object"
        assert not isinstance(attrs.get("properties"), dict)
        assert attrs.get("additionalProperties") is True
        bad = _object_nodes_with_wrong_additional_properties(schema)
        assert bad == [], bad

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, _OUTER_NESTED_DICT_PATCH, clear=False)
    def test_strict_outer_model_with_nested_dict_model_structured_strict_dict_field_unchanged():
        """$defs model gets strict on its root object; nested bare dict attributes stay free-form."""
        schema = _StrictOuterWithNestedDict.multilingual_model_json_schema("cn", strict=True)
        assert "$defs" in schema
        inner_defs = [k for k in schema["$defs"] if k.endswith("StrictWithDict")]
        assert len(inner_defs) == 1, schema["$defs"].keys()
        inner = schema["$defs"][inner_defs[0]]
        attrs = inner["properties"]["attributes"]
        assert attrs.get("type") == "object"
        assert not isinstance(attrs.get("properties"), dict)
        assert attrs.get("additionalProperties") is True
        assert inner.get("additionalProperties") is False
        assert schema["properties"]["payload"].get("$ref") == f"#/$defs/{inner_defs[0]}"
        bad = _object_nodes_with_wrong_additional_properties(schema)
        assert bad == [], bad

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, _ENTITY_EXTRACTION_PATCH, clear=False)
    def test_strict_entity_extraction_all_objects_additional_properties_false():
        """Real extraction model: structured objects in root and $defs get additionalProperties False."""
        schema = EntityExtraction.multilingual_model_json_schema("cn", strict=True)
        bad = _object_nodes_with_wrong_additional_properties(schema)
        assert bad == [], bad

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {}}, clear=False)
    def test_response_format_nested_matches_strict_schema():
        """response_format embeds schema that passes structured-object additionalProperties audit."""
        fmt = _StrictNestedRoot.response_format("cn")
        inner = fmt["json_schema"]["schema"]
        bad = _object_nodes_with_wrong_additional_properties(inner)
        assert bad == [], bad


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
        """response_format returns dict with type json_schema, model name, and embedded strict schema."""
        fmt = _SampleModel.response_format("cn")
        assert fmt["type"] == "json_schema"
        assert "json_schema" in fmt
        assert fmt["json_schema"]["name"] == "_SampleModel"
        assert fmt["json_schema"].get("strict") is False
        assert fmt["json_schema"]["schema"].get("additionalProperties") is False


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
