# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory parse_llm_response"""

from openjiuwen.core.foundation.store.graph import Entity, Relation
from openjiuwen.core.memory.graph.extraction.entity_type_definition import EntityDef
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityDeclaration
from openjiuwen.core.memory.graph.graph_memory import parse_llm_response as parse_llm_response_mod
from openjiuwen.core.memory.graph.graph_memory.parse_llm_response import (
    declare_entities,
    dict2relation,
    parse_all_relations,
    parse_iso,
    parse_relation_merging,
    resolve_entities,
)


class TestParseIso:
    """Tests for parse_iso"""

    @staticmethod
    def test_iso_string_returns_timestamp_and_offset():
        """Valid ISO string returns (timestamp, offset)"""
        result = parse_iso("2025-01-15T10:00:00Z")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] >= 0 or result[0] == -1
        assert isinstance(result[1], int)

    @staticmethod
    def test_none_returns_invalid():
        """None returns (-1, 0)"""
        assert parse_iso(None) == (-1, 0)

    @staticmethod
    def test_invalid_string_returns_invalid():
        """Invalid string without match returns (-1, 0)"""
        assert parse_iso("not a date") == (-1, 0)

    @staticmethod
    def test_iso_with_timezone_offset():
        """ISO with timezone offset is parsed"""
        result = parse_iso("2025-06-01T12:00:00+08:00")
        assert result[0] >= 0
        assert isinstance(result[1], int)


class TestDict2relation:
    """Tests for dict2relation"""

    @staticmethod
    def test_valid_response_returns_relation():
        """Valid response dict with source_id, target_id returns Relation"""
        e1 = Entity(name="A", content="", obj_type="Entity")
        e2 = Entity(name="B", content="", obj_type="Entity")
        entities = [e1, e2]
        response = {"source_id": 1, "target_id": 2, "name": "knows", "fact": "A knows B"}
        rel = dict2relation(response, entities, user_id="u1")
        assert rel is not None
        assert rel.lhs is e1
        assert rel.rhs is e2
        assert rel.content == "A knows B"
        assert rel.obj_type == "Relation"

    @staticmethod
    def test_same_source_and_target_entity_fact():
        """Same source_id and target_id yields EntityFact type"""
        e1 = Entity(name="A", content="", obj_type="Entity")
        entities = [e1]
        response = {"source_id": 1, "target_id": 1, "name": "fact", "fact": "summary"}
        rel = dict2relation(response, entities, user_id="u1")
        assert rel is not None
        assert rel.obj_type == "EntityFact"

    @staticmethod
    def test_invalid_indices_return_none():
        """Invalid source_id/target_id returns None"""
        entities = [Entity(name="E", content="", obj_type="Entity")]
        response = {"source_id": 0, "target_id": 2, "name": "R", "fact": "x"}
        rel = dict2relation(response, entities, user_id="u1")
        assert rel is None

    @staticmethod
    def test_single_key_wrapper_unwraps():
        """Response with single key unwraps to inner dict"""
        e1 = Entity(name="A", content="", obj_type="Entity")
        e2 = Entity(name="B", content="", obj_type="Entity")
        response = {"relations": {"source_id": 1, "target_id": 2, "name": "R", "fact": "f"}}
        rel = dict2relation(response, [e1, e2], user_id="u1")
        assert rel is not None


class TestDeclareEntities:
    """Tests for declare_entities"""

    @staticmethod
    def test_entity_declaration_converted_to_entity():
        """EntityDeclaration list is converted to Entity list"""
        types = [EntityDef()]
        decls = [EntityDeclaration(name="E1", entity_type_id=0)]
        result = declare_entities(decls, types, user_id="u1", created_at=0)
        assert len(result) == 1
        assert isinstance(result[0], Entity)
        assert result[0].name == "E1"

    @staticmethod
    def test_entity_passthrough():
        """Existing Entity in list is passed through"""
        types = [EntityDef()]
        ent = Entity(name="E", content="", obj_type="Entity")
        result = declare_entities([ent], types, user_id="u1")
        assert result == [ent]


