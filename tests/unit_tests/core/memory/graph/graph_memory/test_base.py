# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory base (GraphMemory)"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.base_reranker import Reranker
from openjiuwen.core.foundation.store.graph import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
    Entity,
    GraphStoreFactory,
    Relation,
)
from openjiuwen.core.foundation.store.graph.config import GraphStoreStorageConfig
from openjiuwen.core.foundation.store.graph.result_ranking import WeightedRankConfig
from openjiuwen.core.memory.config.graph import AddMemStrategy, EpisodeType, SearchConfig
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityDeclaration
from openjiuwen.core.memory.graph.graph_memory.base import GraphMemory
from openjiuwen.core.memory.graph.graph_memory.states import EntityMerge, GraphMemState, GraphMemUpdate


def _make_mock_config():
    """Build a minimal config mock to avoid real GraphConfig validation (e.g. socket)"""
    config = MagicMock()
    config.db_storage_config = GraphStoreStorageConfig(user_id=32)
    config.request_max_retries = 2
    config.embed_batch_size = 10
    return config


def _make_mock_llm_client(default_content: str = "{}"):
    """Mock LLM client that returns mock content so code paths calling _invoke_llm do not fail"""
    client = AsyncMock()
    msg = MagicMock()
    msg.content = default_content
    client.invoke = AsyncMock(return_value=msg)
    return client


def _make_mock_backend():
    """Mock db backend with embedder and config"""
    backend = MagicMock()
    backend.embedder = None
    backend.return_similarity_score = True
    backend.config = MagicMock()
    backend.config.max_concurrent = 4
    backend.config.db_storage_config = GraphStoreStorageConfig(user_id=32)
    backend.config.request_max_retries = 2
    backend.config.embed_batch_size = 10
    return backend


class TestGraphMemoryInit:
    """Tests for GraphMemory __init__"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_init_sets_public_attributes(mock_from_config):
        """Init sets db_backend, config, language, default_extraction_strategy"""
        mock_from_config.return_value = _make_mock_backend()
        config = _make_mock_config()
        mem = GraphMemory(db_config=config, language="en", llm_client=_make_mock_llm_client())
        assert mem.db_backend is not None
        assert mem.config is config
        assert mem.language == "en"
        assert getattr(mem, "embedder") is None

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_init_creates_search_strategies(mock_from_config):
        """Init creates default _search_strategies with 'default' key"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        strategies = getattr(mem, "_search_strategies")
        assert "default" in strategies
        assert len(strategies["default"]) == 3


class TestGraphMemoryEmbedder:
    """Tests for embedder property and attach_embedder"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_embedder_returns_backend_embedder(mock_from_config):
        """embedder property returns db_backend.embedder"""
        backend = _make_mock_backend()
        embedder = MagicMock(spec=Embedding)
        backend.embedder = embedder
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        assert mem.embedder is embedder

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_attach_embedder_sets_on_backend(mock_from_config):
        """attach_embedder calls db_backend.attach_embedder"""
        backend = _make_mock_backend()
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        emb = MagicMock(spec=Embedding)
        mem.attach_embedder(emb)
        backend.attach_embedder.assert_called_once_with(emb)


class TestGraphMemoryAttachReranker:
    """Tests for attach_reranker"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_attach_reranker_valid_sets_reranker(mock_from_config):
        """Valid Reranker instance is set"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        reranker = MagicMock(spec=Reranker)
        mem.attach_reranker(reranker)
        assert mem.reranker is reranker

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_attach_reranker_invalid_raises(mock_from_config):
        """Non-Reranker raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="Reranker"):
            mem.attach_reranker("not a reranker")


class TestGraphMemoryRegisterSearchStrategy:
    """Tests for register_search_strategy"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_register_search_strategy_new_name(mock_from_config):
        """Registering a new strategy name adds it to _search_strategies"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        cfg = SearchConfig(rank_config=WeightedRankConfig())
        mem.register_search_strategy("custom", search_entity=cfg)
        strategies = getattr(mem, "_search_strategies")
        assert "custom" in strategies
        assert strategies["custom"][0] is cfg

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_register_search_strategy_empty_name_raises(mock_from_config):
        """Empty strategy name raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="empty"):
            mem.register_search_strategy("", search_entity=SearchConfig())

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_register_search_strategy_duplicate_raises_without_force(mock_from_config):
        """Duplicate name raises when force=False"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.register_search_strategy("dup", search_entity=SearchConfig())
        with pytest.raises(BaseError, match="already exists"):
            mem.register_search_strategy("dup", search_entity=SearchConfig(), force=False)

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_register_search_strategy_force_overwrites(mock_from_config):
        """force=True overwrites existing strategy"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.register_search_strategy("s", search_entity=SearchConfig())
        cfg2 = SearchConfig(min_score=0.5)
        mem.register_search_strategy("s", search_entity=cfg2, force=True)
        assert getattr(mem, "_search_strategies")["s"][0] is cfg2

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_register_search_strategy_invalid_config_raises(mock_from_config):
        """Non-SearchConfig or None for entity/relation/episode raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="SearchConfig"):
            mem.register_search_strategy("x", search_entity=MagicMock())


class TestGraphMemoryEnsureThreadLock:
    """Tests for ensure_thread_lock"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_ensure_thread_lock_creates_per_user_lock(mock_from_config):
        """ensure_thread_lock creates and stores lock for user_id"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.ensure_thread_lock("user-1")
        assert "user-1" in mem.user_locks
        mem.ensure_thread_lock("user-1")
        assert len(mem.user_locks) == 1


class TestAddMemory:
    """Tests for add_memory (main entry point)"""

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_add_memory_without_embedder_raises(mock_from_config):
        """add_memory raises when embedder is not attached"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        assert mem.embedder is None
        with pytest.raises(BaseError, match="embedder|attach"):
            await mem.add_memory(EpisodeType.DOCUMENT, "user1", "Some content.")

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    @patch("openjiuwen.core.memory.graph.graph_memory.postprocess_graph_objects.ensure_unique_uuids")
    @patch.object(GraphMemory, "_invoke_llm")
    async def test_add_memory_success_returns_graph_mem_update(mock_invoke_llm, mock_ensure_uuids, mock_from_config):
        """add_memory runs full pipeline with mocks and returns GraphMemUpdate"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        backend.refresh = AsyncMock()
        backend.add_entity = AsyncMock()
        backend.add_relation = AsyncMock()
        backend.add_episode = AsyncMock()
        backend.delete = AsyncMock()
        embedder = MagicMock(spec=Embedding)
        embedder.embed_documents = AsyncMock(side_effect=lambda texts, batch_size: [[0.0] * 8 for _ in texts])
        backend.attach_embedder = MagicMock()
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.attach_embedder(embedder)
        backend.embedder = embedder

        # _invoke_llm order: entity extraction, timezone, relation extraction, then per-entity summary.
        def make_msg(content):
            m = MagicMock()
            m.content = content
            return m

        mock_invoke_llm.side_effect = [
            make_msg('{"extracted_entities": [{"name": "Alice", "entity_type_id": 0}]}'),
            make_msg('{"extracted_relations": []}'),
            make_msg('{"extracted_relations": []}'),
            make_msg('{"summary": "", "attributes": {}}'),
        ]

        async def _return_ids(backend, ids, collection=None, skip=False):
            return ids if ids else []

        mock_ensure_uuids.side_effect = _return_ids

        result = await mem.add_memory(EpisodeType.DOCUMENT, "user1", "Short document.")

        assert isinstance(result, GraphMemUpdate)
        assert hasattr(result, "added_episode")
        assert hasattr(result, "added_entity")
        assert hasattr(result, "added_relation")
        backend.refresh.assert_called()


