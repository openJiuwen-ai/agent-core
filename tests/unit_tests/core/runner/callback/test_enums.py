# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework enumerations.
"""

from openjiuwen.core.runner.callback import (
    ChainAction,
    FilterAction,
    HookType,
)


def test_filter_action_values():
    """Test FilterAction has correct values."""
    assert FilterAction.CONTINUE.value == "continue"
    assert FilterAction.STOP.value == "stop"
    assert FilterAction.SKIP.value == "skip"
    assert FilterAction.MODIFY.value == "modify"


def test_filter_action_members():
    """Test FilterAction has all expected members."""
    members = list(FilterAction)
    assert len(members) == 4
    assert FilterAction.CONTINUE in members
    assert FilterAction.STOP in members
    assert FilterAction.SKIP in members
    assert FilterAction.MODIFY in members


def test_chain_action_values():
    """Test ChainAction has correct values."""
    assert ChainAction.CONTINUE.value == "continue"
    assert ChainAction.BREAK.value == "break"
    assert ChainAction.RETRY.value == "retry"
    assert ChainAction.ROLLBACK.value == "rollback"


def test_chain_action_members():
    """Test ChainAction has all expected members."""
    members = list(ChainAction)
    assert len(members) == 4
    assert ChainAction.CONTINUE in members
    assert ChainAction.BREAK in members
    assert ChainAction.RETRY in members
    assert ChainAction.ROLLBACK in members


def test_hook_type_values():
    """Test HookType has correct values."""
    assert HookType.BEFORE.value == "before"
    assert HookType.AFTER.value == "after"
    assert HookType.ERROR.value == "error"
    assert HookType.CLEANUP.value == "cleanup"


def test_hook_type_members():
    """Test HookType has all expected members."""
    members = list(HookType)
    assert len(members) == 4
    assert HookType.BEFORE in members
    assert HookType.AFTER in members
    assert HookType.ERROR in members
    assert HookType.CLEANUP in members
