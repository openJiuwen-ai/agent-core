# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MilvusGraphStore (milvus_support.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymilvus import MilvusException

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.constants import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
)
from openjiuwen.core.foundation.store.graph.database_config import (
    GraphStoreIndexConfig,
    GraphStoreStorageConfig,
)
from openjiuwen.core.foundation.store.graph.graph_object import Entity, Episode, Relation
from openjiuwen.core.foundation.store.graph.milvus.milvus_support import MilvusGraphStore
from openjiuwen.core.foundation.store.graph.result_ranking import (
    RRFRankConfig,
    WeightedRankConfig,
)
from openjiuwen.core.foundation.store.query import in_list
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO


class _StubEmbedding(Embedding):
    """Minimal Embedding implementation for tests."""

    dimension = 64
    limiter = MagicMock()

    async def embed_query(self, text, **kwargs):
        return [0.0] * self.dimension

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        return [[0.0] * self.dimension] * len(texts)


def _make_config(embed_dim: int = 64, with_embedder: bool = True):
    """Build GraphConfig with optional stub embedder for _build_indices."""
    storage = GraphStoreStorageConfig()
    embed_config = GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="cosine")
    config = GraphConfig(
        uri="/tmp/test_milvus_graph_db",
        name="test_db",
        timeout=10.0,
        embed_dim=embed_dim,
        db_storage_config=storage,
        db_embed_config=embed_config,
    )
    if with_embedder:
        stub = _StubEmbedding()
        stub.dimension = embed_dim
        config.embedding_model = stub
    return config


def _make_mock_client():
    """Build a mock MilvusClient that satisfies __init__ and _build_indices."""
    client = MagicMock()
    client.list_databases.return_value = []
    client.has_collection.return_value = False
    client.get_collection_stats.return_value = {"row_count": 0}
    client.list_collections.return_value = []
    return client


@pytest.fixture
def mock_client():
    return _make_mock_client()


@pytest.fixture
def store(mock_client):
    """MilvusGraphStore with mocked MilvusClient and config with stub embedder."""
    config = _make_config(with_embedder=True)
    with patch(
        "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
        return_value=mock_client,
    ):
        with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
            return MilvusGraphStore(config=config)


class TestMilvusGraphStoreInit:
    """Tests for __init__ and from_config."""

    @staticmethod
    def test_from_config_returns_instance(mock_client):
        """from_config(config) returns MilvusGraphStore instance."""
        config = _make_config(with_embedder=True)
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                store = MilvusGraphStore.from_config(config)
        assert isinstance(store, MilvusGraphStore)
        assert store.config is config

    @staticmethod
    def test_init_creates_database_if_not_exists(mock_client):
        """When db name not in list_databases, create_database and use_database are called."""
        config = _make_config(with_embedder=True)
        mock_client.list_databases.return_value = []
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                MilvusGraphStore(config=config)
        mock_client.create_database.assert_called_once_with("test_db", timeout=10.0)
        mock_client.use_database.assert_called_with("test_db", timeout=10.0)

    @staticmethod
    def test_init_uses_existing_database(mock_client):
        """When db name in list_databases, create_database is not called."""
        config = _make_config(with_embedder=True)
        mock_client.list_databases.return_value = ["test_db"]
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                MilvusGraphStore(config=config)
        mock_client.create_database.assert_not_called()
        mock_client.use_database.assert_called()


class TestMilvusGraphStoreProperties:
    """Tests for config, semophore, embedder."""

    @staticmethod
    def test_config_returns_config(store):
        assert store.config is not None
        assert store.config.name == "test_db"

    @staticmethod
    def test_embedder_returns_attached_embedder(store):
        assert store.embedder is not None
        assert store.embedder.dimension == 64

    @staticmethod
    def test_semophore_none_when_embedder_cleared(store):
        """When _embedder is set to None, semophore returns None."""
        setattr(store, "_embedder", None)
        assert store.semophore is None


