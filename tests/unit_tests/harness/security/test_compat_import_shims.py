# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep the two jiuwenswarm pre-1a94cc82 import shims wired to the new packages."""

from __future__ import annotations

from openjiuwen.harness.security.core import PermissionEngine as ShimEngine
from openjiuwen.harness.security.engine import PermissionEngine as Engine
from openjiuwen.harness.security.patterns import (
    merge_permission_allow_rule_into_permissions as shim_merge,
)
from openjiuwen.harness.security.toolguard.patterns import (
    merge_permission_allow_rule_into_permissions as merge,
)


def test_core_shim_is_engine_permission_engine() -> None:
    assert ShimEngine is Engine


def test_patterns_shim_is_toolguard_patterns() -> None:
    assert shim_merge is merge
