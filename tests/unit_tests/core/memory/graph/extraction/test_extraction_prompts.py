# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for extraction_prompts"""

from unittest.mock import MagicMock, patch

from openjiuwen.core.foundation.store.graph.graph_object import Entity
from openjiuwen.core.memory.graph.extraction.base import MULTILINGUAL_DESCRIPTION
from openjiuwen.core.memory.graph.extraction.entity_type_definition import EntityDef
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityDeclaration
from openjiuwen.core.memory.graph.extraction.extraction_prompts import (
    dedupe_entity_list,
    dedupe_relation_list,
    extract_entity_attributes,
    extract_entity_declaration,
    extract_relation_declaration,
    extract_timezone,
    filter_relations_for_merge,
    format_new_entities,
    merge_existing_entities,
)


class TestFormatNewEntities:
    """Tests for format_new_entities"""

    @staticmethod
    def test_empty_entities_returns_empty_string():
        """Empty entity list returns empty string"""
        result = format_new_entities([], language="cn")
        assert result == ""

    @staticmethod
    def test_without_entity_types_lists_names_with_index():
        """Without entity_types, entities are listed as 'idx. name'"""
        entities = [
            EntityDeclaration(name="Alice", entity_type_id=0),
            EntityDeclaration(name="Bob", entity_type_id=0),
        ]
        result = format_new_entities(entities, start_idx=1, language="cn")
        assert "1. Alice" in result
        assert "2. Bob" in result

    @staticmethod
    def test_start_idx_affects_numbering():
        """start_idx affects the displayed index"""
        entities = [EntityDeclaration(name="X", entity_type_id=0)]
        result = format_new_entities(entities, start_idx=5, language="cn")
        assert result == "5. X"

    @staticmethod
    @patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {":": ":"}}, clear=False)
    def test_with_entity_types_includes_type_and_separator():
        """With entity_types, output includes type list and 'idx. name (TypeName)'"""
        entity_type = EntityDef()
        entity_type.name = "Person"
        entity_type.description = {"cn": "人", "en": "Person"}
        entities = [
            EntityDeclaration(name="Alice", entity_type_id=0),
        ]
        result = format_new_entities(entities, entity_types=[entity_type], start_idx=1, language="cn")
        assert "Person" in result
        assert "Alice" in result
        assert "---" in result


class TestExtractEntityDeclaration:
    """Tests for extract_entity_declaration"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs")
    def test_returns_kwargs_template_and_response_format(get_kwargs, mock_tm_cls):
        """extract_entity_declaration returns (kwargs, template, response_format)"""
        from openjiuwen.core.memory.config.graph import EpisodeType

        get_kwargs.return_value = {"content": "", "context": "", "extra_message": ""}
        mock_tm_cls.return_value.get.return_value = MagicMock()
        kwargs, template, resp_fmt = extract_entity_declaration(EpisodeType.CONVERSATION, "Hello", language="cn")
        assert isinstance(kwargs, dict)
        assert "entity_types" in kwargs
        assert template is not None
        assert resp_fmt["type"] == "json_schema"
        assert "json_schema" in resp_fmt

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs")
    def test_entity_types_default_single_entity_def(get_kwargs, mock_tm_cls):
        """When entity_types is None, kwargs['entity_types'] uses single EntityDef()"""
        from openjiuwen.core.memory.config.graph import EpisodeType

        get_kwargs.return_value = {}
        mock_tm_cls.return_value.get.return_value = MagicMock()
        kwargs, _, _ = extract_entity_declaration(EpisodeType.CONVERSATION, "Hi", language="cn")
        assert "entity_types" in kwargs
        assert "Entity" in kwargs["entity_types"] or "1." in kwargs["entity_types"]


class TestExtractEntityAttributes:
    """Tests for extract_entity_attributes"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs")
    def test_sets_entity_name_and_summary(get_kwargs, mock_tm_cls):
        """extract_entity_attributes sets entity_name and entity_summary in kwargs"""

        get_kwargs.return_value = {}
        mock_tm_cls.return_value.get.return_value = MagicMock()
        entity = Entity(name="E1", content="Summary", obj_type="human")
        kwargs, _, _ = extract_entity_attributes(entity, "content", language="cn")
        assert kwargs["entity_name"] == "E1"
        assert kwargs["entity_summary"] == "Summary"


