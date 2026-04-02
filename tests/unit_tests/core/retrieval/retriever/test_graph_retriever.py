# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Graph retriever test cases
"""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import GraphRetriever, RetrievalResult
from openjiuwen.core.retrieval.common.triple_beam import TripleBeam
from openjiuwen.core.retrieval.retriever.graph_retriever import TripleBeamSearch
from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever


class _RetrieverStubNoIndexType:
    """Like HybridRetriever: has retrieve/embed_model; index_type optional (AsyncMock would fake index_type)."""

    def __init__(self, index_type: Optional[str] = None) -> None:
        self.embed_model = AsyncMock()
        self.retrieve = AsyncMock(return_value=[])
        if index_type is not None:
            self.index_type = index_type


@pytest.fixture
def mock_chunk_retriever():
    """Create mock chunk retriever"""
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(
        return_value=[
            RetrievalResult(
                text="Chunk result 1",
                score=0.9,
                chunk_id="chunk_1",
                doc_id="doc_1",
                metadata={"chunk_id": "chunk_1", "doc_id": "doc_1"},
            ),
            RetrievalResult(
                text="Chunk result 2",
                score=0.8,
                chunk_id="chunk_2",
                doc_id="doc_1",
                metadata={"chunk_id": "chunk_2", "doc_id": "doc_1"},
            ),
        ]
    )
    return retriever


@pytest.fixture
def mock_triple_retriever():
    """Create mock triple retriever"""
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(
        return_value=[
            RetrievalResult(
                text="Triple result 1",
                score=0.85,
                metadata={"chunk_id": "chunk_3", "doc_id": "doc_1"},
            ),
        ]
    )
    return retriever


@pytest.fixture
def mock_vector_store():
    """Create mock vector store"""
    store = MagicMock()
    store.collection_name = None
    return store


@pytest.fixture
def mock_embed_model():
    """Create mock embedding model"""
    model = AsyncMock()
    model.embed_query = AsyncMock(return_value=[0.1] * 384)
    return model


class TestGraphRetriever:
    """Graph retriever tests"""

    @classmethod
    def test_init_with_retrievers(cls, mock_chunk_retriever, mock_triple_retriever):
        """Test initialization with retrievers"""
        retriever = GraphRetriever(
            chunk_retriever=mock_chunk_retriever,
            triple_retriever=mock_triple_retriever,
        )
        assert retriever.chunk_retriever == mock_chunk_retriever
        assert retriever.triple_retriever == mock_triple_retriever

    @classmethod
    def test_init_with_vector_store(cls, mock_vector_store, mock_embed_model):
        """Test initialization with vector store"""
        retriever = GraphRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            chunk_collection="chunks",
            triple_collection="triples",
        )
        assert retriever.vector_store == mock_vector_store
        assert retriever.embed_model == mock_embed_model
        assert retriever.chunk_collection == "chunks"
        assert retriever.triple_collection == "triples"

    @pytest.mark.asyncio
    async def test_retrieve_score_threshold_invalid_mode(self, mock_chunk_retriever):
        """Test using score threshold in non-vector mode"""
        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)

        with pytest.raises(BaseError, match="score_threshold is only supported"):
            await retriever.retrieve("test query", top_k=5, mode="sparse", score_threshold=0.8)

    @pytest.mark.asyncio
    async def test_graph_expansion_empty_chunks(self, mock_chunk_retriever):
        """Test graph expansion (empty initial chunks)"""
        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)

        results = await retriever.graph_expansion(
            query="test query",
            chunks=[],
            topk=5,
            mode="hybrid",
        )
        # Should return empty list or fallback results
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_close(self, mock_chunk_retriever, mock_triple_retriever):
        """Test closing retriever"""
        mock_chunk_retriever.close = AsyncMock()
        mock_triple_retriever.close = AsyncMock()

        retriever = GraphRetriever(
            chunk_retriever=mock_chunk_retriever,
            triple_retriever=mock_triple_retriever,
        )

        await retriever.close()
        mock_chunk_retriever.close.assert_called_once()
        mock_triple_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sync_close(self, mock_chunk_retriever):
        """Test closing retriever (synchronous close method)"""
        mock_chunk_retriever.close = MagicMock()

        retriever = GraphRetriever(chunk_retriever=mock_chunk_retriever)

        await retriever.close()
        mock_chunk_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_close_method(self):
        """Test closing retriever without close method"""
        mock_retriever = MagicMock()
        del mock_retriever.close  # Remove close method

        retriever = GraphRetriever(chunk_retriever=mock_retriever)
        # Should not raise exception
        await retriever.close()


class TestTripleBeamSearch:
    """TripleBeamSearch tests - testing critical beam search functionality"""

    @pytest.fixture
    def mock_retriever_with_embed(self):
        """Create mock retriever with embed model"""
        retriever = AsyncMock()
        embed_model = AsyncMock()
        embed_model.max_batch_size = 32
        embed_model.embed_documents = AsyncMock()
        retriever.embed_model = embed_model
        retriever.retrieve = AsyncMock()
        return retriever

    @classmethod
    def test_init_invalid_max_length(cls, mock_retriever_with_embed):
        """Test TripleBeamSearch validation"""
        with pytest.raises(BaseError, match="expect max_length >= 1"):
            TripleBeamSearch(
                retriever=mock_retriever_with_embed,
                max_length=0,
            )

    @pytest.mark.asyncio
    async def test_beam_search_no_embed_model(self):
        """Test beam search fails gracefully without embed model"""
        retriever = AsyncMock()
        retriever.embed_model = None
        search = TripleBeamSearch(retriever=retriever)

        triples = [RetrievalResult(text="triple1", score=0.9)]

        with pytest.raises(BaseError, match="embed_model is required"):
            await search.beam_search("test query", triples)

    @pytest.mark.asyncio
    async def test_beam_search_basic(self, mock_retriever_with_embed):
        """Test beam search core algorithm"""
        # Setup mock embeddings - need to handle multiple calls
        # First call: initial embedding of triples + query
        # Second call: embedding of beam paths during expansion
        mock_retriever_with_embed.embed_model.embed_documents = AsyncMock(
            side_effect=[
                # First call: embed triples + query
                [
                    [0.1, 0.2, 0.3],  # triple1 embedding
                    [0.2, 0.3, 0.4],  # triple2 embedding
                    [0.15, 0.25, 0.35],  # query embedding
                ],
                # Second call: embed beam paths (2 beams without expansion)
                [
                    [0.1, 0.2, 0.3],  # beam1 path
                    [0.2, 0.3, 0.4],  # beam2 path
                ],
            ]
        )

        # Mock retrieve to return empty (no candidates for expansion)
        mock_retriever_with_embed.retrieve = AsyncMock(return_value=[])

        triples = [
            RetrievalResult(
                text="entity1 relation entity2",
                score=0.9,
                metadata={"triple": '["entity1", "relation", "entity2"]'},
            ),
            RetrievalResult(
                text="entity3 relation entity4",
                score=0.8,
                metadata={"triple": '["entity3", "relation", "entity4"]'},
            ),
        ]

        search = TripleBeamSearch(
            retriever=mock_retriever_with_embed,
            num_beams=2,
            max_length=2,
        )

        beams = await search.beam_search("test query", triples)

        assert len(beams) == 2
        assert all(isinstance(b, TripleBeam) for b in beams)
        # Since there are no candidates, beams remain at length 1 (no expansion happened)
        assert all(len(b) == 1 for b in beams)

    @pytest.mark.asyncio
    async def test_search_candidates_bm25_index_type_uses_sparse_mode(self, mock_retriever_with_embed):
        """Triple candidate search maps retriever index_type bm25 to retrieve(mode='sparse')."""
        mock_retriever_with_embed.index_type = "bm25"
        mock_retriever_with_embed.retrieve = AsyncMock(return_value=[])
        search = TripleBeamSearch(retriever=mock_retriever_with_embed, num_candidates_per_beam=50)
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="entity1 relation entity2",
                    score=0.9,
                    metadata={"triple": '["entity1", "relation", "entity2"]'},
                )
            ],
            0.9,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        mock_retriever_with_embed.retrieve.assert_called_once()
        _, kwargs = mock_retriever_with_embed.retrieve.call_args
        assert kwargs["mode"] == "sparse"
        assert kwargs["top_k"] == 50
        assert "entity1" in kwargs["query"] and "entity2" in kwargs["query"]

    @pytest.mark.asyncio
    async def test_search_candidates_non_bm25_index_type_passes_through(self, mock_retriever_with_embed):
        """Other index_type values are forwarded to retrieve unchanged."""
        mock_retriever_with_embed.index_type = "hybrid"
        mock_retriever_with_embed.retrieve = AsyncMock(return_value=[])
        search = TripleBeamSearch(retriever=mock_retriever_with_embed)
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="a rel b",
                    score=1.0,
                    metadata={"triple": '["a", "rel", "b"]'},
                )
            ],
            1.0,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        _, kwargs = mock_retriever_with_embed.retrieve.call_args
        assert kwargs["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_search_candidates_uses_retrieve_mode_when_no_index_type(self):
        """When nested retriever has no index_type, retrieve_mode from GraphRetriever must be used."""
        stub = _RetrieverStubNoIndexType()
        assert not hasattr(stub, "index_type")
        search = TripleBeamSearch(
            retriever=stub,
            retrieve_mode="hybrid",
            num_candidates_per_beam=7,
        )
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="entity1 relation entity2",
                    score=0.9,
                    metadata={"triple": '["entity1", "relation", "entity2"]'},
                )
            ],
            0.9,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        stub.retrieve.assert_called_once()
        _, kwargs = stub.retrieve.call_args
        assert kwargs["mode"] == "hybrid"
        assert kwargs["top_k"] == 7

    @pytest.mark.asyncio
    async def test_search_candidates_retrieve_mode_vector_when_no_index_type(self):
        stub = _RetrieverStubNoIndexType()
        search = TripleBeamSearch(
            retriever=stub,
            retrieve_mode="vector",
            num_candidates_per_beam=3,
        )
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="x rel y",
                    score=1.0,
                    metadata={"triple": '["x", "rel", "y"]'},
                )
            ],
            1.0,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        assert stub.retrieve.call_args.kwargs["mode"] == "vector"

    @pytest.mark.asyncio
    async def test_search_candidates_defaults_hybrid_without_index_type_or_retrieve_mode(self):
        """Regression: getattr(..., None) + default hybrid avoids AttributeError on HybridRetriever."""
        stub = _RetrieverStubNoIndexType()
        search = TripleBeamSearch(retriever=stub, num_candidates_per_beam=5)
        assert search.retrieve_mode is None
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="a rel b",
                    score=1.0,
                    metadata={"triple": '["a", "rel", "b"]'},
                )
            ],
            1.0,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        assert stub.retrieve.call_args.kwargs["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_search_candidates_retriever_index_type_overrides_retrieve_mode(self):
        """Explicit index_type on retriever wins over retrieve_mode."""
        stub = _RetrieverStubNoIndexType(index_type="vector")
        search = TripleBeamSearch(
            retriever=stub,
            retrieve_mode="hybrid",
            num_candidates_per_beam=2,
        )
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="p rel q",
                    score=1.0,
                    metadata={"triple": '["p", "rel", "q"]'},
                )
            ],
            1.0,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        assert stub.retrieve.call_args.kwargs["mode"] == "vector"

    @pytest.mark.asyncio
    async def test_search_candidates_real_hybrid_retriever_no_index_type_uses_retrieve_mode(self):
        """Production HybridRetriever has no index_type until injected; retrieve_mode must suffice."""
        vs = MagicMock()
        emb = AsyncMock()
        hr = HybridRetriever(vector_store=vs, embed_model=emb)
        assert getattr(hr, "index_type", None) is None
        hr.retrieve = AsyncMock(return_value=[])
        search = TripleBeamSearch(retriever=hr, retrieve_mode="hybrid", num_candidates_per_beam=11)
        beam = TripleBeam(
            [
                RetrievalResult(
                    text="u rel v",
                    score=1.0,
                    metadata={"triple": '["u", "rel", "v"]'},
                )
            ],
            1.0,
        )

        await search._search_candidates(beam)  # pylint: disable=protected-access

        hr.retrieve.assert_called_once()
        assert hr.retrieve.call_args.kwargs["mode"] == "hybrid"


class TestGraphRetrieverDynamicRetrieverIndexType:
    """get_retriever_for_mode copies GraphRetriever.index_type onto dynamically built retrievers."""

    @staticmethod
    def test_injects_index_type_hybrid_onto_hybrid_triple_retriever(
        mock_vector_store, mock_embed_model
    ):
        gr = GraphRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            chunk_collection="kb_x_chunks",
            triple_collection="kb_x_triples",
        )
        gr.index_type = "hybrid"
        triple_r = gr.get_retriever_for_mode("hybrid", is_chunk=False)
        assert triple_r.index_type == "hybrid"

    @staticmethod
    def test_injects_index_type_onto_vector_retriever(mock_vector_store, mock_embed_model):
        gr = GraphRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            chunk_collection="kb_x_chunks",
            triple_collection="kb_x_triples",
        )
        gr.index_type = "vector"
        r = gr.get_retriever_for_mode("vector", is_chunk=True)
        assert r.index_type == "vector"

    @staticmethod
    def test_skips_injection_when_graph_index_type_unset(mock_vector_store, mock_embed_model):
        gr = GraphRetriever(
            vector_store=mock_vector_store,
            embed_model=mock_embed_model,
            chunk_collection="kb_x_chunks",
            triple_collection="kb_x_triples",
        )
        assert gr.index_type is None
        triple_r = gr.get_retriever_for_mode("hybrid", is_chunk=False)
        assert not hasattr(triple_r, "index_type")
