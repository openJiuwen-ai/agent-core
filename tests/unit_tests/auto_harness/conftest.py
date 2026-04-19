# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""conftest — mock missing optional deps before collection."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# a2a is an optional dependency not installed in the test
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

if "a2a" not in sys.modules:
    for _name in _A2A_SUBMODULES:
        sys.modules[_name] = MagicMock()
