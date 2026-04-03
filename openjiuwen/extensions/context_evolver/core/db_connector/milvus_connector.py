# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Milvus connector for VectorNode persistence.


Requirements
------------
- Milvus ≥ 2.3 running and accessible (default port 19530).
- Python package: ``pymilvus`` (``pip install pymilvus``).

Connection examples
-------------------
    # Local / default
    conn = MilvusConnector(host="localhost", port=19530, dim=1536)

    # Remote
    conn = MilvusConnector(host="my-milvus.example.com", port=19530, dim=768)

Example::

    from openjiuwen.extensions.context_evolver.core.db_connector import MilvusConnector

    conn = MilvusConnector(host="localhost", port=19530, dim=1536)
    conn.save_to_db("my_memories", data)
    loaded  = conn.load_from_db("my_memories")
    results = conn.search("my_memories", query_embedding, top_k=5)
    conn.delete("my_memories")
    conn.close()
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pymilvus import utility

from openjiuwen.core.common.logging import context_engine_logger as logger

# ---------------------------------------------------------------------------
# Schema / size constants
# ---------------------------------------------------------------------------
_FIELD_ID = "id"
_FIELD_NS = "namespace"
_FIELD_CONTENT = "content"
_FIELD_EMBEDDING = "embedding"
_FIELD_METADATA = "metadata"

_ID_MAX_LEN = 256
_NS_MAX_LEN = 256
# Milvus VARCHAR has a hard ceiling of 65 535 bytes; content may be long text,
# so we truncate at this limit rather than raise an error.
_CONTENT_MAX_LEN = 65_535


