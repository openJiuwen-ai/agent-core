# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Restoring an empty state must clear the previous state."""

from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.core.session.state.base import InMemoryStateLike


def test_in_memory_set_state_empty_dict_clears() -> None:
    state = InMemoryStateLike()
    state.update({"stale": {"value": 1}})

    state.set_state({})

    assert state.get_state() == {}


def test_state_collection_empty_restore_clears_both_namespaces() -> None:
    collection = StateCollection()
    collection.update({"agent": 1})
    collection.update_global({"global": 2})

    collection.set_state({"global_state": {}, "agent_state": {}})

    assert collection.get() == {}
    assert collection.get_global(None) == {}


def test_in_memory_set_state_none_clears() -> None:
    state = InMemoryStateLike()
    state.update({"stale": 1})

    state.set_state(None)

    assert state.get_state() == {}
