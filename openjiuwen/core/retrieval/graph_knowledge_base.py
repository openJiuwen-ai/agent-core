# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
GraphRAG Knowledge Base Implementation

Knowledge base implementation supporting graph indexing and retrieval.
"""
import json
from typing import Any, List, Optional, Dict
import uuid

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever
from openjiuwen.core.retrieval.simple_knowledge_base import retrieve_multi_kb, retrieve_multi_kb_with_source


class GraphKnowledgeBase(KnowledgeBase):
    """Graph-enhanced knowledge base implementation"""

    def __init__(
        self,
        config: KnowledgeBaseConfig,
        vector_store: Optional[VectorStore] = None,
        embed_model: Optional[Embedding] = None,
        parser: Optional[Parser] = None,
        chunker: Optional[Chunker] = None,
        extractor: Optional[Extractor] = None,
        index_manager: Optional[Indexer] = None,
        chunk_retriever: Optional[Retriever] = None,
        triple_retriever: Optional[Retriever] = None,
        llm_client: Optional[Any] = None,
        llm_model_name: Optional[Any] = None,
        **kwargs: Any,
    ):
        """
        Initialize GraphRAG knowledge base
        
        Args:
            config: Knowledge base configuration
            vector_store: Vector store instance
            embed_model: Embedding model instance
            parser: Document parser instance
            chunker: Text chunker instance
            extractor: Triple extractor instance (required)
            index_manager: Index manager instance
            chunk_retriever: Chunk retriever instance (optional)
            triple_retriever: Triple retriever instance (optional)
            llm_client: LLM client instance (for triple extraction)
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
        self.chunk_retriever = chunk_retriever
        self.triple_retriever = triple_retriever
        self.llm_model_name = llm_model_name
        self.graph_retriever: Optional[GraphRetriever] = None

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
        """Add documents to the knowledge base (including chunk index and triple index)"""
        if not self.chunker:
            raise ValueError("chunker is required for add_documents")
        if not self.index_manager:
            raise ValueError("index_manager is required for add_documents")

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)
        logger.info(f"Chunked {len(documents)} documents into {len(chunks)} chunks")

        # Build chunk index
        from openjiuwen.core.retrieval.common.config import IndexConfig

        chunk_index_config = IndexConfig(
            index_name=f"kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        success = await self.index_manager.build_index(
            chunks=chunks,
            config=chunk_index_config,
            embed_model=self.embed_model,
        )

        if not success:
            raise RuntimeError("Failed to build chunk index")

        # If graph indexing is enabled, extract triples and build triple index
        if self.config.use_graph and self.extractor:
            logger.info("Extracting triples for graph index...")
            triples = await self.extractor.extract(chunks)

            if triples:
                logger.info(f"Extracted {len(triples)} triples")

                # Build triple index
                triple_index_config = IndexConfig(
                    index_name=f"kb_{self.config.kb_id}_triples",
                    index_type=self.config.index_type,
                )

                # Convert triples to TextChunk format for indexing
                triple_chunks = []
                for i, triple in enumerate(triples):
                    # Convert triple to text format
                    triple_text = f"{triple.subject} {triple.predicate} {triple.object}"
                    chunk = TextChunk(
                        id_=f"triple_{i}",
                        text=triple_text,
                        doc_id=triple.metadata.get("doc_id", ""),
                        metadata={
                            **triple.metadata,
                            "triple": json.dumps([triple.subject, triple.predicate, triple.object]),
                            "confidence": triple.confidence if triple.confidence else 0,
                            "chunk_index": i,
                        },
                    )
                    triple_chunks.append(chunk)

                success = await self.index_manager.build_index(
                    chunks=triple_chunks,
                    config=triple_index_config,
                    embed_model=self.embed_model,
                )

                if not success:
                    logger.error("Failed to build triple index")
                else:
                    logger.info(f"Built triple index with {len(triple_chunks)} triples")

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
        """Retrieve relevant documents (supports graph retrieval)"""
        retrieval_config = config or RetrievalConfig()

        # If using graph retrieval, create or use graph retriever
        if retrieval_config.use_graph or self.config.use_graph:
            if not self.graph_retriever:
                if not self.vector_store:
                    raise ValueError(
                        "vector_store is required for graph retrieval"
                    )
                chunk_collection = f"kb_{self.config.kb_id}_chunks"
                triple_collection = f"kb_{self.config.kb_id}_triples"

                # Create GraphRetriever, pass necessary parameters for dynamic retriever creation
                self.graph_retriever = GraphRetriever(
                    chunk_retriever=self.chunk_retriever,  # If fixed retriever is provided, use it first
                    triple_retriever=self.triple_retriever,  # If fixed retriever is provided, use it first
                    vector_store=self.vector_store,  # For dynamic retriever creation
                    embed_model=self.embed_model,  # For dynamic retriever creation
                    chunk_collection=chunk_collection,  # For dynamic retriever creation
                    triple_collection=triple_collection,  # For dynamic retriever creation
                )
                # Inject index_type from upper layer for GraphRetriever mode validation
                self.graph_retriever.index_type = self.config.index_type
                if retrieval_config.agentic:
                    self.graph_retriever = AgenticRetriever(
                        graph_retriever=self.graph_retriever,
                        llm_client=self.llm_client,
                        llm_model_name=self.llm_model_name,
                        agent_topk=retrieval_config.top_k,
                        )

            # Use graph retriever
            mode = "hybrid"
            if self.config.index_type == "vector":
                mode = "vector"
            elif self.config.index_type == "bm25":
                mode = "sparse"

            results = await self.graph_retriever.retrieve(
                query=query,
                top_k=retrieval_config.top_k,
                score_threshold=retrieval_config.score_threshold,
                filters=retrieval_config.filters,
                mode=mode,
                graph_expansion=retrieval_config.graph_expansion,
            )

            return results
        else:
            # Use normal retrieval (fallback to simple knowledge base retrieval method)
            from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase

            base_kb = SimpleKnowledgeBase(
                config=self.config,
                vector_store=self.vector_store,
                embed_model=self.embed_model,
                parser=self.parser,
                chunker=self.chunker,
                index_manager=self.index_manager,
            )

            return await base_kb.retrieve(query, config, **kwargs)

    async def delete_documents(
        self,
        doc_ids: List[str],
        **kwargs: Any,
    ) -> bool:
        """Delete documents (including chunk index and triple index)"""
        if not self.index_manager:
            raise ValueError("index_manager is required for delete_documents")

        chunk_index_name = f"kb_{self.config.kb_id}_chunks"
        triple_index_name = f"kb_{self.config.kb_id}_triples"

        success = True

        # Delete chunk index
        for doc_id in doc_ids:
            result = await self.index_manager.delete_index(
                doc_id=doc_id,
                index_name=chunk_index_name,
            )
            if not result:
                success = False

        # Delete triple index (if exists)
        if self.config.use_graph:
            for doc_id in doc_ids:
                result = await self.index_manager.delete_index(
                    doc_id=doc_id,
                    index_name=triple_index_name,
                )
                if not result:
                    # Triple deletion failure does not affect overall result
                    logger.warning(f"Failed to delete triples for doc_id={doc_id}")

        return success

    async def update_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Update documents (including chunk index and triple index)"""
        # First delete old documents
        doc_ids = [doc.id_ for doc in documents]
        await self.delete_documents(doc_ids)

        # Re-add documents
        return await self.add_documents(documents, **kwargs)

    async def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        chunk_index_name = f"kb_{self.config.kb_id}_chunks"
        triple_index_name = f"kb_{self.config.kb_id}_triples"

        if not self.index_manager:
            return {
                "kb_id": self.config.kb_id,
                "index_exists": False,
            }

        chunk_info = await self.index_manager.get_index_info(chunk_index_name)
        triple_info = None
        if self.config.use_graph:
            triple_info = await self.index_manager.get_index_info(triple_index_name)

        return {
            "kb_id": self.config.kb_id,
            "index_type": self.config.index_type,
            "use_graph": self.config.use_graph,
            "chunk_index_info": chunk_info,
            "triple_index_info": triple_info,
            "has_parser": self.parser is not None,
            "has_chunker": self.chunker is not None,
            "has_extractor": self.extractor is not None,
            "has_embed_model": self.embed_model is not None,
            "has_vector_store": self.vector_store is not None,
            "has_graph_retriever": self.graph_retriever is not None,
        }

    async def close(self) -> None:
        """Close the knowledge base"""
        await super().close()
        if self.graph_retriever:
            await self.graph_retriever.close()
        if self.chunk_retriever:
            await self.chunk_retriever.close()
        if self.triple_retriever:
            await self.triple_retriever.close()


# ========= Multi-Knowledge Base Retrieval Helpers =========

async def retrieve_multi_graph_kb(
    kbs: List[KnowledgeBase],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[str]:
    """Perform retrieval on multiple knowledge bases (returns text list)."""
    return await retrieve_multi_kb(kbs, query, config=config, top_k=top_k)


async def retrieve_multi_graph_kb_with_source(
    kbs: List[KnowledgeBase],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Perform retrieval on multiple knowledge bases (includes source information)."""
    return await retrieve_multi_kb_with_source(kbs, query, config=config, top_k=top_k)