class MilvusConnector:
    """Connector for saving and loading :class:`VectorNode` data via Milvus.

    The ``embedding`` field uses Milvus ``FLOAT_VECTOR`` type with an HNSW
    index by default, enabling fast approximate nearest-neighbour search.

    .. note::
        Milvus requires every inserted vector to be non-null. Nodes whose
        ``embedding`` field is ``None`` are **skipped** during
        :meth:`save_to_db` with a warning.

    Args:
        host:            Milvus server hostname (default: ``"localhost"``).
        port:            Milvus gRPC port (default: ``19530``).
        collection_name: Milvus collection to use (default: ``"vector_nodes"``).
        dim:             Embedding dimension.  Auto-detected from the first
                         :meth:`save_to_db` call if not provided here.
        alias:           pymilvus connection alias (default: ``"default"``).
        metric_type:     Index and search metric: ``"COSINE"`` (default),
                         ``"L2"``, or ``"IP"``.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 19530,
        collection_name: str = "vector_nodes",
        dim: Optional[int] = None,
        alias: str = "default",
        metric_type: str = "COSINE",
    ) -> None:
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self.dim = dim
        self.alias = alias
        self.metric_type = metric_type
        self._collection = None

        self._connect()

        if dim is not None:
            self._init_collection(dim)

        logger.info(
            "MilvusConnector initialised (host=%s, port=%s, collection=%s, dim=%s)",
            host, port, collection_name, dim,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Establish a gRPC connection to the Milvus server."""
        from pymilvus import connections  # pylint: disable=import-outside-toplevel

        try:
            connections.connect(
                alias=self.alias,
                host=self.host,
                port=str(self.port),
            )
            logger.info("Connected to Milvus at %s:%s", self.host, self.port)
        except Exception as exc:
            logger.error("Failed to connect to Milvus at %s:%s: %s", self.host, self.port, exc)
            raise

    def _init_collection(self, dim: int) -> None:
        """Create (or reuse) the Milvus collection for the given *dim*.

        Idempotent: safe to call multiple times with the same *dim*.
        Creates a default HNSW index if none exists, then loads the
        collection into memory for search.
        """
        from pymilvus import (  # pylint: disable=import-outside-toplevel
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
        )

        if self._collection is not None:
            return

        self.dim = dim

        if utility.has_collection(self.collection_name, using=self.alias):
            self._collection = Collection(
                name=self.collection_name, using=self.alias
            )
            logger.info(
                "Reusing existing Milvus collection '%s'", self.collection_name
            )
        else:
            fields = [
                FieldSchema(
                    name=_FIELD_ID,
                    dtype=DataType.VARCHAR,
                    max_length=_ID_MAX_LEN,
                    is_primary=True,
                    auto_id=False,
                ),
                FieldSchema(
                    name=_FIELD_NS,
                    dtype=DataType.VARCHAR,
                    max_length=_NS_MAX_LEN,
                ),
                FieldSchema(
                    name=_FIELD_CONTENT,
                    dtype=DataType.VARCHAR,
                    max_length=_CONTENT_MAX_LEN,
                ),
                FieldSchema(
                    name=_FIELD_EMBEDDING,
                    dtype=DataType.FLOAT_VECTOR,
                    dim=dim,
                ),
                FieldSchema(
                    name=_FIELD_METADATA,
                    dtype=DataType.JSON,
                ),
            ]
            schema = CollectionSchema(
                fields=fields,
                description="VectorNode storage for context evolver",
                enable_dynamic_field=False,
            )
            self._collection = Collection(
                name=self.collection_name,
                schema=schema,
                using=self.alias,
            )
            logger.info(
                "Created Milvus collection '%s' (dim=%d)",
                self.collection_name, dim,
            )

        self._ensure_index()
        self._collection.load()

    def _ensure_index(self) -> None:
        """Create a default HNSW index on the embedding field if none exists."""
        if not self._collection:
            return
        if self._collection.has_index():
            return
        index_params = {
            "index_type": "HNSW",
            "metric_type": self.metric_type,
            "params": {"M": 16, "efConstruction": 64},
        }
        self._collection.create_index(
            field_name=_FIELD_EMBEDDING,
            index_params=index_params,
        )
        logger.info(
            "Created default HNSW index on '%s' (metric=%s)",
            self.collection_name, self.metric_type,
        )

    def _get_collection(self, dim: Optional[int] = None):
        """Return the collection, initialising and loading it if necessary."""
        if self._collection is None:
            if dim is None:
                # Try to attach to an existing collection without knowing dim.
                # Required for read-only ops (exists/count/load_from_db) on a
                # collection that was created by a previous save_to_db() call.
                from pymilvus import Collection  # pylint: disable=import-outside-toplevel
                if not utility.has_collection(self.collection_name, using=self.alias):
                    raise ValueError(
                        "Vector dimension unknown. Provide `dim` in the constructor "
                        "or call save_to_db() with non-empty embedded data first."
                    )
                self._collection = Collection(
                    name=self.collection_name, using=self.alias
                )
                # Discover dim from the existing schema so self.dim is populated
                for field in self._collection.schema.fields:
                    if hasattr(field, "dtype") and field.dtype.name == "FLOAT_VECTOR":
                        self.dim = field.params.get("dim")
                        break
                self._collection.load()
                logger.info(
                    "Attached to existing Milvus collection '%s' (dim=%s)",
                    self.collection_name, self.dim,
                )
            else:
                self._init_collection(dim)
        return self._collection

    @staticmethod
    def truncate(text: str, max_bytes: int) -> str:
        """UTF-8-safe truncation so content stays within Milvus VARCHAR limits."""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        return encoded[:max_bytes].decode("utf-8", errors="ignore")

    @staticmethod
    def ids_expr(ids: List[str]) -> str:
        """Build a Milvus ``id in [...]`` expression from a list of IDs."""
        quoted = ", ".join(f'"{id_}"' for id_ in ids)
        return f'{_FIELD_ID} in [{quoted}]'

    def set_collection(self, collection: Any) -> None:
        """Inject a collection object directly, bypassing normal initialisation.

        Intended for unit tests that supply an in-memory mock collection so
        that no live Milvus server is required.

        Args:
            collection: Collection-like object exposing the pymilvus
                        Collection API (insert, query, search, etc.).
        """
        self._collection = collection

    # ------------------------------------------------------------------
    # Public API – mirrors JSONFileConnector
    # ------------------------------------------------------------------

    def save_to_db(self, namespace: str, data: Dict[str, Any]) -> None:
        """Upsert all nodes in *data* under *namespace*.

        Analogous to :meth:`JSONFileConnector.save_to_file`.

        Nodes without an ``embedding`` are skipped because Milvus requires
        non-null float vectors.  Existing rows with the same ``id`` are
        replaced (delete-then-insert).

        Args:
            namespace: Logical partition key (e.g. ``"memory_RB_user1"``).
            data:      Mapping of ``node_id → serialised VectorNode dict``.

        Raises:
            ValueError: If the embedding dimension cannot be determined.
            Exception:  On Milvus errors.
        """
        if not data:
            logger.info("save_to_db: empty data, nothing to do")
            return

        # Auto-detect embedding dimension from the first embedded node
        dim: Optional[int] = None
        for nd in data.values():
            emb = nd.get("embedding")
            if emb:
                dim = len(emb)
                break

        collection = self._get_collection(dim)

        ids, namespaces, contents, embeddings, metadatas = [], [], [], [], []
        skipped = 0

        for node_id, node_data in data.items():
            emb = node_data.get("embedding")
            if not emb:
                skipped += 1
                continue
            ids.append(self.truncate(node_id, _ID_MAX_LEN))
            namespaces.append(self.truncate(namespace, _NS_MAX_LEN))
            contents.append(
                self.truncate(node_data.get("content", ""), _CONTENT_MAX_LEN)
            )
            embeddings.append(list(emb))
            metadatas.append(node_data.get("metadata") or {})

        if skipped:
            logger.warning(
                "save_to_db: skipped %d node(s) without embeddings in namespace '%s'",
                skipped, namespace,
            )

        if not ids:
            logger.warning("save_to_db: no embeddable nodes found – nothing saved")
            return

        try:
            # Milvus upsert = delete existing PKs + insert fresh rows
            collection.delete(self.ids_expr(ids))

            rows = []
            for id_, ns, c, emb, meta in zip(ids, namespaces, contents, embeddings, metadatas):
                rows.append({
                    _FIELD_ID: id_,
                    _FIELD_NS: ns,
                    _FIELD_CONTENT: c,
                    _FIELD_EMBEDDING: emb,
                    _FIELD_METADATA: meta,
                })
            collection.insert(rows)
            collection.flush()

            logger.info(
                "Saved %d nodes to namespace '%s' (collection='%s')",
                len(ids), namespace, self.collection_name,
            )
        except Exception as exc:
            logger.error(
                "Failed to save data to namespace '%s': %s", namespace, exc
            )
            raise

    def load_from_db(self, namespace: str) -> Dict[str, Any]:
        """Load all nodes from *namespace*.

        Analogous to :meth:`JSONFileConnector.load_from_file`.

        Args:
            namespace: Logical partition key.

        Returns:
            Mapping of ``node_id → serialised VectorNode dict``.
            Returns an empty dict if no rows exist for *namespace*.

        Raises:
            Exception: On Milvus errors.
        """
        try:
            collection = self._get_collection()
            expr = f'{_FIELD_NS} == "{namespace}"'
            results = collection.query(
                expr=expr,
                output_fields=[
                    _FIELD_ID,
                    _FIELD_NS,
                    _FIELD_CONTENT,
                    _FIELD_EMBEDDING,
                    _FIELD_METADATA,
                ],
            )

            data: Dict[str, Any] = {}
            for hit in results:
                node_id = hit.get(_FIELD_ID, "")
                data[node_id] = {
                    "id": node_id,
                    "content": hit.get(_FIELD_CONTENT, ""),
                    "embedding": hit.get(_FIELD_EMBEDDING),
                    "metadata": hit.get(_FIELD_METADATA) or {},
                }

            logger.info(
                "Loaded %d nodes from namespace '%s'", len(data), namespace
            )
            return data

        except Exception as exc:
            logger.error("Failed to load namespace '%s': %s", namespace, exc)
            raise

    def search(
        self,
        namespace: str,
        embedding: List[float],
        top_k: int = 10,
        metric: str = "cosine",
    ) -> List[Dict[str, Any]]:
        """Find the *top_k* most similar vectors in *namespace*.

        Leverages Milvus's native ANN search – vectors are never loaded
        into Python for comparison.

        Args:
            namespace: Partition to search within.
            embedding: Query vector (must match ``dim`` of stored vectors).
            top_k:     Maximum number of results to return.
            metric:    Distance metric.

                       - ``"cosine"`` / ``"COSINE"``  – cosine similarity
                         (default).  Higher score = more similar.
                       - ``"l2"`` / ``"L2"``           – Euclidean distance.
                         Lower score = more similar.
                       - ``"ip"`` / ``"IP"`` / ``"inner_product"`` – inner
                         product.  Higher score = more similar.

        Returns:
            List of result dicts, each containing VectorNode fields plus a
            ``"score"`` key (Milvus distance value).

        Raises:
            Exception: On Milvus errors.
        """
        _metric_map = {
            "cosine": "COSINE",
            "l2": "L2",
            "ip": "IP",
            "inner_product": "IP",
        }
        requested_metric = _metric_map.get(metric.lower(), "COSINE")

        # Milvus locks the metric type at index-creation time; using a
        # different metric at search time raises MilvusException (code=65535).
        # Always honour self.metric_type and warn if there is a mismatch.
        milvus_metric = self.metric_type.upper()
        if requested_metric != milvus_metric:
            logger.warning(
                "search() called with metric=%s but collection index uses %s; "
                "falling back to index metric %s.",
                requested_metric, milvus_metric, milvus_metric,
            )

        try:
            collection = self._get_collection()
            search_params = {
                "metric_type": milvus_metric,
                # ef must be >= top_k; larger values improve recall at the cost of latency
                "params": {"ef": max(top_k * 2, 64)},
            }
            expr = f'{_FIELD_NS} == "{namespace}"'

            raw = collection.search(
                data=[embedding],
                anns_field=_FIELD_EMBEDDING,
                param=search_params,
                limit=top_k,
                expr=expr,
                output_fields=[
                    _FIELD_ID,
                    _FIELD_CONTENT,
                    _FIELD_METADATA,
                ],
            )

            results: List[Dict[str, Any]] = []
            for hits in raw:
                for hit in hits:
                    entity = hit.entity
                    results.append({
                        "id": entity.get(_FIELD_ID, ""),
                        "content": entity.get(_FIELD_CONTENT, ""),
                        "embedding": None,  # omitted from search results for efficiency
                        "metadata": entity.get(_FIELD_METADATA) or {},
                        "score": float(hit.distance),
                    })

            logger.debug(
                "search returned %d hits from namespace '%s'",
                len(results), namespace,
            )
            return results

        except Exception as exc:
            logger.error("Search failed in namespace '%s': %s", namespace, exc)
            raise

    def exists(self, namespace: str) -> bool:
        """Return ``True`` if *namespace* contains at least one node.

        Analogous to :meth:`JSONFileConnector.exists`.
        """
        try:
            # Skip the Milvus network round-trip when the collection has already
            # been injected (e.g. via set_collection() in tests).
            if self._collection is None:
                if not utility.has_collection(self.collection_name, using=self.alias):
                    return False
            collection = self._get_collection()
            expr = f'{_FIELD_NS} == "{namespace}"'
            results = collection.query(
                expr=expr,
                output_fields=[_FIELD_ID],
                limit=1,
            )
            return len(results) > 0
        except Exception as exc:
            logger.error("exists() failed for namespace '%s': %s", namespace, exc)
            return False

    def delete(self, namespace: str) -> bool:
        """Delete all nodes belonging to *namespace*.

        Analogous to :meth:`JSONFileConnector.delete`.

        Returns:
            ``True`` if nodes were deleted; ``False`` if *namespace* was empty.

        Raises:
            Exception: On Milvus errors.
        """
        try:
            collection = self._get_collection()
            expr = f'{_FIELD_NS} == "{namespace}"'
            # Retrieve IDs first; Milvus delete is most reliable via PK expression
            results = collection.query(expr=expr, output_fields=[_FIELD_ID])
            if not results:
                logger.info("delete(): namespace '%s' was already empty", namespace)
                return False

            ids = [r[_FIELD_ID] for r in results]
            collection.delete(self.ids_expr(ids))
            collection.flush()
            logger.info(
                "Deleted %d nodes from namespace '%s'", len(ids), namespace
            )
            return True

        except Exception as exc:
            logger.error("delete() failed for namespace '%s': %s", namespace, exc)
            raise

    def delete_nodes(self, namespace: str, node_ids: List[str]) -> bool:
        """Delete specific nodes by their IDs within *namespace*.

        Args:
            namespace: Logical partition key (used for logging only).
            node_ids:  Node IDs to remove.

        Returns:
            ``True`` on success (including the no-op case of an empty list).

        Raises:
            Exception: On Milvus errors.
        """
        if not node_ids:
            return True
        try:
            collection = self._get_collection()
            collection.delete(self.ids_expr(node_ids))
            collection.flush()
            logger.info(
                "Deleted %d nodes from namespace '%s'", len(node_ids), namespace
            )
            return True
        except Exception as exc:
            logger.error(
                "delete_nodes() failed for namespace '%s': %s", namespace, exc
            )
            raise

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def create_index(
        self,
        index_type: str = "HNSW",
        metric_type: Optional[str] = None,
        m: int = 16,
        ef_construction: int = 64,
        nlist: int = 128,
    ) -> None:
        """(Re)create an ANN index on the embedding field.

        Drops the existing index first if one is present.  The collection
        is reloaded into memory after the new index is built.

        Args:
            index_type:      Index algorithm.

                             - ``"HNSW"`` (default) – hierarchical NSW graph.
                             - ``"IVF_FLAT"`` – inverted file with exact storage.
                             - ``"IVF_SQ8"``  – inverted file with 8-bit quantisation.
                             - ``"FLAT"``     – brute-force (no approximation).
            metric_type:     Distance metric.  Defaults to the connector's
                             ``metric_type`` set at construction time.
                             ``"COSINE"``, ``"L2"``, or ``"IP"``.
            m:               HNSW – number of bi-directional links per node
                             (default 16; range 4–64).
            ef_construction: HNSW – size of the dynamic candidate list during
                             index build (default 64).
            nlist:           IVF – number of cluster centroids (default 128).

        Raises:
            Exception: On Milvus errors.
        """
        collection = self._get_collection()
        mt = metric_type or self.metric_type

        if collection.has_index():
            collection.drop_index()
            logger.info("Dropped existing index on '%s'", self.collection_name)

        if index_type == "HNSW":
            params = {"M": m, "efConstruction": ef_construction}
        elif index_type.startswith("IVF"):
            params = {"nlist": nlist}
        else:
            params = {}

        index_params = {
            "index_type": index_type,
            "metric_type": mt,
            "params": params,
        }
        collection.create_index(
            field_name=_FIELD_EMBEDDING,
            index_params=index_params,
        )
        collection.load()
        logger.info(
            "Created %s index on '%s' (metric=%s)",
            index_type, self.collection_name, mt,
        )

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def list_namespaces(self) -> List[str]:
        """Return all distinct namespace values stored in the collection.

        .. note::
            This fetches all entity IDs and namespace fields to deduplicate
            in Python.  For very large collections, prefer maintaining a
            separate namespace registry.

        Returns:
            Sorted list of namespace strings.
        """
        try:
            collection = self._get_collection()
            # id != "" is always true for our non-empty primary keys
            results = collection.query(
                expr=f'{_FIELD_ID} != ""',
                output_fields=[_FIELD_NS],
            )
            return sorted({r[_FIELD_NS] for r in results})
        except Exception as exc:
            logger.error("list_namespaces() failed: %s", exc)
            return []

    def count(self, namespace: Optional[str] = None) -> int:
        """Return the number of stored nodes, optionally filtered by *namespace*.

        Args:
            namespace: If given, count only nodes in that partition.
                       If ``None``, return the total entity count across all
                       namespaces (uses the cached ``num_entities`` value).
        """
        try:
            collection = self._get_collection()
            if namespace is not None:
                expr = f'{_FIELD_NS} == "{namespace}"'
                results = collection.query(expr=expr, output_fields=[_FIELD_ID])
                return len(results)
            # num_entities is a fast property; may lag slightly after recent inserts
            return collection.num_entities
        except Exception as exc:
            logger.error("count() failed: %s", exc)
            return 0

    def flush(self) -> None:
        """Flush buffered inserts/deletes to persistent Milvus storage."""
        if self._collection:
            self._collection.flush()
            logger.debug("Flushed Milvus collection '%s'", self.collection_name)

    def close(self) -> None:
        """Disconnect this client from Milvus.

        Only the gRPC connection identified by ``self.alias`` is closed.
        The collection is intentionally **not** released from Milvus server
        memory here, because ``collection.release()`` is a *server-side*
        operation that affects every client connected to the same collection —
        not just this instance.  Calling release() on close() would unload the
        collection for any other :class:`MilvusConnector` sharing the same
        ``collection_name``, causing "collection not loaded" (code=101) errors.
        """
        from pymilvus import connections  # pylint: disable=import-outside-toplevel

        try:
            connections.disconnect(self.alias)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Error disconnecting from Milvus: %s", exc)

        self._collection = None
        logger.info(
            "MilvusConnector closed (host=%s, port=%s)", self.host, self.port
        )
