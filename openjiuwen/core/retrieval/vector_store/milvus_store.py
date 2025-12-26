# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Milvus 向量存储实现

支持向量搜索、稀疏搜索（BM25）和混合搜索。
"""
import asyncio
from typing import Any, List, Optional

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
from openjiuwen.core.retrieval.common.config import VectorStoreConfig
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion


class MilvusVectorStore(VectorStore):
    """Milvus 向量存储实现"""

    def __init__(
        self,
        config: VectorStoreConfig,
        milvus_uri: str,
        milvus_token: Optional[str] = None,
        text_field: str = "content",
        vector_field: str = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        **kwargs: Any,
    ):
        """
        初始化 Milvus 向量存储
        
        Args:
            config: 向量存储配置
            milvus_uri: Milvus URI
            milvus_token: Milvus Token（可选）
            text_field: 文本字段名
            vector_field: 向量字段名
            sparse_vector_field: 稀疏向量字段名
            metadata_field: 元数据字段名
        """
        self.config = config
        self.collection_name = config.collection_name
        self.milvus_uri = milvus_uri
        self.milvus_token = milvus_token
        self.text_field = text_field
        self.vector_field = vector_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        
        self._client = MilvusClient(
            uri=self.milvus_uri,
            token=self.milvus_token,
        )

    @property
    def client(self) -> MilvusClient:
        """获取 Milvus 客户端"""
        return self._client

    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs: Any,
    ) -> None:
        if batch_size is None or batch_size <= 0:
            batch_size = 128

        processed = 0
        total = len(data)
        cache: list[dict] = []
        for doc in data:
            cache.append(doc)
            if len(cache) >= batch_size:
                nodes = cache[:batch_size]
                cache = []
                await asyncio.to_thread(
                    self._client.insert,
                    collection_name=self.collection_name,
                    data=nodes,
                )
                processed += len(nodes)
                if processed % 100 == 0:
                    logger.info(
                        "Written %d/%d records to %s",
                        processed,
                        total,
                        self.collection_name,
                    )
        if cache:
            await asyncio.to_thread(
                self._client.insert,
                collection_name=self.collection_name,
                data=cache,
            )
            processed += len(cache)
        logger.info(
            "Writing completed, total %d/%d records to %s",
            processed,
            total,
            self.collection_name,
        )

        # Flush using client API
        self._client.flush(self.collection_name)

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """向量搜索"""
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field]

        # 构建过滤表达式
        filter_expr = None
        if filters:
            # 简单的过滤表达式构建（可以根据需要扩展）
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f'{key} == "{value}"')
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)

        # 执行搜索
        results = await asyncio.to_thread(
            self._client.search,
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field=self.vector_field,
            limit=top_k,
            output_fields=output_fields,
            search_params={"metric_type": "COSINE", "params": {}},
            filter=filter_expr,
        )

        if results and len(results) > 0:
            return self._milvus_result_to_search_results(results[0], mode="vector")
        return []

    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """稀疏搜索（BM25）"""
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field]

        # 构建过滤表达式
        filter_expr = None
        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f'{key} == "{value}"')
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)

        try:
            # 使用原生 BM25 全文搜索
            results = await asyncio.to_thread(
                self._client.search,
                collection_name=self.collection_name,
                data=[query_text],  # 直接传递文本，BM25 函数会处理分词
                anns_field=self.sparse_vector_field,
                limit=top_k,
                output_fields=output_fields,
                search_params={"metric_type": "BM25"},
                filter=filter_expr,
            )

            if results and len(results) > 0:
                return self._milvus_result_to_search_results(results[0], mode="sparse")
            return []
        except Exception as e:
            logger.warning(f"BM25 text search failed: {e}")
            return []

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """混合搜索（稀疏检索 + 向量检索）"""
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field]

        # 构建过滤表达式
        filter_expr = None
        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f'{key} == "{value}"')
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)

        try:
            # 构建搜索请求
            search_requests = []

            # 密集向量搜索请求
            if query_vector is not None:
                dense_req = AnnSearchRequest(
                    data=[query_vector],
                    anns_field=self.vector_field,
                    param={"metric_type": "COSINE", "params": {}},
                    limit=top_k,
                )
                search_requests.append(dense_req)

            # 稀疏 BM25 搜索请求
            sparse_req = AnnSearchRequest(
                data=[query_text],  # 直接传递文本
                anns_field=self.sparse_vector_field,
                param={"metric_type": "BM25"},
                limit=top_k,
            )
            search_requests.append(sparse_req)

            if not search_requests:
                return []

            # 使用原生混合搜索和 RRF 排序
            results = await asyncio.to_thread(
                self._client.hybrid_search,
                collection_name=self.collection_name,
                reqs=search_requests,
                ranker=RRFRanker(k=60),  # RRF with k=60
                limit=top_k,
                output_fields=output_fields,
                filter=filter_expr,
            )

            if results and len(results) > 0:
                result_list = results[0] if isinstance(results[0], list) else results
                return self._milvus_result_to_search_results(result_list, mode="hybrid")
            return []
        except Exception as e:
            logger.warning(
                f"Hybrid search failed, falling back to separate searches: {e}"
            )
            # 回退到分别搜索然后融合
            return await self._hybrid_search_fallback(
                query_text, query_vector, top_k, filters
            )

    async def _hybrid_search_fallback(
        self,
        query_text: str,
        query_vector: Optional[List[float]],
        top_k: int,
        filters: Optional[dict],
    ) -> List[SearchResult]:
        """回退混合搜索：分别执行搜索然后融合"""
        # 并发执行两个搜索
        task_vector = (
            asyncio.create_task(self.search(query_vector, top_k, None))
            if query_vector
            else asyncio.create_task(asyncio.coroutine(lambda: [])())
        )
        task_text = asyncio.create_task(self.sparse_search(query_text, top_k, None))

        results_vector = await task_vector
        results_text = await task_text

        fused_results = rrf_fusion([results_vector, results_text], k=60)
        return fused_results[:top_k]

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """删除向量"""
        try:
            result = await asyncio.to_thread(
                self._client.delete,
                collection_name=self.collection_name,
                ids=ids,
                filter=filter_expr,
            )
            self._client.flush(self.collection_name)

            # 检查删除结果
            if isinstance(result, dict):
                return result.get("delete_count", 0) > 0
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return False

    def _milvus_result_to_search_results(
        self,
        results: List[dict],
        mode: str,
    ) -> List[SearchResult]:
        """将 Milvus 搜索结果转换为 SearchResult 列表"""
        search_results = []
        for item in results:
            # 提取字段
            result_id = str(item.get("id", item.get("pk", "")))
            text = item.get(self.text_field, "")
            metadata = item.get(self.metadata_field, {})
            if isinstance(metadata, str):
                import json

                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}

            # 带出 chunk_id 便于上层关联
            if item.get("chunk_id") is not None:
                metadata.setdefault("chunk_id", item.get("chunk_id"))

            raw_score = item.get("score", item.get("distance", None))
            raw_score_val = float(raw_score) if raw_score is not None else None
            raw_score_scaled: Optional[float] = None
            final_score: float = 0.0

            if mode == "vector":
                if raw_score_val is not None:
                    raw_score_scaled = (raw_score_val + 1.0) / 2.0
                    final_score = raw_score_scaled
            elif mode == "sparse":
                if raw_score_val is not None:
                    final_score = raw_score_val
            else:  # hybrid 或其他
                if raw_score_val is not None:
                    final_score = raw_score_val

            metadata.setdefault("raw_score", raw_score_val)
            if raw_score_scaled is not None:
                metadata.setdefault("raw_score_scaled", raw_score_scaled)

            search_result = SearchResult(
                id=result_id,
                text=text,
                score=final_score,
                metadata=metadata,
            )
            search_results.append(search_result)

        return search_results

    def close(self) -> None:
        """关闭向量存储"""
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception as e:
            logger.warning(f"Failed to close Milvus client: {e}")
