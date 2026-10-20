# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fixtures for tracer_otel unit tests."""

import pytest

from openjiuwen.core.single_agent.rail.default_rails import clear_default_rail_factories
from openjiuwen.extensions.tracer_otel.setup import AUTO_ATTACH_RAIL_ENV


@pytest.fixture(autouse=True)
def _isolated_default_rail_registry(monkeypatch: pytest.MonkeyPatch):
    """Keep the process-wide default-rail registry out of other suites.

    ``init_otel_tracer`` registers the OtelRail factory as a side effect;
    without this fixture that registration would leak into any later test
    that constructs a BaseAgent in the same process.
    """
    monkeypatch.delenv(AUTO_ATTACH_RAIL_ENV, raising=False)
    clear_default_rail_factories()
    yield
    clear_default_rail_factories()
