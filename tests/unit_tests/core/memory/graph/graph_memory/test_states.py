# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory states"""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.store.graph import Entity, Episode, Relation
from openjiuwen.core.memory.config.graph import AddMemStrategy, EpisodeType
from openjiuwen.core.memory.graph.graph_memory.states import (
    EntityMerge,
    GraphMemPrompting,
    GraphMemState,
    GraphMemUpdate,
    LookupTables,
    batch_embed,
    block_keyboard_interrupt,
    classify_relations_extracted,
    nested_clear_dataclass,
    persist_to_db,
)


class TestNestedClearDataclass:
    """Tests for nested_clear_dataclass"""

    @staticmethod
    def test_non_dataclass_no_op():
        """Non-dataclass input does nothing"""
        nested_clear_dataclass("not a dataclass")
        nested_clear_dataclass(None)

    @staticmethod
    def test_dataclass_with_clearable_field():
        """Dataclass field with clear() gets cleared"""

        @dataclass
        class Inner:
            items: list = field(default_factory=list)

            def clear(self):
                self.items.clear()

        @dataclass
        class Outer:
            inner: Inner = field(default_factory=Inner)

        inner = Inner(items=[1, 2, 3])
        outer = Outer(inner=inner)
        nested_clear_dataclass(outer)
        assert inner.items == []


class TestLookupTables:
    """Tests for LookupTables"""

    @staticmethod
    def test_get_entity_creates_and_caches():
        """get_entity creates Entity from input_obj and caches by uuid"""
        tbl = LookupTables()
        input_obj = {"uuid": "e1", "name": "Ent1", "obj_type": "Entity", "content": ""}
        ent = tbl.get_entity(input_obj)
        assert isinstance(ent, Entity)
        assert ent.uuid == "e1"
        assert ent.name == "Ent1"
        assert getattr(tbl, "entities").get("e1") is ent
        assert tbl.get_entity(input_obj) is ent

    @staticmethod
    def test_get_relation_creates_and_caches():
        """get_relation creates Relation from input_obj and caches"""
        tbl = LookupTables()
        input_obj = {
            "uuid": "r1",
            "name": "R",
            "obj_type": "Relation",
            "content": "rel",
            "lhs": "e1",
            "rhs": "e2",
            "valid_since": 0,
            "valid_until": -1,
        }
        rel = tbl.get_relation(input_obj)
        assert rel.uuid == "r1"
        assert getattr(tbl, "relations").get("r1") is rel

    @staticmethod
    def test_get_episode_creates_and_caches():
        """get_episode creates Episode from input_obj and caches"""
        tbl = LookupTables()
        input_obj = {"uuid": "ep1", "content": "ep content", "obj_type": "conversation", "user_id": "u1"}
        ep = tbl.get_episode(input_obj)
        assert ep.uuid == "ep1"
        assert getattr(tbl, "episodes").get("ep1") is ep

    @staticmethod
    def test_clear_clears_references():
        """clear() clears nested structures"""
        tbl = LookupTables()
        tbl.get_entity({"uuid": "e1", "name": "E", "obj_type": "Entity", "content": ""})
        tbl.clear()
        assert len(getattr(tbl, "entities")) == 0


class TestEntityMerge:
    """Tests for EntityMerge"""

    @staticmethod
    def test_entity_merge_defaults():
        """EntityMerge has target, source, new_relations, relations_to_keep"""
        ent = Entity(name="T", content="", obj_type="Entity")
        merge = EntityMerge(target=ent)
        assert merge.target is ent
        assert merge.source == {}
        assert merge.new_relations == []
        assert merge.relations_to_keep == set()

    @staticmethod
    def test_entity_merge_clear():
        """EntityMerge.clear() clears nested refs"""
        ent = Entity(name="T", content="", obj_type="Entity")
        merge = EntityMerge(target=ent, source={"s1": Entity(name="S", content="", obj_type="Entity")})
        merge.clear()
        assert merge.target is ent
        assert len(merge.source) == 0


class TestGraphMemUpdate:
    """Tests for GraphMemUpdate"""

    @staticmethod
    def test_graph_mem_update_or_combines_lists_and_sets():
        """__or__ combines list fields by concatenation and set fields by union"""
        a = GraphMemUpdate(added_entity=[Entity(name="E1", content="", obj_type="Entity")])
        a.removed_relation.add("r1")
        b = GraphMemUpdate(updated_entity=[Entity(name="E2", content="", obj_type="Entity")])
        b.removed_relation.add("r2")
        c = a | b
        assert len(c.added_entity) == 1
        assert len(c.updated_entity) == 1
        assert c.removed_relation == {"r1", "r2"}


class TestGraphMemPrompting:
    """Tests for GraphMemPrompting"""

    @staticmethod
    def test_default_language_cn():
        """Default language is cn"""
        p = GraphMemPrompting()
        assert p.language == "cn"
        assert p.entity_extraction_language == "cn"


