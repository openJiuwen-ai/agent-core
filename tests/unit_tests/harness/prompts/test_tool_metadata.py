# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ToolMetadataProvider ABC, validation, and registry."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
    validate_provider,
)
from openjiuwen.harness.prompts.sections.tools import (
    build_tool_card,
    get_tool_description,
    get_tool_input_params,
    register_tool_provider,
    validate_all_tool_providers,
    _REGISTRY,
)


# ---------------------------------------------------------------------------
# Helper: minimal valid provider
# ---------------------------------------------------------------------------
class _ValidProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "test_valid"

    def get_description(self, language: str = "cn") -> str:
        return {"cn": "测试工具", "en": "Test tool"}.get(language, "测试工具")

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        desc = {"cn": "参数A", "en": "Param A"}.get(language, "参数A")
        return {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": desc},
            },
            "required": ["a"],
        }


# ---------------------------------------------------------------------------
# validate_provider tests
# ---------------------------------------------------------------------------
class TestValidateProvider:
    @staticmethod
    def test_valid_provider_passes():
        validate_provider(_ValidProvider())

    @staticmethod
    def test_empty_cn_description_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return {"cn": "", "en": "ok"}.get(language, "")

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                return {"type": "object", "properties": {}, "required": []}

        with pytest.raises(ValueError, match="cn description is empty"):
            validate_provider(Bad())

    @staticmethod
    def test_empty_en_description_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return {"cn": "好", "en": "  "}.get(language, "好")

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                return {"type": "object", "properties": {}, "required": []}

        with pytest.raises(ValueError, match="en description is empty"):
            validate_provider(Bad())

    @staticmethod
    def test_schema_type_not_object_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return "ok"

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                if language == "cn":
                    return {"type": "array", "properties": {}, "required": []}
                return {"type": "object", "properties": {}, "required": []}

        with pytest.raises(ValueError, match="cn schema type"):
            validate_provider(Bad())

    @staticmethod
    def test_property_keys_differ_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return "ok"

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                if language == "cn":
                    return {
                        "type": "object",
                        "properties": {"a": {"type": "string", "description": "x"}},
                        "required": [],
                    }
                return {
                    "type": "object",
                    "properties": {"b": {"type": "string", "description": "y"}},
                    "required": [],
                }

        with pytest.raises(ValueError, match="property keys differ"):
            validate_provider(Bad())

    @staticmethod
    def test_missing_description_in_property_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return "ok"

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                if language == "cn":
                    return {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "required": [],
                    }
                return {
                    "type": "object",
                    "properties": {"a": {"type": "string", "description": "y"}},
                    "required": [],
                }

        with pytest.raises(ValueError, match="missing description"):
            validate_provider(Bad())

    @staticmethod
    def test_nested_object_validated():
        """Nested object properties should also be checked."""
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad"

            def get_description(self, language: str = "cn") -> str:
                return "ok"

            def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
                nested_cn = {
                    "type": "object",
                    "properties": {"x": {"type": "string", "description": "x"}},
                    "required": [],
                }
                nested_en = {
                    "type": "object",
                    "properties": {"y": {"type": "string", "description": "y"}},
                    "required": [],
                }
                if language == "cn":
                    return {
                        "type": "object",
                        "properties": {"sub": {**nested_cn, "description": "子对象"}},
                        "required": [],
                    }
                return {
                    "type": "object",
                    "properties": {"sub": {**nested_en, "description": "sub obj"}},
                    "required": [],
                }

        with pytest.raises(ValueError, match="property keys differ"):
            validate_provider(Bad())


# ---------------------------------------------------------------------------
# validate_all_tool_providers
# ---------------------------------------------------------------------------
class TestValidateAllProviders:
    @staticmethod
    def test_all_builtin_providers_pass():
        validate_all_tool_providers()


# ---------------------------------------------------------------------------
# build_tool_card
# ---------------------------------------------------------------------------
class TestBuildToolCard:
    @staticmethod
    def test_returns_correct_card():
        card = build_tool_card("bash", "BashTool", "cn")
        assert card.id == "BashTool"
        assert card.name == "bash"
        assert card.description == get_tool_description("bash", "cn")
        assert card.input_params == get_tool_input_params("bash", "cn")

    @staticmethod
    def test_en_language():
        card = build_tool_card("code", "CodeTool", "en")
        assert card.description == get_tool_description("code", "en")
        assert card.input_params == get_tool_input_params("code", "en")

    @staticmethod
    def test_unknown_tool_raises():
        with pytest.raises(KeyError):
            build_tool_card("nonexistent", "X", "cn")


# ---------------------------------------------------------------------------
# register_tool_provider
# ---------------------------------------------------------------------------
class TestRegisterToolProvider:
    @staticmethod
    def test_register_valid_provider():
        provider = _ValidProvider()
        old_keys = set(_REGISTRY.keys())
        register_tool_provider(provider)
        assert "test_valid" in _REGISTRY
        # cleanup
        del _REGISTRY["test_valid"]
        assert set(_REGISTRY.keys()) == old_keys

    @staticmethod
    def test_register_invalid_provider_raises():
        class Bad(ToolMetadataProvider):
            def get_name(self) -> str:
                return "bad_reg"

            def get_description(self, language: str = "cn") -> str:
                return {"cn": "", "en": "ok"}.get(language, "")

            def get_input_params(
                self, language: str = "cn"
            ) -> Dict[str, Any]:
                return {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }

        with pytest.raises(ValueError):
            register_tool_provider(Bad())
        assert "bad_reg" not in _REGISTRY


# ---------------------------------------------------------------------------
# Fail-fast: unknown tool name
# ---------------------------------------------------------------------------
class TestFailFast:
    @staticmethod
    def test_get_tool_description_unknown_raises():
        with pytest.raises(KeyError, match="not registered"):
            get_tool_description("no_such_tool", "cn")

    @staticmethod
    def test_get_tool_input_params_unknown_raises():
        with pytest.raises(KeyError, match="not registered"):
            get_tool_input_params("no_such_tool", "cn")
