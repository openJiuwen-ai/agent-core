# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fixtures for harness system tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _mock_image_modality_probe(monkeypatch):
    """Prevent auto image-modality probe from consuming mock LLM responses."""
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr("openjiuwen.harness.deep_agent.probe_image_support", probe)
    return probe