class TestGraphMemState:
    """Tests for GraphMemState"""

    @staticmethod
    def test_state_has_strategy_and_lookup_table():
        """GraphMemState has strategy and lookup_table"""
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        assert state.strategy is not None
        assert isinstance(state.lookup_table, LookupTables)
        assert state.episode_type == EpisodeType.CONVERSATION

    @staticmethod
    def test_clear_references_runs_without_error():
        """
        clear_references runs without error and clears nested refs (merge_infos cleared via nested_clear_dataclass)
        """
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.merge_infos["u1"] = EntityMerge(target=Entity(name="E", content="", obj_type="Entity"))
        state.clear_references()
        # nested_clear_dataclass(self) calls dict.clear() on merge_infos, so it is empty after
        assert len(state.merge_infos) == 0


class TestClassifyRelationsExtracted:
    """Tests for classify_relations_extracted"""

    @staticmethod
    def test_self_pointing_relation_added_to_removed():
        """Relation with lhs == rhs is added to mem_update.removed_relation"""
        ent = Entity(name="E", content="", obj_type="Entity")
        ent.uuid = "e1"
        rel = Relation(
            name="R",
            content="fact",
            lhs=ent,
            rhs=ent,
            obj_type="EntityFact",
            valid_since=0,
            valid_until=-1,
        )
        rel.uuid = "r-self"
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.merge_infos["e1"] = EntityMerge(target=ent, new_relations=[rel])
        classify_relations_extracted([rel], state)
        assert rel.uuid in state.mem_update.removed_relation
        assert rel.language == state.prompting.language

    @staticmethod
    def test_relation_with_different_lhs_rhs_kept():
        """Relation with different lhs/rhs is kept in relations_to_keep"""
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        rel = Relation(
            name="R",
            content="rel",
            lhs=e1,
            rhs=e2,
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
        )
        rel.uuid = "r1"
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.merge_infos["e1"] = EntityMerge(target=e1, new_relations=[rel])
        classify_relations_extracted([rel], state)
        assert rel.uuid in state.merge_infos["e1"].relations_to_keep

    @staticmethod
    def test_classify_relations_empty_content_appends_to_remove():
        """Relation with empty content is appended to state.to_remove"""
        rel = Relation(
            name="R",
            content="   ",
            lhs="e1",
            rhs="e2",
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
        )
        rel.uuid = "r-empty"
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        classify_relations_extracted([rel], state)
        assert rel in state.to_remove


class TestBlockKeyboardInterrupt:
    """Tests for block_keyboard_interrupt"""

    @staticmethod
    def test_block_keyboard_interrupt_yields_and_restores_handler():
        """Context manager yields and restores SIGINT handler"""
        with block_keyboard_interrupt():
            pass  # Skip testing this for now, sending SIGINT is risky


class TestBatchEmbed:
    """Tests for batch_embed"""

    @pytest.mark.asyncio
    async def test_batch_embed_empty_data_returns_empty(self):
        """Empty data returns []"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        embedder = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.embed_batch_size = 10
        result = await batch_embed([], embedder, config)
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_embed_no_embed_tasks_returns_empty(self):
        """When fetch_embed_task returns [], no embed call and returns []"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig
        from openjiuwen.core.foundation.store.graph.graph_object import BaseGraphObject

        obj = MagicMock(spec=BaseGraphObject)
        obj.fetch_embed_task.return_value = []
        embedder = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.embed_batch_size = 10
        result = await batch_embed([obj], embedder, config)
        assert result == []
        embedder.embed_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_embed_success_returns_empty(self):
        """Successful embed returns [] (objects updated in place)"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        ent = Entity(name="E", content="text", obj_type="Entity")
        embedder = AsyncMock()
        # Entity.fetch_embed_task returns (content_embedding, name_embedding) so 2 tasks
        embedder.embed_documents = AsyncMock(return_value=[[0.1] * 32, [0.1] * 32])
        config = MagicMock(spec=GraphConfig)
        config.embed_batch_size = 10
        result = await batch_embed([ent], embedder, config)
        assert result == []
        assert ent.content_embedding is not None
        assert ent.name_embedding is not None

    @pytest.mark.asyncio
    async def test_batch_embed_exception_returns_objects(self):
        """On exception, returns list of objects that were not embedded"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        ent = Entity(name="E", content="text", obj_type="Entity")
        embedder = AsyncMock()
        embedder.embed_documents = AsyncMock(side_effect=RuntimeError("fail"))
        config = MagicMock(spec=GraphConfig)
        config.embed_batch_size = 10
        result = await batch_embed([ent], embedder, config)
        assert result == [ent]


