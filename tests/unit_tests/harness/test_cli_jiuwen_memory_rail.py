# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for CLI JiuwenMemoryRail mounting and its enable switch.

Covers the CLI assembly layer only (``_build_jiuwen_memory_rail``);
the rail's own lifecycle contract is tested in
``tests/unit_tests/harness/rails/test_jiuwen_memory_rail.py``.
"""

from __future__ import annotations

import pytest

# ``jiuwen_memory_adapter`` is an optional external adapter (mounted via
# PYTHONPATH by the jiuwenswarm host, not a declared project/test dependency),
# so CI runs without it. Import it defensively: switch-off cases need no
# provider and must still run; cases that inspect a real
# ``AgentMemoryMemoryProvider`` are skipped when the adapter is absent
# (see F_22 §背景: 记忆服务属可选依赖).
try:
    from jiuwen_memory_adapter.jiuwenswarm.agent_memory_provider import (
        AgentMemoryMemoryProvider,
    )

    _HAS_ADAPTER = True
except ImportError:
    AgentMemoryMemoryProvider = None  # type: ignore[assignment, misc]
    _HAS_ADAPTER = False

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.agent.factory import (
    _build_jiuwen_memory_rail,
)
from openjiuwen.harness.rails.memory.jiuwen_memory_rail import (
    JiuwenMemoryRail,
)

needs_adapter = pytest.mark.skipif(
    not _HAS_ADAPTER,
    reason="jiuwen_memory_adapter not installed (optional external adapter)",
)


@pytest.fixture
def cfg() -> CLIConfig:
    return CLIConfig(api_key="mock-api-key")


_ENV_VARS = (
    "JIUWEN_MEMORY_ENABLED",
    "JIUWEN_MEMORY_BASE_URL",
    "JIUWEN_MEMORY_API_KEY",
    "JIUWEN_MEMORY_TENANT_ID",
    "JIUWEN_MEMORY_USER_ID",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep the switch's env surface isolated between tests."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_switch_off_by_default(cfg):
    """Without JIUWEN_MEMORY_ENABLED the rail must not be constructed."""
    assert _build_jiuwen_memory_rail(cfg) is None


@needs_adapter
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " on "])
def test_switch_on_truthy_values(cfg, value, monkeypatch):
    """Truthy spellings mount the rail."""
    monkeypatch.setenv("JIUWEN_MEMORY_ENABLED", value)
    assert _build_jiuwen_memory_rail(cfg) is not None


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
def test_switch_off_falsy_values(cfg, value, monkeypatch):
    """Falsy spellings (and empty) keep the rail unmounted."""
    monkeypatch.setenv("JIUWEN_MEMORY_ENABLED", value)
    assert _build_jiuwen_memory_rail(cfg) is None


@needs_adapter
def test_enabled_rail_wires_env_config(cfg, monkeypatch):
    """Enabled rail carries the env-provided connection config."""
    monkeypatch.setenv("JIUWEN_MEMORY_ENABLED", "1")
    monkeypatch.setenv("JIUWEN_MEMORY_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("JIUWEN_MEMORY_API_KEY", "secret")
    monkeypatch.setenv("JIUWEN_MEMORY_TENANT_ID", "tenant-x")
    monkeypatch.setenv("JIUWEN_MEMORY_USER_ID", "alice")

    rail = _build_jiuwen_memory_rail(cfg)

    assert isinstance(rail, JiuwenMemoryRail)
    provider = rail._provider
    assert isinstance(provider, AgentMemoryMemoryProvider)
    assert provider._base_url == "http://127.0.0.1:9999"
    assert provider._api_key == "secret"
    assert rail._user_id == "alice"
    assert rail._scope_id == "tenant-x"


@needs_adapter
def test_enabled_rail_defaults(cfg, monkeypatch):
    """Enabled rail falls back to provider defaults when env is unset."""
    monkeypatch.setenv("JIUWEN_MEMORY_ENABLED", "1")

    rail = _build_jiuwen_memory_rail(cfg)

    assert isinstance(rail, JiuwenMemoryRail)
    provider = rail._provider
    assert isinstance(provider, AgentMemoryMemoryProvider)
    assert provider._base_url == ""
    assert rail._user_id == "jiuwenswarm-user"
    assert rail._scope_id == "default"
    # Cross-session sharing is the explicit default (design doc §6.1.2 #3).
    assert rail._session_id == ""
