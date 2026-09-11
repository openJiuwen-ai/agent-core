# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the context-evolve operator base (per-target update policies)."""

import pytest

from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    LOCAL_APPLY_COMPLETED,
    PENDING_CHANGE_EFFECT,
    REPLACE_MODE,
    STATE_EFFECT,
)
from openjiuwen.agent_evolving.types import UpdateEffect, UpdateMode, UpdateValue
from openjiuwen.core.operator.context_evolve_call import ContextEvolveOperator, UpdatePolicy


class _TwoTargetOperator(ContextEvolveOperator):
    operator_id_prefix = "stub_ctx_"
    update_policies = {
        "target_a": UpdatePolicy(allowed=frozenset({(APPEND_MODE, STATE_EFFECT)})),
        "target_b": UpdatePolicy(
            allowed=frozenset({(REPLACE_MODE, PENDING_CHANGE_EFFECT)}),
            kind="custom_kind",
            path="custom/path",
            constraint={"type": "full"},
        ),
    }


def _update(mode: UpdateMode, effect: UpdateEffect, payload="delta") -> UpdateValue:
    return UpdateValue(payload=payload, mode=mode, effect=effect, change_type="entry")


def test_operator_identity_and_tunables():
    op = _TwoTargetOperator("u1")
    assert op.operator_id == "stub_ctx_u1"
    assert op.scope_id == "u1"
    tunables = op.get_tunables()
    assert set(tunables) == {"target_a", "target_b"}
    assert tunables["target_a"].constraint == {"type": "delta"}
    assert tunables["target_b"].kind == "custom_kind"
    assert tunables["target_b"].path == "custom/path"
    assert tunables["target_b"].constraint == {"type": "full"}


def test_preview_accepts_declared_pair():
    op = _TwoTargetOperator("u1")
    result = op.preview_update("target_a", _update(APPEND_MODE, STATE_EFFECT))
    assert result.ok
    assert result.records == ["delta"]
    assert result.lifecycle_stage == LOCAL_APPLY_COMPLETED
    assert result.metadata["scope_id"] == "u1"


@pytest.mark.parametrize(
    ("target", "mode", "effect"),
    [
        ("other", APPEND_MODE, STATE_EFFECT),
        ("target_a", REPLACE_MODE, PENDING_CHANGE_EFFECT),
        ("target_b", REPLACE_MODE, STATE_EFFECT),
    ],
)
def test_preview_rejects_undeclared_target_or_pair(target, mode, effect):
    op = _TwoTargetOperator("u1")
    result = op.preview_update(target, _update(mode, effect))
    assert not result.ok and result.errors
