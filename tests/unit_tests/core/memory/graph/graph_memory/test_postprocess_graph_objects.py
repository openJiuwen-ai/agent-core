# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory postprocess_graph_objects"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.store.graph import (
    Entity,
    Episode,
    Relation,
)
from openjiuwen.core.memory.config.graph import AddMemStrategy
from openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects import (
    create_episode,
    parse_relation_uuids_to_remove,
    process_entities,
    process_relations,
    validate_entities_episodes,
)
from openjiuwen.core.memory.graph.graph_memory.states import EntityMerge, GraphMemState


@pytest.fixture
def mock_db():
    """Mock GraphStore with minimal async methods"""
    db = AsyncMock()
    db.query = AsyncMock(return_value=[])
    db.search = AsyncMock(return_value={})
    return db


@pytest.fixture
def sample_state():
    """GraphMemState for tests"""
    return GraphMemState(strategy=AddMemStrategy(), entity_types=[])


class TestValidateEntitiesEpisodes:
    """Tests for validate_entities_episodes"""

    @staticmethod
    def test_current_episode_entities_union():
        """current_episode.entities is union of episode entity uuids and given entity uuids"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        # Source expects current_episode.entities to be iterable of objects with .uuid for the first comparison
        ep.entities = [e1]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        validate_entities_episodes([e1, e2], ep, state)
        assert set(ep.entities) == {"e1", "e2"}

    @staticmethod
    def test_validate_entities_episodes_merge_infos_updates_episodes():
        """When merge_infos present, episodes in lookup_table get entity refs updated"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_src.episodes = ["ep1"]
        ep.entities = [e_src]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.episodes["ep1"] = ep
        state.merge_infos["tgt-uuid"] = EntityMerge(target=e_tgt, source={"src-uuid": e_src})
        validate_entities_episodes([e_tgt], ep, state)
        assert "src-uuid" not in ep.entities
        assert "tgt-uuid" in ep.entities

    @staticmethod
    def test_validate_entities_episodes_merge_episode_not_in_lookup_skipped():
        """When episode uuid not in lookup_table.episodes, that episode is skipped"""
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_src.episodes = ["ep-missing"]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.merge_infos["tgt-uuid"] = EntityMerge(target=e_tgt, source={"src-uuid": e_src})
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        ep.entities = [e_tgt]
        validate_entities_episodes([e_tgt], ep, state)
        assert "tgt-uuid" in ep.entities

    @staticmethod
    def test_validate_entities_episodes_merge_src_entity_object_in_ep_entities():
        """When ep.entities contains entity object (not just uuid), src is removed"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_src.episodes = ["ep1"]
        ep.entities = [e_src]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.episodes["ep1"] = ep
        state.merge_infos["tgt-uuid"] = EntityMerge(target=e_tgt, source={"src-uuid": e_src})
        validate_entities_episodes([e_tgt], ep, state)
        assert "src-uuid" not in ep.entities
        assert "tgt-uuid" in ep.entities

    @staticmethod
    def test_validate_entities_episodes_merge_elif_src_in_ep_entities():
        """Merge path hits elif src in ep.entities when lookup episode is not current_episode"""
        current_episode = Episode(content="cur", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        current_episode.uuid = "cur-ep"
        current_episode.entities = []
        ep_in_lookup = Episode(content="old", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep_in_lookup.uuid = "ep1"
        e_tgt = Entity(name="Tgt", content="", obj_type="Entity")
        e_tgt.uuid = "tgt-uuid"
        e_src = Entity(name="Src", content="", obj_type="Entity")
        e_src.uuid = "src-uuid"
        e_src.episodes = ["ep1"]
        ep_in_lookup.entities = [e_src]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.episodes["ep1"] = ep_in_lookup
        state.merge_infos["tgt-uuid"] = EntityMerge(target=e_tgt, source={"src-uuid": e_src})
        validate_entities_episodes([e_tgt], current_episode, state)
        assert e_src not in ep_in_lookup.entities
        assert "tgt-uuid" in ep_in_lookup.entities
        assert "src-uuid" not in ep_in_lookup.entities

    @staticmethod
    def test_validate_entities_episodes_sync_ep2e_not_e2ep_removes_from_episode():
        """When entity in episode.entities but episode not in entity.episodes, remove entity from episode"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.episodes = []
        ep.entities = ["e1"]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update_skip_embed.updated_episode = [ep]
        state.mem_update_skip_embed.updated_entity = [e1]
        validate_entities_episodes([e1], ep, state)
        assert "e1" not in ep.entities

    @staticmethod
    def test_validate_entities_episodes_sync_episode_entity_lists():
        """validate_entities_episodes syncs episode.entities and entity.episodes bidirectionally"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.episodes = ["ep1"]
        ep.entities = [e1]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update_skip_embed.updated_episode = [ep]
        validate_entities_episodes([e1], ep, state)
        assert "e1" in ep.entities
        assert "ep1" in e1.episodes

    @staticmethod
    def test_validate_entities_episodes_sync_e2ep_not_ep2e_appends_entity_to_episode():
        """Sync hits elif e2ep and not ep2e: episode.entities.append(entity.uuid)"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        ep.entities = []
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.episodes = ["ep1"]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update_skip_embed.updated_episode = [ep]
        state.mem_update_skip_embed.updated_entity = [e1]
        current_episode = Episode(content="cur", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        current_episode.uuid = "cur-ep"
        current_episode.entities = []
        validate_entities_episodes([e1], current_episode, state)
        assert "e1" in ep.entities
        assert ep.entities == ["e1"]

    @staticmethod
    def test_validate_entities_episodes_sync_dedupes_episode_entities():
        """Sync path runs episode.entities = list(set(episode.entities))"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.episodes = ["ep1"]
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        e2.episodes = []
        ep.entities = ["e1", "e2", "e1"]
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update_skip_embed.updated_episode = [ep]
        state.mem_update_skip_embed.updated_entity = [e1, e2]
        current_episode = Episode(content="cur", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        current_episode.uuid = "cur-ep"
        validate_entities_episodes([e1, e2], current_episode, state)
        assert sorted(ep.entities) == sorted(set(ep.entities))

    @staticmethod
    def test_validate_entities_episodes_sync_dedupes_each_episode():
        """Sync loop runs episode.entities = list(set(episode.entities)) for each episode"""
        ep1 = Episode(content="p1", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep1.uuid = "ep1"
        ep1.entities = ["a", "b", "a"]
        current_episode = Episode(content="cur", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        current_episode.uuid = "cur"
        current_episode.entities = ["x", "x"]
        e = Entity(name="E", content="", obj_type="Entity")
        e.uuid = "e"
        e.episodes = []
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.mem_update_skip_embed.updated_episode = [ep1]
        state.mem_update_skip_embed.updated_entity = [e]
        validate_entities_episodes([e], current_episode, state)
        assert set(ep1.entities) == {"a", "b"} and len(ep1.entities) == 2


class TestCreateEpisode:
    """Tests for create_episode"""

    @pytest.mark.asyncio
    async def test_create_episode_appends_to_mem_update(self, mock_db, sample_state):
        """create_episode appends new episode to state.mem_update.added_episode"""
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids",
            new_callable=AsyncMock,
            return_value=["new-ep-uuid"],
        ):
            ep = await create_episode(mock_db, "user-1", "content text", sample_state)
            assert ep.content == "content text"
            assert ep.user_id == "user-1"
            assert len(sample_state.mem_update.added_episode) == 1
            assert sample_state.mem_update.added_episode[0] is ep
            assert ep.uuid == "new-ep-uuid"


class TestProcessRelations:
    """Tests for process_relations"""

    @pytest.mark.asyncio
    async def test_process_relations_appends_to_added_relation(self, mock_db, sample_state):
        """process_relations appends relations to mem_update.added_relation"""
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
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids",
            new_callable=AsyncMock,
            return_value=["r-uuid-1"],
        ):
            await process_relations(mock_db, [e1, e2], [rel], sample_state)
            assert len(sample_state.mem_update.added_relation) == 1
            assert sample_state.mem_update.added_relation[0] is rel

    @pytest.mark.asyncio
    async def test_process_relations_removes_removed_relation_from_entities(self, mock_db, sample_state):
        """When state.mem_update.removed_relation is set, relations are removed from entities"""
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.relations = ["r-old", "r-keep"]
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        old_rel = Relation(
            name="R", content="x", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        old_rel.uuid = "r-old"
        sample_state.mem_update.removed_relation.add("r-old")
        sample_state.lookup_table.relations["r-old"] = old_rel
        new_rel = Relation(name="R2", content="y", lhs=e1, rhs=e2, obj_type="Relation", valid_since=0, valid_until=-1)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids",
            new_callable=AsyncMock,
            return_value=["r-new-uuid"],
        ):
            await process_relations(mock_db, [e1, e2], [new_rel], sample_state)
        assert "r-old" not in e1.relations
        assert "r-keep" in e1.relations

    @pytest.mark.asyncio
    async def test_process_relations_removes_relation_object_from_entity(self, mock_db, sample_state):
        """When entity.relations contains Relation object, it is removed when in removed_relation"""
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        old_rel = Relation(
            name="R", content="x", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        old_rel.uuid = "r-old"
        e1.relations = [old_rel]
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        sample_state.mem_update.removed_relation.add("r-old")
        sample_state.lookup_table.relations["r-old"] = old_rel
        new_rel = Relation(name="R2", content="y", lhs=e1, rhs=e2, obj_type="Relation", valid_since=0, valid_until=-1)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids",
            new_callable=AsyncMock,
            return_value=["r-new-uuid"],
        ):
            await process_relations(mock_db, [e1, e2], [new_rel], sample_state)
        assert old_rel not in e1.relations


class TestProcessEntities:
    """Tests for process_entities"""

    @pytest.mark.asyncio
    async def test_process_entities_adds_new_entity_to_added_entity(self, mock_db, sample_state):
        """New entity is appended to mem_update.added_entity"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects._resolve_entity_uuid",
            new_callable=AsyncMock,
        ):
            await process_entities(mock_db, [e1], ep, sample_state)
            assert len(sample_state.mem_update.added_entity) == 1
            assert sample_state.mem_update.added_entity[0] is e1
            assert ep.uuid in e1.episodes or ep in e1.episodes

    @pytest.mark.asyncio
    async def test_process_entities_merging_tasks_and_retrieved_entity_updated(self, mock_db, sample_state):
        """Process entities completes merging_tasks and marks retrieved entity as updated_entity"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.episodes = []
        sample_state.retrieved_entities["e1"] = e1

        async def _merge_coro():

            class MockReturn:
                """Mock class"""

                content = '{"summary": "merged"}'

            return MockReturn()

        sample_state.merging_tasks.append(asyncio.ensure_future(_merge_coro()))
        sample_state.merging_tasks_entities[sample_state.merging_tasks[0]] = e1
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects._resolve_entity_uuid",
            new_callable=AsyncMock,
        ):
            await process_entities(mock_db, [e1], ep, sample_state)
        assert e1 in sample_state.mem_update.updated_entity
        assert e1 not in sample_state.mem_update.added_entity

    @pytest.mark.asyncio
    async def test_process_entities_merging_task_entity_not_in_entities_appended(self, mock_db, sample_state):
        """When merging_tasks entity is not in passed entities list, it is appended"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        e2.episodes = []
        sample_state.retrieved_entities["e2"] = e2

        async def _merge_coro():

            class MockReturn:
                """Mock class"""

                content = '{"summary": "merged e2"}'

            return MockReturn()

        sample_state.merging_tasks.append(asyncio.ensure_future(_merge_coro()))
        sample_state.merging_tasks_entities[sample_state.merging_tasks[0]] = e2
        entities_list = [e1]
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects._resolve_entity_uuid",
            new_callable=AsyncMock,
        ):
            await process_entities(mock_db, entities_list, ep, sample_state)
        assert e2 in entities_list
        assert e2 in sample_state.mem_update.updated_entity

    @pytest.mark.asyncio
    async def test_process_entities_removes_relation_from_entity(self, mock_db, sample_state):
        """When relation is in mem_update.removed_relation, it is removed from entity.relations"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e1.relations = ["r-gone"]
        sample_state.mem_update.removed_relation.add("r-gone")
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects._resolve_entity_uuid",
            new_callable=AsyncMock,
        ):
            await process_entities(mock_db, [e1], ep, sample_state)
        assert "r-gone" not in e1.relations

    @pytest.mark.asyncio
    async def test_process_entities_resolve_entity_uuid_assigns_uuids(self, mock_db, sample_state):
        """process_entities runs _resolve_entity_uuid and new entities get unique uuids"""
        ep = Episode(content="c", obj_type="conversation", user_id="u1", created_at=0, valid_since=0)
        ep.uuid = "ep1"
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids",
            new_callable=AsyncMock,
            return_value=["resolved-uuid-1"],
        ):
            await process_entities(mock_db, [e1], ep, sample_state)
        assert e1.uuid == "resolved-uuid-1"
        assert e1 in sample_state.mem_update.added_entity


class TestParseRelationUuidsToRemove:
    """Tests for parse_relation_uuids_to_remove"""

    @pytest.mark.asyncio
    async def test_parse_relation_uuids_to_remove_extends_to_remove(self, sample_state):
        """Successful dedupe response extends state.to_remove with parsed uuids"""
        rel = Relation(
            name="R",
            content="c",
            lhs="e1",
            rhs="e2",
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
        )
        current_relations = [{"uuid": "old-r1"}]

        class MockResponse:
            content = '{"need_merging": true, "combined_content": "m", "duplicate_ids": [1]}'

        async def coro():
            return MockResponse()

        fut = asyncio.ensure_future(coro())
        tasks = [(rel, current_relations, fut)]
        await parse_relation_uuids_to_remove(tasks, sample_state)
        assert "old-r1" in sample_state.to_remove

    @pytest.mark.asyncio
    async def test_parse_relation_uuids_to_remove_exception_logged(self, sample_state):
        """When future raises or parse fails, exception is logged and no crash"""
        rel = Relation(
            name="R",
            content="c",
            lhs="e1",
            rhs="e2",
            obj_type="Relation",
            valid_since=0,
            valid_until=-1,
        )

        async def _fail_coro():
            raise ValueError("mock fail")

        fut = asyncio.ensure_future(_fail_coro())
        tasks = [(rel, [{"uuid": "x"}], fut)]
        await parse_relation_uuids_to_remove(tasks, sample_state)
        assert "x" not in sample_state.to_remove
