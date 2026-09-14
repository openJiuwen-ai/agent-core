# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generic KV-cache identity and range metadata tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.kv_cache import (
    context_compressor_cache_identity,
    message_range_kwargs,
    resolve_session_lineage,
    team_member_cache_identity,
    tools_range_kwargs,
)


def test_range_kwargs_always_return_complete_half_open_ranges():
    assert message_range_kwargs(2, 5) == {"msg_start": 2, "msg_end": 5}
    assert tools_range_kwargs(0, 1) == {"tools_start": 0, "tools_end": 1}


def test_session_lineage_ignores_non_string_affinity_overrides():
    session = SimpleNamespace(
        get_session_id=lambda: "session-id",
        get_parent_session_id=lambda: None,
        get_env=MagicMock(return_value=object()),
    )

    assert resolve_session_lineage(session) == ("session-id", "session-id")


def test_team_member_cache_identity_uses_card_id_scope():
    assert (
        team_member_cache_identity("team-sid", "team-a", "coder")
        == "team:team-sid:team:team-a:member:coder"
    )


@pytest.mark.parametrize(
    ("compressor_type", "suffix"),
    [
        ("RoundLevelCompressor", "round-level"),
        ("CurrentRoundCompressor", "current-round"),
        ("DialogueCompressor", "dialogue"),
    ],
)
def test_context_compressor_cache_identity_is_a_stable_child(compressor_type, suffix):
    identity = context_compressor_cache_identity("session-a", compressor_type)

    assert identity.cache_id == f"session-a:compressor:{suffix}"
    assert identity.parent_cache_id == "session-a"


def test_context_compressor_cache_identity_rejects_unknown_type():
    with pytest.raises(ValueError, match="unsupported context compressor"):
        context_compressor_cache_identity("session-a", "UnknownCompressor")
