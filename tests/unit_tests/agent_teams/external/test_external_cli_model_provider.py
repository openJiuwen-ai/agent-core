# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for provider identity resolution in external CLI model conversion.

``_team_model_config_to_external`` must not pass the wire-protocol label
(``client_provider`` — "OpenAI" for most OpenAI-compatible gateways) through
as the CLI-side provider name: codex-style runtimes gate server-side
protocols (remote compaction, request-body compression) on the provider name
matching their official endpoint, so an external gateway named "OpenAI"
triggers protocols it does not implement.
"""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.spawn.external_cli_spawn import (
    _external_cli_provider_name,
    _host_provider_name,
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
def test_endpoint_profile_wins_over_protocol_label():
    config = _client_config(endpoint_profile="deepseek")
    assert _external_cli_provider_name(config) == "deepseek"


@pytest.mark.level0
def test_neutral_endpoint_profile_falls_through():
    # "openai_compatible" describes the wire protocol, not a vendor; it must
    # not leak into the provider identity.
    config = _client_config(endpoint_profile="openai_compatible")
    assert _external_cli_provider_name(config) != "openai_compatible"


@pytest.mark.level0
def test_vendor_key_used_when_no_endpoint_profile():
    # Registry presets without a dialect profile (kimi/zhipu/...) still carry
    # vendor_key from the frontend selection.
    config = _client_config(vendor_key="kimi")
    assert _external_cli_provider_name(config) == "kimi"


@pytest.mark.level0
def test_endpoint_profile_beats_vendor_key():
    config = _client_config(endpoint_profile="dashscope", vendor_key="alibaba")
    assert _external_cli_provider_name(config) == "dashscope"


@pytest.mark.level0
def test_handwritten_config_falls_back_to_api_base_host():
    # The incident config: a hand-written config.yaml entry with only
    # client_provider: OpenAI and an api_base. The host-derived name keeps
    # codex's is_openai() gate false for the gateway.
    config = _client_config()
    assert _external_cli_provider_name(config) == "deepseek"


@pytest.mark.level0
def test_official_endpoint_without_api_base_keeps_protocol_label():
    # No api_base means the member targets the CLI's official endpoint, where
    # the "OpenAI" name is accurate and enables official-endpoint protocols.
    config = _client_config(api_base="")
    assert _external_cli_provider_name(config) == "OpenAI"


@pytest.mark.level0
def test_host_provider_name_variants():
    assert _host_provider_name("https://api.deepseek.com") == "deepseek"
    assert _host_provider_name("https://dashscope.aliyuncs.com/compatible-mode/v1") == "aliyuncs"
    assert _host_provider_name("http://113.46.219.251:8080/v1") == "host-113-46-219-251"
    # Bare host without scheme and URL-less fallbacks.
    assert _host_provider_name("api.moonshot.cn") == "moonshot"
    assert _host_provider_name("") == ""


@pytest.mark.level0
def test_host_provider_name_sanitizes_malformed_input():
    # urlparse accepts scheme-less garbage ("not a url at all!!!" parses the
    # whole string as a path/host); every derived name must stay within the
    # codex bare-key character class.
    from openjiuwen.agent_teams.external.cli_agent.codex.options import _BARE_KEY_RE

    for raw in (
        "not a url at all!!!",
        "https://[2001:db8::1]:8080/v1",
        "https://xn--fiqs8s.com",
        "https://api..deepseek.com",
        "http://host with spaces.example.com",
    ):
        name = _host_provider_name(raw)
        assert name, raw
        assert _BARE_KEY_RE.fullmatch(name), f"{raw!r} -> {name!r}"


@pytest.mark.level0
def test_endpoint_profile_and_vendor_key_sanitized():
    # Controlled values in theory, but the single exit point guarantees the
    # character class even for hand-written config.yaml entries.
    config = _client_config(endpoint_profile="my profile!!")
    assert _external_cli_provider_name(config) == "my-profile"

    config = _client_config(vendor_key="厂商 Key")
    assert _external_cli_provider_name(config) == "Key"


@pytest.mark.level0
def test_team_model_config_conversion_uses_resolved_provider():
    member_model = _member_model(_client_config())
    external = _team_model_config_to_external(member_model)
    assert external is not None
    assert external.provider == "deepseek"
    assert external.model == "deepseek-v4-flash"
    assert external.api_base == "https://api.deepseek.com"
    assert external.api_key == "sk-test"


@pytest.mark.level0
def test_team_model_config_conversion_without_client_config():
    assert _team_model_config_to_external(SimpleNamespace()) is None
    assert _team_model_config_to_external(SimpleNamespace(model_client_config=None)) is None


@pytest.mark.level0
def test_resolved_provider_name_is_codex_bare_key_safe():
    # codex renders provider table keys via a TOML bare-key regex; hyphenated
    # host fallbacks (bare IPs) must stay within [A-Za-z0-9_-].
    from openjiuwen.agent_teams.external.cli_agent.codex.options import _BARE_KEY_RE

    for name in (
        "deepseek",
        "dashscope",
        "host-113-46-219-251",
        "kimi",
    ):
        assert _BARE_KEY_RE.fullmatch(name), name
