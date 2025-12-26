# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
图检索器实现

结合块检索和图检索的图检索器，支持图扩展。
"""
import asyncio
import itertools
from typing import Any, List, Optional, Dict, Literal

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion


class GraphRetriever(Retriever):
    """图检索器实现，结合块检索和图检索"""

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
        初始化图检索器
        
        Args:
            chunk_retriever: 块检索器（用于检索文档块，可选，如果不提供则根据 mode 动态创建）
            triple_retriever: 三元组检索器（用于检索三元组，可选，如果不提供则根据 mode 动态创建）
            vector_store: 向量存储实例（用于动态创建检索器）
            embed_model: 嵌入模型实例（用于动态创建检索器）
            chunk_collection: 块集合名称（用于动态创建检索器）
            triple_collection: 三元组集合名称（用于动态创建检索器）
        """
        self.chunk_retriever = chunk_retriever
        self.triple_retriever = triple_retriever
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.chunk_collection = chunk_collection
        self.triple_collection = triple_collection
        self.index_type: Optional[str] = None  # 将由上层（如 KnowledgeBase）自动注入

    def _allowed_modes(self) -> Dict[str, set]:
        return {
            "vector": {"vector"},
            "bm25": {"sparse"},
            "hybrid": {"vector", "sparse", "hybrid"},
        }

    def _ensure_mode_allowed(self, mode: Literal["vector", "sparse", "hybrid"]) -> None:
        if self.index_type is None:
            # 未注入 index_type 时不强制校验（由上层保证）
            return
        allowed = self._allowed_modes().get(self.index_type)
        if allowed is None:
            raise ValueError(f"Unsupported index_type={self.index_type}")
        if mode not in allowed:
            raise ValueError(
                f"mode={mode} 与 index_type={self.index_type} 不兼容；"
                f"允许的模式: {sorted(allowed)}"
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
        根据 mode 获取对应的检索器
        
        Args:
            mode: 检索模式
            is_chunk: 是否为块检索器（True=chunk_retriever, False=triple_retriever）
            
        Returns:
            对应的检索器实例
        """
        self._ensure_mode_allowed(mode)
        
        # 如果提供了固定的检索器，直接使用（但需要检查是否支持该 mode）
        fixed_retriever = self.chunk_retriever if is_chunk else self.triple_retriever
        if fixed_retriever:
            if not self._retriever_supports_mode(fixed_retriever, mode):
                raise ValueError(
                    f"Provided {'chunk' if is_chunk else 'triple'} retriever "
                    f"{fixed_retriever.__class__.__name__} does not support mode={mode}"
                )
            return fixed_retriever
        
        # 动态创建检索器
        if not self.vector_store:
            raise ValueError("vector_store is required for dynamic retriever creation")

        collection_name = self.chunk_collection if is_chunk else self.triple_collection
        self.vector_store.collection_name = collection_name
        if not collection_name:
            collection_type = "chunk" if is_chunk else "triple"
            raise ValueError(
                f"{collection_type}_collection is required for dynamic retriever creation"
            )

        # 根据 mode 创建对应的检索器
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
        检索文档（图检索）
        
        Args:
            query: 查询字符串
            top_k: 返回数量
            score_threshold: 分数阈值
            mode: 检索模式（必须与 index_type 兼容）
            **kwargs: 额外参数（可包含 topk_triples, graph_hops 等）
            
        Returns:
            检索结果列表
        """
        self._ensure_mode_allowed(mode)
        topk_triples = kwargs.get("topk_triples", None)
        graph_hops = kwargs.get("graph_hops", 1)
        # GraphRetriever 默认总是执行图扩展，调用方无需再传 graph_expansion 开关
        if score_threshold is not None and mode != "vector":
            raise ValueError("score_threshold is only supported when mode='vector'")
        effective_threshold = score_threshold

        # 根据 mode 获取对应的检索器
        chunk_retriever = self._get_retriever_for_mode(mode, is_chunk=True)

        # 先进行块检索
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
        图扩展：基于初始块检索结果，通过三元组扩展检索
        
        Args:
            query: 查询字符串
            chunks: 初始块检索结果
            topk: 最终返回数量
            topk_triples: 三元组检索数量
            mode: 检索模式
            
        Returns:
            扩展后的检索结果列表
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

        # 多 hop 扩展：允许引入新 chunk_id（并集），每 hop 都可扩展
        current_chunk_ids = set(chunk_ids)
        all_results = [chunks]

        # 使用对应 mode 的三元组检索器
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

        # 使用对应 mode 的块检索器
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
        """批量检索"""
        # 并发执行多个检索
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        results = await asyncio.gather(*tasks)
        return results

    async def close(self) -> None:
        """关闭检索器"""
        import inspect

        # 关闭固定的检索器
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
