# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Simple Knowledge Base Implementation

Provides complete knowledge base functionality including document parsing, chunking, index building, and retrieval.
"""
from typing import Any, List, Optional, Dict
import uuid

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.common.config import IndexConfig


class SimpleKnowledgeBase(KnowledgeBase):
    """Simple Knowledge Base Implementation"""

    def __init__(
        self,
        config: KnowledgeBaseConfig,
        vector_store: Optional[VectorStore] = None,
        embed_model: Optional[Embedding] = None,
        parser: Optional[Parser] = None,
        chunker: Optional[Chunker] = None,
        extractor: Optional[Extractor] = None,
        index_manager: Optional[Indexer] = None,
        retriever: Optional[Retriever] = None,
        llm_client: Optional[Any] = None,
        **kwargs: Any,
    ):
        """
        Initialize the knowledge base
        
        Args:
            config: Knowledge base configuration
            vector_store: Vector store instance
            embed_model: Embedding model instance
            parser: Document parser instance
            chunker: Text chunker instance
            extractor: Extractor instance (optional)
            index_manager: Index manager instance
            retriever: Retriever instance (optional, will be auto-created if not provided)
            llm_client: LLM client instance (optional, for graph retrieval, etc.)
        """
        super().__init__(
            config=config,
            vector_store=vector_store,
            embed_model=embed_model,
            parser=parser,
            chunker=chunker,
            extractor=extractor,
            index_manager=index_manager,
            llm_client=llm_client,
            **kwargs,
        )
        self.retriever = retriever

    async def parse_files(
        self,
        file_paths: List[str],
        **kwargs: Any,
    ) -> List[Document]:
        """Parse files from file paths into a list of Document objects"""
        if not self.parser:
            raise ValueError("parser is required for parse_files")

        all_documents = []
        for file_path in file_paths:
            try:
                file_name = kwargs.get("file_name", file_path.split("/")[-1])
                file_id = kwargs.get("file_id", str(uuid.uuid4()))
                
                documents = await self.parser.parse(
                    file_path,
                    file_name=file_name,
                    file_id=file_id,
                )
                all_documents.extend(documents)
            except Exception as e:
                logger.error(f"Failed to parse file {file_path}: {e}")
                continue

        return all_documents

    async def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Add documents to the knowledge base"""
        if not self.chunker:
            raise ValueError("chunker is required for add_documents")
        if not self.index_manager:
            raise ValueError("index_manager is required for add_documents")

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)
        logger.info(f"Chunked {len(documents)} documents into {len(chunks)} chunks")

        # Build index

        index_config = IndexConfig(
            index_name=f"kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        success = await self.index_manager.build_index(
            chunks=chunks,
            config=index_config,
            embed_model=self.embed_model,
        )

        if not success:
            raise RuntimeError("Failed to build index")

        # Return document ID list
        doc_ids = [doc.id_ for doc in documents]
        logger.info(f"Successfully added {len(doc_ids)} documents to knowledge base")
        return doc_ids

    async def retrieve(
        self,
        query: str,
        config: Optional[RetrievalConfig] = None,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Retrieve relevant documents"""
        if not self.retriever:
            # Auto-create retriever
            if not self.vector_store:
                raise ValueError(
                    "vector_store or retriever is required for retrieve"
                )
            
            # Select appropriate retriever based on index_type
            if self.config.index_type == "vector":
                from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
                self.retriever = VectorRetriever(
                    vector_store=self.vector_store,
                    embed_model=self.embed_model,
                )
            elif self.config.index_type == "bm25":
                from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
                self.retriever = SparseRetriever(
                    vector_store=self.vector_store,
                )
            else:  # hybrid or others
                from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
                self.retriever = HybridRetriever(
                    vector_store=self.vector_store,
                    embed_model=self.embed_model,
                )

        # Use config or default values
        retrieval_config = config or RetrievalConfig()
        
        # Determine retrieval mode
        mode = "hybrid"
        if self.config.index_type == "vector":
            mode = "vector"
        elif self.config.index_type == "bm25":
            mode = "sparse"

        results = await self.retriever.retrieve(query=query, top_k=retrieval_config.top_k,
                                                score_threshold=retrieval_config.score_threshold,
                                                filters=retrieval_config.filters, mode=mode, )

        return results

    async def delete_documents(
        self,
        doc_ids: List[str],
        **kwargs: Any,
    ) -> bool:
        """Delete documents"""
        if not self.index_manager:
            raise ValueError("index_manager is required for delete_documents")

        index_name = f"kb_{self.config.kb_id}_chunks"
        success = True

        for doc_id in doc_ids:
            result = await self.index_manager.delete_index(
                doc_id=doc_id,
                index_name=index_name,
            )
            if not result:
                success = False

        return success

    async def update_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Update documents"""
        if not self.chunker:
            raise ValueError("chunker is required for update_documents")
        if not self.index_manager:
            raise ValueError("index_manager is required for update_documents")

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)

        # Update index
        index_config = IndexConfig(
            index_name=f"kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        doc_ids = []
        for doc in documents:
            doc_chunks = [c for c in chunks if c.doc_id == doc.id_]
            if doc_chunks:
                success = await self.index_manager.update_index(
                    chunks=doc_chunks,
                    doc_id=doc.id_,
                    config=index_config,
                    embed_model=self.embed_model,
                )
                if success:
                    doc_ids.append(doc.id_)

        return doc_ids

    async def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        index_name = f"kb_{self.config.kb_id}_chunks"
        
        if not self.index_manager:
            return {
                "kb_id": self.config.kb_id,
                "index_exists": False,
            }

        index_info = await self.index_manager.get_index_info(index_name)
        
        return {
            "kb_id": self.config.kb_id,
            "index_type": self.config.index_type,
            "index_info": index_info,
            "has_parser": self.parser is not None,
            "has_chunker": self.chunker is not None,
            "has_extractor": self.extractor is not None,
            "has_embed_model": self.embed_model is not None,
            "has_vector_store": self.vector_store is not None,
        }


async def retrieve_multi_kb(
    kbs: List["KnowledgeBase"],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[str]:
    """
    Perform retrieval on multiple knowledge bases, deduplicate by text and merge by score in descending order.
    """
    if not kbs:
        return []

    async def _retrieve_one(kb: "KnowledgeBase"):
        try:
            return await kb.retrieve(query, config)
        except Exception as e:  # noqa: BLE001
            logger.warning("retrieve_multi_kb: kb_id=%s failed: %s", getattr(kb.config, "kb_id", None), e)
            return []

    import asyncio

    all_results = await asyncio.gather(*[_retrieve_one(kb) for kb in kbs])
    merged: Dict[str, float] = {}
    for results in all_results:
        for r in results or []:
            text = r.text
            score = r.score if r.score is not None else 0.0
            if text not in merged or score > merged[text]:
                merged[text] = score

    ranked = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    limit = top_k or (config.top_k if config else None) or 5
    return [txt for txt, _ in ranked[:limit]]


async def retrieve_multi_kb_with_source(
    kbs: List["KnowledgeBase"],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Perform retrieval on multiple knowledge bases, return results with source information.
    Result items: text/score/raw_score/raw_score_scaled/kb_ids
    """
    if not kbs:
        return []

    async def _retrieve_one(kb: "KnowledgeBase"):
        try:
            return await kb.retrieve(query, config)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "retrieve_multi_kb_with_source: kb_id=%s failed: %s", getattr(kb.config, "kb_id", None), e
            )
            return []

    import asyncio

    all_results = await asyncio.gather(*[_retrieve_one(kb) for kb in kbs])
    merged: Dict[str, Dict[str, Any]] = {}
    for kb, results in zip(kbs, all_results):
        kb_id = getattr(kb.config, "kb_id", None)
        for r in results or []:
            text = r.text
            score = 0.0 if r.score is None else float(r.score)
            meta = r.metadata or {}
            raw_score = meta.get("raw_score")
            raw_score_scaled = meta.get("raw_score_scaled")
            if text not in merged:
                merged[text] = {
                    "text": text,
                    "score": score,
                    "raw_score": raw_score,
                    "raw_score_scaled": raw_score_scaled,
                    "kb_ids": set(),
                }
            merged[text]["score"] = max(merged[text]["score"], score)
            if raw_score is not None:
                prev = merged[text].get("raw_score")
                merged[text]["raw_score"] = max(prev, raw_score) if prev is not None else raw_score
            if raw_score_scaled is not None:
                prev = merged[text].get("raw_score_scaled")
                merged[text]["raw_score_scaled"] = (
                    max(prev, raw_score_scaled) if prev is not None else raw_score_scaled
                )
            merged[text]["kb_ids"].add(kb_id)

    ranked = sorted(
        (
            {
                "text": v["text"],
                "score": v["score"],
                "raw_score": v.get("raw_score"),
                "raw_score_scaled": v.get("raw_score_scaled"),
                "kb_ids": sorted(list(v["kb_ids"])),
            }
            for v in merged.values()
        ),
        key=lambda x: x["score"],
        reverse=True,
    )
    limit = top_k or (config.top_k if config else None) or 5
    return ranked[:limit]