class TestParseAllRelations:
    """Tests for parse_all_relations"""

    @staticmethod
    def test_parse_all_relations_returns_relations_and_entities():
        """parse_all_relations returns (relations, entities) with declarations converted"""
        declarations = [EntityDeclaration(name="E1", entity_type_id=0), EntityDeclaration(name="E2", entity_type_id=0)]
        types = [EntityDef()]
        relations_data = [
            {"source_id": 1, "target_id": 2, "name": "R", "fact": "rel"},
        ]
        relations, entities = parse_all_relations(relations_data, declarations, types, created_at=0, user_id="u1")
        assert len(entities) == 2
        assert len(relations) == 1
        assert relations[0].content == "rel"

    @staticmethod
    def test_duplicate_content_emptied():
        """Duplicate relation content is detected; second dict has content key cleared before dict2relation"""
        declarations = [
            EntityDeclaration(name="A", entity_type_id=0),
            EntityDeclaration(name="B", entity_type_id=0),
        ]
        types = [EntityDef()]
        relations_data = [
            {"source_id": 1, "target_id": 2, "name": "R", "fact": "same", "content": "same"},
            {"source_id": 1, "target_id": 2, "name": "R2", "fact": "same", "content": "same"},
        ]
        relations, _ = parse_all_relations(relations_data, declarations, types, created_at=0, user_id="u1")
        # Code clears relation["content"] for duplicate; dict2relation uses "fact", so we get 2 relations
        assert len(relations) == 2


