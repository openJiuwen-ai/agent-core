# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for the vector-store factory plugin framework.

These tests have two concerns:
  1. Regression — built-in backends (chroma/milvus/gaussvector) still resolve the
     same way they did before the plugin framework landed.
  2. Plugin framework — explicit registration and entry_points discovery both
     work, name collisions are resolved deterministically, and a broken plugin
     never crashes the factory.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore


# ---------------------------------------------------------------------------
# Module-level setup: inject stub modules for optional backend dependencies
# so that unittest.mock.patch() can resolve the dotted patch targets without
# requiring chromadb / pymilvus / psycopg2 to be installed.
# ---------------------------------------------------------------------------
_BACKEND_STUBS = {
    "openjiuwen.core.foundation.store.vector.chroma_vector_store": "ChromaVectorStore",
    "openjiuwen.core.foundation.store.vector.milvus_vector_store": "MilvusVectorStore",
    "openjiuwen.core.foundation.store.vector.gauss_vector_store": "GaussVectorStore",
}

@pytest.fixture(autouse=True, scope="module")
def _stub_missing_backend_modules():
    """
    Pre-inject MagicMock stubs for optional backend modules so that
    ``unittest.mock.patch("...chroma_vector_store.ChromaVectorStore")`` can
    resolve its target without ``chromadb`` / ``pymilvus`` / ``psycopg2``
    being installed.

    The guard ``if mod_path not in sys.modules`` means this is a no-op in
    full-dependency environments (upstream CI) where the real modules are
    already loaded. Injected stubs are removed at module teardown so other
    test modules in the same pytest session can import the real backends.
    """
    injected = []
    for mod_path, cls_name in _BACKEND_STUBS.items():
        if mod_path not in sys.modules:
            stub = MagicMock()
            setattr(stub, cls_name, MagicMock())
            sys.modules[mod_path] = stub
            injected.append(mod_path)
    yield
    for mod_path in injected:
        sys.modules.pop(mod_path, None)