class TestAttachEmbedder:
    """Tests for attach_embedder."""

    @staticmethod
    def test_attach_embedder_success(store):
        """attach_embedder sets _embedder when embedder is None and type is Embedding."""
        setattr(store, "_embedder", None)
        emb = _StubEmbedding()
        store.attach_embedder(emb)
        assert getattr(store, "_embedder") is emb

    @staticmethod
    def test_attach_embedder_redefine_raises(store):
        """attach_embedder when already set raises ValueError."""
        with pytest.raises(BaseError, match="Attempt to re-define"):
            store.attach_embedder(store.embedder)

    @staticmethod
    def test_attach_embedder_non_embedder_raises(store):
        """attach_embedder with non-Embedding type raises ValueError."""
        setattr(store, "_embedder", None)
        with pytest.raises(BaseError, match="instance of Embedding"):
            store.attach_embedder(object())


class TestIsEmpty:
    """Tests for is_empty."""

    @staticmethod
    def test_is_empty_true_when_row_count_zero(store):
        store.client.get_collection_stats.return_value = {"row_count": 0}
        assert store.is_empty(ENTITY_COLLECTION) is True

    @staticmethod
    def test_is_empty_false_when_row_count_positive(store):
        store.client.get_collection_stats.return_value = {"row_count": 5}
        assert store.is_empty(ENTITY_COLLECTION) is False


class TestRebuild:
    """Tests for rebuild."""

    @staticmethod
    def test_rebuild_drops_collections_and_recreates_db(store):
        store.client.list_collections.return_value = [ENTITY_COLLECTION, RELATION_COLLECTION]
        store.rebuild()
        assert store.client.drop_collection.call_count == 2
        store.client.drop_database.assert_called_once_with("test_db", timeout=10.0)
        store.client.create_database.assert_called()
        store.client.use_database.assert_called()


class TestClose:
    """Tests for close."""

    @staticmethod
    def test_close_calls_client_close(store):
        store.close()
        store.client.close.assert_called_once()

    @staticmethod
    def test_close_logs_on_error(store):
        store.client.close.side_effect = Exception("connection error")
        with patch("openjiuwen.core.foundation.store.graph.milvus.milvus_support.store_logger") as logger:
            store.close()
            logger.error.assert_called()


class TestAddData:
    """Tests for add_data."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_insert_and_flush(store):
        data = [{"uuid": "1", "content": "hello"}]
        await store.add_data(ENTITY_COLLECTION, data, flush=True, upsert=False)
        store.client.insert.assert_called_once_with(
            collection_name=ENTITY_COLLECTION,
            data=data,
            timeout=10.0,
        )
        store.client.flush.assert_called_once_with(
            collection_name=ENTITY_COLLECTION,
            timeout=10.0,
        )

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_upsert(store):
        data = [{"uuid": "1", "content": "hi"}]
        await store.add_data(ENTITY_COLLECTION, data, flush=False, upsert=True)
        store.client.upsert.assert_called_once()
        store.client.flush.assert_not_called()


class TestQuery:
    """Tests for query."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_query_by_ids_calls_client_get(store):
        store.client.get.return_value = [{"uuid": "e1", "content": "x"}]
        result = await store.query(ENTITY_COLLECTION, ids=["e1"])
        store.client.get.assert_called_once()
        assert result[0]["uuid"] == "e1"

    @pytest.mark.asyncio
    @staticmethod
    async def test_query_with_expr_calls_client_query(store):
        store.client.query.return_value = []
        expr = in_list("uuid", ["a", "b"])
        await store.query(ENTITY_COLLECTION, expr=expr)
        store.client.query.assert_called_once()
        call_kwargs = store.client.query.call_args[1]
        assert "filter" in call_kwargs

    @pytest.mark.asyncio
    @staticmethod
    async def test_query_expr_and_ids_none_requires_limit(store):
        with pytest.raises(BaseError, match="limit"):
            await store.query(ENTITY_COLLECTION)

    @pytest.mark.asyncio
    @staticmethod
    async def test_query_silence_errors_returns_empty_on_milvus_exception(store):
        store.client.get.side_effect = MilvusException("error")
        result = await store.query(ENTITY_COLLECTION, ids=["e1"], silence_errors=True)
        assert result == []