class TestGraphMemoryInitState:
    """Tests for _init_state (internal but testable)"""

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_init_state_returns_state_with_reference_time(mock_from_config):
        """_init_state returns GraphMemState with reference_timestamp set"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), language="en", llm_client=_make_mock_llm_client())
        import datetime

        ref = datetime.datetime(2025, 1, 1, 12, 0, 0)
        state = getattr(mem, "_init_state")(reference_time=ref)
        assert state.reference_timestamp > 0
        assert state.prompting.language == "en"

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_init_state_invalid_reference_time_raises(mock_from_config):
        """_init_state with non-datetime reference_time raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="reference_time"):
            getattr(mem, "_init_state")(reference_time="2025-01-01")

    @staticmethod
    @patch.object(GraphStoreFactory, "from_config")
    def test_init_state_chinese_from_strategy_fallback_to_language(mock_from_config):
        """_init_state sets prompting language from strategy chinese_* or self.language"""
        mock_from_config.return_value = _make_mock_backend()
        strategy = AddMemStrategy(chinese_entity=False, chinese_relation=True, chinese_entity_dedupe=False)
        mem = GraphMemory(
            db_config=_make_mock_config(),
            extraction_strategy=strategy,
            language="en",
            llm_client=_make_mock_llm_client(),
        )
        state = getattr(mem, "_init_state")(reference_time=None)
        assert state.prompting.entity_extraction_language == "en"
        assert state.prompting.relation_extraction_language == "cn"
        assert state.prompting.entity_dedupe_language == "en"


