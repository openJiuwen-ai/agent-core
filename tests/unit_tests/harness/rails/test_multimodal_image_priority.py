# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the native-image resolution priority.

Two call sites must agree on the same priority order:

- ``jiuwenswarm.server.runtime.agent_adapter.interface_deep.
  _native_image_input_status`` (server-side notice) resolves
  **per-model > global > probe**.
- ``openjiuwen.harness.rails._multimodal.should_enable_read_image_multimodal``
  (actual image injection) used to resolve **global > per-model > probe**.

When both a global ``enable_read_image_multimodal`` and a per-model
``supports_vision`` were configured, the notice said one thing and the rail
did another. These tests pin the unified order: per-model declaration wins
over the global config (explicit override still wins over everything).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.harness.rails import _multimodal


def _model(supports_vision: bool | None):
    if supports_vision is None:
        return SimpleNamespace(model_client_config=None)
    mcc = SimpleNamespace(supports_vision=supports_vision)
    return SimpleNamespace(model_client_config=mcc)


def _agent(supports_vision: bool | None, global_enabled: bool | None):
    deep_config = SimpleNamespace(
        model=_model(supports_vision),
        enable_read_image_multimodal=global_enabled,
    )
    return SimpleNamespace(deep_config=deep_config)


@pytest.fixture()
def empty_probe_cache(monkeypatch):
    """Force the probe cache to report 'undetermined'."""
    monkeypatch.setattr(_multimodal, "get_cached_image_support", lambda _model: None)


def test_per_model_false_wins_over_global_true(empty_probe_cache):
    """supports_vision=False must disable native input even when the global
    switch is on -- otherwise image bytes are sent to a model that rejected
    them and the request fails."""
    agent = _agent(supports_vision=False, global_enabled=True)
    assert _multimodal.should_enable_read_image_multimodal(agent) is False


def test_per_model_true_wins_over_global_false(empty_probe_cache):
    agent = _agent(supports_vision=True, global_enabled=False)
    assert _multimodal.should_enable_read_image_multimodal(agent) is True


def test_global_used_when_no_per_model_declaration(empty_probe_cache):
    no_declaration = SimpleNamespace(
        model=SimpleNamespace(model_client_config=SimpleNamespace()),
        enable_read_image_multimodal=True,
    )
    assert _multimodal.should_enable_read_image_multimodal(no_declaration) is True


def test_probe_cache_is_last_fallback(empty_probe_cache):
    agent = _agent(supports_vision=None, global_enabled=None)
    assert _multimodal.should_enable_read_image_multimodal(agent) is False


def test_probe_cache_true_enables_when_nothing_declared(monkeypatch):
    monkeypatch.setattr(_multimodal, "get_cached_image_support", lambda _model: True)
    agent = _agent(supports_vision=None, global_enabled=None)
    assert _multimodal.should_enable_read_image_multimodal(agent) is True


def test_explicit_override_wins_over_everything(empty_probe_cache):
    agent = _agent(supports_vision=False, global_enabled=True)
    assert _multimodal.should_enable_read_image_multimodal(agent, explicit_value=True) is True


def test_resolver_builder_tracks_per_model_declaration(empty_probe_cache):
    agent = _agent(supports_vision=False, global_enabled=True)
    resolve = _multimodal.build_read_image_multimodal_resolver(agent)
    assert resolve() is False
