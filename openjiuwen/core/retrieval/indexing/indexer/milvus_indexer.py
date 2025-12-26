# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Milvus 索引管理器实现

负责构建、更新和删除 Milvus 索引。
"""
import asyncio
from typing import Any, List, Optional, Dict

from pymilvus import DataType, Function, FunctionType, MilvusClient, MilvusException

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.common.config import IndexConfig
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore
from openjiuwen.core.retrieval.common.config import VectorStoreConfig


class MilvusIndexer(Indexer):
    """Milvus 索引管理器实现"""

    def __init__(
        self,
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
        初始化 Milvus 索引管理器
        
        Args:
            milvus_uri: Milvus URI
            milvus_token: Milvus Token（可选）
            text_field: 文本字段名
            vector_field: 向量字段名
            sparse_vector_field: 稀疏向量字段名
            metadata_field: 元数据字段名
        """
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

    async def build_index(
        self,
        chunks: List[TextChunk],
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs: Any,
    ) -> bool:
        """构建索引"""
        try:
            collection_name = config.index_name
            
            # 确保集合存在
            await self._ensure_collection(
                collection_name=collection_name,
                config=config,
                embed_model=embed_model,
            )

            # 如果需要向量索引，生成嵌入
            embeddings = None
            if config.index_type in ("vector", "hybrid"):
                if not embed_model:
                    raise ValueError(
                        "embed_model is required for vector/hybrid index type"
                    )
                texts = [chunk.text for chunk in chunks]
                embeddings = await embed_model.embed_documents(texts)
                for chunk, embedding in zip(chunks, embeddings):
                    chunk.embedding = embedding

            vector_store_config = VectorStoreConfig(
                collection_name=collection_name,
            )

            vector_store = MilvusVectorStore(
                config=vector_store_config,
                milvus_uri=self.milvus_uri,
                milvus_token=self.milvus_token,
            )

            # 将 TextChunk 转换为 Milvus 需要的字段，避免写入未在 schema 中定义的 id_ 字段
            data = []
            for idx, chunk in enumerate(chunks):
                meta = chunk.metadata or {}
                item = {
                    self.doc_id_field: chunk.doc_id,
                    self.text_field: chunk.text,
                    self.metadata_field: meta,
                }
                if chunk.embedding is not None:
                    item[self.vector_field] = chunk.embedding
                data.append(item)

            await vector_store.add(data=data)

            logger.info(
                f"Successfully built index {collection_name} with {len(chunks)} chunks"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to build index: {e}")
            return False

    async def update_index(
        self,
        chunks: List[TextChunk],
        doc_id: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs: Any,
    ) -> bool:
        """更新索引"""
        try:
            # 先删除旧数据
            await self.delete_index(doc_id, config.index_name)

            # 重新构建
            return await self.build_index(chunks, config, embed_model, **kwargs)
        except Exception as e:
            logger.error(f"Failed to update index: {e}")
            return False

    async def delete_index(
        self,
        doc_id: str,
        index_name: str,
        **kwargs: Any,
    ) -> bool:
        """删除索引"""
        try:
            filter_expr = f'{self.doc_id_field} == "{doc_id}"'
            result = await asyncio.to_thread(
                self._client.delete,
                collection_name=index_name,
                filter=filter_expr,
            )

            if isinstance(result, dict):
                delete_count = result.get("delete_count", 0)
            else:
                delete_count = int(result) if result else 0

            logger.info(f"Deleted {delete_count} entries for doc_id={doc_id}")
            return delete_count > 0
        except MilvusException as e:
            logger.error(f"Failed to delete index entries: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting index: {e}")
            return False

    async def index_exists(
        self,
        index_name: str,
    ) -> bool:
        """检查索引是否存在"""
        try:
            return await asyncio.to_thread(
                self._client.has_collection,
                collection_name=index_name,
            )
        except Exception as e:
            logger.error(f"Failed to check index existence: {e}")
            return False

    async def get_index_info(
        self,
        index_name: str,
    ) -> Dict[str, Any]:
        """获取索引信息"""
        try:
            if not await self.index_exists(index_name):
                return {"exists": False}

            # 优先使用 collection stats，避免 query count 触发分页错误
            stats = await asyncio.to_thread(
                self._client.get_collection_stats,
                collection_name=index_name,
            )
            row_count = 0
            if isinstance(stats, dict):
                # row_count 可能是字符串，或在 stats 列表里
                rc = stats.get("row_count")
                try:
                    row_count = int(rc or 0)
                except Exception:
                    row_count = 0
                if row_count == 0 and isinstance(stats.get("stats"), list):
                    for item in stats["stats"]:
                        if item.get("key") == "row_count":
                            try:
                                row_count = int(item.get("value") or 0)
                                break
                            except Exception:
                                logger.warning("Failed to get row count", exc_info=True)
                                continue

            collection_info = await asyncio.to_thread(
                self._client.describe_collection,
                collection_name=index_name,
            )

            return {
                "exists": True,
                "collection_name": index_name,
                "info": collection_info,
                "count": row_count,
            }
        except Exception as e:
            logger.error(f"Failed to get index info: {e}")
            return {"exists": False, "error": str(e)}

    async def _ensure_collection(
        self,
        collection_name: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
    ) -> None:
        """确保集合存在，如果不存在则创建"""
        if await self.index_exists(collection_name):
            return

        # 构建 schema
        schema = self._client.create_schema(
            auto_id=True,
            enable_dynamic_field=False,
        )

        index_params = self._client.prepare_index_params()

        # 文档 ID 字段
        schema.add_field(
            field_name=self.doc_id_field,
            datatype=DataType.VARCHAR,
            max_length=256,
        )

        # Add scalar index on document_id for fast deletion/filtering
        index_params.add_index(
            field_name=self.doc_id_field,
            index_type="INVERTED",  # Inverted index for VARCHAR
        )

        # 主键字段（自动生成）
        schema.add_field(
            field_name="pk",
            datatype=DataType.INT64,
            is_primary=True,
            auto_id=True,
        )

        # 文本内容字段（启用分析器用于 BM25）
        enable_bm25 = config.index_type in ("bm25", "hybrid")
        schema.add_field(
            field_name=self.text_field,
            datatype=DataType.VARCHAR,
            max_length=4096,
            enable_analyzer=enable_bm25,
            analyzer_params={"tokenizer": "jieba"} if enable_bm25 else {},
        )
        # BM25 sparse vector field for full-text search
        if enable_bm25:
            schema.add_field(
                field_name=self.sparse_vector_field,
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )

            # Add BM25 function to convert text to sparse vector
            bm25_function = Function(
                name="text_bm25_emb",
                input_field_names=[self.text_field],
                output_field_names=[self.sparse_vector_field],
                function_type=FunctionType.BM25,
            )
            schema.add_function(bm25_function)

            # Add sparse vector index for BM25
            index_params.add_index(
                field_name=self.sparse_vector_field,
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
            )

        # 向量字段（如果需要）
        if config.index_type in ("vector", "hybrid"):
            dimension = None
            if embed_model:
                dimension = embed_model.dimension
            
            # 如果维度是 0（占位值）或 None，通过实际调用获取真实维度
            if (dimension is None or dimension == 0) and embed_model:
                try:
                    test_embedding = await embed_model.embed_query("X")
                    dimension = len(test_embedding)
                    logger.debug(f"通过实际调用获取到维度: {dimension}")
                except Exception as e:
                    logger.warning(f"无法通过实际调用获取维度: {e}")
                    dimension = None

            if dimension is None or dimension == 0:
                raise ValueError(
                    "dimension is required for vector/hybrid index type"
                )

            schema.add_field(
                field_name=self.vector_field,
                datatype=DataType.FLOAT_VECTOR,
                dim=dimension,
            )

            # Add dense vector index
            index_params.add_index(
                field_name=self.vector_field,
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 1024},
            )

        # 元数据字段
        schema.add_field(
            field_name=self.metadata_field,
            datatype=DataType.JSON,
        )

        # 创建集合
        await asyncio.to_thread(
            self._client.create_collection,
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )

        logger.info(f"Created collection: {collection_name}")

    def close(self) -> None:
        """关闭索引管理器"""
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception as e:
            logger.warning(f"Failed to close Milvus client: {e}")
