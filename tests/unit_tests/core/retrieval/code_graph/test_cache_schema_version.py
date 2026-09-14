# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""An index written by an older schema must never load into a newer reader."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph import models as models_module
from openjiuwen.core.retrieval.code_graph.models import (
    INDEX_SCHEMA_VERSION,
    CallResolution,
    CodeGraphConfig,
    CodeGraphIndex,
    Relation,
    RelationEvidence,
    RelationKind,
    Symbol,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.store.index_store import (
    CACHE_FORMAT_VERSION,
    DiskIndexStore,
)

pytestmark = pytest.mark.level0

CACHE_KEY = "repo-snapshot-hash"


def _index() -> CodeGraphIndex:
    index = CodeGraphIndex(repo_root="/repo", snapshot="snap", config_hash="cfg")
    for name in ("caller", "callee"):
        index.add_symbol(
            Symbol(
                symbol_id=f"mod.py::{name}",
                name=name,
                kind=SymbolKind.FUNCTION,
                file="mod.py",
                start_line=1,
                end_line=2,
            )
        )
    index.add_relation(
        Relation(
            source_id="mod.py::caller",
            kind=RelationKind.CALLS,
            target_id="mod.py::callee",
            evidence=RelationEvidence(
                file="mod.py",
                start_line=2,
                end_line=2,
                expression="callee()",
                resolution=CallResolution.SAME_FILE.value,
                confidence=0.8,
            ),
        )
    )
    return index


def test_roundtrip_preserves_relation_evidence(tmp_path: Path) -> None:
    store = DiskIndexStore(tmp_path, max_size_mb=8)
    store.save(CACHE_KEY, _index())

    loaded = store.load(CACHE_KEY)

    assert loaded is not None
    evidence = loaded.evidence_for(
        "mod.py::caller", RelationKind.CALLS, "mod.py::callee"
    )
    assert [item.resolution for item in evidence] == [CallResolution.SAME_FILE.value]
    assert evidence[0].start_line == 2


def test_stale_envelope_version_is_rejected(tmp_path: Path) -> None:
    store = DiskIndexStore(tmp_path, max_size_mb=8)
    payload = {
        "version": CACHE_FORMAT_VERSION - 1,
        "schema": INDEX_SCHEMA_VERSION,
        "index": _index(),
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{CACHE_KEY}.pkl").write_bytes(pickle.dumps(payload, protocol=4))

    assert store.load(CACHE_KEY) is None


def test_stale_schema_version_is_rejected(tmp_path: Path) -> None:
    store = DiskIndexStore(tmp_path, max_size_mb=8)
    payload = {
        "version": CACHE_FORMAT_VERSION,
        "schema": INDEX_SCHEMA_VERSION - 1,
        "index": _index(),
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{CACHE_KEY}.pkl").write_bytes(pickle.dumps(payload, protocol=4))

    assert store.load(CACHE_KEY) is None


def test_index_stamped_with_an_older_schema_is_rejected(tmp_path: Path) -> None:
    store = DiskIndexStore(tmp_path, max_size_mb=8)
    stale = _index()
    stale.schema_version = INDEX_SCHEMA_VERSION - 1
    payload = {
        "version": CACHE_FORMAT_VERSION,
        "schema": INDEX_SCHEMA_VERSION,
        "index": stale,
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{CACHE_KEY}.pkl").write_bytes(pickle.dumps(payload, protocol=4))

    assert store.load(CACHE_KEY) is None


def test_config_hash_changes_with_the_schema_version(monkeypatch) -> None:
    config = CodeGraphConfig()
    before = config.config_hash()

    monkeypatch.setattr(models_module, "INDEX_SCHEMA_VERSION", INDEX_SCHEMA_VERSION + 1)

    assert config.config_hash() != before
