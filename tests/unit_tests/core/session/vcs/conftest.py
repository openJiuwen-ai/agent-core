# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared fixtures for vcs manager tests."""
from copy import deepcopy

import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.manager import VersioningManager


@pytest.fixture
def make_manager():
    """Return a factory building a VersioningManager over an in-memory kv store.

    The factory returns ``(manager, live)`` where ``live`` is the mutable
    ``{"context", "state"}`` dict the manager reads via its snapshot provider;
    tests mutate ``live`` to simulate context / state changes. A deterministic
    clock and id_factory keep records reproducible.
    """

    def _make(*, snapshot_every=0, forker=None, ids=None):
        live = {"context": {}, "state": {}}

        async def provider():
            return deepcopy(live)

        async def applier(snap):
            live["context"] = deepcopy(snap["context"])
            live["state"] = deepcopy(snap["state"])

        seq = iter(ids) if ids is not None else None
        id_factory = (lambda: next(seq)) if seq is not None else (lambda: "id")
        manager = VersioningManager(
            "sess",
            KvBackend("sess", InMemoryKVStore()),
            snapshot_provider=provider,
            applier=applier,
            forker=forker,
            snapshot_every=snapshot_every,
            clock=lambda: 1.0,
            id_factory=id_factory,
        )
        return manager, live

    return _make
