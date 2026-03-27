# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared persistence helper for memory PersistMemoryOp classes.

Provides :class:`MemoryPersistenceHelper` — a thin wrapper used by every
``PersistMemoryOp`` across the ACE, ReasoningBank, and ReMe summary
pipelines.  It abstracts the choice between a local JSON file
(:class:`~core.file_connector.JSONFileConnector`) and a Milvus vector
database (:class:`~core.db_connector.MilvusConnector`).
"""

from typing import Any, Dict, Optional

from openjiuwen.core.common.logging import context_engine_logger as logger

from .file_connector import JSONFileConnector
from .db_connector import MilvusConnector


class MemoryPersistenceHelper:
    """Handles persistence of ``{node_id: node_dict}`` data to a backend.

    Supported values for *persist_type*:

    * ``"json"``   – always use the local JSON file backend.
    * ``"milvus"`` – always use the Milvus backend (raises if unavailable).
    * ``"auto"``   – probe Milvus once on first use; if the server is
                     reachable use Milvus, otherwise fall back to JSON.
                     The resolved backend is cached for the lifetime of
                     this helper instance.

    Args:
        persist_type:       ``"auto"`` (default), ``"json"``, or ``"milvus"``.
        persist_path:       File-path template for the JSON backend.
                            ``{user_id}`` and ``{algo_name}`` are expanded
                            at runtime.
                            Default: ``"./memories/{algo_name}/{user_id}.json"``.
        milvus_host:        Milvus server hostname (default: ``"localhost"``).
        milvus_port:        Milvus gRPC port (default: ``19530``).
        milvus_collection:  Milvus collection name
                            (default: ``"vector_nodes"``).
    """

    def __init__(
        self,
        persist_type: str = "auto",
        persist_path: str = "./memories/{algo_name}/{user_id}.json",
        milvus_host: str = "localhost",
        milvus_port: int = 19530,
        milvus_collection: str = "vector_nodes",
    ) -> None:
        self.persist_type = persist_type
        self.persist_path = persist_path
        self._milvus_host = milvus_host
        self._milvus_port = milvus_port
        self._milvus_collection = milvus_collection

        # JSON connector is cheap — create eagerly.
        self._json_connector = JSONFileConnector()
        # Milvus connector requires a live server — create lazily.
        self._milvus_connector: Optional[MilvusConnector] = None
        # Resolved backend for "auto" mode; None means not yet probed.
        self._resolved_type: Optional[str] = None

    @property
    def resolved_type(self) -> Optional[str]:
        """The effective backend type after auto-detection, or ``None`` if not yet resolved."""
        return self._resolved_type

    def set_milvus_connector(self, connector: MilvusConnector) -> None:
        """Inject a Milvus connector directly, bypassing the auto-probe.

        Useful in tests to supply a mock connector without a live Milvus server.
        """
        self._milvus_connector = connector

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    def _resolve_backend(self) -> str:
        """Return the effective backend type, probing Milvus if needed.

        For ``persist_type="auto"`` this method is called once on first
        use.  It tries to establish a Milvus connection; on success the
        resolved type is ``"milvus"``, on any failure it falls back to
        ``"json"``.  The result is cached in ``_resolved_type``.
        """
        if self.persist_type != "auto":
            return self.persist_type

        if self._resolved_type is not None:
            return self._resolved_type

        # Probe Milvus
        try:
            conn = MilvusConnector(
                host=self._milvus_host,
                port=self._milvus_port,
                collection_name=self._milvus_collection,
            )
            self._milvus_connector = conn
            self._resolved_type = "milvus"
            logger.info(
                "Auto-detected Milvus at %s:%s — using Milvus persistence",
                self._milvus_host, self._milvus_port,
            )
        except Exception as exc:
            self._resolved_type = "json"
            logger.warning(
                "Milvus not reachable at %s:%s (%s) — falling back to JSON persistence",
                self._milvus_host, self._milvus_port, exc,
            )

        return self._resolved_type

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, user_id: str, algo_name: str, nodes_dict: Dict[str, Any]) -> None:
        """Upsert *nodes_dict* for *user_id* into the configured backend.

        For the JSON backend the existing file is loaded first so that data
        from previous runs is merged rather than overwritten.

        In ``"auto"`` mode, Milvus is probed on the first call; if it is
        reachable the data is written to Milvus, otherwise to a JSON file.

        Args:
            user_id:    User / workspace identifier.
            algo_name:  Short algorithm tag used in paths/namespaces
                        (``"ace"``, ``"rb"``, ``"reme"``).
            nodes_dict: ``{node_id: node_dict}`` mapping produced by
                        ``VectorNode.to_dict()``.
        """
        if not nodes_dict:
            logger.debug("PersistMemoryHelper: nothing to persist for user=%s", user_id)
            return

        backend = self._resolve_backend()
        if backend == "json":
            self._save_json(user_id, algo_name, nodes_dict)
        elif backend == "milvus":
            self._save_milvus(user_id, algo_name, nodes_dict)
        else:
            raise ValueError(
                f"Unknown persist_type '{self.persist_type}'. Must be 'auto', 'json', or 'milvus'."
            )

    def load(self, user_id: str, algo_name: str) -> Dict[str, Any]:
        """Load previously persisted nodes for *user_id*.

        Returns an empty dict when no data has been saved yet.
        In ``"auto"`` mode, Milvus is probed on the first call.
        """
        backend = self._resolve_backend()
        if backend == "json":
            return self._load_json(user_id, algo_name)
        elif backend == "milvus":
            return self._load_milvus(user_id, algo_name)
        else:
            raise ValueError(
                f"Unknown persist_type '{self.persist_type}'. Must be 'auto', 'json', or 'milvus'."
            )

    # ------------------------------------------------------------------
    # JSON backend
    # ------------------------------------------------------------------

    def _json_path(self, user_id: str, algo_name: str) -> str:
        return self.persist_path.format(user_id=user_id, algo_name=algo_name)

    def _save_json(self, user_id: str, algo_name: str, nodes_dict: Dict[str, Any]) -> None:
        path = self._json_path(user_id, algo_name)
        # Merge with existing data (upsert semantics)
        existing: Dict[str, Any] = {}
        if self._json_connector.exists(path):
            existing = self._json_connector.load_from_file(path)
        existing.update(nodes_dict)
        self._json_connector.save_to_file(path, existing)
        logger.info(
            "Persisted %d %s memories to JSON file: %s",
            len(nodes_dict), algo_name, path,
        )

    def _load_json(self, user_id: str, algo_name: str) -> Dict[str, Any]:
        path = self._json_path(user_id, algo_name)
        if not self._json_connector.exists(path):
            return {}
        data = self._json_connector.load_from_file(path)
        logger.info("Loaded %d %s memories from JSON file: %s", len(data), algo_name, path)
        return data

    # ------------------------------------------------------------------
    # Milvus backend
    # ------------------------------------------------------------------

    def _get_milvus(self) -> MilvusConnector:
        if self._milvus_connector is None:
            self._milvus_connector = MilvusConnector(
                host=self._milvus_host,
                port=self._milvus_port,
                collection_name=self._milvus_collection,
            )
        return self._milvus_connector

    @staticmethod
    def _namespace(user_id: str, algo_name: str) -> str:
        return f"memory_{algo_name}_{user_id}"

    def _save_milvus(self, user_id: str, algo_name: str, nodes_dict: Dict[str, Any]) -> None:
        ns = self._namespace(user_id, algo_name)
        self._get_milvus().save_to_db(ns, nodes_dict)
        logger.info(
            "Persisted %d %s memories to Milvus namespace '%s'",
            len(nodes_dict), algo_name, ns,
        )

    def _load_milvus(self, user_id: str, algo_name: str) -> Dict[str, Any]:
        ns = self._namespace(user_id, algo_name)
        conn = self._get_milvus()
        if not conn.exists(ns):
            return {}
        data = conn.load_from_db(ns)
        logger.info("Loaded %d %s memories from Milvus namespace '%s'", len(data), algo_name, ns)
        return data

    def __repr__(self) -> str:
        return (
            f"MemoryPersistenceHelper("
            f"persist_type={self.persist_type!r}, "
            f"persist_path={self.persist_path!r})"
        )
