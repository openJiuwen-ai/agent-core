# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enable strict PowerShell security checks for unit tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enable_bash_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENJIUWEN_BASH_STRICT", "1")
