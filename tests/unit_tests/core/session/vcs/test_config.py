# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for vcs configuration and backend factory."""
import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.vcs.config import VersioningConfig, build_backend
from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.kv_backend import KvBackend


def test_defaults():
    cfg = VersioningConfig()
    assert cfg.backend == "jsonl"
    assert cfg.snapshot_every == 50
    assert cfg.fsync_policy == "batch"


def test_jsonl_backend_built(tmp_path):
    backend = build_backend("s1", VersioningConfig(backend="jsonl", root=str(tmp_path)))
    assert isinstance(backend, JsonlBackend)


def test_kv_backend_built_with_store():
    backend = build_backend("s1", VersioningConfig(backend="kv"), kv_store=InMemoryKVStore())
    assert isinstance(backend, KvBackend)


def test_kv_backend_requires_store():
    with pytest.raises(Exception):
        build_backend("s1", VersioningConfig(backend="kv"))