class TestDelete:
    """Tests for delete."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_delete_by_ids(store):
        store.client.delete.return_value = {}
        await store.delete(ENTITY_COLLECTION, ids=["id1", "id2"])
        store.client.delete.assert_called_once()
        call_kwargs = store.client.delete.call_args[1]
        assert "filter" in call_kwargs

    @pytest.mark.asyncio
    @staticmethod
    async def test_delete_by_expr(store):
        expr = in_list("uuid", ["a"])
        await store.delete(ENTITY_COLLECTION, expr=expr)
        store.client.delete.assert_called_once()

    @pytest.mark.asyncio
    @staticmethod
    async def test_delete_ids_and_expr_none_raises(store):
        with pytest.raises(BaseError, match="ids.*expr"):
            await store.delete(ENTITY_COLLECTION)


class TestRefresh:
    """Tests for refresh."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_refresh_flushes_and_compacts_collections(store):
        await store.refresh()
        assert store.client.flush.call_count >= 1
        assert store.client.compact.call_count >= 1


class TestRerank:
    """Tests for static rerank method."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_rerank_sorts_candidates_in_place():
        candidates = [
            {"content": "a", "distance": 0.5},
            {"content": "b", "distance": 0.9},
        ]
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value={"a": 0.5, "b": 0.9})
        await MilvusGraphStore.rerank("q", candidates, mock_reranker, "en")
        assert candidates[0]["content"] == "b"
        assert candidates[1]["content"] == "a"


class TestAddEntityRelationEpisode:
    """Tests for add_entity, add_relation, add_episode."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_entity_no_embed_calls_insert(store):
        store.client.insert = MagicMock()
        store.client.flush = MagicMock()
        e = Entity(name="E1", content="text")
        e.uuid = "mock_uuid"
        await store.add_entity([e], flush=True, no_embed=True)
        store.client.insert.assert_called_once()
        call_args = store.client.insert.call_args
        assert call_args[0][0] == ENTITY_COLLECTION
        assert len(call_args[1]["data"]) == 1

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_relation_no_embed(store):
        store.client.insert = MagicMock()
        store.client.flush = MagicMock()
        r = Relation(lhs="l1", rhs="r1", name="")
        r.uuid = "rel1"
        await store.add_relation([r], flush=True, no_embed=True)
        assert store.client.insert.call_count == 1
        call = store.client.insert.call_args
        assert call[0][0] == RELATION_COLLECTION
        assert call[1]["timeout"] == 10.0

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_episode_no_embed(store):
        store.client.insert = MagicMock()
        store.client.flush = MagicMock()
        ep = Episode(content="ep content", entities=[])
        ep.uuid = "ep1"
        await store.add_episode([ep], flush=True, no_embed=True)
        assert store.client.insert.call_count == 1
        call = store.client.insert.call_args
        assert call[0][0] == EPISODE_COLLECTION
        assert call[1]["timeout"] == 10.0


