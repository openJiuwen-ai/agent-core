# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the gateway-cut truncation give-up gate (#6101).

The persist-loop wiring in react_agent._inner_invoke is a thin caller of this
helper (same pattern as _maybe_toolcall_progress_output): on each truncated
iteration it reads metadata finish_reason_detail from the model reply and
delegates the give-up decision here.
"""
from openjiuwen.core.single_agent.agents.react_agent import (
    TRUNCATION_MAX_PERSISTS,
    _truncation_gives_up,
)


def test_cap_is_two_persist_rounds():
    # 1 retry (separate budget) + at most 2 persist-continuations
    assert TRUNCATION_MAX_PERSISTS == 2


def test_gateway_cut_gives_up_at_cap():
    assert _truncation_gives_up(TRUNCATION_MAX_PERSISTS, "gateway_cut") is True
    assert _truncation_gives_up(TRUNCATION_MAX_PERSISTS + 3, "gateway_cut") is True


def test_gateway_cut_below_cap_continues():
    assert _truncation_gives_up(0, "gateway_cut") is False
    assert _truncation_gives_up(TRUNCATION_MAX_PERSISTS - 1, "gateway_cut") is False


def test_real_server_length_cut_never_gives_up():
    # real "length" replies carry no normalization metadata: the historical
    # unbounded persist-until-max_iterations behavior is preserved
    for count in (0, 1, 10, 100):
        assert _truncation_gives_up(count, None) is False


def test_missing_or_foreign_metadata_never_gives_up():
    assert _truncation_gives_up(10, "") is False
    assert _truncation_gives_up(10, "other_detail") is False
    assert _truncation_gives_up(10, {}) is False
