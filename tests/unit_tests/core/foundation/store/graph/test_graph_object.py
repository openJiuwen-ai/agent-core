# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph objects."""

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.graph.graph_object import (
    BaseGraphObject,
    Entity,
    Episode,
    NamedGraphObject,
    Relation,
)


class TestBaseGraphObject:
    """Tests for BaseGraphObject."""

    @staticmethod
    def test_defaults():
        """Defaults: user_id='default_user', obj_type='', language='cn', metadata={}, content=''."""
        obj = BaseGraphObject()
        assert obj.user_id == "default_user"
        assert obj.obj_type == ""
        assert obj.language == "cn"
        assert obj.metadata == {}
        assert obj.content == ""

    @staticmethod
    def test_uuid_and_created_at_default_factory():
        """uuid and created_at default_factory: non-empty, int."""
        obj = BaseGraphObject()
        assert len(obj.uuid) == 32
        assert isinstance(obj.created_at, int)
        assert obj.created_at > 0

    @staticmethod
    def test_language_pattern_cn_en_only():
        """language pattern: only 'cn' or 'en' allowed."""
        obj = BaseGraphObject(language="cn")
        assert obj.language == "cn"
        obj2 = BaseGraphObject(language="en")
        assert obj2.language == "en"
        with pytest.raises(ValidationError):
            BaseGraphObject(language="fr")

    @staticmethod
    def test_version_property_returns_one():
        """version property returns 1."""
        obj = BaseGraphObject()
        assert obj.version == 1

    @staticmethod
    def test_fetch_embed_task_returns_content_embedding():
        """fetch_embed_task() returns [(self, 'content_embedding', self.content)]."""
        obj = BaseGraphObject(content="hello")
        tasks = obj.fetch_embed_task()
        assert len(tasks) == 1
        assert tasks[0][0] is obj
        assert tasks[0][1] == "content_embedding"
        assert tasks[0][2] == "hello"

    @staticmethod
    def test_model_validator_metadata_none_becomes_dict():
        """Model validator: None metadata -> dict."""
        obj = BaseGraphObject()
        assert obj.metadata is not None
        assert obj.metadata == {}


class TestNamedGraphObject:
    """Tests for NamedGraphObject."""

    @staticmethod
    def test_inherits_base_name_optional_default_empty():
        """Inherits BaseGraphObject; name optional, default empty string."""
        obj = NamedGraphObject()
        assert obj.name == ""
        obj2 = NamedGraphObject(name="foo")
        assert obj2.name == "foo"


class TestEntity:
    """Tests for Entity."""

    @staticmethod
    def test_defaults():
        """Defaults: obj_type='Entity', relations=[], episodes=[], attributes={}."""
        ent = Entity()
        assert ent.obj_type == "Entity"
        assert ent.relations == []
        assert ent.episodes == []
        assert ent.attributes == {}

    @staticmethod
    def test_fetch_embed_task_content_and_name_embedding():
        """fetch_embed_task() returns content_embedding and name_embedding tasks."""
        ent = Entity(content="c", name="n")
        tasks = ent.fetch_embed_task()
        assert len(tasks) == 2
        assert tasks[0][1] == "content_embedding" and tasks[0][2] == "c"
        assert tasks[1][1] == "name_embedding" and tasks[1][2] == "n"

    @staticmethod
    def test_serialize_relations_episodes_sorted_uuids():
        """Serialization of relations/episodes: list of objects/str -> sorted list of uuid strings."""
        other = Entity()
        other.uuid = "aaa"
        ent = Entity(relations=[other, "bbb"], episodes=["ep1", "ep2"])
        # Via model_dump with mode='json' or serialization
        dumped = ent.model_dump(mode="json")
        assert sorted(dumped["relations"]) == ["aaa", "bbb"]
        assert sorted(dumped["episodes"]) == ["ep1", "ep2"]


class TestRelation:
    """Tests for Relation."""

    @staticmethod
    def test_required_lhs_rhs():
        """Required: lhs, rhs (Entity or str)."""
        with pytest.raises(ValidationError):
            Relation()
        rel = Relation(lhs="id1", rhs="id2")
        assert rel.lhs == "id1"
        assert rel.rhs == "id2"

    @staticmethod
    def test_defaults():
        """Defaults: obj_type='Relation', valid_until=-1, etc. (valid_since set by validator to created_at)."""
        rel = Relation(lhs="l", rhs="r")
        assert rel.obj_type == "Relation"
        assert rel.valid_until == -1
        # valid_since=-1 is replaced by model validator with created_at
        assert rel.valid_since == rel.created_at

    @staticmethod
    def test_valid_since_set_to_created_at():
        """Model validator: valid_since=-1 -> set to created_at."""
        rel = Relation(lhs="l", rhs="r")
        assert rel.valid_since == rel.created_at

    @staticmethod
    def test_update_connected_entities_adds_self_to_lhs_rhs():
        """update_connected_entities(): adds self to lhs/rhs .relations if not already present."""
        e1 = Entity()
        e2 = Entity()
        rel = Relation(lhs=e1, rhs=e2)
        rel.update_connected_entities()
        assert rel in e1.relations
        assert rel in e2.relations

    @staticmethod
    def test_update_connected_entities_idempotent():
        """update_connected_entities() does not duplicate if already present."""
        e1 = Entity()
        rel = Relation(lhs=e1, rhs="r")
        rel.update_connected_entities()
        rel.update_connected_entities()
        assert e1.relations.count(rel) == 1

    @staticmethod
    def test_serialize_lhs_rhs_entity_to_uuid():
        """Serialization of lhs/rhs: Entity -> uuid string."""
        e = Entity()
        e.uuid = "entity-uuid-1"
        rel = Relation(lhs=e, rhs="str-id")
        dumped = rel.model_dump(mode="json")
        assert dumped["lhs"] == "entity-uuid-1"
        assert dumped["rhs"] == "str-id"


class TestEpisode:
    """Tests for Episode."""

    @staticmethod
    def test_no_name_obj_type_episode_entities_default():
        """No name; obj_type='Episode'; entities=[]."""
        ep = Episode()
        assert not hasattr(ep, "name") or getattr(ep, "name", None) is None
        assert ep.obj_type == "Episode"
        assert ep.entities == []

    @staticmethod
    def test_serialize_entities_sorted_uuids():
        """Serialization of entities: list to sorted list of uuid strings."""
        ep = Episode(entities=["z-id", "a-id"])
        dumped = ep.model_dump(mode="json")
        assert dumped["entities"] == ["a-id", "z-id"]