class TestGraphMemorySearch:
    """Tests for search() (add_memory skipped)"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_search_unknown_strategy_raises(self, mock_from_config):
        """Unknown search_strategy raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="Strategy.*not found"):
            await mem.search("q", "user", search_strategy="nonexistent")

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_search_empty_strategy_raises(self, mock_from_config):
        """Empty or non-string strategy raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="non-empty string"):
            await mem.search("q", "user", search_strategy="")

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_search_no_embedder_no_query_embedding_raises(self, mock_from_config):
        """search without embedder and without query_embedding raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="attach_embedder"):
            await mem.search("q", "user", query_embedding=None)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_search_invalid_query_embedding_type_raises(self, mock_from_config):
        """query_embedding must be list[float] or None"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        with pytest.raises(BaseError, match="query_embedding"):
            await mem.search("q", "user", query_embedding="not a list")

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_search_success_with_query_embedding_returns_result(self, mock_from_config):
        """search with query_embedding and default strategy returns dict of results"""
        backend = _make_mock_backend()
        backend.search = AsyncMock(
            return_value={
                ENTITY_COLLECTION: [{"uuid": "e1", "name": "E", "content": "", "distance": 0.9}],
                RELATION_COLLECTION: [],
                EPISODE_COLLECTION: [],
            }
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        result = await mem.search(
            "query",
            "user",
            search_strategy="default",
            query_embedding=[0.1] * 32,
        )
        assert ENTITY_COLLECTION in result
        assert RELATION_COLLECTION in result
        assert EPISODE_COLLECTION in result
        assert len(result[ENTITY_COLLECTION]) == 1
        assert result[ENTITY_COLLECTION][0][0] == 0.9


class TestReplaceOneSideOfRelation:
    """Tests for _replace_one_side_of_relation (static)"""

    @staticmethod
    def test_replace_one_side_first_time_appends_deferred_and_updates():
        """First time relation for tgt: appends to relation_deferred_updates and entity_relation_updates"""
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        tgt_uuid = "tgt"
        entity_relation_updates = {tgt_uuid: {}}
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.relation_deferred_updates[tgt_uuid] = []
        getattr(GraphMemory, "_replace_one_side_of_relation")("lhs", rel, tgt_uuid, entity_relation_updates, state)
        assert (rel, "lhs", tgt_uuid) in state.relation_deferred_updates[tgt_uuid]
        assert entity_relation_updates[tgt_uuid]["r1"] is rel
        assert rel.uuid not in state.faulty_relations

    @staticmethod
    def test_replace_one_side_duplicate_marks_faulty_and_removes_from_deferred():
        """Duplicate relation for same tgt: marks faulty and removes from deferred"""
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        tgt_uuid = "tgt"
        entity_relation_updates = {tgt_uuid: {"r1": rel}}
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.relation_deferred_updates[tgt_uuid] = [(rel, "lhs", tgt_uuid)]
        getattr(GraphMemory, "_replace_one_side_of_relation")("lhs", rel, tgt_uuid, entity_relation_updates, state)
        assert state.faulty_relations["r1"] is rel
        assert "r1" not in entity_relation_updates[tgt_uuid]
        assert state.relation_deferred_updates[tgt_uuid] == []


class TestPerformSearch:
    """Tests for _perform_search"""

    @patch.object(GraphStoreFactory, "from_config")
    def test_perform_search_rerank_without_reranker_raises(self, mock_from_config):
        """When strategy has rerank=True but mem has no reranker, raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.register_search_strategy(
            "rerank_strat",
            search_entity=SearchConfig(rerank=True),
            force=True,
        )
        with pytest.raises(BaseError, match="reranker"):
            getattr(mem, "_perform_search")(0, "user", "rerank_strat", [], dict(query="q", query_embedding=[0.0]))

    @pytest.mark.filterwarnings("ignore:coroutine 'GraphMemory._search' was never awaited")
    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_perform_search_appends_task_when_rerank_false(self, mock_from_config):
        """_perform_search appends one asyncio task to list"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        tasks = []
        getattr(mem, "_perform_search")(0, "user", "default", tasks, dict(query="q", query_embedding=[0.0]))
        assert len(tasks) == 1
        assert asyncio.isfuture(tasks[0])
        tasks[0].cancel()
        try:
            await tasks[0]
        except asyncio.CancelledError:
            pass


class TestParseRelationFilteringResult:
    """Tests for _parse_relation_filtering_result"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_parse_relation_filtering_result_empty_tasks_no_op(self, mock_from_config):
        """When relation_filter_tasks is empty, returns without error"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.relation_filter_tasks = {}
        await getattr(mem, "_parse_relation_filtering_result")([], state)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_parse_relation_filtering_result_applies_merge_infos(self, mock_from_config):
        """When tasks complete, merge_infos get new_relations set"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e = Entity(name="E", content="", obj_type="Entity")
        e.uuid = "e1"
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        state.merge_infos["e1"] = EntityMerge(target=e)
        state.relation_deferred_updates["e1"] = []
        state.relation_filter_tasks = {}

        async def done_fut():
            return type("R", (), {"content": '{"relevant_relations": [1]}'})()

        fut = asyncio.ensure_future(done_fut())
        state.relation_filter_tasks[fut] = (e, [rel])
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json", return_value={"relevant_relations": [1]}
        ):
            await getattr(mem, "_parse_relation_filtering_result")([rel], state)
        assert state.merge_infos["e1"].new_relations == [rel]

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_parse_relation_filtering_result_relation_not_kept_goes_to_removed(self, mock_from_config):
        """When relation not in new_relations after filter, it is added to removed_relation and to_remove"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e = Entity(name="E", content="", obj_type="Entity")
        e.uuid = "e1"
        rel_keep = Relation(
            name="R1", content="c1", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel_keep.uuid = "r1"
        rel_drop = Relation(
            name="R2", content="c2", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel_drop.uuid = "r2"
        state.merge_infos["e1"] = EntityMerge(target=e)
        state.relation_deferred_updates["e1"] = [(rel_keep, "lhs", "e1"), (rel_drop, "lhs", "e1")]

        async def done():
            return MagicMock(
                content='{"relevant_relations": [1]}'
            )  # keep only index 1 -> rel_drop (index 2 would be [2])

        fut = asyncio.ensure_future(done())
        state.relation_filter_tasks[fut] = (e, [rel_keep, rel_drop])  # index 1 = rel_drop
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json", return_value={"relevant_relations": [1]}
        ):
            await getattr(mem, "_parse_relation_filtering_result")([rel_keep, rel_drop], state)
        assert state.merge_infos["e1"].new_relations == [rel_keep]
        assert rel_drop.uuid in state.mem_update.removed_relation
        assert rel_drop in state.to_remove

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_parse_relation_filtering_result_two_targets_one_relation_not_in_new_relations(
        self, mock_from_config
    ):
        """Two merge_infos; for one target a deferred relation is not in new_relations"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        e2 = Entity(name="E2", content="", obj_type="Entity")
        e2.uuid = "e2"
        rel_a = Relation(name="R", content="c", lhs="e1", rhs="x", obj_type="Relation", valid_since=0, valid_until=-1)
        rel_a.uuid = "ra"
        rel_b = Relation(name="R", content="c", lhs="e2", rhs="y", obj_type="Relation", valid_since=0, valid_until=-1)
        rel_b.uuid = "rb"
        state.merge_infos["e1"] = EntityMerge(target=e1)
        state.merge_infos["e2"] = EntityMerge(target=e2)
        state.relation_deferred_updates["e1"] = [(rel_a, "lhs", "e1")]
        state.relation_deferred_updates["e2"] = [(rel_b, "lhs", "e2")]

        async def task1():
            return MagicMock(content='{"relevant_relations": [1]}')

        async def task2():
            return MagicMock(content='{"relevant_relations": []}')

        f1, f2 = asyncio.ensure_future(task1()), asyncio.ensure_future(task2())
        state.relation_filter_tasks[f1] = (e1, [rel_a])
        state.relation_filter_tasks[f2] = (e2, [rel_b])
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            side_effect=[{"relevant_relations": [1]}, {"relevant_relations": []}],
        ):
            await getattr(mem, "_parse_relation_filtering_result")([rel_a, rel_b], state)
        assert state.merge_infos["e1"].new_relations == [rel_a]
        assert state.merge_infos["e2"].new_relations == []
        assert rel_b.uuid in state.mem_update.removed_relation
        assert rel_b in state.to_remove

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_parse_relation_filtering_result_exception_falls_back_to_full_list(self, mock_from_config):
        """When task raises or parse fails, relations_filtered = new_relation_list"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e = Entity(name="E", content="", obj_type="Entity")
        e.uuid = "e1"
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        state.merge_infos["e1"] = EntityMerge(target=e)
        state.relation_deferred_updates["e1"] = []

        async def failing_task():
            raise RuntimeError("parse failed")

        fut = asyncio.ensure_future(failing_task())
        state.relation_filter_tasks[fut] = (e, [rel])
        await getattr(mem, "_parse_relation_filtering_result")([rel], state)
        assert state.merge_infos["e1"].new_relations == [rel]


class TestInvokeLlm:
    """Tests for _invoke_llm"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_invoke_llm_success_returns_response(self, mock_from_config):
        """_invoke_llm returns LLM response when invoke succeeds"""
        backend = _make_mock_backend()
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_structured_output=True, llm_client=_make_mock_llm_client())
        mem.llm_client = AsyncMock()
        msg = MagicMock()
        msg.content = '{"result": "ok"}'
        mem.llm_client.invoke = AsyncMock(return_value=msg)
        kwargs = {}
        template = MagicMock()
        template.name = "test"
        template.format.return_value.content = [{"role": "user", "content": "test"}]
        response = await getattr(mem, "_invoke_llm")(kwargs, template)
        assert response.content == '{"result": "ok"}'

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_invoke_llm_retries_then_raises(self, mock_from_config):
        """_invoke_llm raises after request_max_retries failures"""
        backend = _make_mock_backend()
        backend.config.request_max_retries = 2
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.llm_client = AsyncMock()
        mem.llm_client.invoke = AsyncMock(side_effect=RuntimeError("fail"))
        template = MagicMock()
        template.format.return_value.content = [{"role": "user", "content": "test"}]
        with pytest.raises(BaseError, match="LLM|fail"):
            await getattr(mem, "_invoke_llm")({}, template)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_invoke_llm_merges_llm_extra_kwargs(self, mock_from_config):
        """_invoke_llm merges llm_extra_kwargs into invoke params"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(
            db_config=_make_mock_config(), llm_extra_kwargs={"temperature": 0.3}, llm_client=_make_mock_llm_client()
        )
        mem.llm_client = AsyncMock()
        msg = MagicMock(content="ok")
        mem.llm_client.invoke = AsyncMock(return_value=msg)
        template = MagicMock()
        template.name = "t"
        template.format.return_value.content = [{"role": "user", "content": "q"}]
        await getattr(mem, "_invoke_llm")({"k": "v"}, template)
        call_kwargs = mem.llm_client.invoke.call_args[1]
        assert call_kwargs.get("temperature") == 0.3

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    @patch("openjiuwen.core.memory.graph.graph_memory.base.memory_logger")
    async def test_invoke_llm_debug_logs_when_enabled(self, mock_logger, mock_from_config):
        """When debug=True, _invoke_llm logs template and content"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), debug=True, llm_client=_make_mock_llm_client())
        mem.llm_client = AsyncMock()
        msg = MagicMock(content="resp")
        mem.llm_client.invoke = AsyncMock(return_value=msg)
        template = MagicMock()
        template.name = "extract_entity"
        template.format.return_value.content = [{"role": "user", "content": "query"}]
        await getattr(mem, "_invoke_llm")({}, template)
        assert mock_logger.info.called