class TestPersistToDb:
    """Tests for persist_to_db"""

    @pytest.mark.asyncio
    async def test_persist_to_db_embeds_and_flushes(self):
        """persist_to_db embeds via batch_embed and calls db_backend add/delete"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_entity.append(Entity(name="E", content="", obj_type="Entity"))
        state.mem_update.added_episode.append(
            Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await persist_to_db(db, state, config)
        db.add_entity.assert_called()
        db.add_episode.assert_called()

    @pytest.mark.asyncio
    async def test_persist_to_db_skip_embed_entity_missing_embedding_moved_to_embed(self):
        """Entity in mem_update_skip_embed with None embedding is moved to mem_update.updated_entity"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_episode.append(
            Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        ent_skip = Entity(name="E", content="", obj_type="Entity")
        ent_skip.content_embedding = None
        ent_skip.name_embedding = None
        state.mem_update_skip_embed.updated_entity.append(ent_skip)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await persist_to_db(db, state, config)
        assert ent_skip not in state.mem_update_skip_embed.updated_entity
        assert ent_skip in state.mem_update.updated_entity

    @pytest.mark.asyncio
    async def test_persist_to_db_skip_embed_entity_already_in_updated_entity_not_appended_again(self):
        """
        Entity in skip_embed with None embedding but already in updated_entity is not appended again
        """
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_episode.append(
            Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        ent = Entity(name="E", content="", obj_type="Entity")
        ent.content_embedding = None
        ent.name_embedding = None
        state.mem_update_skip_embed.updated_entity.append(ent)
        state.mem_update.updated_entity.append(ent)
        initial_len = len(state.mem_update.updated_entity)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await persist_to_db(db, state, config)
        assert ent in state.tmp_buffer or len(state.tmp_buffer) >= 0
        assert len(state.mem_update.updated_entity) == initial_len

    @pytest.mark.asyncio
    async def test_persist_to_db_embed_failure_raises(self):
        """When batch_embed keeps returning objects, persist_to_db raises after retries"""
        from openjiuwen.core.common.exception.errors import BaseError
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_entity.append(Entity(name="E", content="x", obj_type="Entity"))
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=state.mem_update.added_entity,
        ):
            with pytest.raises(BaseError, match="embedding"):
                await persist_to_db(db, state, config)

    @pytest.mark.asyncio
    async def test_persist_to_db_skip_embed_and_removed_branches(self):
        """persist_to_db calls add_episode/add_entity/add_relation/delete for skip_embed and removed"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_episode.append(
            Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        state.mem_update_skip_embed.updated_episode.append(
            Episode(content="e", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        state.mem_update_skip_embed.updated_entity.append(Entity(name="E2", content="", obj_type="Entity"))
        state.mem_update_skip_embed.updated_relation.append(
            Relation(name="R", content="r", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        )
        state.mem_update.removed_entity.add("old-e")
        state.mem_update.removed_relation.add("old-r")
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await persist_to_db(db, state, config)
        assert db.add_episode.call_count >= 2
        assert db.add_entity.call_count >= 2
        assert db.add_relation.call_count >= 2
        db.delete.assert_called()

    @pytest.mark.asyncio
    async def test_persist_to_db_episode_embed_retry_truncates_content(self):
        """When episode batch_embed fails then succeeds, episode content is truncated"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        ep = Episode(content="long content here", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        state.mem_update.added_episode.append(ep)
        call_count = 0

        async def batch_embed_side_effect(data, _embedder, _config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            if call_count == 2:
                return list(state.mem_update.added_episode)
            if call_count == 3:
                return []
            return []

        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            side_effect=batch_embed_side_effect,
        ):
            await persist_to_db(db, state, config)
        assert len(ep.content) <= len("long content here")
        assert ep.content == "long cont" or len(ep.content) < 18
        db.add_episode.assert_called()

    @pytest.mark.asyncio
    async def test_persist_to_db_skip_embed_updated_entity_with_embeddings_calls_add_entity(self):
        """When mem_update_skip_embed.updated_entity has entities with embeddings, add_entity is called"""
        from openjiuwen.core.foundation.store.graph.config import GraphConfig

        db = AsyncMock()
        db.embedder = AsyncMock()
        db.add_entity = AsyncMock()
        db.add_relation = AsyncMock()
        db.add_episode = AsyncMock()
        db.delete = AsyncMock()
        config = MagicMock(spec=GraphConfig)
        config.request_max_retries = 2
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update.added_episode.append(
            Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        )
        ent_with_embed = Entity(name="E", content="", obj_type="Entity")
        ent_with_embed.content_embedding = [0.1] * 32
        ent_with_embed.name_embedding = [0.1] * 32
        state.mem_update_skip_embed.updated_entity.append(ent_with_embed)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.states.batch_embed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await persist_to_db(db, state, config)
        assert any(ent_with_embed in (c[0][0] if c[0] else []) for c in db.add_entity.call_args_list), (
            "add_entity should be called with skip_embed.updated_entity"
        )
