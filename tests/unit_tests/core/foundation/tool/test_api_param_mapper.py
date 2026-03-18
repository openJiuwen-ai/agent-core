# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any
from pydantic import BaseModel, Field
from openjiuwen.core.foundation.tool.service_api.api_param_mapper import APIParamLocation, APIParamMapper


def test_init_base():
    simple_input_schemas = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "location": "path"},
            "name": {"type": "string", "location": "query"},
            "age": {"type": "integer", "location": "query"},
            "data": {"type": "object", "location": "body"},
            "auth_token": {"type": "string", "location": "header"}
        }
    }
    mapper = APIParamMapper(simple_input_schemas)
    assert isinstance(mapper.schema, dict)
    assert mapper.schema == simple_input_schemas
    assert mapper.defaults[APIParamLocation.QUERY] == {}
    assert mapper.defaults[APIParamLocation.HEADER] == {}
    assert mapper.defaults[APIParamLocation.PATH] == {}


DEFAULT_SCHEMAS = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "location": "path"},
        "name": {"type": "string", "location": "query"},
        "age": {"type": "integer", "location": "query"},
        "data": {"type": "object", "location": "body"},
        "auth_token": {"type": "string", "location": "header"}
    }
}


def test_map_with_dict_schema():
    mapper = APIParamMapper(DEFAULT_SCHEMAS)
    inputs = {
        "id": 123,
        "name": "John",
        "age": 30,
        "data": {"key": "value"},
        "auth_token": "abc123"
    }

    result = mapper.map(inputs)
    assert result[APIParamLocation.PATH] == {"id": 123}
    assert result[APIParamLocation.QUERY] == {"name": "John", "age": 30}
    assert result[APIParamLocation.BODY] == {"data": {"key": "value"}}
    assert result[APIParamLocation.HEADER] == {"auth_token": "abc123"}


class DemoInputParams(BaseModel):
    """Test Pydantic model for schema testing."""
    id: int = Field(..., description="User ID", location="path")
    name: str = Field(..., description="User name", location="query")
    data: Dict[str, Any] = Field(default_factory=dict, description="Request data", location="body")
    token: str = Field(default="", description="Auth token", location="header")


def test_map_with_pydantic_model_schema():
    mapper = APIParamMapper(DemoInputParams)
    inputs = {
        "id": 123,
        "name": "John",
        "data": {"key": "value"},
        "token": "xyz789"
    }

    result = mapper.map(inputs)

    assert result[APIParamLocation.PATH] == {"id": 123}
    assert result[APIParamLocation.QUERY] == {"name": "John"}
    assert result[APIParamLocation.BODY] == {"data": {"key": "value"}}
    assert result[APIParamLocation.HEADER] == {"token": "xyz789"}


def test_map_with_default_values():
    mapper = APIParamMapper(
        schema=DEFAULT_SCHEMAS,
        default_queries={"lang": "en", "format": "json"},
        default_headers={"X-API-Key": "test-key"},
        default_paths={"version": "v1"}
    )

    result = mapper.map({"id": 123, "name": "John"})

    # Defaults should be merged with inputs
    assert result[APIParamLocation.PATH] == {"version": "v1", "id": 123}
    assert result[APIParamLocation.QUERY] == {"lang": "en", "format": "json", "name": "John"}
    assert result[APIParamLocation.HEADER] == {"X-API-Key": "test-key"}
    assert result[APIParamLocation.BODY] == {}


def test_map_input_overrides_defaults():
    """Test that input values override default values."""
    mapper = APIParamMapper(
        schema=DEFAULT_SCHEMAS,
        default_queries={"lang": "en", "name": "Default Name"},
        default_paths={"id": 999, "version": "v1"}
    )

    result = mapper.map({"id": 123, "name": "Actual Name"})
    assert result[APIParamLocation.PATH] == {"version": "v1", "id": 123}
    assert result[APIParamLocation.QUERY] == {"lang": "en", "name": "Actual Name"}


def test_map_none_and_empty_string_preserve_defaults():
    """Test that None and empty string values preserve default values instead of overwriting them."""
    mapper = APIParamMapper(
        schema=DEFAULT_SCHEMAS,
        default_queries={"lang": "en", "format": "json"},
        default_headers={"X-API-Key": "test-key", "X-User-ID": "default-user"},
        default_paths={"version": "v1"}
    )

    # Inputs with None and empty string should not override defaults
    inputs = {
        "id": None,  # Should preserve default path param if existed, but id has no default
        "name": "",  # Should preserve default (none in this case for name)
        "age": 25,  # Normal value should be used
        "auth_token": None,  # Should preserve default header
    }

    result = mapper.map(inputs)

    # None for 'id' means no value is set (id not in result since it has no default)
    assert result[APIParamLocation.PATH] == {"version": "v1"}
    # Empty string for 'name' preserves defaults, age is set normally
    assert result[APIParamLocation.QUERY] == {"lang": "en", "format": "json", "age": 25}
    # None for 'auth_token' preserves default header
    assert result[APIParamLocation.HEADER] == {"X-API-Key": "test-key", "X-User-ID": "default-user"}
    assert result[APIParamLocation.BODY] == {}
