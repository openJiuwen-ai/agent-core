# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""State keys containing '.' must survive a diff/apply round-trip."""

from openjiuwen.core.session.vcs.delta import apply_state, diff_state


def test_dotted_state_key_is_not_split_into_nested_dicts() -> None:
    original = {"a.b": 1, "x~y": {"c.d": 2}}

    delta = diff_state({}, original)

    assert apply_state({}, delta) == original


def test_removed_dotted_state_key_deletes_flat_key() -> None:
    old = {"a.b": 1, "keep": 2}

    delta = diff_state(old, {"keep": 2})

    assert apply_state(old, delta) == {"keep": 2}