class TestSearch:
    """Tests for search() - single collection, collection='all', and query_embedding."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_single_collection_returns_raw_hybrid_search_result(store):
        """Single collection: delegates to _raw_hybrid_search and fills output_dict."""
        store.client.hybrid_search = MagicMock(
            return_value=[[{"entity": {"uuid": "e1", "content": "x"}, "distance": 0.9}]]
        )
        with patch.object(
            store, "_get_ranker_and_reqs", return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()])
        ):
            result = await store.search("q", k=5, collection=ENTITY_COLLECTION, ranker_config=WeightedRankConfig())
        assert ENTITY_COLLECTION in result
        assert len(result[ENTITY_COLLECTION]) == 1
        assert result[ENTITY_COLLECTION][0]["uuid"] == "e1"

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_collection_all_calls_combined_rerank(store):
        """collection='all': searches all three collections and calls _combined_rerank."""
        store.client.hybrid_search = MagicMock(
            return_value=[[{"entity": {"uuid": "e1", "content": "c"}, "distance": 0.8}]]
        )
        with patch.object(
            store, "_get_ranker_and_reqs", return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()])
        ):
            with patch.object(store, "_combined_rerank", new_callable=AsyncMock) as combined:
                result = await store.search("q", k=5, collection="all", ranker_config=WeightedRankConfig())
        assert ENTITY_COLLECTION in result and RELATION_COLLECTION in result and EPISODE_COLLECTION in result
        combined.assert_called_once()

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_with_query_embedding_runs_successfully(store):
        """search() with query_embedding runs and returns results (embedding passed to _raw_hybrid_search)."""
        store.client.hybrid_search = MagicMock(return_value=[[{"entity": {"uuid": "e1"}, "distance": 0.7}]])
        with patch.object(
            store,
            "_get_ranker_and_reqs",
            return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()]),
        ):
            result = await store.search(
                "q",
                k=3,
                collection=ENTITY_COLLECTION,
                ranker_config=WeightedRankConfig(),
                query_embedding=[0.1] * 64,
            )
        assert len(result[ENTITY_COLLECTION]) == 1
        assert result[ENTITY_COLLECTION][0]["uuid"] == "e1"


class TestSearchBfs:
    """Tests for search() with bfs_depth > 0 (graph expansion path)."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_bfs_depth_1_entity_expansion(store):
        """BFS with collection=ENTITY_COLLECTION: _raw_hybrid_search, _expand_entities, then _rank_results."""
        doc1 = {"uuid": "e1", "content": "c1", "distance": 0.9}
        doc2 = {"uuid": "e2", "content": "c2", "distance": 0.8}
        store.client.hybrid_search = MagicMock(
            side_effect=[
                [[{"entity": doc1, "distance": 0.9}]],
                [[{"entity": doc2, "distance": 0.8}]],
            ]
        )
        with patch.object(
            store,
            "_get_ranker_and_reqs",
            return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()]),
        ):
            with patch.object(store, "_expand_entities", new_callable=AsyncMock, return_value={"e2"}) as expand:
                result = await store.search(
                    "q",
                    k=5,
                    collection=ENTITY_COLLECTION,
                    ranker_config=WeightedRankConfig(),
                    bfs_depth=1,
                )
        assert expand.called
        assert store.client.hybrid_search.call_count == 2
        assert len(result[ENTITY_COLLECTION]) == 2
        uuids = {r["uuid"] for r in result[ENTITY_COLLECTION]}
        assert uuids == {"e1", "e2"}

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_bfs_depth_1_relation_expansion(store):
        """BFS with collection=RELATION_COLLECTION: _raw_hybrid_search, _expand_relations, then _rank_results."""
        doc1 = {"uuid": "r1", "content": "rel1", "lhs": "e1", "rhs": "e2", "distance": 0.9}
        doc2 = {"uuid": "r2", "content": "rel2", "lhs": "e2", "rhs": "e3", "distance": 0.8}
        store.client.hybrid_search = MagicMock(
            side_effect=[
                [[{"entity": doc1, "distance": 0.9}]],
                [[{"entity": doc2, "distance": 0.8}]],
            ]
        )
        with patch.object(
            store,
            "_get_ranker_and_reqs",
            return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()]),
        ):
            with patch.object(store, "_expand_relations", new_callable=AsyncMock, return_value={"r2"}) as expand:
                result = await store.search(
                    "q",
                    k=5,
                    collection=RELATION_COLLECTION,
                    ranker_config=WeightedRankConfig(),
                    bfs_depth=1,
                )
        assert expand.called
        assert store.client.hybrid_search.call_count == 2
        assert len(result[RELATION_COLLECTION]) == 2
        uuids = {r["uuid"] for r in result[RELATION_COLLECTION]}
        assert uuids == {"r1", "r2"}

    @pytest.mark.asyncio
    @staticmethod
    async def test_search_bfs_expansion_returns_no_new_uuids_breaks_loop(store):
        """When _expand_entities returns empty set (no new uuids), BFS loop breaks and we rank current results."""
        store.client.hybrid_search = MagicMock(
            return_value=[[{"entity": {"uuid": "e1", "content": "c1"}, "distance": 0.9}]]
        )
        with patch.object(
            store,
            "_get_ranker_and_reqs",
            return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()]),
        ):
            with patch.object(store, "_expand_entities", new_callable=AsyncMock, return_value=set()) as expand:
                result = await store.search(
                    "q",
                    k=5,
                    collection=ENTITY_COLLECTION,
                    ranker_config=WeightedRankConfig(),
                    bfs_depth=1,
                )
        expand.assert_called_once()
        assert len(result[ENTITY_COLLECTION]) == 1
        assert result[ENTITY_COLLECTION][0]["uuid"] == "e1"


