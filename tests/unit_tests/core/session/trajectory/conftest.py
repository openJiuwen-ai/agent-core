# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared fixtures for trajectory tests."""
import pytest_asyncio

from openjiuwen.core.session.tracer.tracer import TracerHandlerRegistry


@pytest_asyncio.fixture(autouse=True)
async def _clean_registry():
    """Recorder tests register handlers in the global registry; always clean up."""
    yield
    TracerHandlerRegistry.clear()
