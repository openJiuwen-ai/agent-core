# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in providers map a host ``ModelChoice`` onto their own configuration."""

from __future__ import annotations

import pytest

from openjiuwen.harness_protocol import HarnessCapability, ModelChoice, ModelConfigProvider, ModelEndpoint
from openjiuwen.harness_providers import resolve_provider
from openjiuwen.harness_providers.factory import provider_model_config

_ENDPOINT = ModelEndpoint(api_base="https://gw", api_key="secret", provider="openai")


def test_model_choice_and_endpoint_validate_their_fields() -> None:
    assert ModelChoice(model="sonnet").is_builtin
    assert not ModelChoice(model="gpt-x", endpoint=_ENDPOINT).is_builtin
    assert "secret" not in repr(_ENDPOINT)
    with pytest.raises(ValueError, match="api_base, a provider"):
        ModelEndpoint()
    with pytest.raises(ValueError, match="effort"):
        ModelChoice(effort=" ")


@pytest.mark.parametrize("provider", ["claudecode", "codex", "dsh"])
def test_builtin_providers_configure_models_and_take_endpoints(provider: str) -> None:
    resolved = resolve_provider(provider)

    assert isinstance(resolved, ModelConfigProvider)
    assert resolved.card.supports(HarnessCapability.ENDPOINT_MODELS)


def test_claude_code_maps_the_choice_and_its_fallback() -> None:
    fragment = provider_model_config(
        "claudecode",
        ModelChoice(model="sonnet", effort="high"),
        fallback=ModelChoice(model="pool-model", endpoint=_ENDPOINT),
    )

    assert fragment == {
        "model": {"model": "sonnet", "effort": "high"},
        "fallback_model": {"model": "pool-model", "api_base": "https://gw", "api_key": "secret"},
    }
    resolve_provider("claudecode").create(fragment)


def test_codex_keeps_the_endpoint_protocol_label() -> None:
    fragment = provider_model_config("codex", ModelChoice(model="gpt-x", endpoint=_ENDPOINT))

    assert fragment == {"model": {"model": "gpt-x", "provider": "openai", "api_base": "https://gw", "api_key": "secret"}}
    resolve_provider("codex").create(fragment)


def test_dsh_rejects_what_it_has_no_setting_for() -> None:
    assert provider_model_config("dsh", ModelChoice(model="m", endpoint=_ENDPOINT)) == {
        "model": "m",
        "base_url": "https://gw",
        "api_key": "secret",
    }
    with pytest.raises(ValueError, match="effort"):
        provider_model_config("dsh", ModelChoice(model="m", effort="high"))
    with pytest.raises(ValueError, match="no authentication fallback"):
        provider_model_config("dsh", ModelChoice(model="m"), fallback=ModelChoice(model="n"))


@pytest.mark.parametrize(("provider", "expected"), [("claudecode", True), ("codex", True), ("dsh", False)])
def test_auth_fallback_is_a_declared_capability(provider: str, expected: bool) -> None:
    assert resolve_provider(provider).card.supports(HarnessCapability.AUTH_FALLBACK) is expected
