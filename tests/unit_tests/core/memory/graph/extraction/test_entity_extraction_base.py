# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for prompts.entity_extraction.base (format helpers and ensure_valid_language)"""

from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.memory.graph.extraction.entity_type_definition import (
    AIEntity,
    HumanEntity,
    RelationDef,
)
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityExtraction
from openjiuwen.core.memory.graph.extraction.prompts.entity_extraction import base as entity_base


class TestFormatSchemaInfo:
    """Tests for format_schema_info"""

    @staticmethod
    def test_format_schema_info_none_returns_empty():
        """format_schema_info with output_model=None returns empty string"""
        result = entity_base.format_schema_info(None, language="cn")
        assert result == ""

    @staticmethod
    @patch.dict(
        entity_base.REF_JSON_OBJECT_DEF,
        {"cn": "JSON定义"},
        clear=False,
    )
    @patch.dict(
        entity_base.OUTPUT_FORMAT,
        {"cn": "输出格式"},
        clear=False,
    )
    def test_format_schema_info_with_model_returns_string():
        """format_schema_info with output_model returns schema string"""
        from openjiuwen.core.memory.graph.extraction.base import MULTILINGUAL_DESCRIPTION

        with patch.dict(
            MULTILINGUAL_DESCRIPTION,
            {"cn": {"{{[ent_ext_list]}}": "列表", "{{[ent_def_name]}}": "名", "{{[ent_def_type]}}": "型"}},
            clear=False,
        ):
            result = entity_base.format_schema_info(EntityExtraction, indent=2, language="cn")
        assert "---" in result
        assert "extracted_entities" in result or "EntityDeclaration" in result


class TestFormatSourceDescription:
    """Tests for format_source_description"""

    @staticmethod
    @patch.dict(entity_base.SOURCE_DESCRIPTION, {"cn": "Source: {source_description}"}, clear=False)
    def test_format_source_description_with_text():
        """format_source_description with text returns formatted string"""
        result = entity_base.format_source_description("my source", language="cn")
        assert "my source" in result

    @staticmethod
    def test_format_source_description_none_returns_empty():
        """format_source_description with None returns empty string"""
        result = entity_base.format_source_description(None, language="cn")
        assert result == ""


class TestGetFormattingKwargs:
    """Tests for get_formatting_kwargs"""

    @staticmethod
    @patch.dict(entity_base.MARK_HISTORY_MSG, {"cn": "History: {history}"}, clear=False)
    @patch.dict(entity_base.MARK_CURRENT_MSG, {"cn": "Current: {content}"}, clear=False)
    @patch.dict(entity_base.SOURCE_DESCRIPTION, {"cn": "{source_description}"}, clear=False)
    def test_get_formatting_kwargs_with_history_and_content():
        """get_formatting_kwargs assembles context from history and content"""
        result = entity_base.get_formatting_kwargs(history="past", content="now", language="cn")
        assert "context" in result
        assert "past" in result["context"]
        assert "now" in result["context"]
        assert "source_description" in result
        assert "extra_message" in result

    @staticmethod
    @patch.dict(entity_base.SOURCE_DESCRIPTION, {"cn": "Desc: {source_description}"}, clear=False)
    def test_get_formatting_kwargs_with_source_description():
        """get_formatting_kwargs includes format_source_description"""
        result = entity_base.get_formatting_kwargs(source_description="src", language="cn")
        assert "src" in result["source_description"]