class TestBuildIndices:
    """Tests for _build_indices - load existing collection, rebuild on load error."""

    @staticmethod
    def test_build_indices_has_collection_loads_it(mock_client):
        """When has_collection is True, load_collection is called and create_collection is not."""
        mock_client.has_collection.return_value = True
        config = _make_config(with_embedder=True)
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                MilvusGraphStore(config=config)
        assert mock_client.load_collection.call_count == 3
        mock_client.create_collection.assert_not_called()

    @staticmethod
    def test_build_indices_load_raises_milvus_exception_calls_rebuild(mock_client):
        """When load_collection raises MilvusException, rebuild() is called."""
        mock_client.has_collection.return_value = True
        mock_client.load_collection.side_effect = MilvusException("load error")
        config = _make_config(with_embedder=True)
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                with patch.object(MilvusGraphStore, "rebuild") as rebuild:
                    MilvusGraphStore(config=config)
        rebuild.assert_called_once()


class TestRankResults:
    """Tests for _rank_results - min_score, with/without reranker."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_rank_results_filters_by_min_score_similarity(store):
        """Similarity metric: filters candidates with distance >= min_score."""
        candidates = [
            {"uuid": "a", "distance": 0.5},
            {"uuid": "b", "distance": 0.9},
            {"uuid": "c", "distance": 0.3},
        ]
        result = await getattr(store, "_rank_results")("q", candidates, reranker=None, language="en", min_score=0.6)
        assert len(result) == 1
        assert result[0]["uuid"] == "b"

    @pytest.mark.asyncio
    @staticmethod
    async def test_rank_results_with_reranker_calls_rerank(store):
        """When reranker is provided, rerank() is called and order may change."""
        candidates = [{"uuid": "a", "content": "x", "distance": 0.5}, {"uuid": "b", "content": "y", "distance": 0.9}]
        with patch.object(MilvusGraphStore, "rerank", new_callable=AsyncMock) as rerank_mock:
            await getattr(store, "_rank_results")("q", candidates, reranker=MagicMock(), language="en")
        rerank_mock.assert_called_once()
        assert len(candidates) == 2

    @pytest.mark.asyncio
    @staticmethod
    async def test_rank_results_l2_metric_filters_and_sorts_lower_better(mock_client):
        """L2 metric: filter distance <= min_score, sort ascending (lower is better)."""
        config = _make_config(with_embedder=True)
        config.db_embed_config.distance_metric = "euclidean"
        with patch(
            "openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusClient",
            return_value=mock_client,
        ):
            with patch("openjiuwen.core.foundation.store.graph.config.os.makedirs"):
                store = MilvusGraphStore(config=config)
        assert store.metric == "L2"
        candidates = [
            {"uuid": "a", "distance": 10.0},
            {"uuid": "b", "distance": 2.0},
            {"uuid": "c", "distance": 5.0},
        ]
        result = await getattr(store, "_rank_results")("q", candidates, reranker=None, language="en", min_score=6.0)
        assert len(result) == 2
        assert result[0]["uuid"] == "b"
        assert result[1]["uuid"] == "c"


class TestCombinedRerank:
    """Tests for _combined_rerank."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_combined_rerank_none_reranker_returns_early(store):
        """When reranker is None, does nothing."""
        output_dict = {ENTITY_COLLECTION: [], RELATION_COLLECTION: [], EPISODE_COLLECTION: []}
        await getattr(store, "_combined_rerank")("q", output_dict, reranker=None, language="en")
        assert output_dict[ENTITY_COLLECTION] == []

    @pytest.mark.asyncio
    @staticmethod
    async def test_combined_rerank_with_reranker_ranks_entities(store):
        """With reranker, enriches entity content from relations and calls _rank_results."""
        entities = [{"uuid": "e1", "content": "orig", "relations": ["r1"], "original_content": "orig"}]
        relations = [{"uuid": "r1", "content": "rel content", "distance": 0.8}]
        output_dict = {
            ENTITY_COLLECTION: list(entities),
            RELATION_COLLECTION: relations,
            EPISODE_COLLECTION: [],
        }
        with patch.object(store, "_rank_results", new_callable=AsyncMock, return_value=entities) as rank_mock:
            await getattr(store, "_combined_rerank")("q", output_dict, reranker=MagicMock(), language="en")
        rank_mock.assert_called_once()
        assert output_dict[ENTITY_COLLECTION][0]["content"] == "orig"  # restored after ranking

    @pytest.mark.asyncio
    @staticmethod
    async def test_combined_rerank_enriches_content_when_mentions_positive_then_restores(store):
        """When entity has relations in rel_uuids, content is enriched with relation content then restored."""
        entities = [{"uuid": "e1", "content": "entity text", "relations": ["r1", "r2"], "distance": 0.8}]
        relations = [
            {"uuid": "r1", "content": "rel one", "distance": 0.9},
            {"uuid": "r2", "content": "rel two", "distance": 0.7},
        ]
        output_dict = {
            ENTITY_COLLECTION: [dict(e) for e in entities],
            RELATION_COLLECTION: list(relations),
            EPISODE_COLLECTION: [],
        }
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value={"entity text\n - ----------\n - rel one\n - rel two": 0.85})
        with patch.object(MilvusGraphStore, "rerank", new_callable=AsyncMock):
            await getattr(store, "_combined_rerank")("q", output_dict, reranker=mock_reranker, language="en")
        assert len(output_dict[ENTITY_COLLECTION]) == 1
        ent = output_dict[ENTITY_COLLECTION][0]
        assert ent["content"] == "entity text"
        assert "original_content" not in ent


