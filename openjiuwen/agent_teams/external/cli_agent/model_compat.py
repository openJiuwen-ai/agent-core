# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared model-compatibility primitives for external CLI agents.

Used by both the team-member spawn path (member tools and external-CLI
spawning) and the swarmflow worker path, so provider / protocol / model-config
mapping lives in exactly one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable, Optional

from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig

_JIUWEN_EXTERNAL_PROVIDER = "jiuwen"


def is_anthropic_provider(provider: str) -> bool:
    """Return whether a provider name is Anthropic-compatible."""
    return "anthropic" in provider.lower()


def provider_filter_for_cli(cli_agent: str) -> Callable[[str], bool] | None:
    """Return a provider filter for the CLI kind, or None for unknown kinds.

    A third-party harness provider either declares ``ENDPOINT_MODELS`` and
    maps any endpoint through its ``model_config`` (no filter), or takes none
    at all, in which case no pool entry is offered for it.
    """
    if cli_agent == "claude":
        return is_anthropic_provider
    if cli_agent == "codex":
        return lambda p: not is_anthropic_provider(p)
    from openjiuwen.agent_teams.external.cli_agent.backends import backend_for, provider_takes_endpoint_models

    backend = backend_for(cli_agent)
    if backend is None or backend.kind != "provider":
        return None
    if provider_takes_endpoint_models(cli_agent):
        return None
    return lambda _provider: False


def compatible_pool_model_names(cli_agent: str, pool: Sequence[Any]) -> list[str]:
    """Return sorted distinct pool model names the CLI's provider filter accepts.

    Shared by the model resolver (``plan_external_model`` pool scans) and the
    two-audience auth-failure copy (``ExternalMemberTurns`` workaround list), so
    the compatible-pool notion lives in exactly one place.
    """
    provider_filter = provider_filter_for_cli(cli_agent)
    names: set[str] = set()
    for entry in pool:
        name = getattr(entry, "model_name", "")
        if not name:
            continue
        if provider_filter is None or provider_filter(getattr(entry, "api_provider", "")):
            names.add(name)
    return sorted(names)


def model_api_protocol(provider: str) -> str:
    """Return the external CLI protocol label for a model provider."""
    return "Anthropic" if is_anthropic_provider(provider) else "OpenAI"


def _external_cli_provider_name(client_config: Any, *, cli_agent: str | None = None) -> str:
    """Resolve the CLI-side provider identity for a model client config.

    Codex-style CLI runtimes gate server-side behaviors (remote compaction,
    request-body compression) on the provider *name* matching their official
    endpoint ("OpenAI"). A pool entry's ``client_provider`` is only the wire
    protocol label — mostly "OpenAI" for OpenAI-compatible gateways — so
    passing it through verbatim makes every external gateway look official and
    breaks the gated protocols against endpoints that do not implement them.

    Codex's OpenAI-compatible non-official endpoints use ``jiuwen``; other
    CLI backends retain their configured provider identity.
    """
    api_base = str(getattr(client_config, "api_base", "") or "").strip()
    provider = str(getattr(client_config, "client_provider", "") or "").strip()
    if cli_agent == "codex" and provider.lower() == "openai" and api_base:
        return _JIUWEN_EXTERNAL_PROVIDER
    return provider


def team_model_config_to_external(
    member_model: Any,
    *,
    cli_agent: str | None = None,
) -> Optional[ExternalCliModelConfig]:
    """Convert a pool-allocated TeamModelConfig to ExternalCliModelConfig."""
    client_config = getattr(member_model, "model_client_config", None)
    request_config = getattr(member_model, "model_request_config", None)
    if client_config is None:
        return None
    if str(getattr(client_config, "client_provider", "")) == "intelli_router":
        return None
    provider = _external_cli_provider_name(client_config, cli_agent=cli_agent)
    model = ""
    if request_config is not None:
        model = str(getattr(request_config, "model_name", "") or getattr(request_config, "model", "") or "")
    return ExternalCliModelConfig(
        provider=provider or None,
        model=model or None,
        api_base=str(getattr(client_config, "api_base", "") or "") or None,
        api_key=str(getattr(client_config, "api_key", "") or "") or None,
    )