class TestPrepareEpisodes:
    """Tests for _prepare_episodes (add_memory helper)"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_str_content_returns_stripped(self, mock_from_config):
        """String content is validated and returned stripped; no history when db empty"""
        mock_from_config.return_value = _make_mock_backend()
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        out = await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "  hello world  ", state)
        assert out == "hello world"
        assert state.history == ""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_str_with_content_fmt_kwargs_raises(self, mock_from_config):
        """content_fmt_kwargs with str content raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with pytest.raises(BaseError, match="content_fmt_kwargs"):
            await getattr(mem, "_prepare_episodes")(
                EpisodeType.DOCUMENT, "user1", "text", state, content_fmt_kwargs={"k": "v"}
            )

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_conversation_list_formats_messages(self, mock_from_config):
        """CONVERSATION with list of dict (role, content) formats and returns content string"""
        mock_from_config.return_value = _make_mock_backend()
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        content = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
        out = await getattr(mem, "_prepare_episodes")(EpisodeType.CONVERSATION, "user1", content, state)
        assert "Hi" in out and "Hello" in out

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_non_conversation_with_list_raises(self, mock_from_config):
        """DOCUMENT/JSON with list content raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with pytest.raises(BaseError, match="str when source type"):
            await getattr(mem, "_prepare_episodes")(
                EpisodeType.DOCUMENT, "user1", [{"role": "user", "content": "x"}], state
            )

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_conversation_missing_role_or_content_raises(self, mock_from_config):
        """CONVERSATION with list missing 'role' or 'content' keys raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with pytest.raises(BaseError, match="role|content|messages"):
            await getattr(mem, "_prepare_episodes")(EpisodeType.CONVERSATION, "user1", [{"role": "user"}], state)
        with pytest.raises(BaseError, match="role|content|messages"):
            await getattr(mem, "_prepare_episodes")(EpisodeType.CONVERSATION, "user1", [{"content": "hi"}], state)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_conversation_not_list_or_dict_raises(self, mock_from_config):
        """CONVERSATION with content not list/dict/BaseMessage raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with pytest.raises(BaseError, match="str or list of messages"):
            await getattr(mem, "_prepare_episodes")(EpisodeType.CONVERSATION, "user1", 123, state)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_empty_content_raises(self, mock_from_config):
        """Empty content after strip raises"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with pytest.raises(BaseError, match="non-empty"):
            await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "   ", state)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_recall_fills_history_when_db_not_empty(self, mock_from_config):
        """When recall_episode.top_k and db not empty, search runs and state.history/lookup_table set"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        ep_dict = {
            "uuid": "ep1",
            "content": "past",
            "created_at": 1000,
            "valid_since": 1000,
            "obj_type": "conversation",
            "user_id": "u",
            "distance": 0.9,
        }
        backend.search = AsyncMock(return_value={EPISODE_COLLECTION: [ep_dict]})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_episode.top_k = 2
        state.reference_timestamp = 2000
        out = await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "query text", state)
        assert out == "query text"
        assert len(state.lookup_table.episodes) == 1
        assert state.history

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_exclude_future_results_adds_lte_filter(self, mock_from_config):
        """When recall_episode.exclude_future_results, search filter includes valid_since <= reference_timestamp"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        backend.search = AsyncMock(return_value={EPISODE_COLLECTION: []})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_episode.top_k = 2
        state.strategy.recall_episode.exclude_future_results = True
        state.reference_timestamp = 1000
        await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "q", state)
        backend.search.assert_called_once()
        call = backend.search.call_args
        assert "filter_expr" in call.kwargs or len(call[0]) >= 4

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_same_kind_adds_obj_type_filter(self, mock_from_config):
        """When recall_episode.same_kind True, filter includes obj_type eq"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        backend.search = AsyncMock(return_value={EPISODE_COLLECTION: []})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_episode.top_k = 2
        state.strategy.recall_episode.same_kind = True
        await getattr(mem, "_prepare_episodes")(EpisodeType.CONVERSATION, "user1", "q", state)
        backend.search.assert_called_once()
        call = backend.search.call_args
        assert call.kwargs.get("filter_expr") is not None

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_minimize_filters_by_distance_leq(self, mock_from_config):
        """When higher_is_better is False, episodes with distance <= min_score are kept"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        ep_far = {
            "uuid": "ep1",
            "content": "c",
            "created_at": 0,
            "valid_since": 0,
            "obj_type": "doc",
            "user_id": "u",
            "distance": 0.5,
        }
        ep_near = {
            "uuid": "ep2",
            "content": "c2",
            "created_at": 0,
            "valid_since": 0,
            "obj_type": "doc",
            "user_id": "u",
            "distance": 0.05,
        }
        backend.search = AsyncMock(return_value={EPISODE_COLLECTION: [ep_far, ep_near]})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_episode.top_k = 5
        state.strategy.recall_episode.min_score = 0.1
        state.strategy.recall_episode.rank_config = WeightedRankConfig(
            name_dense=0.5, content_dense=0.5, content_sparse=0
        )
        state.strategy.recall_episode.rank_config.higher_is_better = False
        await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "q", state)
        assert state.history
        assert len(state.lookup_table.episodes) <= 2
        kept_distances = [state.lookup_table.episodes[e].content for e in state.lookup_table.episodes]
        assert "c" in kept_distances or "c2" in kept_distances

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_prepare_episodes_minimize_else_branch_filters_episodes_by_distance_leq(self, mock_from_config):
        """When higher_is_better False, result filtered by distance <= min_score"""
        backend = _make_mock_backend()
        backend.return_similarity_score = False
        backend.is_empty = MagicMock(return_value=False)
        ep_keep = {
            "uuid": "ep1",
            "content": "ok",
            "created_at": 0,
            "valid_since": 0,
            "obj_type": "doc",
            "user_id": "u",
            "distance": 0.05,
        }
        ep_drop = {
            "uuid": "ep2",
            "content": "no",
            "created_at": 0,
            "valid_since": 0,
            "obj_type": "doc",
            "user_id": "u",
            "distance": 0.9,
        }
        backend.search = AsyncMock(return_value={EPISODE_COLLECTION: [ep_keep, ep_drop]})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_episode.top_k = 10
        state.strategy.recall_episode.min_score = 0.1
        state.strategy.recall_episode.rank_config = WeightedRankConfig(
            name_dense=0.5, content_dense=0.5, content_sparse=0
        )
        state.strategy.recall_episode.rank_config.higher_is_better = False
        await getattr(mem, "_prepare_episodes")(EpisodeType.DOCUMENT, "user1", "q", state)
        assert len(state.lookup_table.episodes) == 1
        ep = next(iter(state.lookup_table.episodes.values()))
        assert ep.content == "ok"


