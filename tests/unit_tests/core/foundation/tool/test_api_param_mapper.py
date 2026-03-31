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


FORM_TYPE_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {
            "type": "string",
            "location": "form",
            "form_handler_type": "file",
            "description": "PDF file"
        },
        "image": {
            "type": "string",
            "location": "form",
            "form_handler_type": "file",
            "description": "Image file"
        },
        "name": {
            "type": "string",
            "location": "body",
            "description": "File name"
        }
    }
}


class TestAPIParamMapperFormParams:
    """APIParamMapper form parameter mapping tests"""

    class TestFormParamMapping:
        """Form parameter mapping to FORM location"""

        @staticmethod
        def test_single_form_param_mapping():
            """Single form parameter mapping"""
            schema = {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "file",
                        "description": "PDF file"
                    }
                }
            }
            mapper = APIParamMapper(schema)
            result = mapper.map({"file": "http://example.com/document.pdf"})

            assert APIParamLocation.FORM in result
            assert "file" in result[APIParamLocation.FORM]
            assert result[APIParamLocation.FORM]["file"]["form_handler_type"] == "file"
            assert result[APIParamLocation.FORM]["file"]["value"] == "http://example.com/document.pdf"

        @staticmethod
        def test_multiple_form_params_mapping():
            """Multiple form parameters mapping"""
            mapper = APIParamMapper(FORM_TYPE_SCHEMA)
            inputs = {
                "file": "http://example.com/document.pdf",
                "image": "http://example.com/image.png",
                "name": "test_document"
            }

            result = mapper.map(inputs)

            assert result[APIParamLocation.FORM] == {
                "file": {"form_handler_type": "file", "value": "http://example.com/document.pdf"},
                "image": {"form_handler_type": "file", "value": "http://example.com/image.png"}
            }
            assert result[APIParamLocation.BODY] == {"name": "test_document"}

        @staticmethod
        def test_mixed_form_and_regular_params():
            """Mixed form and regular parameter mapping"""
            schema = {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "file",
                        "description": "Document file"
                    },
                    "title": {
                        "type": "string",
                        "location": "body",
                        "description": "Document title"
                    },
                    "user_id": {
                        "type": "integer",
                        "location": "query",
                        "description": "User ID"
                    },
                    "auth_token": {
                        "type": "string",
                        "location": "header",
                        "description": "Auth token"
                    },
                    "version": {
                        "type": "string",
                        "location": "path",
                        "description": "API version"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            inputs = {
                "document": "http://example.com/doc.pdf",
                "title": "My Document",
                "user_id": 123,
                "auth_token": "token123",
                "version": "v1"
            }

            result = mapper.map(inputs)

            assert result[APIParamLocation.FORM] == {
                "document": {"form_handler_type": "file", "value": "http://example.com/doc.pdf"}
            }
            assert result[APIParamLocation.BODY] == {"title": "My Document"}
            assert result[APIParamLocation.QUERY] == {"user_id": 123}
            assert result[APIParamLocation.HEADER] == {"auth_token": "token123"}
            assert result[APIParamLocation.PATH] == {"version": "v1"}

    class TestFormHandlerTypeProcessing:
        """Form parameter form_handler_type processing"""

        @staticmethod
        def test_default_form_handler_type():
            """Default form_handler_type"""
            schema = {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "location": "form",
                        "description": "File without form_handler_type"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"file": "http://example.com/file.pdf"})

            assert result[APIParamLocation.FORM] == {
                "file": {"form_handler_type": "default", "value": "http://example.com/file.pdf"}
            }

        @staticmethod
        def test_custom_form_handler_type():
            """Custom form_handler_type"""
            schema = {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "custom",
                        "description": "Custom handler data"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"data": "custom_value"})

            assert result[APIParamLocation.FORM] == {
                "data": {"form_handler_type": "custom", "value": "custom_value"}
            }

        @staticmethod
        def test_empty_form_handler_type():
            """Empty form_handler_type"""
            schema = {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "",
                        "description": "File with empty form_handler_type"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"file": "http://example.com/file.pdf"})

            assert result[APIParamLocation.FORM] == {
                "file": {"form_handler_type": "", "value": "http://example.com/file.pdf"}
            }

    class TestFormParamValueProcessing:
        """Form parameter value processing"""

        @staticmethod
        def test_form_param_value_is_none():
            """Form parameter value is None"""
            schema = {
                "type": "object",
                "properties": {
                    "form_field": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Form field"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"form_field": None})

            assert result[APIParamLocation.FORM] == {}

        @staticmethod
        def test_form_param_value_is_empty_string():
            """Form parameter value is empty string"""
            schema = {
                "type": "object",
                "properties": {
                    "form_field": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Form field"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"form_field": ""})

            assert result[APIParamLocation.FORM] == {}

        @staticmethod
        def test_form_param_value_is_valid():
            """Form parameter value is valid"""
            schema = {
                "type": "object",
                "properties": {
                    "form_field": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Form field"
                    }
                }
            }

            mapper = APIParamMapper(schema)
            result = mapper.map({"form_field": "test_value"})

            assert result[APIParamLocation.FORM] == {
                "form_field": {"form_handler_type": "default", "value": "test_value"}
            }

        @staticmethod
        def test_inputs_not_contain_form_param():
            """inputs does not contain form parameter"""
            mapper = APIParamMapper(FORM_TYPE_SCHEMA)
            result = mapper.map({"name": "test"})

            assert result[APIParamLocation.FORM] == {}
            assert result[APIParamLocation.BODY] == {"name": "test"}

    class TestEmptySchemaProcessing:
        """Empty schema processing"""

        @staticmethod
        def test_empty_schema_uses_default_location():
            """Empty schema processing"""
            mapper = APIParamMapper(schema=None)
            result = mapper.map({"field": "value"})

            assert result[APIParamLocation.BODY] == {"field": "value"}
