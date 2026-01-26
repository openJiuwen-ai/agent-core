# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class SemanticStore:
    """
    Concrete implementation of semantic storage that uses an embedding model and vector store.

    This class provides an implementation of the semantic storage interface defined in BaseSemanticStore.
    It uses an embedding model to generate vector representations of text documents and a vector store
    to store and search these embeddings efficiently.
    """
    
    def __init__(self, vector_store: VectorStore, embedding_model: Embedding | None = None):
        """
        Initialize the semantic store with an embedding model and vector store.
        
        Args:
            vector_store: The vector store to use for storing and searching embeddings.
            embedding_model: Optional embedding model to use for generating embeddings.
                If not provided, must be initialized later using initialize_embedding_model.
        """
        self.embedding_model = embedding_model
        self.vector_store = vector_store
    
    def initialize_embedding_model(self, embedding_model: Embedding):
        """
        Initialize or update the embedding model used by the semantic store.
        
        Args:
            embedding_model: The embedding model to use for generating text embeddings.
        """
        self.embedding_model = embedding_model
    
    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str, scope_id: str | None = None) -> bool:
        """
        Add documents to a specified table after generating their embeddings.

        Args:
            docs (List[Tuple[str, str]]): A list of (id, text) tuples where id is a unique identifier
                and text is the raw string to be embedded.
            table_name (str): The name of the table where the embeddings will be stored.
            scope_id (str | None): Optional scope identifier to associate with the documents.
                Can be used for filtering during search.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        if not self.embedding_model:
            logger.error("Embedding model not initialized, please call initialize_embedding_model first.")
            return False
            
        try:
            memory_ids, texts = zip(*docs)
            memory_ids = list(memory_ids)
            texts = list(texts)
            
            # Generate embeddings for the texts
            embeddings = await self.embedding_model.embed_documents(texts=texts)
            
            if len(memory_ids) != len(embeddings):
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type="semantic store",
                    error_msg=f"memory_ids and embeddings must have same length",
                )
            
            # Prepare data for vector store, content is not stored
            data = []
            for doc_id, embedding in zip(memory_ids, embeddings):
                data.append({
                    "id": doc_id,
                    "embedding": embedding,
                })
            
            # Add to vector store
            await self.vector_store.add(data=data, table_name=table_name)
            return True
        except Exception as e:
            logger.error(f"Failed to add documents to semantic store: {e}")
            return False
    
    async def delete_docs(self, ids: List[str], table_name: str) -> bool:
        """
        Delete documents from a specified table by their unique identifiers.

        Args:
            ids (List[str]): A list of unique document ids whose embeddings should be removed.
            table_name (str): The name of the table from which to delete embeddings.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        try:
            return await self.vector_store.delete(ids=ids, table_name=table_name)
        except Exception as e:
            logger.error(f"Failed to delete documents from semantic store: {e}")
            return False
    
    async def search(self, query: str, table_name: str,
                     scope_id: str | None = None, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        Search for the top-k most similar documents to a query string.

        The query string is embedded internally before similarity comparison.

        Args:
            query (str): The raw query string to embed and search for.
            table_name (str): The name of the table to search within.
            scope_id (str | None): Optional scope identifier to filter results.
                Only documents with matching scope_id will be returned.
            top_k (int): The number of most similar results to return.
                Defaults to 5.

        Returns:
            List[Tuple[str, float]]: A list of (id, score) tuples where `id`
                is the unique identifier of the matched document and `score`
                is the similarity score, with higher values indicating greater similarity.
        """
        if not self.embedding_model:
            logger.error("Embedding model not initialized, please call initialize_embedding_model first.")
            return []
            
        try:
            # Generate embedding for the query
            query_embeddings = await self.embedding_model.embed_documents(texts=[query])
            if len(query_embeddings) == 0:
                logger.error(f"Failed to embed query: {query}")
                return []
            query_embedding = query_embeddings[0]

            # Search in vector store
            results = await self.vector_store.search(
                query_vector=query_embedding,
                top_k=top_k,
                table_name=table_name
            )
            
            # Convert to required format
            return [(result.id, result.score) for result in results]
        except Exception as e:
            logger.error(f"Failed to search semantic store: {e}")
            return []
    
    async def delete_table(self, table_name: str) -> bool:
        """
        Delete an entire table and all its stored embeddings.

        Args:
            table_name (str): The name of the table to delete.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        try:
            return await self.vector_store.delete_table(table_name=table_name)
        except Exception as e:
            logger.error(f"Failed to delete table from semantic store: {e}")
            return False