# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for entity_type_definition"""

from unittest.mock import patch

import pytest

from openjiuwen.core.memory.graph.extraction.entity_type_definition import (
    AIEntity,
    EntityDef,
    EntityDefAttr,
    HumanEntity,
    RelationDef,
)


@pytest.fixture(autouse=True)
def patch_descriptions():
    """Patch description dicts so models can build schema"""
    with (
        patch(
            "openjiuwen.core.memory.graph.extraction.entity_type_definition.ENTITY_DEFINITION_DESCRIPTION",
            {"cn": "", "en": ""},
        ),
        patch(
            "openjiuwen.core.memory.graph.extraction.entity_type_definition.RELATION_DEFINITION_DESCRIPTION",
            {"cn": "", "en": ""},
        ),
    ):
        yield


class TestEntityDefAttr:
    """Tests for EntityDefAttr"""

    @staticmethod
    def test_default_content_empty():
        """content defaults to empty string"""
        attr = EntityDefAttr()
        assert attr.content == ""

    @staticmethod
    def test_content_set():
        """content can be set"""
        attr = EntityDefAttr(content="summary text")
        assert attr.content == "summary text"


class TestEntityDef:
    """Tests for EntityDef"""

    @staticmethod
    def test_default_name_entity():
        """Default name is 'Entity'"""
        ent = EntityDef()
        assert ent.name == "Entity"

    @staticmethod
    def test_attributes_is_entity_def_attr():
        """attributes is EntityDefAttr by default"""
        ent = EntityDef()
        assert isinstance(ent.attributes, EntityDefAttr)


class TestHumanEntity:
    """Tests for HumanEntity"""

    @staticmethod
    def test_name_human():
        """HumanEntity name is 'Human'"""
        ent = HumanEntity()
        assert ent.name == "Human"


class TestAIEntity:
    """Tests for AIEntity"""

    @staticmethod
    def test_name_ai():
        """AIEntity name is 'AI'"""
        ent = AIEntity()
        assert ent.name == "AI"


class TestRelationDef:
    """Tests for RelationDef"""

    @staticmethod
    def test_default_name_relation():
        """Default name is 'Relation'"""

        # RelationDef has lhs/rhs as type[EntityDef]; need to pass types
        class MockL(EntityDef):
            """Mock EntityDef Subclass"""

        class MockR(EntityDef):
            """Mock EntityDef Subclass"""

        rel = RelationDef(lhs=MockL, rhs=MockR)
        assert rel.name == "Relation"
        assert rel.lhs is MockL
        assert rel.rhs is MockR
