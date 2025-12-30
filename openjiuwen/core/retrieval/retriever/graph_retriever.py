# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Graph Retriever Implementation

A graph retriever combining chunk retrieval and graph retrieval with graph expansion support.
"""
import asyncio
import itertools
from typing import Any, List, Optional, Dict, Literal

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion


class GraphRetriever(Retriever):
    """Graph retriever implementation combining chunk retrieval and graph retrieval"""

    def __init__(
        self,
        chunk_retriever: Optional[Retriever] = None,
        triple_retriever: Optional[Retriever] = None,
        vector_store: Optional[Any] = None,
        embed_model: Optional[Any] = None,
        chunk_collection: Optional[str] = None,
        triple_collection: Optional[str] = None,
        **kwargs: Any,
    ):
        """
        Initialize graph retriever
        
        Args:
            chunk_retriever: Chunk retriever (for document chunk retrieval, optional,
                dynamically created based on mode if not provided)
            triple_retriever: Triple retriever (for triple retrieval, optional,
                dynamically created based on mode if not provided)
            vector_store: Vector store instance (for dynamic retriever creation)
            embed_model: Embedding model instance (for dynamic retriever creation)
            chunk_collection: Chunk collection name (for dynamic retriever creation)
            triple_collection: Triple collection name (for dynamic retriever creation)
        """
        self.chunk_retriever = chunk_retriever
        self.triple_retriever = triple_retriever
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.chunk_collection = chunk_collection
        self.triple_collection = triple_collection
        self.index_type: Optional[str] = None  # Will be automatically injected by upper layer (e.g. KnowledgeBase)

    def _allowed_modes(self) -> Dict[str, set]:
        return {
            "vector": {"vector"},
            "bm25": {"sparse"},
            "hybrid": {"vector", "sparse", "hybrid"},
        }

    def _ensure_mode_allowed(self, mode: Literal["vector", "sparse", "hybrid"]) -> None:
        if self.index_type is None:
            # Don't enforce validation when index_type is not injected (ensured by upper layer)
            return
        allowed = self._allowed_modes().get(self.index_type)
        if allowed is None:
            raise ValueError(f"Unsupported index_type={self.index_type}")
        if mode not in allowed:
            raise ValueError(
                f"mode={mode} is incompatible with index_type={self.index_type}; "
                f"allowed modes: {sorted(allowed)}"
            )

    def _retriever_supports_mode(self, retriever: Retriever, mode: str) -> bool:
        from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
        from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
        from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever

        if isinstance(retriever, VectorRetriever):
            return mode == "vector"
        if isinstance(retriever, SparseRetriever):
            return mode == "sparse"
        if isinstance(retriever, HybridRetriever):
            return mode in {"vector", "sparse", "hybrid"}

        supported = getattr(retriever, "SUPPORTED_MODES", None)
        if supported is not None:
            return mode in supported
        return True

    def _get_retriever_for_mode(
        self,
        mode: Literal["vector", "sparse", "hybrid"],
        is_chunk: bool = True,
    ) -> Retriever:
        """
        Get corresponding retriever based on mode
        
        Args:
            mode: Retrieval mode
            is_chunk: Whether chunk retriever (True=chunk_retriever, False=triple_retriever)
            
        Returns:
            Corresponding retriever instance
        """
        self._ensure_mode_allowed(mode)
        
        # If fixed retriever is provided, use it directly (but need to check if it supports the mode)
        fixed_retriever = self.chunk_retriever if is_chunk else self.triple_retriever
        if fixed_retriever:
            if not self._retriever_supports_mode(fixed_retriever, mode):
                raise ValueError(
                    f"Provided {'chunk' if is_chunk else 'triple'} retriever "
                    f"{fixed_retriever.__class__.__name__} does not support mode={mode}"
                )
            return fixed_retriever
        
        # Dynamically create retriever
        if not self.vector_store:
            raise ValueError("vector_store is required for dynamic retriever creation")

        collection_name = self.chunk_collection if is_chunk else self.triple_collection
        self.vector_store.collection_name = collection_name
        if not collection_name:
            collection_type = "chunk" if is_chunk else "triple"
            raise ValueError(
                f"{collection_type}_collection is required for dynamic retriever creation"
            )

        # Create corresponding retriever based on mode
        if mode == "vector":
            from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
            if not self.embed_model:
                raise ValueError("embed_model is required for vector mode")
            retriever = VectorRetriever(
                vector_store=self.vector_store,
                embed_model=self.embed_model,
            )
        elif mode == "sparse":
            from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
            retriever = SparseRetriever(
                vector_store=self.vector_store,
            )
        else:  # hybrid
            from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
            retriever = HybridRetriever(
                vector_store=self.vector_store,
                embed_model=self.embed_model,
            )
        
        return retriever

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """
        Retrieve documents (graph retrieval)
        
        Args:
            query: Query string
            top_k: Number of results to return
            score_threshold: Score threshold
            mode: Retrieval mode (must be compatible with index_type)
            **kwargs: Additional parameters (may include topk_triples, graph_hops, etc.)
            
        Returns:
            List of retrieval results
        """
        self._ensure_mode_allowed(mode)
        topk_triples = kwargs.get("topk_triples", None)
        graph_hops = kwargs.get("graph_hops", 1)
        # GraphRetriever always performs graph expansion by default, caller doesn't need to pass graph_expansion flag
        if score_threshold is not None and mode != "vector":
            raise ValueError("score_threshold is only supported when mode='vector'")
        effective_threshold = score_threshold

        # Get corresponding retriever based on mode
        chunk_retriever = self._get_retriever_for_mode(mode, is_chunk=True)

        # First perform chunk retrieval
        chunk_results = await chunk_retriever.retrieve(
            query=query,
            top_k=top_k,
            score_threshold=effective_threshold,
            mode=mode,
        )

        logger.info(
            f"[graph] Graph retrieval: graph_expansion=True "
            f"chunk_hits={len(chunk_results)} topk={top_k} mode={mode}"
        )

        expanded_results = chunk_results
        for _ in range(max(1, graph_hops)):
            expanded_results = await self.graph_expansion(
                query=query,
                chunks=expanded_results,
                topk=top_k,
                topk_triples=topk_triples,
                mode=mode,
                score_threshold=effective_threshold,
                graph_hops=graph_hops,
            )
        return expanded_results

    async def graph_expansion(
        self,
        query: str,
        chunks: List[RetrievalResult],
        topk: Optional[int] = None,
        topk_triples: Optional[int] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        score_threshold: Optional[float] = None,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """
        Graph expansion: expand retrieval through triples based on initial chunk retrieval results
        
        Args:
            query: Query string
            chunks: Initial chunk retrieval results
            topk: Final return count
            topk_triples: Triple retrieval count
            mode: Retrieval mode
            
        Returns:
            List of expanded retrieval results
        """
        self._ensure_mode_allowed(mode)
        if not chunks:
            logger.warning("[graph] chunk_retriever returned empty, no results to expand (mode=%s)", mode)
            if mode == "sparse":
                sparse_retriever = self._get_retriever_for_mode("sparse", is_chunk=True)
                fallback = await sparse_retriever.retrieve(
                    query=query,
                    top_k=topk or 5,
                    mode="sparse",
                )
                return fallback[:topk] if topk else fallback
            return []

        chunk_ids = [c.chunk_id for c in chunks if c.chunk_id]
        if not chunk_ids:
            return chunks[:topk] if topk else chunks

        if topk_triples is None:
            topk_triples = len(chunks) * 5

        # Multi-hop expansion: allows introducing new chunk_ids (union), each hop can expand
        current_chunk_ids = set(chunk_ids)
        all_results = [chunks]

        # Use triple retriever with corresponding mode
        triple_retriever = self._get_retriever_for_mode(mode, is_chunk=False)
        triple_results = await triple_retriever.retrieve(
            query=query,
            top_k=topk_triples,
            mode=mode,
        )
        expanded_chunk_ids = {t.metadata.get("chunk_id") for t in triple_results if t.metadata.get("chunk_id")}
        target_doc_ids = {t.metadata.get("doc_id") for t in triple_results if t.metadata.get("doc_id")}
        target_chunk_ids = current_chunk_ids | expanded_chunk_ids
        if not target_chunk_ids and not target_doc_ids:
            return chunks[:topk] if topk else chunks

        # Use chunk retriever with corresponding mode
        chunk_retriever = self._get_retriever_for_mode(mode, is_chunk=True)
        candidate_chunks = await chunk_retriever.retrieve(
            query=query,
            top_k=topk_triples,
            mode=mode,
        )
        expanded_chunks: List[RetrievalResult] = []
        for c in candidate_chunks:
            if score_threshold is not None and c.score is not None and c.score < score_threshold:
                if c.chunk_id and c.chunk_id in target_chunk_ids:
                    continue
                if c.doc_id and c.doc_id in target_doc_ids:
                    continue
                expanded_chunks.append(c)
                current_chunk_ids.add(c.chunk_id)

        if expanded_chunks:
            all_results.append(expanded_chunks)

        fused = rrf_fusion(all_results, k=60) if len(all_results) > 1 else chunks
        return fused[:topk] if topk else fused

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[List[RetrievalResult]]:
        """Batch retrieval"""
        # Execute multiple retrievals concurrently
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        results = await asyncio.gather(*tasks)
        return results

    async def close(self) -> None:
        """Close retriever"""
        import inspect

        # Close fixed retrievers
        if self.chunk_retriever:
            close_fn = getattr(self.chunk_retriever, "close", None)
            if close_fn:
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
        if self.triple_retriever:
            close_fn = getattr(self.triple_retriever, "close", None)
            if close_fn:
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