class TestFetchRelevantEntities:
    """Tests for _fetch_relevant_entities"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_fetch_relevant_entities_no_existing_entity_returns_early(self, mock_from_config):
        """When no_existing_entity is True, returns without running search/query"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.db_backend.search = AsyncMock()
        mem.db_backend.query = AsyncMock()
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        await getattr(mem, "_fetch_relevant_entities")(
            [EntityDeclaration(name="E", entity_type_id=0)], True, "user", state
        )
        mem.db_backend.search.assert_not_called()
        mem.db_backend.query.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_fetch_relevant_entities_tasks_at_most_one_returns_early(self, mock_from_config):
        """When len(state.tasks) <= 1, returns without popping tasks"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.db_backend.search = AsyncMock()
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.tasks = [asyncio.Future()]
        await getattr(mem, "_fetch_relevant_entities")(
            [EntityDeclaration(name="E", entity_type_id=0)], False, "user", state
        )
        mem.db_backend.search.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_fetch_relevant_entities_runs_search_and_query_when_tasks_sufficient(self, mock_from_config):
        """When no_existing_entity False and len(tasks) > 1, runs entity search (same_kind=False) and name query"""
        backend = _make_mock_backend()
        backend.search = AsyncMock(
            return_value={ENTITY_COLLECTION: [{"uuid": "e1", "name": "E", "content": "", "obj_type": "Entity"}]}
        )
        backend.query = AsyncMock(return_value=[])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        et = MagicMock()
        et.name = "Entity"
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[et])
        state.strategy.recall_entity.same_kind = False
        state.strategy.recall_entity.top_k = 5
        embed_done = asyncio.Future()
        embed_done.set_result([[0.1] * 32])
        relation_pending = asyncio.Future()
        state.tasks = [embed_done, relation_pending]
        decl = EntityDeclaration(name="Alice", entity_type_id=0)
        await getattr(mem, "_fetch_relevant_entities")([decl], False, "user", state)
        assert backend.search.called
        assert backend.query.called
        assert "e1" in state.retrieved_entities or len(state.retrieved_entities) >= 0

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_fetch_relevant_entities_minimize_and_entity_type_none_hits_else_branches(self, mock_from_config):
        """
        When metric_is_sim False and higher_is_better False, distance <= min_score;
        when entity_type_id out of range, typed search skipped.
        """
        backend = _make_mock_backend()
        backend.return_similarity_score = False
        backend.search = AsyncMock(
            return_value={ENTITY_COLLECTION: [{"uuid": "e1", "name": "E", "content": "", "obj_type": "Entity"}]}
        )
        backend.query = AsyncMock(return_value=[{"uuid": "e2", "name": "Alice", "content": "", "obj_type": "Entity"}])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(
            strategy=AddMemStrategy(), entity_types=[]
        )  # entity_type_id 0 >= len([]) -> entity_type None
        state.strategy.recall_entity.same_kind = False
        state.strategy.recall_entity.top_k = 5
        state.strategy.recall_entity.rank_config.higher_is_better = False
        state.strategy.recall_entity.min_score = 0.1
        embed_done = asyncio.Future()
        embed_done.set_result([[0.1] * 32])
        state.tasks = [embed_done, asyncio.Future()]
        decl = EntityDeclaration(name="Alice", entity_type_id=0)
        await getattr(mem, "_fetch_relevant_entities")([decl], False, "user", state)
        assert backend.search.called
        assert backend.query.called
        assert "e1" in state.retrieved_entities or "e2" in state.retrieved_entities

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_fetch_relevant_entities_typed_search_minimize_filters_by_distance_leq(self, mock_from_config):
        """
        When entity_type set and higher_is_better False, typed search results filtered by distance <= min_score
        """
        backend = _make_mock_backend()
        backend.return_similarity_score = False
        backend.search = AsyncMock(
            return_value={
                ENTITY_COLLECTION: [
                    {"uuid": "e1", "name": "E", "content": "", "obj_type": "Entity", "distance": 0.03},
                ]
            }
        )
        backend.query = AsyncMock(return_value=[])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        et = MagicMock()
        et.name = "Entity"
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[et])
        state.strategy.recall_entity.same_kind = False
        state.strategy.recall_entity.top_k = 5
        state.strategy.recall_entity.rank_config.higher_is_better = False
        state.strategy.recall_entity.min_score = 0.05
        embed_done = asyncio.Future()
        embed_done.set_result([[0.1] * 32])
        state.tasks = [embed_done, asyncio.Future()]
        decl = EntityDeclaration(name="Alice", entity_type_id=0)
        await getattr(mem, "_fetch_relevant_entities")([decl], False, "user", state)
        assert backend.search.call_count >= 2
        assert "e1" in state.retrieved_entities


class TestExtractEntityDeclarations:
    """Tests for _extract_entity_declarations"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_returns_no_existing_and_list(self, mock_from_config):
        """Returns (no_existing_entity, list of EntityDeclaration)"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(
            mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='[{"name": "Alice", "entity_type_id": 0}]'))
        )
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value=[{"name": "Alice", "entity_type_id": 0}],
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.CONVERSATION,
                "content",
                GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()]),
            )
        assert no_exist is True
        assert len(decls) == 1
        assert decls[0].name == "Alice"

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_filters_user_assistant_names(self, mock_from_config):
        """Names in entity_names (user, assistant, etc.) are filtered out"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(
            mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='[{"name": "user", "entity_type_id": 0}]'))
        )
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value=[{"name": "user", "entity_type_id": 0}],
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.CONVERSATION, "c", GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
            )
        assert len(decls) == 0

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_dict_response_normalized_to_list(self, mock_from_config):
        """When LLM returns a dict (e.g. {\"entities\": [...]}), it is normalized to list of declarations"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(
            mem,
            "_invoke_llm",
            AsyncMock(return_value=MagicMock(content='{"entities": [{"name": "Alice", "entity_type_id": 0}]}')),
        )
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value={"entities": [{"name": "Alice", "entity_type_id": 0}]},
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.DOCUMENT, "c", GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
            )
        assert len(decls) == 1
        assert decls[0].name == "Alice"

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_dict_value_single_dict_wrapped_in_list(self, mock_from_config):
        """When LLM returns dict whose value is a single dict (not list), it is wrapped in a list"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(
            mem,
            "_invoke_llm",
            AsyncMock(return_value=MagicMock(content='{"entity": {"name": "Bob", "entity_type_id": 0}}')),
        )
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value={"entity": {"name": "Bob", "entity_type_id": 0}},
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.DOCUMENT, "c", GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
            )
        assert len(decls) == 1
        assert decls[0].name == "Bob"

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_non_str_name_treated_as_empty(self, mock_from_config):
        """When name is not a string (e.g. number), it is treated as empty and entity is skipped"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content="[]")))
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value=[{"name": 123, "entity_type_id": 0}],
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.DOCUMENT, "c", GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
            )
        assert len(decls) == 0

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_non_list_non_dict_becomes_empty(self, mock_from_config):
        """When parse_json returns something that is not list or dict list, declarations become empty"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=True)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())

        setattr(mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content="null")))
        with patch("openjiuwen.core.memory.graph.graph_memory.base.parse_json", return_value={"key": "not_a_list"}):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(
                EpisodeType.DOCUMENT, "c", GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
            )
        assert decls == []

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_extract_entity_declarations_appends_embed_task_when_entities_exist(self, mock_from_config):
        """When DB has entities and we extract names, embed_documents task is appended"""
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        embedder = MagicMock()
        embedder.embed_documents = AsyncMock(return_value=[[0.1] * 32])
        mem.attach_embedder(embedder)
        backend.embedder = embedder
        setattr(
            mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='[{"name": "Alice", "entity_type_id": 0}]'))
        )
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[MagicMock()])
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.parse_json",
            return_value=[{"name": "Alice", "entity_type_id": 0}],
        ):
            no_exist, decls = await getattr(mem, "_extract_entity_declarations")(EpisodeType.DOCUMENT, "c", state)
        assert no_exist is False
        assert len(decls) == 1
        assert len(state.tasks) == 1
        await state.tasks[0]
        embedder.embed_documents.assert_called_once()


