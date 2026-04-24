# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Test bootstrap for harness/rails unit tests.

Patches a pre-existing issue that blocks collection in this environment:

1. ``jsonschema_path`` — optional dep not installed in CI.
"""
from __future__ import annotations

import sys
import types


def _stub_jsonschema_path() -> None:
    if "jsonschema_path" not in sys.modules:
        m = types.ModuleType("jsonschema_path")
        m.SchemaPath = object  # type: ignore[attr-defined]
        sys.modules["jsonschema_path"] = m


_stub_jsonschema_path()
