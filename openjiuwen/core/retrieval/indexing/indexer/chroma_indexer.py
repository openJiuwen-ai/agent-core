# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ChromaDB Index Manager Implementation

Responsible for building, updating and deleting ChromaDB indices.
"""
import asyncio
from typing import Any, List, Optional, Dict
import chromadb

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.common.config import IndexConfig
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
from openjiuwen.core.retrieval.common.config import VectorStoreConfig


class ChromaIndexer(Indexer):
    """ChromaDB index manager implementation"""

    def __init__(
        self,
        chroma_path: str,
        text_field: str = "content",
        vector_field: str = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        **kwargs: Any,
    ):
        """
        Initialize ChromaDB index manager
        
        Args:
            chroma_path: ChromaDB persistence path
            text_field: Text field name
            vector_field: Vector field name
            sparse_vector_field: Sparse vector field name
            metadata_field: Metadata field name
            doc_id_field: Document ID field name
        """
        if not chroma_path or not chroma_path.strip():
            raise ValueError("chroma_path is required and cannot be empty")
        
        self.chroma_path = chroma_path
        self.text_field = text_field
        self.vector_field = vector_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        
        self._client = chromadb.PersistentClient(path=self.chroma_path)

    @property
    def client(self) -> chromadb.PersistentClient:
        """Get ChromaDB client"""
        return self._client

    async def build_index(
        self,
        chunks: List[TextChunk],
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs: Any,
    ) -> bool:
        """Build index"""
        try:
            collection_name = config.index_name
            
            # If vector index is needed, generate embeddings
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

            vector_store = ChromaVectorStore(
                config=vector_store_config,
                chroma_path=self.chroma_path,
                text_field=self.text_field,
                vector_field=self.vector_field,
                sparse_vector_field=self.sparse_vector_field,
                metadata_field=self.metadata_field,
                doc_id_field=self.doc_id_field,
            )

            # Convert TextChunk to ChromaDB required fields
            data = []
            for idx, chunk in enumerate(chunks):
                meta = chunk.metadata or {}
                item = {
                    "id": chunk.id_,
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
        """Update index"""
        try:
            # Delete old data first
            await self.delete_index(doc_id, config.index_name)

            # Rebuild
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
        """Delete index"""
        try:
            # ChromaDB doesn't support complex filter expressions, need to query first then delete
            collection = await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )
            
            # Query all records matching doc_id
            results = await asyncio.to_thread(
                collection.get,
                where={self.doc_id_field: doc_id},
            )
            
            if not results or not results.get("ids") or len(results["ids"]) == 0:
                logger.info(f"No entries found for doc_id={doc_id}")
                return False
            
            # Delete matching records
            ids_to_delete = results["ids"]
            await asyncio.to_thread(
                collection.delete,
                ids=ids_to_delete,
            )
            
            delete_count = len(ids_to_delete)
            logger.info(f"Deleted {delete_count} entries for doc_id={doc_id}")
            return delete_count > 0
        except Exception as e:
            logger.error(f"Failed to delete index entries: {e}")
            return False

    async def index_exists(
        self,
        index_name: str,
    ) -> bool:
        """Check if index exists"""
        try:
            # Try to get collection, throws exception if not exists
            await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )
            return True
        except Exception:
            return False

    async def get_index_info(
        self,
        index_name: str,
    ) -> Dict[str, Any]:
        """Get index information"""
        try:
            if not await self.index_exists(index_name):
                return {"exists": False}

            collection = await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )
            
            # Get collection statistics
            count = await asyncio.to_thread(
                collection.count,
            )
            
            # Get collection metadata
            metadata = collection.metadata or {}

            return {
                "exists": True,
                "collection_name": index_name,
                "count": count,
                "metadata": metadata,
            }
        except Exception as e:
            logger.error(f"Failed to get index info: {e}")
            return {"exists": False, "error": str(e)}

    def close(self) -> None:
        """Close index manager"""
        # ChromaDB client usually doesn't need explicit closing
        # But can reset client reference
        if self._client is not None:
            try:
                # ChromaDB client doesn't have close method, but can reset
                pass
            except Exception as e:
                logger.warning(f"Failed to close ChromaDB client: {e}")

