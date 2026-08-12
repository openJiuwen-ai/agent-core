# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""conftest — mock missing optional deps before collection."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _a2a_importable() -> bool:
    """Return True when the real a2a-sdk can be imported.

    Checking ``sys.modules`` is not enough: at collection time a2a has not
    been imported yet even when the SDK is installed, so a naive
    ``"a2a" not in sys.modules`` check would unconditionally shadow the real
    package for the whole session and break the dedicated a2a extension
    tests that need real types.
    """
    try:
        import a2a.types  # noqa: F401
    except Exception:
        return False
    return True


# a2a is an optional dependency that may be absent from the test
# venv. Pre-inject stubs so the import chain through
# harness.rails → Runner → a2a doesn't blow up.
# Each intermediate path needs its own entry so Python
# treats them as packages (not plain attributes).
_A2A_SUBMODULES = [
    "a2a",
    "a2a.types",
    "a2a.types.a2a_pb2",
    "a2a.client",
    "a2a.client.client",
    "a2a.server",
    "a2a.server.apps",
    "a2a.server.request_handlers",
    "a2a.server.agent_execution",
]

if not _a2a_importable():
    for _name in _A2A_SUBMODULES:
        sys.modules[_name] = MagicMock()