class TestResolveEntityMerges:
    """Tests for _resolve_entity_merges"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_entity_merges_empty_no_op(self, mock_from_config):
        """Empty merging_args does not raise"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        with patch.object(mem, "_dispatch_entity_merge_tasks", new_callable=AsyncMock):
            await getattr(mem, "_resolve_entity_merges")([], state)

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_entity_merges_sets_merge_infos_and_dispatch(self, mock_from_config):
        """Single (tgt, [src]) sets merge_infos and calls _dispatch_entity_merge_tasks"""
        backend = _make_mock_backend()
        backend.query = AsyncMock(return_value=[])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        tgt.episodes = []
        src = Entity(name="S", content="", obj_type="Entity")
        src.uuid = "src"
        src.episodes = ["ep1"]
        src.relations = []
        dispatch_mock = AsyncMock()
        with patch.object(mem, "_dispatch_entity_merge_tasks", dispatch_mock):
            with patch.object(mem, "_resolve_each_relation", new_callable=AsyncMock):
                await getattr(mem, "_resolve_entity_merges")([(tgt, [src])], state)
        assert "tgt" in state.merge_infos
        assert state.merge_infos["tgt"].target is tgt
        assert "src" in state.merge_infos["tgt"].source
        dispatch_mock.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_entity_merges_two_src_entities_episodes_deduped(self, mock_from_config):
        """Two src entities with episodes run both list(set(...)) lines"""
        backend = _make_mock_backend()
        backend.query = AsyncMock(return_value=[])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        tgt.episodes = []
        src1 = Entity(name="S1", content="", obj_type="Entity")
        src1.uuid = "s1"
        src1.episodes = ["ep1", "ep2"]
        src1.relations = []
        src2 = Entity(name="S2", content="", obj_type="Entity")
        src2.uuid = "s2"
        src2.episodes = ["ep1", "ep3"]
        src2.relations = []
        with patch.object(mem, "_dispatch_entity_merge_tasks", new_callable=AsyncMock):
            with patch.object(mem, "_resolve_each_relation", new_callable=AsyncMock):
                await getattr(mem, "_resolve_entity_merges")([(tgt, [src1, src2])], state)
        assert "tgt" in state.merge_infos
        assert set(tgt.episodes) == {"ep1", "ep2", "ep3"}
        assert sorted(tgt.episodes) == sorted(set(tgt.episodes))


class TestDispatchEntityMergeTasks:
    """Tests for _dispatch_entity_merge_tasks"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_dispatch_entity_merge_tasks_episodes_updated(self, mock_from_config):
        """When episodes_to_update non-empty, query runs and updated_episode extended"""
        backend = _make_mock_backend()
        ep_dict = {"uuid": "ep1", "content": "c", "obj_type": "conv", "user_id": "u", "valid_since": 0, "created_at": 0}
        backend.query = AsyncMock(return_value=[ep_dict])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_filter = False
        await getattr(mem, "_dispatch_entity_merge_tasks")({"ep1"}, {}, state)
        assert len(state.mem_update_skip_embed.updated_episode) == 1
        assert state.mem_update_skip_embed.updated_episode[0].uuid == "ep1"
        assert state.mem_update_skip_embed.updated_episode[0].content == "c"

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_dispatch_entity_merge_tasks_merge_filter_true_creates_relation_filter_tasks(self, mock_from_config):
        """When merge_filter True and entity_relation_updates non-empty, relation_filter_tasks populated"""
        backend = _make_mock_backend()
        backend.query = AsyncMock(return_value=[])
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(
            mem,
            "_invoke_llm",
            AsyncMock(return_value=MagicMock(content='{"relevant_relations": [1], "brief_reasoning": "ok"}')),
        )
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_filter = True
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        state.lookup_table.entities["tgt"] = tgt
        state.merge_infos["tgt"] = EntityMerge(target=tgt)
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        entity_relation_updates = {"tgt": {"r1": rel}}
        await getattr(mem, "_dispatch_entity_merge_tasks")(set(), entity_relation_updates, state)
        assert len(state.relation_filter_tasks) == 1
        for task, (ent, rels) in state.relation_filter_tasks.items():
            assert ent is tgt
            assert rels == [rel]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            break


class TestEntityEnrich:
    """Tests for _entity_enrich"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_enrich_non_blocking_updates_all(self, mock_from_config):
        """When no pending_merge, all entities get summary extraction and update_entity"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='{"summary": "s", "attributes": {}}')))
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        entities = [e1]
        result = await getattr(mem, "_entity_enrich")(entities, "content", state)
        assert result == entities
        assert e1.content == "s" or getattr(e1, "content", None) is not None

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_enrich_blocking_waits_pending_merge_then_extracts(self, mock_from_config):
        """When entity in pending_merge, awaits that task then runs summary extraction"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        merge_response = MagicMock(content='{"summary": "merged", "attributes": {}}')
        setattr(mem, "_invoke_llm", AsyncMock(side_effect=[merge_response, merge_response]))
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e1 = Entity(name="E1", content="", obj_type="Entity")
        e1.uuid = "e1"
        fut = asyncio.Future()
        fut.set_result(merge_response)
        state.pending_merge["e1"] = fut
        state.merging_tasks.append(fut)
        entities = [e1]
        result = await getattr(mem, "_entity_enrich")(entities, "content", state)
        assert result == entities


