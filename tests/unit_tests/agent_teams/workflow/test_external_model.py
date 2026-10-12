# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``plan_external_model``: the pure model-option resolver for third-party workers.

One test per design rule plus the enumerated edge cases: protocol-incompatible
pool hit, effort not in the catalog's ``efforts``, all-empty hints, and a
catalog-less entry on a pool miss.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel
from openjiuwen.agent_teams.workflow.backends.external_model import plan_external_model
from openjiuwen.agent_teams.workflow.engine.errors import BackendError

POOL_URL = "https://api.jiuwen.example/v1"

_POOL_ENTRY = SimpleNamespace(
    model_name="deepseek-v4-flash",
    api_key="k1",
    api_base_url=POOL_URL,
    api_provider="deepseek",
)

POOL = SimpleNamespace(get_model_pool=lambda: [_POOL_ENTRY])

POOL_MIXED = SimpleNamespace(
    get_model_pool=lambda: [
        _POOL_ENTRY,
        SimpleNamespace(
            model_name="anthropic-sonnet",
            api_key="k2",
            api_base_url=POOL_URL,
            api_provider="anthropic",
        ),
    ],
)

_BUILTIN = ExternalCliBuiltinModel(
    name="gpt-5-codex",
    efforts=["low", "medium", "high"],
    default_effort="medium",
)

ENTRY = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[_BUILTIN])
ENTRY_WITH_CATALOG = ENTRY
ENTRY_NO_CATALOG = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[])


def _assert_not_retryable(exc: Exception) -> None:
    assert exc.retryable is False


def test_pool_hit_builds_endpoint_primary():
    plan = plan_external_model(
        cli_agent="codex", model_hint="deepseek-v4-flash",
        builtin_hint=None, effort_hint=None, entry=ENTRY, team_backend=POOL,
    )
    assert plan.primary is not None and plan.primary.api_base == POOL_URL
    assert plan.primary.provider == "jiuwen" and plan.display == "deepseek-v4-flash"


def test_pool_miss_lists_compatible_and_catalog():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint="gpt-5-codex", builtin_hint=None,
            effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "gpt-5-codex" in msg and "deepseek-v4-flash" in msg   # compatible pool list
    assert "builtin_model" in msg                                # three-tier hint
    _assert_not_retryable(exc.value)


def test_builtin_hit_yields_endpointless_config_with_default_effort():
    plan = plan_external_model(
        cli_agent="codex", model_hint=None, builtin_hint="gpt-5-codex",
        effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
    )
    assert plan.primary.api_base is None and plan.primary.provider is None
    assert plan.primary.model == "gpt-5-codex" and plan.primary.effort == "medium"
    assert plan.display == "gpt-5-codex"


def test_mutual_exclusion_rejects_model_and_builtin():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint="deepseek-v4-flash", builtin_hint="gpt-5-codex",
            effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "mutually exclusive" in msg
    _assert_not_retryable(exc.value)


def test_bare_effort_requires_builtin_model():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint=None, builtin_hint=None,
            effort_hint="high", entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "'effort' requires 'builtin_model'" in msg
    _assert_not_retryable(exc.value)


def test_unknown_builtin_lists_catalog():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint=None, builtin_hint="gpt-5-ultra",
            effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "gpt-5-ultra" in msg and "gpt-5-codex" in msg   # catalog names
    _assert_not_retryable(exc.value)


def test_native_agent_builtin_hint_rejected():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent=None, model_hint=None, builtin_hint="gpt-5-codex",
            effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "builtin_model" in msg and "effort" in msg
    _assert_not_retryable(exc.value)


def test_protocol_incompatible_pool_hit_lists_compatible():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="claude", model_hint="deepseek-v4-flash", builtin_hint=None,
            effort_hint=None, entry=None, team_backend=POOL_MIXED,
        )
    msg = str(exc.value)
    assert "deepseek-v4-flash" in msg
    assert "anthropic-sonnet" in msg   # compatible pool list
    _assert_not_retryable(exc.value)


def test_effort_not_in_efforts_lists_supported():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint=None, builtin_hint="gpt-5-codex",
            effort_hint="ultra", entry=ENTRY_WITH_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "ultra" in msg
    assert "low" in msg and "medium" in msg and "high" in msg   # supported efforts
    _assert_not_retryable(exc.value)


def test_all_empty_returns_no_plan():
    plan = plan_external_model(
        cli_agent="codex", model_hint=None, builtin_hint=None,
        effort_hint=None, entry=ENTRY_WITH_CATALOG, team_backend=POOL,
    )
    assert plan.primary is None and plan.display is None


def test_catalogless_pool_miss_names_only_pool():
    with pytest.raises(BackendError) as exc:
        plan_external_model(
            cli_agent="codex", model_hint="gpt-5-codex", builtin_hint=None,
            effort_hint=None, entry=ENTRY_NO_CATALOG, team_backend=POOL,
        )
    msg = str(exc.value)
    assert "deepseek-v4-flash" in msg
    assert "builtin_model" not in msg
    _assert_not_retryable(exc.value)