class TestExpandEntities:
    """Tests for _expand_entities."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_expand_entities_empty_uuids_returns_empty_set(store):
        result = await getattr(store, "_expand_entities")(None, set())
        assert result == set()

    @pytest.mark.asyncio
    @staticmethod
    async def test_expand_entities_non_empty_queries_relations(store):
        store.client.query.return_value = [
            {"lhs": "e1", "rhs": "e2"},
            {"lhs": "e2", "rhs": "e3"},
        ]
        result = await getattr(store, "_expand_entities")(None, {"e1"})
        assert result == {"e1", "e2", "e3"}
        store.client.query.assert_called_once()
        call_args, call_kw = store.client.query.call_args
        assert call_args[0] == RELATION_COLLECTION
        assert call_kw["output_fields"] == ["lhs", "rhs"]


class TestExpandRelations:
    """Tests for _expand_relations."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_expand_relations_empty_uuids_returns_empty_set(store):
        result = await getattr(store, "_expand_relations")(None, set(), lookup={})
        assert result == set()

    @pytest.mark.asyncio
    @staticmethod
    async def test_expand_relations_non_empty_queries_entities(store):
        store.client.query.return_value = [
            {"relations": ["r1", "r2"]},
            {"relations": ["r2", "r3"]},
        ]
        lookup = {"rel_uuid": {"lhs": "e1", "rhs": "e2"}}
        result = await getattr(store, "_expand_relations")(None, {"rel_uuid"}, lookup=lookup)
        assert result == {"r1", "r2", "r3"}
        store.client.query.assert_called_once()


