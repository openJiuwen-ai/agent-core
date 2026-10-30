# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the engine's JSON-Schema acceptance boundary.

Swarmflow scripts are authored by the leader LLM and sometimes carry the
shorthand ``"items": ["string"]`` (array of strings) or the draft-07 tuple
spelling. Neither is valid under the draft 2020-12 metaschema
``jsonschema.validate`` applies, so the structured-output tool raised
``SchemaError`` on every submission. The schema is now normalized at
:func:`resolve_schema` — the single place every worker / session / tiny-agent
call resolves its schema through.
"""

from __future__ import annotations

import asyncio

import jsonschema

from openjiuwen.agent_teams.tools.structured_output_tool import StructuredOutputTool
from openjiuwen.agent_teams.workflow.engine.schema import resolve_schema

# Mirrors the schema the leader sent in the failing E2E run: ``pitfalls`` and
# ``open_questions`` use the draft-07 tuple form for an array of strings.
LEGACY_TUPLE_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "pitfalls": {"type": "array", "items": ["string"]},
        "open_questions": {"type": "array", "items": ["string"]},
    },
    "required": ["topic", "pitfalls"],
}


def test_resolve_schema_lowers_type_name_items_shorthand():
    json_schema, model = resolve_schema(LEGACY_TUPLE_ITEMS_SCHEMA)

    assert model is None
    pitfalls = json_schema["properties"]["pitfalls"]
    assert pitfalls["items"] == {"type": "string"}
    jsonschema.Draft202012Validator.check_schema(json_schema)


def test_resolve_schema_rewrites_tuple_items_to_prefix_items():
    schema = {
        "type": "array",
        "items": [{"type": "string"}, {"type": "integer"}],
    }

    json_schema, _ = resolve_schema(schema)

    assert "items" not in json_schema
    assert json_schema["prefixItems"] == [{"type": "string"}, {"type": "integer"}]
    jsonschema.Draft202012Validator.check_schema(json_schema)


def test_resolve_schema_does_not_mutate_the_caller_schema():
    resolve_schema(LEGACY_TUPLE_ITEMS_SCHEMA)

    assert LEGACY_TUPLE_ITEMS_SCHEMA["properties"]["pitfalls"]["items"] == ["string"]


def test_normalized_schema_lets_structured_output_accept_submissions():
    """Without normalization ``invoke`` raises SchemaError on check_schema."""
    json_schema, _ = resolve_schema(LEGACY_TUPLE_ITEMS_SCHEMA)
    tool = StructuredOutputTool(json_schema)

    out = asyncio.run(tool.invoke({"topic": "杭州", "pitfalls": ["地铁 1 号线"]}))

    assert out.success is True
    assert tool.called is True
    assert tool.captured == {"topic": "杭州", "pitfalls": ["地铁 1 号线"]}


def test_normalization_recurses_into_nested_subschemas():
    schema = {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tags": {"type": "array", "items": ["string", "integer"]},
                    },
                },
            },
        },
    }

    json_schema, _ = resolve_schema(schema)

    tags = json_schema["properties"]["groups"]["items"]["properties"]["tags"]
    assert tags["items"] == {"type": ["string", "integer"]}
    jsonschema.Draft202012Validator.check_schema(json_schema)


def test_additional_items_maps_to_items():
    schema = {
        "type": "array",
        "items": [{"type": "string"}],
        "additionalItems": False,
    }

    json_schema, _ = resolve_schema(schema)

    assert json_schema["prefixItems"] == [{"type": "string"}]
    assert json_schema["items"] is False
    jsonschema.Draft202012Validator.check_schema(json_schema)


def test_canonical_schema_is_returned_unchanged():
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
    }

    json_schema, _ = resolve_schema(schema)

    assert json_schema == schema


def test_explicit_legacy_dialect_is_left_to_its_own_validator():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "array",
        "items": [{"type": "string"}],
    }

    json_schema, _ = resolve_schema(schema)

    assert json_schema["items"] == [{"type": "string"}]
    jsonschema.Draft7Validator.check_schema(json_schema)
