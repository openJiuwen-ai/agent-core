# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Dict, List

from pydantic import BaseModel, Field
from sympy.testing import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.utils.schema_utils import SchemaUtils


class UserModel(BaseModel):
    """Test Pydantic model for user data"""
    name: str = Field(default="Anonymous", min_length=1, max_length=50)
    age: int = Field(default=18, ge=0, le=150)
    email: str = Field(default="user@example.com")
    is_active: bool = Field(default=True)
    tags: List[str] = Field(default_factory=lambda: ["new_user"])
    metadata: Dict[str, Any] = Field(default_factory=dict)


USER_SCHEMA = {
    "type": "object",
    "title": "User",
    "properties": {
        "name": {
            "type": "string",
            "default": "Anonymous",
            "minLength": 1,
            "maxLength": 50,
            "description": "User's name"
        },
        "age": {
            "type": "integer",
            "default": 18,
            "minimum": 0,
            "maximum": 150
        },
        "email": {
            "type": "string",
            "format": "email",
            "default": "user@example.com"
        },
        "is_active": {
            "type": "boolean",
            "default": True
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "default": ["new_user"],
            "minItems": 1
        },
        "metadata": {
            "type": "object",
            "default": {},
            "additionalProperties": True
        }
    },
    "required": ["name", "age", "email"]
}


def test_format_with_pydantic_model():
    """Test formatting with Pydantic model"""
    PARTIAL_USER_DATA = {
        "name": "Jane Doe",
        "age": 25
        # Missing email, should use default
    }

    result = SchemaUtils.format_with_schema(PARTIAL_USER_DATA, UserModel)
    assert result["name"] == "Jane Doe"
    assert result["age"] == 25
    assert result["email"] == "user@example.com"  # Default value
    assert result["is_active"] is True  # Default value
    assert result["tags"] == ["new_user"]  # Default value
    assert result["metadata"] == {}  # Default value


def test_format_with_json_schema():
    """Test formatting with JSON Schema"""
    PARTIAL_USER_DATA = {
        "name": "Jane Doe",
        "age": 25
        # Missing email, should use default
    }

    result = SchemaUtils.format_with_schema(PARTIAL_USER_DATA, USER_SCHEMA)

    assert result["name"] == "Jane Doe"
    assert result["age"] == 25
    assert result["email"] == "user@example.com"  # Default value
    assert result["is_active"] is True  # Default value
    assert result["tags"] == ["new_user"]  # Default value


def test_format_none_data():
    """Test formatting with None data"""
    with pytest.raises(ValidationError):
        result = SchemaUtils.format_with_schema(None, UserModel)


def test_format_empty_dict():
    result = SchemaUtils.format_with_schema({}, USER_SCHEMA)
    assert "name" in result
    assert "age" in result
    assert "email" in result

def test_validate_valid_data():
    VALID_USER_DATA = {
        "name": "John Doe",
        "age": 30,
        "email": "john@example.com",
        "is_active": True,
        "tags": ["developer", "premium"],
        "metadata": {"created_at": "2024-01-01"}
    }
    SchemaUtils.validate_with_schema(VALID_USER_DATA, UserModel)
    SchemaUtils.validate_with_schema(VALID_USER_DATA, USER_SCHEMA)


def test_validate_invalidate_date():
    INVALID_USER_DATA = {
        "name": "",  # Empty string, violates minLength
        "age": 200,  # Too high, violates maximum
        "email": "invalid-email"  # Invalid email format
    }
    with pytest.raises(ValidationError):
        SchemaUtils.validate_with_schema(INVALID_USER_DATA, UserModel)

    with pytest.raises(ValidationError):
        SchemaUtils.validate_with_schema(INVALID_USER_DATA, USER_SCHEMA)


def test_get_schema_from_simple_model():
    schema_dict = SchemaUtils.get_schema_dict(UserModel)
    assert "type" in schema_dict
    assert schema_dict["type"] == "object"
    assert "properties" in schema_dict
    assert "name" in schema_dict["properties"]
    assert "age" in schema_dict["properties"]
    name_prop = schema_dict["properties"]["name"]
    assert "default" in name_prop
    assert name_prop["default"] == "Anonymous"


def test_create_model_from_simple_schema():
    model = SchemaUtils.get_schema_class(USER_SCHEMA)
    instance = model(
        name="Test User",
        age=30,
        email="test@example.com"
    )
    assert instance.name == "Test User"
    assert instance.age == 30
    assert instance.email == "test@example.com"
    assert instance.is_active is True