class TestAddDataTruncationAndBatchRetry:
    """Tests for _add_data content/name truncation and MilvusException batch retry."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_truncates_content_when_over_limit(store):
        """Content longer than db_storage_config.content is truncated with '...'."""
        max_content = store.config.db_storage_config.content
        long_content = "x" * (max_content + 100)
        e = Entity(name="E", content=long_content)
        e.uuid = "mock_uuid"
        store.client.insert = MagicMock()
        await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], flush=True, no_embed=True)
        assert len(e.content) <= max_content
        assert e.content.endswith("...")

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_truncates_name_when_over_limit(store):
        """Name longer than db_storage_config.name is truncated with '...'."""
        max_name = store.config.db_storage_config.name
        e = Entity(name="n" * (max_name + 50), content="c")
        e.uuid = "mock_uuid"
        store.client.insert = MagicMock()
        await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], no_embed=True)
        assert len(e.name) <= max_name
        assert e.name.endswith("...")

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_insert_raises_milvus_exception_batches_retry(store):
        """When insert raises MilvusException, batch insert is retried."""
        e = Entity(name="E", content="c")
        e.uuid = "mock_uuid"
        store.client.insert = MagicMock(side_effect=[MilvusException("too large"), None, None])
        store.client.delete = MagicMock()
        with patch("openjiuwen.core.foundation.store.graph.milvus.milvus_support.batched") as batched_mock:

            def batched_side_effect(data, n):
                return [tuple(data)]

            batched_mock.side_effect = batched_side_effect
            await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], flush=True, no_embed=True)
        assert store.client.insert.call_count >= 2

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_with_embedding_calls_embed_documents(store):
        """When no_embed=False, embedder.embed_documents is called and attributes are set."""
        e = Entity(name="E", content="content to embed")
        e.uuid = "mock_uuid"
        # Embedding fields should both be unset
        assert e.content_embedding is None
        assert e.name_embedding is None

        store.client.insert = MagicMock()
        store.embedder.embed_documents = AsyncMock(return_value=[[0.1] * 64, [0.2] * 64])
        await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], flush=True, no_embed=False)
        store.embedder.embed_documents.assert_called_once()
        store.client.insert.assert_called_once()

        # Embedding fields should both be list[float] of length 64 after _add_data
        assert sum(isinstance(x, float) for x in list(e.content_embedding)) == 64
        assert sum(isinstance(x, float) for x in list(e.name_embedding)) == 64

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_insert_fails_delete_fails_logs_warning(store):
        """When insert raises MilvusException and delete also raises, warning is logged and batch retry runs."""
        e = Entity(name="E", content="c")
        e.uuid = "mock_uuid"
        store.client.insert = MagicMock(side_effect=[MilvusException("too large"), None])
        store.client.delete = MagicMock(side_effect=Exception("delete failed"))
        with patch("openjiuwen.core.foundation.store.graph.milvus.milvus_support.batched") as batched_mock:

            def _mock_batched(data: object, n: int):
                return [tuple(data)]

            batched_mock.side_effect = _mock_batched
            with patch("openjiuwen.core.foundation.store.graph.milvus.milvus_support.store_logger") as log_mock:
                await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], flush=True, no_embed=True)
                log_mock.warning.assert_called_once()
                args = log_mock.warning.call_args[0]
                assert "clean up failed" in (args[0] or "")
                assert "delete failed" in str(args[1])
        assert store.client.insert.call_count >= 2

    @pytest.mark.asyncio
    @staticmethod
    async def test_add_data_upsert_uses_client_upsert(store):
        """When upsert=True, client.upsert is used instead of client.insert."""
        e = Entity(name="E", content="c")
        e.uuid = "mock_uuid"
        store.client.upsert = MagicMock()
        store.client.insert = MagicMock()
        await getattr(store, "_add_data")(ENTITY_COLLECTION, [e], flush=True, no_embed=True, upsert=True)
        store.client.upsert.assert_called_once()
        store.client.insert.assert_not_called()


class TestFlushAndCompact:
    """Tests for _flush_and_compact."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_flush_and_compact_skip_compact_does_not_compact(store):
        await getattr(store, "_flush_and_compact")(ENTITY_COLLECTION, skip_compact=True)
        store.client.flush.assert_called_once_with(ENTITY_COLLECTION, skip_compact=True)
        store.client.compact.assert_not_called()


class TestGetRankerAndReqs:
    """Tests for _get_ranker_and_reqs."""

    @staticmethod
    def test_get_ranker_and_reqs_episode_zeroes_dense_name(store):
        """Episode collection: dense_name/dense_content zeroed for RRF, fewer requests."""
        reqs = getattr(store, "_get_search_req")("q", [0.0] * 64, k=5, expr="")
        ranker, filtered = getattr(store, "_get_ranker_and_reqs")(RRFRankConfig(), EPISODE_COLLECTION, reqs)
        assert ranker is not None
        assert len(filtered) <= len(reqs)

    @staticmethod
    def test_get_ranker_and_reqs_relation_zeroes_dense_name(store):
        """Relation collection: first weight zeroed for RRF."""
        reqs = getattr(store, "_get_search_req")("q", [0.0] * 64, k=5, expr="")
        ranker, _ = getattr(store, "_get_ranker_and_reqs")(RRFRankConfig(), RELATION_COLLECTION, reqs)
        assert ranker is not None

    @staticmethod
    def test_get_ranker_and_reqs_weighted_returns_ranker_and_requests(store):
        """WeightedRankConfig returns ranker and filtered search requests."""
        reqs = getattr(store, "_get_search_req")("q", [0.0] * 64, k=5, expr="")
        ranker, filtered = getattr(store, "_get_ranker_and_reqs")(WeightedRankConfig(), ENTITY_COLLECTION, reqs)
        assert ranker is not None
        assert len(filtered) <= 3


class TestGetSearchReq:
    """Tests for _get_search_req."""

    @staticmethod
    def test_get_search_req_returns_three_requests(store):
        """Returns list of 3 AnnSearchRequest (name_embedding, content_embedding, content_bm25)."""
        reqs = getattr(store, "_get_search_req")("query", [0.0] * 64, k=10, expr="uuid == 'x'")
        assert len(reqs) == 3