class TestExtractRelationDeclaration:
    """Tests for extract_relation_declaration"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    def test_returns_kwargs_with_entities_tz_and_relation_types(mock_get_kwargs, mock_tm_cls):
        """extract_relation_declaration returns kwargs with entities, tz_info, relation_types"""
        mock_get_kwargs.return_value["entities"] = ""
        mock_get_kwargs.return_value["relation_types"] = ""
        mock_tm_cls.return_value.get.return_value = MagicMock()
        entities = [EntityDeclaration(name="E1", entity_type_id=0)]
        kwargs, template, resp_fmt = extract_relation_declaration(
            None, entities, reference_time=0, tz_info="UTC", content="Hi", language="cn"
        )
        assert "tz_info" in kwargs
        assert "entities" in kwargs
        assert "relation_types" in kwargs
        assert "reference_time" in kwargs
        assert "id_range" in kwargs
        assert resp_fmt["type"] == "json_schema"


class TestExtractTimezone:
    """Tests for extract_timezone"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    def test_returns_kwargs_template_and_response_format(mock_get_kwargs, mock_tm_cls):
        """extract_timezone returns (kwargs, template, response_format)"""
        mock_tm_cls.return_value.get.return_value = MagicMock()
        kwargs, template, resp_fmt = extract_timezone("content", language="cn")
        assert "context" in kwargs
        assert resp_fmt["type"] == "json_schema"


class TestMergeExistingEntities:
    """Tests for merge_existing_entities"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_existing_entities",
        return_value="",
    )
    def test_returns_kwargs_with_entities_to_merge(mock_fmt_entities, mock_get_kwargs, mock_tm_cls):
        """merge_existing_entities returns kwargs with entity_name, entities_to_merge"""
        mock_tm_cls.return_value.get.return_value = MagicMock()
        target = Entity(name="T", content="", obj_type="human")
        sources = [Entity(name="S1", content="", obj_type="human")]
        kwargs, _, _ = merge_existing_entities(target, sources, language="cn")
        assert kwargs["entity_name"] == "T"
        assert "entities_to_merge" in kwargs


class TestFilterRelationsForMerge:
    """Tests for filter_relations_for_merge"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_existing_relations",
        return_value="",
    )
    def test_returns_kwargs_with_existing_relations(mock_fmt_rel, mock_get_kwargs, mock_tm_cls):
        """filter_relations_for_merge returns kwargs with entity_name, existing_relations"""
        from openjiuwen.core.foundation.store.graph.graph_object import Relation

        mock_tm_cls.return_value.get.return_value = MagicMock()
        target = Entity(name="T", content="", obj_type="human")
        rel = Relation(content="r1", lhs="e1", rhs="e2", obj_type="Relation")
        kwargs, _, _ = filter_relations_for_merge(target, [rel], language="cn")
        assert kwargs["entity_name"] == "T"
        assert "existing_relations" in kwargs


class TestDedupeEntityList:
    """Tests for dedupe_entity_list"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_existing_entities",
        return_value="",
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_new_entities",
        return_value="",
    )
    def test_returns_kwargs_with_entities_and_candidates(mock_fmt_new, mock_fmt_ex, mock_get_kwargs, mock_tm_cls):
        """dedupe_entity_list returns kwargs with entities, candidate_entities"""
        mock_tm_cls.return_value.get.return_value = MagicMock()
        candidates = [EntityDeclaration(name="C1", entity_type_id=0)]
        kwargs, _, _ = dedupe_entity_list("content", candidates, existing_entities=[], language="cn")
        assert "entities" in kwargs
        assert "candidate_entities" in kwargs


class TestDedupeRelationList:
    """Tests for dedupe_relation_list"""

    @staticmethod
    @patch("openjiuwen.core.memory.graph.extraction.extraction_prompts.TemplateManager")
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.get_formatting_kwargs",
        return_value={"source_description": "", "extra_message": "", "context": ""},
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_existing_entities",
        return_value="",
    )
    @patch(
        "openjiuwen.core.memory.graph.extraction.extraction_prompts.format_existing_relations",
        return_value="",
    )
    def test_returns_kwargs_with_relations_and_new_relation(mock_fmt_rel, mock_fmt_ent, mock_get_kwargs, mock_tm_cls):
        """dedupe_relation_list returns kwargs with entities, existing_relations, new_relation"""
        from openjiuwen.core.foundation.store.graph.graph_object import Relation

        mock_tm_cls.return_value.get.return_value = MagicMock()
        rel = Relation(content="r1", lhs="e1", rhs="e2", obj_type="Relation")
        kwargs, _, _ = dedupe_relation_list("content", rel, existing_relations=[], existing_entities=[], language="cn")
        assert "entities" in kwargs
        assert "existing_relations" in kwargs
        assert "new_relation" in kwargs
