# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate and resolve the model options of one third-party worker call.

Pure resolver: reads only the script's model hints and the team backend's
model pool, never spawns or allocates. A pool hit becomes an endpoint config,
a builtin pick becomes an endpoint-less config carrying the effort; every rule
violation raises a non-retryable :class:`BackendError`. All-empty hints leave
the model to the CLI's own default on its own login.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.external.cli_agent.model_compat import compatible_pool_model_names, provider_filter_for_cli
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliModelConfig
from openjiuwen.agent_teams.workflow.engine.errors import BackendError


@dataclass(frozen=True)
class ExternalModelPlan:
    """Resolved model options for one third-party worker call.

    ``primary`` is a pool hit (endpoint config) or a builtin pick (endpoint-less
    config carrying effort); both ``None`` means the call leaves the model to
    the CLI's own default.
    """

    primary: ExternalCliModelConfig | None
    display: str | None


def plan_external_model(
    *,
    cli_agent: str | None,
    model_hint: str | None,
    builtin_hint: str | None,
    effort_hint: str | None,
    entry: ExternalCliAgentSpec | None,
    team_backend: Any,
) -> ExternalModelPlan:
    """Validate and resolve the model options of one third-party call.

    Rules, in order: ``model`` and ``builtin_model`` are mutually exclusive;
    ``model`` resolves against the team model pool (grouped by name, first
    entry) under the CLI's provider filter; ``builtin_model`` resolves against
    the kind's catalog with ``effort`` constrained to the declared efforts;
    ``effort`` alone is rejected; native agents reject ``builtin_model`` /
    ``effort`` (their ``model`` handling lives in the backend); all-empty hints
    return ``primary=None`` / ``display=None``.

    Raises:
        BackendError: on every rule violation, with two-audience copy.
    """
    if model_hint and builtin_hint:
        raise BackendError(
            "'builtin_model' and 'model' are mutually exclusive: a built-in model runs on the CLI's "
            "own login, 'model' on a team model pool endpoint",
            retryable=False,
        )
    if effort_hint is not None and builtin_hint is None:
        raise BackendError(
            "'effort' requires 'builtin_model': the reasoning effort option only applies to a model "
            "the CLI offers on its own login",
            retryable=False,
        )
    if cli_agent is None:
        if builtin_hint is not None or effort_hint is not None:
            raise BackendError(
                "builtin_model / effort are unavailable for native agents: only external CLI agents "
                "offer models on their own login",
                retryable=False,
            )
        # A native agent's model_hint is resolved by the backend, not here.
        return ExternalModelPlan(primary=None, display=None)
    if model_hint is not None:
        return _resolve_pool_model(cli_agent, model_hint, entry, team_backend)
    if builtin_hint is not None:
        return _resolve_builtin_model(cli_agent, builtin_hint, effort_hint, entry)
    return ExternalModelPlan(primary=None, display=None)


def _resolve_pool_model(
    cli_agent: str,
    model_hint: str,
    entry: ExternalCliAgentSpec | None,
    team_backend: Any,
) -> ExternalModelPlan:
    """Resolve ``model_hint`` against the team model pool under the CLI's protocol filter."""
    pool = team_backend.get_model_pool() if team_backend is not None else []
    group = [e for e in pool if getattr(e, "model_name", None) == model_hint]
    compatible = compatible_pool_model_names(cli_agent, pool)
    if not group:
        raise BackendError(
            _pool_miss_message(cli_agent, model_hint, compatible, entry, cause="is not offered by the team model pool"),
            retryable=False,
        )
    provider_filter = provider_filter_for_cli(cli_agent)
    hit = group[0]   # same "group by name, take the first entry" rule as resolve_member_model
    if provider_filter is not None and not provider_filter(getattr(hit, "api_provider", "")):
        raise BackendError(
            _pool_miss_message(cli_agent, model_hint, compatible, entry, cause="is unavailable or incompatible"),
            retryable=False,
        )
    api_base = str(getattr(hit, "api_base_url", "") or "").strip() or None
    api_key = str(getattr(hit, "api_key", "") or "").strip() or None
    # Mirror the team path (_external_cli_provider_name): every configured
    # endpoint is exposed under the stable "jiuwen" provider identity so
    # codex-style runtimes never mistake a gateway for the official endpoint.
    provider = "jiuwen" if api_base else str(getattr(hit, "api_provider", "") or "").strip() or None
    config = ExternalCliModelConfig(provider=provider, model=model_hint, api_base=api_base, api_key=api_key)
    return ExternalModelPlan(primary=config, display=model_hint)


def _pool_miss_message(
    cli_agent: str,
    model_hint: str,
    compatible: list[str],
    entry: ExternalCliAgentSpec | None,
    *,
    cause: str,
) -> str:
    """Explain a rejected ``model_hint`` and point at the built-in catalog.

    Same three-tier wording family as the member tool's ``_model_name_failure``:
    a name the kind offers on its own login is redirected to ``builtin_model``
    (the pool list still follows so a compatible pool name stays visible); an
    unknown name lists pool then catalog; a catalog-less entry names the pool
    only. Category and root cause come first; lists are appended last so a
    truncated message still names the violation.
    """
    catalog = entry.builtin_models if entry is not None else []
    builtin_names = [item.name for item in catalog]
    if model_hint in builtin_names:
        message = (
            f"model '{model_hint}' is a built-in model of cli_agent '{cli_agent}', not a team model pool "
            "endpoint; pass it as 'builtin_model' instead (optionally with 'effort')"
        )
    else:
        message = f"model '{model_hint}' {cause} for cli_agent '{cli_agent}'"
    if compatible:
        message += f"; the team model pool offers: {', '.join(compatible)}"
    else:
        message += "; the team model pool has no compatible model"
    if builtin_names and model_hint not in builtin_names:
        message += f". Built-in models of this cli_agent, via 'builtin_model': {', '.join(builtin_names)}"
    return message


def _resolve_builtin_model(
    cli_agent: str,
    builtin_hint: str,
    effort_hint: str | None,
    entry: ExternalCliAgentSpec | None,
) -> ExternalModelPlan:
    """Resolve ``builtin_hint`` against the kind's catalog with the effort check."""
    catalog = entry.builtin_models if entry is not None else []
    builtin_names = [item.name for item in catalog]
    model = entry.find_builtin_model(builtin_hint) if entry is not None else None
    if model is None:
        if builtin_names:
            message = (
                f"builtin_model '{builtin_hint}' is not declared by cli_agent '{cli_agent}'; "
                f"this cli_agent offers: {', '.join(builtin_names)}"
            )
        else:
            message = (
                f"builtin_model '{builtin_hint}' is not available for cli_agent '{cli_agent}': "
                "this cli_agent declares no builtin_models"
            )
        raise BackendError(message, retryable=False)
    if effort_hint is not None and effort_hint not in model.efforts:
        supported = ", ".join(model.efforts) if model.efforts else "none"
        raise BackendError(
            f"effort '{effort_hint}' is not supported by builtin_model '{builtin_hint}' of "
            f"cli_agent '{cli_agent}'; supported efforts: {supported}",
            retryable=False,
        )
    effort = effort_hint or model.default_effort
    config = ExternalCliModelConfig(model=builtin_hint, effort=effort)
    return ExternalModelPlan(primary=config, display=builtin_hint)