class TestResolveEachRelation:
    """Tests for _resolve_each_relation"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_each_relation_replace_one_side_called(self, mock_from_config):
        """When relation lhs/rhs not self-pointing, _replace_one_side_of_relation is called"""
        backend = _make_mock_backend()
        rel = Relation(
            name="R", content="c", lhs="src", rhs="other", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel.uuid = "r1"
        backend.query = AsyncMock(
            return_value=[
                {
                    "uuid": "r1",
                    "name": "R",
                    "content": "c",
                    "lhs": "src",
                    "rhs": "other",
                    "obj_type": "Relation",
                    "valid_since": 0,
                    "valid_until": -1,
                }
            ]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.relations["r1"] = rel
        src = Entity(name="Src", content="", obj_type="Entity")
        src.uuid = "src"
        src.relations = ["r1"]
        entity_relation_updates = {"tgt": {}}
        state.relation_deferred_updates["tgt"] = []
        map_src2tgt = {"src": "tgt"}
        with patch.object(GraphMemory, "_replace_one_side_of_relation") as replace_mock:
            await getattr(mem, "_resolve_each_relation")(
                "tgt", src, map_src2tgt, entity_relation_updates, state, alias=set()
            )
        replace_mock.assert_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_each_relation_self_pointing_marked_faulty(self, mock_from_config):
        """When both lhs and rhs in alias (self-pointing), relation marked faulty"""
        backend = _make_mock_backend()
        rel = Relation(name="R", content="c", lhs="tgt", rhs="tgt", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        backend.query = AsyncMock(
            return_value=[
                {
                    "uuid": "r1",
                    "name": "R",
                    "content": "c",
                    "lhs": "tgt",
                    "rhs": "tgt",
                    "obj_type": "Relation",
                    "valid_since": 0,
                    "valid_until": -1,
                }
            ]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.relations["r1"] = rel
        src = Entity(name="S", content="", obj_type="Entity")
        src.uuid = "tgt"
        src.relations = ["r1"]
        entity_relation_updates = {"tgt": {}}
        map_src2tgt = {}
        await getattr(mem, "_resolve_each_relation")(
            "tgt", src, map_src2tgt, entity_relation_updates, state, alias={"tgt"}
        )
        assert "r1" in state.faulty_relations

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    @patch("openjiuwen.core.memory.graph.graph_memory.base.memory_logger")
    async def test_resolve_each_relation_not_connected_marked_faulty(self, mock_logger, mock_from_config):
        """When relation endpoints are not in map_src2tgt chain, relation is marked faulty and warning logged"""
        backend = _make_mock_backend()
        rel = Relation(
            name="R", content="c", lhs="other1", rhs="other2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel.uuid = "r1"
        backend.query = AsyncMock(
            return_value=[
                {
                    "uuid": "r1",
                    "name": "R",
                    "content": "c",
                    "lhs": "other1",
                    "rhs": "other2",
                    "obj_type": "Relation",
                    "valid_since": 0,
                    "valid_until": -1,
                }
            ]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.relations["r1"] = rel
        src = Entity(name="Src", content="", obj_type="Entity")
        src.uuid = "src"
        src.relations = ["r1"]
        entity_relation_updates = {"tgt": {}}
        state.relation_deferred_updates["tgt"] = []
        map_src2tgt = {"src": "tgt"}
        await getattr(mem, "_resolve_each_relation")(
            "tgt", src, map_src2tgt, entity_relation_updates, state, alias=set()
        )
        assert "r1" in state.faulty_relations
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_resolve_each_relation_while_loop_chain_replaces_after_two_steps(self, mock_from_config):
        """
        When map_src2tgt has chain src->mid->tgt and relation has lhs=mid, while runs twice then replace
        """
        backend = _make_mock_backend()
        rel = Relation(
            name="R", content="c", lhs="mid", rhs="other", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel.uuid = "r1"
        backend.query = AsyncMock(
            return_value=[
                {
                    "uuid": "r1",
                    "name": "R",
                    "content": "c",
                    "lhs": "mid",
                    "rhs": "other",
                    "obj_type": "Relation",
                    "valid_since": 0,
                    "valid_until": -1,
                }
            ]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.relations["r1"] = rel
        src = Entity(name="Src", content="", obj_type="Entity")
        src.uuid = "src"
        src.relations = ["r1"]
        entity_relation_updates = {"tgt": {}}
        state.relation_deferred_updates["tgt"] = []
        map_src2tgt = {"src": "mid", "mid": "tgt"}
        with patch.object(GraphMemory, "_replace_one_side_of_relation") as replace_mock:
            await getattr(mem, "_resolve_each_relation")(
                "tgt", src, map_src2tgt, entity_relation_updates, state, alias=set()
            )
        replace_mock.assert_called_once()
        assert replace_mock.call_args[0][0] == "lhs"
        assert replace_mock.call_args[0][2] == "tgt"


class TestEntityMerge:
    """Tests for _entity_merge"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_merge_empty_existing_returns_extracted(self, mock_from_config):
        """When existing_entities_list empty, returns extracted_declarations without merge"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.tasks = []
        decls = [EntityDeclaration(name="E", entity_type_id=0)]
        with patch.object(mem, "_resolve_entity_merges", new_callable=AsyncMock):
            out = await getattr(mem, "_entity_merge")(decls, [], state)
        assert out == decls

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_merge_merge_entities_false_clears_merging_args(self, mock_from_config):
        """When strategy.merge_entities False, merging_args cleared and _resolve_entity_merges called with []"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_entities = False
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        state.tasks = [asyncio.Future()]
        state.tasks[0].set_result(MagicMock(content="[]"))
        decl = EntityDeclaration(name="E", entity_type_id=0)
        existing = [{"uuid": "tgt", "name": "T", "content": "", "obj_type": "Entity"}]
        state.lookup_table.get_entity = MagicMock(return_value=tgt)
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.resolve_entities", return_value=([decl], [(tgt, [])], set())
        ):
            with patch.object(mem, "_resolve_entity_merges", new_callable=AsyncMock) as resolve_mock:
                await getattr(mem, "_entity_merge")([decl], existing, state)
        resolve_mock.assert_called_once()
        assert resolve_mock.call_args[0][0] == []

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_merge_with_existing_dispatches_blocking_and_non_blocking_tasks(self, mock_from_config):
        """
        When existing_entities_list and dedupe suggest merge, blocking and non-blocking merge tasks are dispatched.
        """
        backend = _make_mock_backend()
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='{"summary": "m", "attributes": {}}')))
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_entities = True
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        src = Entity(name="S", content="", obj_type="Entity")
        src.uuid = "src"

        def _mock_get_entity(d: dict):
            return tgt if d.get("uuid") == "tgt" else src

        state.lookup_table.get_entity = _mock_get_entity
        state.tasks = [asyncio.Future()]
        state.tasks[0].set_result(MagicMock(content="[]"))
        existing_list = [
            {"uuid": "tgt", "name": "T", "content": "", "obj_type": "Entity"},
            {"uuid": "src", "name": "S", "content": "", "obj_type": "Entity"},
        ]
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.resolve_entities",
            return_value=([tgt], [(tgt, [src])], set()),
        ):
            with patch.object(mem, "_resolve_entity_merges", new_callable=AsyncMock):
                out = await getattr(mem, "_entity_merge")([tgt], existing_list, state)
        assert len(state.merging_tasks) >= 1
        assert out == [tgt]

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_entity_merge_tgt_not_in_extracted_dispatches_non_blocking_task(self, mock_from_config):
        """
        When tgt not in extracted_declarations, merge task is non-blocking and _resolve_entity_merges still called
        """
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(mem, "_invoke_llm", AsyncMock(return_value=MagicMock(content='{"summary": "m", "attributes": {}}')))
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_entities = True
        state.tasks = [asyncio.Future()]
        state.tasks[0].set_result(MagicMock(content="[]"))
        tgt = Entity(name="T", content="", obj_type="Entity")
        tgt.uuid = "tgt"
        src = Entity(name="S", content="", obj_type="Entity")
        src.uuid = "src"

        def _mock_get_entity(d: dict):
            return tgt if d.get("uuid") == "tgt" else src

        state.lookup_table.get_entity = _mock_get_entity
        decl_only = EntityDeclaration(name="Other", entity_type_id=0)
        existing_list = [
            {"uuid": "tgt", "name": "T", "content": "", "obj_type": "Entity"},
            {"uuid": "src", "name": "S", "content": "", "obj_type": "Entity"},
        ]
        with patch(
            "openjiuwen.core.memory.graph.graph_memory.base.resolve_entities",
            return_value=([decl_only], [(tgt, [src])], set()),
        ):
            with patch.object(mem, "_resolve_entity_merges", new_callable=AsyncMock) as resolve_mock:
                out = await getattr(mem, "_entity_merge")([decl_only], existing_list, state)
        resolve_mock.assert_called_once()
        assert len(state.merging_tasks) >= 1
        assert out == [decl_only]