class TestFormatRelationDefinitions:
    """Tests for format_relation_definitions"""

    @staticmethod
    @patch.dict(entity_base.RELATION_FORMAT, {"cn": "{name}: {description}"}, clear=False)
    @patch.dict(entity_base.NO_RELATION_GIVEN, {"cn": "No relations"}, clear=False)
    def test_format_relation_definitions_none_returns_no_relation():
        """format_relation_definitions with None returns NO_RELATION_GIVEN"""
        result = entity_base.format_relation_definitions(None, language="cn")
        assert result == "No relations"

    @staticmethod
    @patch.dict(
        entity_base.RELATION_FORMAT,
        {"cn": "{name}: {description} ({lhs}-{rhs})"},
        clear=False,
    )
    def test_format_relation_definitions_with_types():
        """format_relation_definitions with list returns formatted string; Defs are instances"""
        # Use instances so .name is available (format_relation_definitions uses rtype.lhs.name)
        lhs_instance = HumanEntity()
        rhs_instance = AIEntity()
        rel = RelationDef(lhs=HumanEntity, rhs=AIEntity)
        rel.name = "Knows"
        rel.description = {"cn": "knows", "en": "knows"}
        rel.lhs = lhs_instance
        rel.rhs = rhs_instance
        result = entity_base.format_relation_definitions([rel], language="cn")
        assert "Knows" in result
        assert "Human" in result
        assert "AI" in result


class TestFormatExistingRelations:
    """Tests for format_existing_relations"""

    @staticmethod
    def test_format_existing_relations_empty_list():
        """format_existing_relations with empty list returns empty string"""
        result = entity_base.format_existing_relations([])
        assert result == ""

    @staticmethod
    @patch.object(entity_base, "load_stored_time_from_db")
    def test_format_existing_relations_with_time(mock_load_time):
        """format_existing_relations with valid_since/valid_until includes time"""
        from datetime import datetime

        mock_load_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
        relations = [
            {"content": "rel1", "valid_since": 0, "valid_until": 0, "offset_since": 0, "offset_until": 0},
        ]
        result = entity_base.format_existing_relations(relations, start_idx=1, include_time=True)
        assert "rel1" in result
        mock_load_time.assert_called()

    @staticmethod
    def test_format_existing_relations_include_time_false():
        """format_existing_relations with include_time=False skips time"""
        relations = [{"content": "r1", "valid_since": -1, "valid_until": -1}]
        result = entity_base.format_existing_relations(relations, include_time=False)
        assert "r1" in result


class TestFormatExistingEntities:
    """Tests for format_existing_entities"""

    @staticmethod
    @patch.dict(entity_base.DISPLAY_ENTITY, {"cn": "{i}. {name}: {content}"}, clear=False)
    def test_format_existing_entities():
        """format_existing_entities formats list of entity dicts"""
        entities = [{"name": "E1", "content": "summary"}]
        result = entity_base.format_existing_entities(entities, start_idx=1, language="cn")
        assert "E1" in result
        assert "summary" in result


class TestEnsureValidLanguage:
    """Tests for ensure_valid_language"""

    @staticmethod
    @patch.object(entity_base, "REGISTERED_LANGUAGE", {"cn", "en"}, create=True)
    def test_ensure_valid_language_valid_returns_language():
        """ensure_valid_language with registered language returns it"""
        result = entity_base.ensure_valid_language("cn", 10)
        assert result == "cn"

    @staticmethod
    @patch.object(entity_base, "REGISTERED_LANGUAGE", {"cn"}, create=True)
    def test_ensure_valid_language_invalid_raises():
        """ensure_valid_language with unregistered language raises"""
        with pytest.raises(BaseError, match="does not support language"):
            entity_base.ensure_valid_language("xx", 10)

    @staticmethod
    @patch.object(entity_base, "REGISTERED_LANGUAGE", {"cn"}, create=True)
    def test_ensure_valid_language_too_long_raises():
        """ensure_valid_language when len > max_len raises"""
        with pytest.raises(BaseError, match="exceeds max length"):
            entity_base.ensure_valid_language("cn", 1)

    @staticmethod
    @patch.object(entity_base, "REGISTERED_LANGUAGE", {"cn"}, create=True)
    def test_ensure_valid_language_non_str_with_str_convertible():
        """ensure_valid_language with object that has __str__ converts"""

        class MockLanguage:
            """Mock class"""

            @staticmethod
            def __str__():
                return "cn"

        result = entity_base.ensure_valid_language(MockLanguage(), 10)
        assert result == "cn"

    @staticmethod
    def test_ensure_valid_language_non_str_raises():
        """ensure_valid_language with non-string (e.g. object()) raises BaseError"""
        with pytest.raises(BaseError):
            entity_base.ensure_valid_language(object(), 10)
