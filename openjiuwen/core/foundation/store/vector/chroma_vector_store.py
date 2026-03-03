# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from typing import Any, Dict, List, Optional, Union

import chromadb

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, ValidationError
from openjiuwen.core.common.logging import store_logger, LogEventType
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)
from openjiuwen.core.foundation.store.vector.utils import (
    convert_cosine_distance,
    convert_l2_squared,
    convert_ip_distance,
)
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation


class ChromaVectorStore(BaseVectorStore):
    """
    ChromaDB vector store implementation.

    This class implements BaseVectorStore interface using ChromaDB as the backend.
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
    ):
        """
        Initialize ChromaVectorStore.

        Args:
            persist_directory: Path to persist ChromaDB data. If None, uses in-memory storage.
        """
        self._client = chromadb.PersistentClient(path=persist_directory)

        # Cache for collections
        self._collections: Dict[str, chromadb.Collection] = {}

    def _get_collection(self, collection_name: str) -> chromadb.Collection:
        """Get or create a collection."""
        if collection_name not in self._collections:
            try:
                collection = self._client.get_collection(name=collection_name)
            except Exception as e:
                # Collection doesn't exist, will be created in create_collection
                raise ValidationError(
                    StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND,
                    msg="collection doesn't exist",
                    details=str(e),
                    collection_name=collection_name,
                ) from e
            self._collections[collection_name] = collection
        return self._collections[collection_name]

    async def create_collection(
        self,
        collection_name: str,
        schema: Union[CollectionSchema, Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Create a new collection with specified schema.

        Args:
            collection_name: Name of the collection to create
            schema: CollectionSchema instance or schema dictionary
            **kwargs: Additional parameters for collection creation
                - distance_metric (str): Distance metric for vector search (default: "cosine")
                  Options: "cosine", "l2", "ip"
        """
        distance_metric = kwargs.get("distance_metric", "cosine")
        # Map distance metric to ChromaDB format
        chroma_metric = distance_metric.replace("dot", "ip").replace("euclidean", "l2")

        # Convert dict to CollectionSchema if needed
        if isinstance(schema, dict):
            schema = CollectionSchema.from_dict(schema)

        def _create():
            # Validate: primary key field is required
            primary_field = schema.get_primary_key_field()
            if not primary_field:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain a primary key field (is_primary=True)"
                )

            # Validate: FLOAT_VECTOR field must be unique
            vector_fields = schema.get_vector_fields()
            if len(vector_fields) == 0:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain at least one FLOAT_VECTOR field"
                )

            # Extract vector field info
            vector_field = vector_fields[0]

            # Build field mapping: user-defined field names -> ChromaDB built-in fields
            # ChromaDB built-in fields: ids, embeddings, documents, metadatas
            field_mapping = {
                "primary_key": primary_field.name,  # Maps to ChromaDB's "ids"
                "vector_field": vector_field.name,  # Maps to ChromaDB's "embeddings"
                "text_field": None,  # Will be determined below, maps to ChromaDB's "documents"
            }

            # Identify text field (first non-primary VARCHAR field)
            for f in schema.fields:
                if f.dtype == VectorDataType.VARCHAR and not f.is_primary:
                    field_mapping["text_field"] = f.name
                    break

            # Store schema, field mapping, and distance metric in metadata
            metadata = {
                "schema": json.dumps(schema.to_dict()),
                "fields": json.dumps(schema.to_dict()),
                "field_mapping": json.dumps(field_mapping),
                "vector_field": vector_field.name,
                "distance_metric": chroma_metric,  # Save metric for later use
            }

            # Configure HNSW index with distance metric
            configuration = {
                "hnsw": {"space": chroma_metric}
            }

            collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata=metadata,
                configuration=configuration,
            )
            self._collections[collection_name] = collection
            store_logger.info(
                f"Created collection with {len(schema.fields)} fields",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name
            )

        await asyncio.to_thread(_create)

    async def delete_collection(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> None:
        """
        Delete a collection by name.

        Args:
            collection_name: Name of the collection to delete
            **kwargs: Additional parameters for collection deletion
        """
        def _delete():
            try:
                self._client.delete_collection(name=collection_name)
                if collection_name in self._collections:
                    del self._collections[collection_name]
                store_logger.info(
                    "Deleted collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name
                )
            except Exception as e:
                store_logger.error(
                    "Failed to delete collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    exception=str(e)
                )
                raise

        await asyncio.to_thread(_delete)

    async def collection_exists(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> bool:
        """
        Check if a collection exists.

        Args:
            collection_name: Name of the collection to check
            **kwargs: Additional parameters for collection existence check

        Returns:
            bool: True if the collection exists, False otherwise
        """
        def _exists():
            try:
                self._client.get_collection(name=collection_name)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_exists)

    async def get_schema(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> CollectionSchema:
        """
        Get the schema of a collection.

        Args:
            collection_name: Name of the collection
            **kwargs: Additional parameters for getting schema

        Returns:
            CollectionSchema: The schema of the collection

        Raises:
            ValueError: If the collection does not exist
        """
        collection = self._get_collection(collection_name)

        def _get_schema():
            # Try to get schema from collection metadata
            try:
                metadata = collection.metadata
                if metadata and "schema" in metadata:
                    schema_dict = json.loads(metadata["schema"])
                    return CollectionSchema.from_dict(schema_dict)
                elif metadata and "fields" in metadata:
                    schema_dict = json.loads(metadata["fields"])
                    return CollectionSchema.from_dict(schema_dict)
                else:
                    # If schema not stored in metadata, build default schema
                    # Try to get field mapping from metadata
                    field_mapping = {}
                    if metadata and "field_mapping" in metadata:
                        field_mapping = json.loads(metadata["field_mapping"])

                    primary_key = field_mapping.get("primary_key", "id")
                    vector_field = field_mapping.get("vector_field", "embedding")
                    text_field = field_mapping.get("text_field", "text")

                    schema = CollectionSchema(
                        description=f"Collection '{collection_name}'",
                        enable_dynamic_field=True,
                    )
                    # Add fields based on field mapping
                    schema.add_field(
                        FieldSchema(
                            name=primary_key,
                            dtype=VectorDataType.VARCHAR,
                            max_length=256,
                            is_primary=True,
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name=vector_field,
                            dtype=VectorDataType.FLOAT_VECTOR,
                            dim=None,  # Dimension may vary
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name=text_field,
                            dtype=VectorDataType.VARCHAR,
                            max_length=65535,
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name="metadata",
                            dtype=VectorDataType.JSON,
                        )
                    )
                    return schema
            except Exception as e:
                store_logger.warning(
                    "Could not get schema from collection",
                    event_type=LogEventType.STORE_RETRIEVE,
                    table_name=collection_name,
                    exception=str(e)
                )
                # Return default schema as fallback
                schema = CollectionSchema(
                    description=f"Collection '{collection_name}'",
                    enable_dynamic_field=True,
                )
                schema.add_field(
                    FieldSchema(
                        name="id",
                        dtype=VectorDataType.VARCHAR,
                        max_length=256,
                        is_primary=True,
                    )
                )
                schema.add_field(
                    FieldSchema(
                        name="embedding",
                        dtype=VectorDataType.FLOAT_VECTOR,
                    )
                )
                schema.add_field(
                    FieldSchema(
                        name="text",
                        dtype=VectorDataType.VARCHAR,
                        max_length=65535,
                    )
                )
                return schema

        return await asyncio.to_thread(_get_schema)

    async def add_docs(
        self,
        collection_name: str,
        docs: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Add documents to a collection.

        Args:
            collection_name: Name of the target collection
            docs: List of documents to add
            **kwargs: Additional parameters for document insertion
                - batch_size (int, optional): Batch size for bulk insertion (default: 128)
        """
        batch_size = kwargs.get("batch_size", 128)
        if batch_size <= 0:
            batch_size = 128

        collection = self._get_collection(collection_name)


        metadata = collection.metadata or {}
        field_mapping_str = metadata.get("field_mapping", "{}")
        field_mapping = json.loads(field_mapping_str)

        primary_key = field_mapping.get("primary_key", "id")
        vector_field = field_mapping.get("vector_field", "embedding")
        text_field = field_mapping.get("text_field", "text")

        def _add_batch(batch: List[Dict[str, Any]]):
            ids = []
            embeddings = []
            documents = []
            metadatas = []
            has_metadata = True

            for doc in batch:
                # Extract ID from user-defined primary key field
                doc_id = doc.get(primary_key)
                if doc_id is None:
                    raise build_error(
                        StatusCode.STORE_VECTOR_DOC_INVALID,
                        error_msg=f"document must have primary field '{primary_key}'"
                    )
                ids.append(str(doc_id))

                # Extract embedding from user-defined vector field
                embedding = doc.get(vector_field)
                if embedding is None:
                    raise build_error(
                        StatusCode.STORE_VECTOR_DOC_INVALID,
                        error_msg=f"document must have vector field '{vector_field}'"
                    )
                embeddings.append(embedding)

                # Extract text content from user-defined text field
                text = doc.get(text_field, "")
                documents.append(text)

                # Build metadata (all other fields except primary key, vector field, text field)
                metadata = {}
                for key, value in doc.items():
                    if key not in [primary_key, vector_field, text_field]:
                        # ChromaDB metadata must be JSON serializable
                        if isinstance(value, (str, int, float, bool, type(None))):
                            metadata[key] = value
                        elif isinstance(value, (list, dict)):
                            metadata[key] = json.dumps(value)
                        else:
                            metadata[key] = str(value)
                if not metadata:
                    has_metadata = False
                metadatas.append(metadata)

            if has_metadata:
                collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                )
            else:
                collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                )

        # Process in batches
        total = len(docs)
        processed = 0
        for i in range(0, total, batch_size):
            batch = docs[i: i + batch_size]
            await asyncio.to_thread(_add_batch, batch)
            processed += len(batch)
            store_logger.info(
                f"Added {processed}/{total} documents'",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name,
                data_num=processed
            )

        store_logger.info(
            "Successfully added documents",
            event_type=LogEventType.STORE_ADD,
            table_name=collection_name,
            data_num=total
        )

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        vector_field: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[VectorSearchResult]:
        """
        Search for the most relevant documents by vector similarity.

        Args:
            collection_name: Name of the collection to search
            query_vector: Query vector for similarity search
            vector_field: Name of the vector field to search against (for compatibility, ignored)
            top_k: Number of most relevant documents to return
            filters: Optional dictionary of scalar field filters (equality only)
            **kwargs: Additional search parameters
                - metric_type (str, optional): Distance metric (not used, already set in collection)

        Returns:
            List of VectorSearchResult objects
        """
        collection = self._get_collection(collection_name)

        def _search():
            # Get field mapping from collection metadata
            metadata = collection.metadata or {}
            field_mapping_str = metadata.get("field_mapping", "{}")
            field_mapping = json.loads(field_mapping_str)
            primary_key = field_mapping.get("primary_key", "id")
            text_field = field_mapping.get("text_field", None)

            # Build where filter for ChromaDB (equality filters only)
            where = filters if filters else None

            results = collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )

            search_results = []
            if results and results.get("ids") and len(results["ids"]) > 0:
                ids_list = results["ids"][0]
                documents_list = results.get("documents", [[]])[0]
                metadatas_list = results.get("metadatas", [[]])[0]
                distances_list = results.get("distances", [[]])[0]

                for idx, doc_id in enumerate(ids_list):
                    # Get distance and convert to similarity score
                    distance = distances_list[idx] if idx < len(distances_list) else None
                    if distance is not None:
                        # Get metric from collection metadata (saved during creation)
                        metric = metadata.get("distance_metric", "cosine")

                        # Convert distance to similarity score based on metric
                        if metric == "cosine":
                            # ChromaDB cosine distance ranges from 0 to 2
                            # Convert to similarity: (2.0 - distance) / 2.0
                            score = convert_cosine_distance(distance)
                        elif metric == "l2":
                            # L2 distance, convert to similarity: (max_dist - distance) / max_dist
                            score = convert_l2_squared(distance)
                        else:  # ip (inner product)
                            # ChromaDB IP distance = 1 - inner_product, range [0, 2]
                            # Convert to similarity: max(0, min(1, (2.0 - distance) / 2.0))
                            score = convert_ip_distance(distance)
                    else:
                        score = 0.0

                    # Build fields dictionary with user-defined field names
                    text = documents_list[idx] if idx < len(documents_list) else ""
                    metadata = metadatas_list[idx] if idx < len(metadatas_list) else {}
                    if not isinstance(metadata, dict):
                        metadata = {}

                    # Parse JSON strings in metadata
                    parsed_metadata = {}
                    for key, value in metadata.items():
                        if isinstance(value, str):
                            try:
                                parsed_metadata[key] = json.loads(value)
                            except (json.JSONDecodeError, TypeError):
                                parsed_metadata[key] = value
                        else:
                            parsed_metadata[key] = value

                    # Map ChromaDB built-in fields back to user-defined field names
                    fields = {
                        primary_key: str(doc_id),
                        **parsed_metadata,
                    }

                    if text_field:
                        fields[text_field] = text

                    search_results.append(
                        VectorSearchResult(
                            score=score,
                            fields=fields,
                        )
                    )

            return search_results

        return await asyncio.to_thread(_search)

    async def delete_docs_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by their IDs.

        Args:
            collection_name: Name of the collection
            ids: List of document IDs to delete
            **kwargs: Additional parameters for deletion
        """
        collection = self._get_collection(collection_name)

        def _delete():
            collection.delete(ids=ids)
            store_logger.info(
                "Deleted documents",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name,
                data_num=len(ids)
            )

        await asyncio.to_thread(_delete)

    async def delete_docs_by_filters(
        self,
        collection_name: str,
        filters: Dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by scalar field filters.

        Args:
            collection_name: Name of the collection
            filters: Dictionary of scalar field filters (equality only)
            **kwargs: Additional parameters for deletion
        """
        collection = self._get_collection(collection_name)

        def _delete():
            # Build where filter for ChromaDB (equality filters only)
            where = filters
            collection.delete(where=where)
            store_logger.info(
                "Deleted documents matching filters",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )

        await asyncio.to_thread(_delete)

    async def update_collection_metadata(self, collection_name: str, metadata: Dict[str, Any]) -> None:
        pass

    async def get_collection_metadata(self, collection_name: str) -> int:
        pass

    async def list_collection_names(self) -> List[str]:
        pass

    async def update_schema(self, collection_name: str, operations: List[BaseOperation]):
        pass