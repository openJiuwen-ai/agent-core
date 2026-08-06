# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fixtures for harness system tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_image_modality_probe(monkeypatch):
    """Prevent auto image-modality probe from consuming mock LLM responses."""
    schedule = MagicMock()
    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", schedule)
    return schedule
