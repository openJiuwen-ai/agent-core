# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Agentic retriever test cases
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import AgenticRetriever, GraphRetriever, RetrievalResult, Retriever


@pytest.fixture
def mock_graph_retriever():
    """Create mock graph retriever"""
    retriever = AsyncMock(spec=GraphRetriever)
    retriever.retrieve = AsyncMock(
        return_value=[
            RetrievalResult(text="Result 1", score=0.9),
            RetrievalResult(text="Result 2", score=0.8),
        ]
    )
    retriever.index_type = "hybrid"
    return retriever


@pytest.fixture
def mock_base_retriever():
    """Create mock base retriever (non-graph)"""
    retriever = AsyncMock(spec=Retriever)
    retriever.retrieve = AsyncMock(
        return_value=[
            RetrievalResult(text="Result 1", score=0.9),
            RetrievalResult(text="Result 2", score=0.8),
        ]
    )
    retriever.index_type = "hybrid"
    return retriever


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client"""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_llm_response():
    """Create mock LLM response"""
    response = MagicMock()
    response.content = "rewritten query"
    return response


class TestAgenticRetriever:
    """Agentic retriever tests"""

    @classmethod
    def test_init_success_with_graph_retriever(cls, mock_graph_retriever, mock_llm_client):
        """Test successful initialization with graph retriever"""
        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )
        assert retriever.retriever == mock_graph_retriever
        assert retriever.llm == mock_llm_client
        assert retriever.max_iter == 3
        assert retriever.is_graph_retriever is True

    @classmethod
    def test_init_success_with_base_retriever(cls, mock_base_retriever, mock_llm_client):
        """Test successful initialization with a generic (non-graph) retriever"""
        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
            max_iter=2,
        )
        assert retriever.retriever == mock_base_retriever
        assert retriever.llm == mock_llm_client
        assert retriever.max_iter == 2
        assert retriever.is_graph_retriever is False

    @classmethod
    def test_init_with_defaults(cls, mock_graph_retriever, mock_llm_client):
        """Test initialization with default values"""
        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )
        assert retriever.max_iter == 2

    @classmethod
    def test_init_with_invalid_max_iter(cls, mock_graph_retriever, mock_llm_client):
        """Test initialization with invalid max_iter falls back to default"""
        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=-1,
        )
        assert retriever.max_iter == 2

    @classmethod
    def test_init_without_retriever(cls, mock_llm_client):
        """Test initialization without retriever"""
        with pytest.raises(BaseError, match="retriever is required"):
            AgenticRetriever(retriever=None, llm_client=mock_llm_client)

    @classmethod
    def test_init_without_llm_client(cls, mock_graph_retriever):
        """Test initialization without LLM client"""
        with pytest.raises(BaseError, match="llm_client is required"):
            AgenticRetriever(retriever=mock_graph_retriever, llm_client=None)

    @classmethod
    def test_default_mode_vector(cls, mock_llm_client):
        """Test default mode when index_type is vector"""
        mock_retriever = AsyncMock(spec=Retriever)
        mock_retriever.index_type = "vector"
        r = AgenticRetriever(retriever=mock_retriever, llm_client=mock_llm_client)
        assert r.default_mode == "vector"

    @classmethod
    def test_default_mode_bm25(cls, mock_llm_client):
        """Test default mode when index_type is bm25"""
        mock_retriever = AsyncMock(spec=Retriever)
        mock_retriever.index_type = "bm25"
        r = AgenticRetriever(retriever=mock_retriever, llm_client=mock_llm_client)
        assert r.default_mode == "sparse"

    @classmethod
    def test_default_mode_hybrid(cls, mock_llm_client):
        """Test default mode when index_type is hybrid"""
        mock_retriever = AsyncMock(spec=Retriever)
        mock_retriever.index_type = "hybrid"
        r = AgenticRetriever(retriever=mock_retriever, llm_client=mock_llm_client)
        assert r.default_mode == "hybrid"

    # ---- Graph retriever path tests ----

    @pytest.mark.asyncio
    async def test_retrieve_with_graph_single_iteration(self, mock_graph_retriever, mock_llm_client):
        """Test graph retrieval success (single iteration, sufficient evidence)"""
        mock_chunk_retriever = AsyncMock()
        mock_chunk_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )
        mock_triple_retriever = AsyncMock()
        mock_triple_retriever.retrieve_search_results = AsyncMock(return_value=[])
        mock_graph_retriever.get_retriever_for_mode = MagicMock(
            side_effect=lambda mode, is_chunk: mock_chunk_retriever if is_chunk else mock_triple_retriever
        )
        mock_graph_retriever.graph_expansion = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_responses = [
            MagicMock(content="[]"),  # _read (proximal triples for graph_expansion)
            MagicMock(content="[]"),  # _read (main read)
            MagicMock(content='{"sufficient": true, "next_question": null}'),  # _rewrite
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=2,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_retrieve_with_graph_multiple_iterations(self, mock_graph_retriever, mock_llm_client):
        """Test graph retrieval with multiple iterations"""
        mock_chunk_retriever = AsyncMock()
        mock_chunk_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )
        mock_triple_retriever = AsyncMock()
        mock_triple_retriever.retrieve_search_results = AsyncMock(return_value=[])
        mock_graph_retriever.get_retriever_for_mode = MagicMock(
            side_effect=lambda mode, is_chunk: mock_chunk_retriever if is_chunk else mock_triple_retriever
        )
        mock_graph_retriever.graph_expansion = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_responses = [
            MagicMock(content="[]"),  # turn 1: _read (proximal)
            MagicMock(content="[]"),  # turn 1: _read (main)
            MagicMock(content='{"sufficient": false, "next_question": "rewritten query 1"}'),  # turn 1: _rewrite
            MagicMock(content="[]"),  # turn 2: _read (proximal)
            MagicMock(content="[]"),  # turn 2: _read (main)
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=2,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) >= 1
        assert mock_chunk_retriever.retrieve.call_count == 2

    # ---- Generic (non-graph) retriever path tests ----

    @pytest.mark.asyncio
    async def test_retrieve_generic_single_iteration(self, mock_base_retriever, mock_llm_client):
        """Test generic (non-graph) retrieval - single iteration, sufficient evidence"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_responses = [
            MagicMock(content="[]"),  # _read
            MagicMock(content='{"sufficient": true, "next_question": null}'),  # _rewrite
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
            max_iter=2,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        assert mock_base_retriever.retrieve.call_count == 1

    @pytest.mark.asyncio
    async def test_retrieve_generic_multiple_iterations(self, mock_base_retriever, mock_llm_client):
        """Test generic retrieval with multiple iterations"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_responses = [
            MagicMock(content="[]"),  # _read iter 1
            MagicMock(content='{"sufficient": false, "next_question": "rewritten query 1"}'),  # _rewrite iter 1
            MagicMock(content="[]"),  # _read iter 2
            MagicMock(content='{"sufficient": false, "next_question": "rewritten query 2"}'),  # _rewrite iter 2
            MagicMock(content="[]"),  # _read iter 3 (max iter)
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        assert mock_base_retriever.retrieve.call_count == 3

    @pytest.mark.asyncio
    async def test_retrieve_generic_max_iterations(self, mock_base_retriever, mock_llm_client):
        """Test generic retrieval reaching maximum iterations"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_responses = [
            MagicMock(content="[]"),  # _read iter 1
            MagicMock(content='{"sufficient": false, "next_question": "rewritten query 1"}'),  # _rewrite iter 1
            MagicMock(content="[]"),  # _read iter 2
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        assert mock_base_retriever.retrieve.call_count == 2

    @pytest.mark.asyncio
    async def test_retrieve_without_valid_top_k(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval with invalid top_k"""
        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        with pytest.raises(BaseError, match="top_k is invalid"):
            await retriever.retrieve("test query", top_k=None)

    @pytest.mark.asyncio
    async def test_retrieve_with_negative_top_k(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval with negative top_k"""
        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        with pytest.raises(BaseError, match="top_k is invalid"):
            await retriever.retrieve("test query", top_k=-1)

    @pytest.mark.asyncio
    async def test_retrieve_with_custom_mode(self, mock_base_retriever, mock_llm_client):
        """Test retrieval with custom mode"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_llm_client.invoke = AsyncMock(
            side_effect=[
                MagicMock(content="[]"),  # _read
                MagicMock(content='{"sufficient": true, "next_question": null}'),  # _rewrite
            ]
        )

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
        )

        results = await retriever.retrieve("test query", top_k=5, mode="vector")
        assert len(results) == 1
        call_kwargs = mock_base_retriever.retrieve.call_args[1]
        assert call_kwargs["mode"] == "vector"

    @pytest.mark.asyncio
    async def test_retrieve_with_score_threshold(self, mock_base_retriever, mock_llm_client):
        """Test retrieval with score threshold"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_llm_client.invoke = AsyncMock(
            side_effect=[
                MagicMock(content="[]"),  # _read
                MagicMock(content='{"sufficient": true, "next_question": null}'),  # _rewrite
            ]
        )

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
        )

        results = await retriever.retrieve("test query", top_k=5, score_threshold=0.8)
        assert len(results) == 1
        call_kwargs = mock_base_retriever.retrieve.call_args[1]
        assert call_kwargs["score_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_retrieve_fusion_multiple_results(self, mock_base_retriever, mock_llm_client):
        """Test fusing multiple retrieval results (generic path)"""
        mock_results = [
            [RetrievalResult(text="Result 1", score=0.9)],
            [RetrievalResult(text="Result 2", score=0.8)],
        ]
        mock_base_retriever.retrieve = AsyncMock(side_effect=mock_results)

        mock_responses = [
            MagicMock(content="[]"),  # _read iter 1
            MagicMock(content='{"sufficient": false, "next_question": "rewritten query"}'),  # _rewrite
            MagicMock(content="[]"),  # _read iter 2
        ]
        mock_llm_client.invoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
            max_iter=2,
        )

        results = await retriever.retrieve("test query", top_k=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_batch_retrieve(self, mock_base_retriever, mock_llm_client):
        """Test batch retrieval"""
        mock_base_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_llm_client.invoke = AsyncMock(
            side_effect=[
                # query 1
                MagicMock(content="[]"),
                MagicMock(content='{"sufficient": true, "next_question": null}'),
                # query 2
                MagicMock(content="[]"),
                MagicMock(content='{"sufficient": true, "next_question": null}'),
            ]
        )

        retriever = AgenticRetriever(
            retriever=mock_base_retriever,
            llm_client=mock_llm_client,
        )

        queries = ["query 1", "query 2"]
        results_list = await retriever.batch_retrieve(queries, top_k=5)
        assert len(results_list) == 2
        assert len(results_list[0]) == 1
        assert len(results_list[1]) == 1

    @pytest.mark.asyncio
    async def test_close(self, mock_graph_retriever, mock_llm_client):
        """Test closing retriever"""
        mock_graph_retriever.close = AsyncMock()

        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        await retriever.close()
        mock_graph_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sync_close(self, mock_graph_retriever, mock_llm_client):
        """Test closing retriever (synchronous close method)"""
        mock_graph_retriever.close = MagicMock()

        retriever = AgenticRetriever(
            retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        await retriever.close()
        mock_graph_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_close_method(self, mock_llm_client):
        """Test closing retriever without close method"""
        mock_ret = MagicMock()
        del mock_ret.close  # Remove close method

        retriever = AgenticRetriever(
            retriever=mock_ret,
            llm_client=mock_llm_client,
        )
        # Should not raise exception
        await retriever.close()
