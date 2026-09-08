# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for provider identity resolution in external CLI model conversion.

``_team_model_config_to_external`` must not pass the wire-protocol label
(``client_provider`` — "OpenAI" for most OpenAI-compatible gateways) through
as the CLI-side provider name: codex-style runtimes gate server-side
protocols (remote compaction, request-body compression) on the provider name
matching their official endpoint. All explicitly configured external endpoints
therefore use one stable ``jiuwen`` identity.
"""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.spawn.external_cli_spawn import (
    _external_cli_provider_name,
    _team_model_config_to_external,
)


def _client_config(
    *,
    client_provider: str = "OpenAI",
    api_base: str = "https://api.deepseek.com",
    endpoint_profile: str | None = None,
    vendor_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        client_provider=client_provider,
        api_base=api_base,
        api_key="sk-test",
        endpoint_profile=endpoint_profile,
        vendor_key=vendor_key,
    )


def _member_model(client_config: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        model_client_config=client_config,
        model_request_config=SimpleNamespace(model_name="deepseek-v4-flash"),
    )


@pytest.mark.level0
def test_external_endpoint_uses_jiuwen_provider():
    config = _client_config(endpoint_profile="deepseek")
    assert _external_cli_provider_name(config) == "jiuwen"


@pytest.mark.level0
def test_external_endpoint_ignores_vendor_metadata():
    config = _client_config(vendor_key="kimi")
    assert _external_cli_provider_name(config) == "jiuwen"


@pytest.mark.level0
@pytest.mark.parametrize(
    "api_base",
    [
        "https://api.deepseek.com",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "http://113.46.219.251:8080/v1",
        "api.moonshot.cn",
    ],
)
def test_external_endpoint_provider_is_independent_of_host(api_base: str):
    assert _external_cli_provider_name(_client_config(api_base=api_base)) == "jiuwen"


@pytest.mark.level0
def test_official_endpoint_without_api_base_keeps_protocol_label():
    # No api_base means the member targets the CLI's official endpoint, where
    # the "OpenAI" name is accurate and enables official-endpoint protocols.
    config = _client_config(api_base="")
    assert _external_cli_provider_name(config) == "OpenAI"


@pytest.mark.level0
def test_team_model_config_conversion_uses_resolved_provider():
    member_model = _member_model(_client_config())
    external = _team_model_config_to_external(member_model)
    assert external is not None
    assert external.provider == "jiuwen"
    assert external.model == "deepseek-v4-flash"
    assert external.api_base == "https://api.deepseek.com"
    assert external.api_key == "sk-test"


@pytest.mark.level0
def test_team_model_config_conversion_without_client_config():
    assert _team_model_config_to_external(SimpleNamespace()) is None
    assert _team_model_config_to_external(SimpleNamespace(model_client_config=None)) is None


@pytest.mark.level0
def test_resolved_provider_name_is_codex_bare_key_safe():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import _BARE_KEY_RE

    assert _BARE_KEY_RE.fullmatch(_external_cli_provider_name(_client_config()))
