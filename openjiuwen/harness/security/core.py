# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Implementation: :mod:`openjiuwen.harness.security.engine.core`."""

from __future__ import annotations

import importlib
import sys

sys.modules[__name__] = importlib.import_module("openjiuwen.harness.security.engine.core")