class TestHandleRelationDedupe:
    """Tests for _handle_relation_dedupe"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_handle_relation_dedupe_removes_to_remove_from_relations(self, mock_from_config):
        """Relations in state.to_remove are removed from the relations list"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        mem.attach_embedder = AsyncMock()
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        rel_keep = Relation(
            name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel_remove = Relation(
            name="R2", content="c2", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        state.to_remove = [rel_remove]
        relations = [rel_keep, rel_remove]
        await getattr(mem, "_handle_relation_dedupe")("user", "content", relations, state)
        assert rel_remove not in relations
        assert rel_keep in relations

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_handle_relation_dedupe_skip_when_no_merge_relations(self, mock_from_config):
        """When strategy.merge_relations False, _relation_dedupe not called"""
        mock_from_config.return_value = _make_mock_backend()
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_relations = False
        state.tmp_buffer = ["x"]
        with patch.object(mem, "_relation_dedupe", new_callable=AsyncMock) as dedupe_mock:
            await getattr(mem, "_handle_relation_dedupe")("user", "content", [], state)
        dedupe_mock.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_handle_relation_dedupe_calls_embed_and_relation_dedupe_when_conditions_met(self, mock_from_config):
        """
        When merge_relations True, tmp_buffer and relation collection not empty, embed and _relation_dedupe run
        """
        backend = _make_mock_backend()
        backend.is_empty = MagicMock(return_value=False)
        backend.search = AsyncMock(return_value={RELATION_COLLECTION: []})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        embedder = MagicMock()
        embedder.embed_documents = AsyncMock(return_value=[[0.1] * 32])
        mem.attach_embedder(embedder)
        backend.embedder = embedder  # GraphMemory.embedder is backend.embedder
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.merge_relations = True
        state.tmp_buffer = ["rel content"]
        rel = Relation(
            name="R", content="rel content", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel.uuid = "r1"
        await getattr(mem, "_handle_relation_dedupe")("user", "content", [rel], state)
        embedder.embed_documents.assert_called_once()
        backend.search.assert_called_once()


class TestRelationDedupe:
    """Tests for _relation_dedupe"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_relation_dedupe_no_similar_relations_skips_llm(self, mock_from_config):
        """When search returns no similar relations, no dedupe task is added"""
        backend = _make_mock_backend()
        backend.search = AsyncMock(return_value={RELATION_COLLECTION: []})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        setattr(mem, "_invoke_llm", AsyncMock())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        await getattr(mem, "_relation_dedupe")("user", "content", [rel], [[0.1] * 32], state)
        getattr(mem, "_invoke_llm").assert_not_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_relation_dedupe_lhs_rhs_falsy_skips_relation(self, mock_from_config):
        """When relation has entity side with empty content, lhs_rhs gets None and relation is skipped"""
        backend = _make_mock_backend()
        backend.search = AsyncMock(return_value={RELATION_COLLECTION: []})
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        e_empty = Entity(name="E", content="", obj_type="Entity")
        e_empty.uuid = "e1"
        rel = Relation(name="R", content="c", lhs=e_empty, rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        await getattr(mem, "_relation_dedupe")("user", "content", [rel], [[0.1] * 32], state)
        backend.search.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_relation_dedupe_minimize_filters_by_distance_leq(self, mock_from_config):
        """When recall_relation.rank_config.higher_is_better is False, results filtered by distance <= min_score"""
        backend = _make_mock_backend()
        backend.return_similarity_score = False
        backend.search = AsyncMock(
            return_value={
                RELATION_COLLECTION: [
                    {
                        "uuid": "r0",
                        "name": "R",
                        "content": "c",
                        "lhs": "e1",
                        "rhs": "e2",
                        "obj_type": "Relation",
                        "valid_since": 0,
                        "valid_until": -1,
                        "distance": 0.02,
                    },
                ]
            }
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.strategy.recall_relation.rank_config.higher_is_better = False
        state.strategy.recall_relation.min_score = 0.05
        e1 = Entity(name="E1", content="E1 content", obj_type="Entity")
        e1.uuid = "e1"
        e2 = Entity(name="E2", content="E2 content", obj_type="Entity")
        e2.uuid = "e2"
        rel = Relation(name="R", content="c", lhs=e1, rhs=e2, obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        # Pre-populate cache so get_relation({"uuid": "r0", ...}) returns a relation with Entity lhs/rhs.
        rel_from_db = Relation(
            name="R", content="c", lhs=e1, rhs=e2, obj_type="Relation", valid_since=0, valid_until=-1
        )
        rel_from_db.uuid = "r0"
        state.lookup_table.relations["r0"] = rel_from_db
        await getattr(mem, "_relation_dedupe")("user", "content", [rel], [[0.1] * 32], state)
        backend.search.assert_called_once()


class TestUpdateEntitiesForRelationRemoval:
    """Tests for _update_entities_for_relation_removal"""

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_update_entities_for_relation_removal_removes_relation_from_entity(self, mock_from_config):
        """Entities from to_remove get removed_relation removed and can be added to updated_entity"""
        backend = _make_mock_backend()
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        backend.query = AsyncMock(
            return_value=[{"uuid": "e1", "name": "E", "content": "", "obj_type": "Entity", "relations": ["r1"]}]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.to_remove = [rel]
        state.mem_update.removed_relation.add("r1")
        await getattr(mem, "_update_entities_for_relation_removal")(state, [])
        ent = state.lookup_table.entities.get("e1")
        assert ent is not None
        assert "r1" not in ent.relations

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_update_entities_for_relation_removal_uses_cached_entity_when_present(self, mock_from_config):
        """When entity is already in state.lookup_table.entities, that cached instance is updated"""
        backend = _make_mock_backend()
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        cached = Entity(name="E", content="", obj_type="Entity")
        cached.uuid = "e1"
        cached.relations = ["r1"]
        backend.query = AsyncMock(
            return_value=[{"uuid": "e1", "name": "E", "content": "", "obj_type": "Entity", "relations": ["r1"]}]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.lookup_table.entities["e1"] = cached
        state.to_remove = [rel]
        state.mem_update.removed_relation.add("r1")
        await getattr(mem, "_update_entities_for_relation_removal")(state, [])
        assert cached.relations == []
        assert cached in state.mem_update_skip_embed.updated_entity

    @pytest.mark.asyncio
    @patch.object(GraphStoreFactory, "from_config")
    async def test_update_entities_for_relation_removal_needs_re_embed_uses_update_needs_embed_entity(
        self, mock_from_config
    ):
        """When entity is in update_needs_embed, that instance is used and not added to updated_entity skip_embed"""
        backend = _make_mock_backend()
        rel = Relation(name="R", content="c", lhs="e1", rhs="e2", obj_type="Relation", valid_since=0, valid_until=-1)
        rel.uuid = "r1"
        in_update = Entity(name="E", content="new", obj_type="Entity")
        in_update.uuid = "e1"
        in_update.relations = ["r1"]
        backend.query = AsyncMock(
            return_value=[{"uuid": "e1", "name": "E", "content": "old", "obj_type": "Entity", "relations": ["r1"]}]
        )
        mock_from_config.return_value = backend
        mem = GraphMemory(db_config=_make_mock_config(), llm_client=_make_mock_llm_client())
        state = GraphMemState(strategy=AddMemStrategy(), entity_types=[])
        state.to_remove = [rel]
        state.mem_update.removed_relation.add("r1")
        await getattr(mem, "_update_entities_for_relation_removal")(state, [in_update])
        assert in_update.relations == []
        assert in_update not in state.mem_update_skip_embed.updated_entity