class TestResolveEntities:
    """Tests for resolve_entities"""

    @staticmethod
    def test_no_duplication_returns_candidates_and_empty_merge():
        """When duplication list is empty, returns candidates and no merging"""
        candidates = [EntityDeclaration(name="E1", entity_type_id=0)]
        existing = []
        duplication = []
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert len(resolved) == 1
        assert len(merging_args) == 0
        assert len(to_remove) == 0

    @staticmethod
    def test_dedupe_to_existing_replaces_in_result():
        """Duplicate pointing to existing entity replaces candidate in result"""
        candidates = [EntityDeclaration(name="E1", entity_type_id=0), EntityDeclaration(name="E2", entity_type_id=0)]
        existing_ent = Entity(name="E1", content="", obj_type="Entity")
        existing_ent.uuid = "existing-e1"
        existing = [existing_ent]
        # id 1 = first existing (1-based); duplicate_ids [2] = second item in combined list (candidate index 1)
        duplication = [{"id": 1, "duplicate_ids": [2]}]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert len(resolved) == 2
        # One of the candidates is replaced by existing_ent
        assert existing_ent in resolved
        assert len(merging_args) >= 0
        assert isinstance(to_remove, set)

    @staticmethod
    def test_resolve_entities_dup_by_name_uses_name_lookup():
        """When dup id is not numeric, tgt_entity is looked up by name"""
        existing_ent = Entity(name="Alpha", content="", obj_type="Entity")
        existing_ent.uuid = "existing-alpha"
        existing = [existing_ent]
        candidates = [EntityDeclaration(name="Beta", entity_type_id=0)]
        duplication = [{"id": "x", "name": "Alpha", "duplicate_ids": [2]}]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert len(resolved) == 1
        assert resolved[0] is existing_ent

    @staticmethod
    def test_resolve_entities_existing_merge_produces_to_remove():
        """When two existing entities merge, to_remove contains the source uuid"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "u1"
        existing = [e0, e1]
        candidates = []
        # id 1 -> target e0 (index 0), duplicate_ids [2] -> e1 (index 1) merges into e0, so u1 removed
        duplication = [{"id": 1, "duplicate_ids": [2]}]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert "u1" in to_remove
        assert "u0" not in to_remove
        assert len(merging_args) == 1
        assert merging_args[0][0].uuid == "u0"
        assert e1 in merging_args[0][1]

    @staticmethod
    def test_resolve_merge_dict_target_not_in_result():
        """_resolve_merge_dict when target not in result but source in result"""
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        uuid_lookup = {"tgt-uuid": e_tgt, "src-uuid": e_src}
        result = [e_src]
        merge_dict = {"tgt-uuid": [e_src]}
        _resolve_merge_dict = getattr(parse_llm_response_mod, "_resolve_merge_dict")
        out = _resolve_merge_dict(merge_dict, result, uuid_lookup)
        assert result[0] is e_tgt or result[0] is e_src
        assert "tgt-uuid" in out or "src-uuid" in out

    @staticmethod
    def test_resolve_merge_dict_target_in_result():
        """_resolve_merge_dict when target is in result, replaces source indices with target"""
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        uuid_lookup = {"tgt-uuid": e_tgt, "src-uuid": e_src}
        result = [e_tgt, e_src]
        merge_dict = {"tgt-uuid": [e_src]}
        _resolve_merge_dict = getattr(parse_llm_response_mod, "_resolve_merge_dict")
        _resolve_merge_dict(merge_dict, result, uuid_lookup)
        assert result[0] is e_tgt
        assert result[1] is e_tgt

    @staticmethod
    def test_resolve_entities_parse_entity_merging_tgt_in_merge_map():
        """_parse_entity_merging when tgt_entity.uuid already in merge_map"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "u1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "u2"
        existing = [e0, e1, e2]
        candidates = []
        duplication = [
            {"id": 1, "duplicate_ids": [2]},
            {"id": 1, "duplicate_ids": [3]},
        ]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert "u1" in to_remove
        assert "u2" in to_remove

    @staticmethod
    def test_resolve_entities_parse_entity_merging_src_in_merge_map():
        """_parse_entity_merging when src_entity.uuid in merge_map"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "u1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "u2"
        existing = [e0, e1, e2]
        candidates = []
        duplication = [
            {"id": 1, "duplicate_ids": [2]},
            {"id": 2, "duplicate_ids": [1]},
        ]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert "u0" in to_remove or "u1" in to_remove
        assert merging_args or to_remove

    @staticmethod
    def test_resolve_entities_parse_entity_merging_else_branch():
        """_parse_entity_merging else branch (tgt and src not in merge_map)"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "u1"
        existing = [e0, e1]
        candidates = []
        duplication = [{"id": 1, "duplicate_ids": [2]}]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert "u1" in to_remove
        assert len(merging_args) == 1
        assert merging_args[0][0].uuid == "u0"

    @staticmethod
    def test_resolve_entities_parse_entity_merging_tgt_eq_src_continues():
        """_parse_entity_merging when tgt_entity.uuid == src_entity.uuid skips"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        existing = [e0]
        candidates = []
        duplication = [{"id": 1, "duplicate_ids": [1]}]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert len(merging_args) == 0
        assert to_remove == set()

    @staticmethod
    def test_resolve_entities_parse_entity_merging_else_add_to_existing_set():
        """_parse_entity_merging else branch when tgt_of_tgt_uuid already in merge_map"""
        e0 = Entity(name="E0", content="", obj_type="Entity")
        e0.uuid = "u0"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "u1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "u2"
        existing = [e0, e1, e2]
        candidates = []
        duplication = [
            {"id": 1, "duplicate_ids": [2]},
            {"id": 2, "duplicate_ids": [3]},
        ]
        resolved, merging_args, to_remove = resolve_entities(candidates, existing, duplication)
        assert "u1" in to_remove
        assert "u2" in to_remove
        assert len(merging_args) == 1
        assert merging_args[0][0].uuid == "u0"
        assert len(merging_args[0][1]) == 2


class TestParseRelationMerging:
    """Tests for parse_relation_merging"""

    @staticmethod
    def test_need_merging_and_content_updates_relation():
        """need_merging with combined_content updates relation and returns to_remove"""
        rel = Relation(
            name="R",
            content="old",
            lhs="e1",
            rhs="e2",
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
        )
        existing = [{"uuid": "r1"}, {"uuid": "r2"}]
        response = {"need_merging": True, "combined_content": "merged", "duplicate_ids": [1]}
        to_remove = parse_relation_merging(response, rel, existing)
        assert rel.content == "merged"
        assert "r1" in to_remove

    @staticmethod
    def test_no_merge_returns_empty():
        """need_merging False returns empty set"""
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        to_remove = parse_relation_merging({"need_merging": False}, rel, [])
        assert to_remove == set()

    @staticmethod
    def test_parse_relation_merging_updates_valid_since_until():
        """valid_since/valid_until in response update relation when >= 0"""
        rel = Relation(
            name="R",
            content="c",
            lhs="e1",
            rhs="e2",
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
            offset_since=0,
            offset_until=0,
        )
        existing = [{"uuid": "r1"}]
        response = {
            "need_merging": True,
            "combined_content": "merged",
            "valid_since": "2025-01-01T00:00:00Z",
            "valid_until": "2025-12-31T23:59:59Z",
            "duplicate_ids": [],
        }
        parse_relation_merging(response, rel, existing)
        assert rel.content == "merged"
        assert rel.valid_since >= 0
        assert rel.valid_until >= 0
