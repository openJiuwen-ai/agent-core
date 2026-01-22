# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Agentic retriever test cases
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.retrieval import AgenticRetriever
from openjiuwen.core.retrieval import GraphRetriever
from openjiuwen.core.retrieval import RetrievalResult
from openjiuwen.core.common.exception.errors import BaseError


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
    def test_init_success(cls, mock_graph_retriever, mock_llm_client):
        """Test successful initialization"""
        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            llm_model_name="test-model",
            max_iter=3,
            agent_topk=15,
        )
        assert retriever.graph_retriever == mock_graph_retriever
        assert retriever.llm == mock_llm_client
        assert retriever.llm_model_name == "test-model"
        assert retriever.max_iter == 3
        assert retriever.agent_topk == 15

    @classmethod
    def test_init_with_defaults(cls, mock_graph_retriever, mock_llm_client):
        """Test initialization with default values"""
        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )
        assert retriever.max_iter == 3
        assert retriever.agent_topk == 15

    @classmethod
    def test_init_without_graph_retriever(cls, mock_llm_client):
        """Test initialization without graph retriever"""
        with pytest.raises(BaseError, match="graph_retriever is required"):
            AgenticRetriever(graph_retriever=None, llm_client=mock_llm_client)

    @classmethod
    def test_init_without_llm_client(cls, mock_graph_retriever):
        """Test initialization without LLM client"""
        with pytest.raises(BaseError, match="llm_client is required"):
            AgenticRetriever(graph_retriever=mock_graph_retriever, llm_client=None)

    @pytest.mark.asyncio
    async def test_retrieve_success_single_iteration(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval success (single iteration)"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_response = MagicMock()
        mock_response.content = "original query"  # Rewritten query same as original, should stop
        mock_llm_client.ainvoke = AsyncMock(return_value=mock_response)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        # Should only iterate once since rewritten query is same as original
        assert mock_graph_retriever.retrieve.call_count == 1

    @pytest.mark.asyncio
    async def test_retrieve_multiple_iterations(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval (multiple iterations)"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        # First rewrite returns new query, second returns same query
        mock_responses = [
            MagicMock(content="rewritten query 1"),
            MagicMock(content="rewritten query 1"),  # Different from original, but same as second
        ]
        mock_llm_client.ainvoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        # Should iterate 2 times (first + one after rewrite)
        assert mock_graph_retriever.retrieve.call_count == 2

    @pytest.mark.asyncio
    async def test_retrieve_max_iterations(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval reaching maximum iterations"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        # Each time returns a different rewritten query
        mock_responses = [MagicMock(content=f"rewritten query {i}") for i in range(3)]
        mock_llm_client.ainvoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )

        results = await retriever.retrieve("original query", top_k=5)
        assert len(results) == 1
        # Should reach maximum iteration count
        assert mock_graph_retriever.retrieve.call_count == 3

    @pytest.mark.asyncio
    async def test_retrieve_without_top_k(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval without top_k"""
        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        with pytest.raises(BaseError, match="top_k is required"):
            await retriever.retrieve("test query", top_k=None)

    @pytest.mark.asyncio
    async def test_retrieve_with_custom_mode(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval with custom mode"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_response = MagicMock()
        mock_response.content = "original query"
        mock_llm_client.ainvoke = AsyncMock(return_value=mock_response)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        results = await retriever.retrieve("test query", top_k=5, mode="vector")
        assert len(results) == 1
        # Verify specified mode is used
        call_kwargs = mock_graph_retriever.retrieve.call_args[1]
        assert call_kwargs["mode"] == "vector"

    @pytest.mark.asyncio
    async def test_retrieve_with_score_threshold(self, mock_graph_retriever, mock_llm_client):
        """Test retrieval with score threshold"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_response = MagicMock()
        mock_response.content = "original query"
        mock_llm_client.ainvoke = AsyncMock(return_value=mock_response)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        results = await retriever.retrieve("test query", top_k=5, score_threshold=0.8)
        assert len(results) == 1
        # Verify score threshold is passed
        call_kwargs = mock_graph_retriever.retrieve.call_args[1]
        assert call_kwargs["score_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_retrieve_fusion_multiple_results(self, mock_graph_retriever, mock_llm_client):
        """Test fusing multiple retrieval results"""
        # Simulate multiple iterations returning different results
        mock_results = [
            [RetrievalResult(text="Result 1", score=0.9)],
            [RetrievalResult(text="Result 2", score=0.8)],
        ]
        mock_graph_retriever.retrieve = AsyncMock(side_effect=mock_results)

        mock_responses = [
            MagicMock(content="rewritten query"),
            MagicMock(content="rewritten query"),  # Second time same, stop
        ]
        mock_llm_client.ainvoke = AsyncMock(side_effect=mock_responses)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
            max_iter=3,
        )

        results = await retriever.retrieve("test query", top_k=5)
        # Should fuse multiple results
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_batch_retrieve(self, mock_graph_retriever, mock_llm_client):
        """Test batch retrieval"""
        mock_graph_retriever.retrieve = AsyncMock(
            return_value=[
                RetrievalResult(text="Result 1", score=0.9),
            ]
        )

        mock_response = MagicMock()
        mock_response.content = "original query"
        mock_llm_client.ainvoke = AsyncMock(return_value=mock_response)

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
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
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        await retriever.close()
        mock_graph_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sync_close(self, mock_graph_retriever, mock_llm_client):
        """Test closing retriever (synchronous close method)"""
        mock_graph_retriever.close = MagicMock()

        retriever = AgenticRetriever(
            graph_retriever=mock_graph_retriever,
            llm_client=mock_llm_client,
        )

        await retriever.close()
        mock_graph_retriever.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_close_method(self, mock_llm_client):
        """Test closing graph retriever without close method"""
        mock_graph = MagicMock()
        del mock_graph.close  # Remove close method

        retriever = AgenticRetriever(
            graph_retriever=mock_graph,
            llm_client=mock_llm_client,
        )
        # Should not raise exception
        await retriever.close()