class _FakeVectorStore(BaseVectorStore):
    """Minimal BaseVectorStore impl used as a test plugin."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs

    async def create_collection(self, collection_name, schema, **kwargs): pass
    async def delete_collection(self, collection_name, **kwargs): pass
    async def collection_exists(self, collection_name, **kwargs): return False
    async def get_schema(self, collection_name, **kwargs): pass
    async def add_docs(self, collection_name, docs, **kwargs): pass
    async def search(self, collection_name, query_vector, vector_field, top_k=5, filters=None, **kwargs): return []
    async def delete_docs_by_ids(self, collection_name, ids, **kwargs): pass
    async def delete_docs_by_filters(self, collection_name, filters, **kwargs): pass
    async def list_collection_names(self): return []
    async def get_collection_metadata(self, collection_name): return {}
    async def update_collection_metadata(self, collection_name, metadata): pass
    async def update_schema(self, collection_name, operations): pass


class TestBuiltinRegression:
    """Built-in backends must resolve identically to pre-plugin behavior."""

    def test_unknown_returns_none(self):
        assert create_vector_store("this_backend_does_not_exist") is None

    def test_chroma_dispatches_to_chroma_class(self):
        with patch(
            "openjiuwen.core.foundation.store.vector.chroma_vector_store.ChromaVectorStore"
        ) as MockChroma:
            create_vector_store("chroma", persist_directory="/tmp/x")
            MockChroma.assert_called_once_with(persist_directory="/tmp/x")

    def test_milvus_dispatches_to_milvus_class(self):
        with patch(
            "openjiuwen.core.foundation.store.vector.milvus_vector_store.MilvusVectorStore"
        ) as MockMilvus:
            create_vector_store("milvus", uri="http://localhost:19530")
            MockMilvus.assert_called_once_with(uri="http://localhost:19530")

    def test_gaussvector_dispatches_to_gauss_class(self):
        with patch(
            "openjiuwen.core.foundation.store.vector.gauss_vector_store.GaussVectorStore"
        ) as MockGauss:
            create_vector_store("gaussvector", host="h", port=5432)
            MockGauss.assert_called_once_with(host="h", port=5432)


class TestExplicitRegistration:
    """`register_vector_store(name, factory)` adds a new backend at runtime."""

    def setup_method(self):
        # Import lazily; module may not yet expose these symbols before Task 3
        from openjiuwen.core.foundation import store as store_mod
        self._mod = store_mod
        # Snapshot the registry so each test is isolated
        self._snapshot = dict(getattr(store_mod, "_CUSTOM_VECTOR_STORES", {}))

    def teardown_method(self):
        if hasattr(self._mod, "_CUSTOM_VECTOR_STORES"):
            self._mod._CUSTOM_VECTOR_STORES.clear()
            self._mod._CUSTOM_VECTOR_STORES.update(self._snapshot)

    def test_register_then_create(self):
        from openjiuwen.core.foundation.store import register_vector_store
        register_vector_store("test_fake", _FakeVectorStore)

        store = create_vector_store("test_fake", dsn="x")
        assert isinstance(store, _FakeVectorStore)
        assert store.init_kwargs == {"dsn": "x"}

    def test_register_does_not_shadow_builtin(self):
        """A plugin MUST NOT be able to override a built-in by re-registering its name."""
        from openjiuwen.core.foundation.store import register_vector_store
        register_vector_store("chroma", _FakeVectorStore)

        with patch(
            "openjiuwen.core.foundation.store.vector.chroma_vector_store.ChromaVectorStore"
        ) as MockChroma:
            create_vector_store("chroma")
            # Built-in still wins
            MockChroma.assert_called_once()


class TestEntryPointsDiscovery:
    """Third-party packages can register via the `openjiuwen.vector_stores` entry_points group."""

    def test_entry_point_is_discovered(self):
        # Build a fake EntryPoint whose .load() returns _FakeVectorStore
        fake_ep = MagicMock()
        fake_ep.name = "test_ep_fake"
        fake_ep.load.return_value = _FakeVectorStore

        with patch(
            "openjiuwen.core.foundation.store.entry_points"
        ) as mock_eps:
            mock_eps.return_value = [fake_ep]
            store = create_vector_store("test_ep_fake", foo="bar")

        assert isinstance(store, _FakeVectorStore)
        assert store.init_kwargs == {"foo": "bar"}

    def test_entry_point_load_error_is_swallowed(self):
        """A plugin that fails to import must log a warning and NOT crash the factory."""
        broken_ep = MagicMock()
        broken_ep.name = "broken"
        broken_ep.load.side_effect = ImportError("fake import failure")

        with patch(
            "openjiuwen.core.foundation.store.entry_points"
        ) as mock_eps:
            mock_eps.return_value = [broken_ep]
            # Factory must return None for the broken plugin, not raise
            result = create_vector_store("broken")

        assert result is None

    def test_builtin_wins_over_entry_point(self):
        """If a 3rd-party plugin claims a built-in name, the built-in wins."""
        fake_ep = MagicMock()
        fake_ep.name = "chroma"
        fake_ep.load.return_value = _FakeVectorStore

        with patch(
            "openjiuwen.core.foundation.store.entry_points"
        ) as mock_eps, patch(
            "openjiuwen.core.foundation.store.vector.chroma_vector_store.ChromaVectorStore"
        ) as MockChroma:
            mock_eps.return_value = [fake_ep]
            create_vector_store("chroma")
            MockChroma.assert_called_once()


class TestEntryPointsGroupName:
    """Lock in the entry_points group name — once published, it cannot change."""

    def test_group_name_is_documented_constant(self):
        from openjiuwen.core.foundation.store import VECTOR_STORE_ENTRY_POINT_GROUP
        assert VECTOR_STORE_ENTRY_POINT_GROUP == "openjiuwen.vector_stores"
