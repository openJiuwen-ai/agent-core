# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Foundation store module, supporting various kind of relational & vector databases

Lazy-loading module using PEP 562 __getattr__ to avoid unnecessary import of heavy dependencies like SQLAlchemy.
"""

import importlib
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Callable

from openjiuwen.core.common.logging import store_logger
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

# Vector store exports (the ones that don't depend on SQLAlchemy)
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
    VectorSearchResult,
)
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore

# Entry-points group name for 3rd-party vector-store plugins.
# STABLE PUBLIC API: published plugins declare this group in their
# pyproject.toml. Changing this string breaks every external plugin.
VECTOR_STORE_ENTRY_POINT_GROUP = "openjiuwen.vector_stores"

# Built-in backends. Closed to extension by design — use register_vector_store
# or the entry_points mechanism for 3rd-party backends.
_BUILTIN_VECTOR_STORE_NAMES = frozenset({"chroma", "milvus", "gaussvector"})

# Explicit in-process registrations (register_vector_store).
# Maps backend name -> factory callable (typically a class).
_CUSTOM_VECTOR_STORES: dict[str, Callable[..., "BaseVectorStore"]] = {}


def register_vector_store(
    name: str, factory: Callable[..., "BaseVectorStore"]
) -> None:
    """
    Register a vector-store backend at runtime for programmatic use.

    Use this in application init code when shipping a plugin via
    entry_points is not practical (e.g., private in-repo backend).

    Built-in names (chroma, milvus, gaussvector) cannot be overridden — a
    register call with a built-in name is kept in the registry but the
    built-in still wins in ``create_vector_store()`` resolution.

    Args:
        name: Backend identifier used in ``create_vector_store(name, ...)``.
        factory: Callable that accepts **kwargs and returns a BaseVectorStore.
                 Typically a class, but any callable is allowed.

    Thread-safety: not thread-safe. Call during app init, before any worker
    threads start.
    """
    _CUSTOM_VECTOR_STORES[name] = factory


def _resolve_builtin(store_type: str, kwargs: dict) -> "BaseVectorStore | None":
    """Built-in backends are hard-coded to keep import costs tight and
    behavior completely stable across releases."""
    if store_type == "chroma":
        from openjiuwen.core.foundation.store.vector.chroma_vector_store import ChromaVectorStore
        return ChromaVectorStore(**kwargs)
    if store_type == "milvus":
        from openjiuwen.core.foundation.store.vector.milvus_vector_store import MilvusVectorStore
        return MilvusVectorStore(**kwargs)
    if store_type == "gaussvector":
        from openjiuwen.core.foundation.store.vector.gauss_vector_store import GaussVectorStore
        return GaussVectorStore(**kwargs)
    return None


def _resolve_entry_point(store_type: str, kwargs: dict) -> "BaseVectorStore | None":
    """Scan ``openjiuwen.vector_stores`` entry_points for a matching plugin.

    A plugin load failure (ImportError / any exception from ``.load()``) is
    logged and turns into a None result, so a broken third-party wheel
    cannot break the factory for everyone.
    """
    try:
        eps = entry_points(group=VECTOR_STORE_ENTRY_POINT_GROUP)
    except Exception as e:  # noqa: BLE001 — stdlib may raise on broken metadata
        store_logger.warning("Failed to enumerate entry_points for %s: %s",
                             VECTOR_STORE_ENTRY_POINT_GROUP, e)
        return None

    for ep in eps:
        if ep.name != store_type:
            continue
        try:
            cls = ep.load()
        except Exception as e:  # noqa: BLE001 — any plugin import failure
            store_logger.warning(
                "Failed to load vector-store plugin '%s' (entry point %r): %s. "
                "Install/update the plugin package or uninstall it to silence this warning.",
                store_type, ep, e,
            )
            return None
        try:
            return cls(**kwargs)
        except Exception as e:  # noqa: BLE001 — plugin constructor failed
            store_logger.warning(
                "Vector-store plugin '%s' loaded but failed to instantiate: %s",
                store_type, e,
            )
            return None
    return None


def create_vector_store(store_type: str, **kwargs) -> "BaseVectorStore | None":
    """Factory for vector-store backends.

    Resolution order:
      1. Built-in (chroma, milvus, gaussvector) — always wins, closed set.
      2. Explicit registrations via ``register_vector_store()``.
      3. Entry_points in group ``openjiuwen.vector_stores``.

    Returns None if none match. A plugin that fails to load or instantiate
    is logged as a warning and treated as "no match".
    """
    if store_type in _BUILTIN_VECTOR_STORE_NAMES:
        return _resolve_builtin(store_type, kwargs)

    if store_type in _CUSTOM_VECTOR_STORES:
        return _CUSTOM_VECTOR_STORES[store_type](**kwargs)

    return _resolve_entry_point(store_type, kwargs)


__all__ = [
    "BaseKVStore",
    "BaseVectorStore",
    "InMemoryKVStore",
    "VectorSearchResult",
    "CollectionSchema",
    "FieldSchema",
    "VectorDataType",
    "create_vector_store",
    "register_vector_store",
    "VECTOR_STORE_ENTRY_POINT_GROUP",
    # Submodules
    "vector_fields",
    "vector",
    "query",
    "object",
    "kv",
    "graph",
    "db",
    # Lazy attributes
    "BaseDbStore",
    "DbBasedKVStore",
    "DefaultDbStore",
]

# Lazy-loaded attributes (SQLAlchemy-dependent)
_LAZY_ATTRIBUTES = [
    "BaseDbStore",
    "DbBasedKVStore",
    "DefaultDbStore",
]


def __getattr__(name: str):
    """
    Lazy import for SQLAlchemy-dependent modules using PEP 562.

    This allows importing foundation.store.query without pulling in SQLAlchemy
    dependencies that are only needed for BaseDbStore, DbBasedKVStore, etc.
    """
    if name in _LAZY_ATTRIBUTES:
        match name:
            case "BaseDbStore":
                from openjiuwen.core.foundation.store.base_db_store import BaseDbStore

                return BaseDbStore
            case "DbBasedKVStore":
                from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore

                return DbBasedKVStore
            case "DefaultDbStore":
                from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore

                return DefaultDbStore
    return importlib.import_module("." + name, __name__)


def __dir__():
    """
    Support dir() calls by returning __all__ plus any lazy-loaded attributes.
    """
    return __all__ + _LAZY_ATTRIBUTES


if TYPE_CHECKING:
    from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
    from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
    from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
