# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model pool entries and pool-refresh helpers.

A team's ``model_pool`` is the canonical multi-endpoint deployment
shape: a list of LLM endpoints with credentials and provider info that
``ModelAllocator`` distributes across leader and teammates so concurrent
calls spread across endpoints instead of saturating a single one.

Two identifiers anchor the design:

* ``ModelPoolEntry.model_id`` — process-local client identity, surfaced
  as ``ModelClientConfig.client_id`` for foundation client deduplication.
  Auto-generated, never persisted, regenerated on every pool reload.
* ``(model_name, group_index)`` — semantic persistence identity. The DB
  stores this lightweight reference; the live config is rehydrated from
  the in-session pool via ``resolve_member_model``.

``inherit_pool_ids`` is the single bridge between two pool versions: it
preserves ``model_id`` only when an old and new entry are bit-exact, so
a future foundation client cache cannot serve a stale client built
against rotated credentials.
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig


class ModelPoolEntry(BaseModel):
    """Single LLM endpoint in a team's allocation pool.

    Pool entries describe a usable model endpoint together with the
    credentials and provider needed to reach it. ``ModelAllocator`` draws
    entries from the pool and converts them into ``TeamModelConfig`` at
    allocation time so each team member can talk to a different endpoint
    and avoid single-endpoint rate-limit contention.

    Two identifiers play distinct roles:

    * ``model_id`` (auto-uuid): runtime client identity. Wired through to
      ``ModelClientConfig.client_id`` so the foundation layer's resource
      manager can dedupe / cache the underlying HTTP client across
      members that share the same endpoint. Never persisted to the DB
      and never crosses pool versions — regenerated each time the pool
      is reloaded from spec.
    * ``(model_name, group_index)``: semantic persistence identity.
      Stored in the DB as the member's pool reference; resolved
      positionally against the live session pool so credential
      refreshes propagate without re-spawning members.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    api_key: str
    api_base_url: str
    api_provider: str
    description: Optional[str] = None
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Process-local client identity for foundation resource manager.

    Auto-generated as a uuid; surfaced as ``ModelClientConfig.client_id``
    when the entry is materialized. Not persisted to the DB.

    ``inherit_pool_ids`` carries this value across ``update_model_pool``
    only when the new entry is bit-exact (every other field equal) to
    an old one — any value change (api_key rotation included) yields a
    fresh id so a future foundation client cache cannot serve a stale
    client built against the old config.
    """
    metadata: dict = Field(default_factory=dict)
    """Optional extension payload merged into the materialized TeamModelConfig.

    Two reserved sub-keys feed ``to_team_model_config``:

    * ``client``: dict merged into ``ModelClientConfig`` (e.g. ``timeout``,
      ``verify_ssl``, ``ssl_cert``, ``max_retries``, ``custom_headers``,
      or any provider-specific extras allowed by the client schema).
    * ``request``: dict merged into ``ModelRequestConfig`` (e.g.
      ``temperature``, ``top_p``, ``max_tokens``, ``stop``).

    Explicit fields on the pool entry (``api_key``, ``api_base_url``,
    ``api_provider``, ``model_name``) always win over the same key under
    ``client`` / ``request`` — those keys belong on the pool entry
    itself rather than buried in metadata. Any other top-level keys are
    free-form and reserved for allocator policies (e.g. weights,
    affinity hints) and are not consumed during materialization.
    """

    def to_team_model_config(self) -> TeamModelConfig:
        """Materialize a TeamModelConfig from this pool entry.

        Reserved ``metadata.client`` and ``metadata.request`` sub-dicts
        are merged into the corresponding sub-config. Pool-entry fields
        always override same-named keys in metadata so the explicit
        column wins over the optional bag.
        """
        from openjiuwen.core.foundation.llm import (
            ModelClientConfig,
            ModelRequestConfig,
        )

        client_extra = dict(self.metadata.get("client") or {})
        request_extra = dict(self.metadata.get("request") or {})

        client_kwargs = {
            **client_extra,
            "client_id": self.model_id,
            "client_provider": self.api_provider,
            "api_key": self.api_key,
            "api_base": self.api_base_url,
        }
        request_kwargs = {
            **request_extra,
            "model": self.model_name,
        }

        return TeamModelConfig(
            model_client_config=ModelClientConfig(**client_kwargs),
            model_request_config=ModelRequestConfig(**request_kwargs),
        )


class ModelRouterConfig(BaseModel):
    """Single-endpoint router configuration shared across many model names.

    Use this when a router-style backend (OpenRouter, LiteLLM proxy, an
    in-house gateway, ...) serves many model names through one URL plus
    one API key. Instead of repeating ``(api_key, api_base_url, api_provider)``
    in every ``ModelPoolEntry``, declare them once and list the model
    names served by that endpoint.

    The router is converted into a flat ``list[ModelPoolEntry]`` by
    ``to_pool_entries`` at ``TeamAgentSpec.build()`` time so all downstream
    machinery (``resolve_member_model``, ``inherit_pool_ids``,
    ``update_model_pool``) stays unchanged. The runtime allocator picked
    is ``RouterAllocator``, selected by ``model_pool_strategy="router"``.

    Mutually exclusive with ``TeamAgentSpec.model_pool``: the spec layer
    rejects configs that set both, since the strategy choice would be
    ambiguous.
    """

    model_config = ConfigDict(protected_namespaces=())

    api_base_url: str
    api_key: str
    api_provider: str
    model_names: list[str] = Field(min_length=1)
    """Ordered list of model names served by the router endpoint.

    The first name is the default (``RouterAllocator.allocate()`` with no
    hint returns it). Constraints enforced at validation time:

    * The list itself must be non-empty (a router with no model has
      nothing to serve and would silently break leader allocation).
    * Each name must be a non-empty, non-whitespace string (entries
      like ``""`` or ``"  "`` are rejected — they pass ``min_length=1``
      on the list but are meaningless model identifiers).
    * Names must be unique within the list — duplicates make
      ``allocate(model_name=...)`` ambiguous.
    """
    metadata: dict = Field(default_factory=dict)
    """Optional ``ModelPoolEntry.metadata`` payload, copied into every
    expanded entry. See ``ModelPoolEntry.metadata`` for reserved keys
    (``client``, ``request``).
    """

    @model_validator(mode="after")
    def _validate_model_names(self) -> "ModelRouterConfig":
        """Enforce the model_names invariants.

        The list non-emptiness is enforced by ``Field(min_length=1)``;
        this validator additionally rejects entries that are blank or
        whitespace-only (which pass ``min_length`` but aren't real
        model names) and entries that duplicate another entry (which
        would make ``allocate(model_name=...)`` ambiguous and silently
        drop one endpoint).
        """
        blanks = [i for i, name in enumerate(self.model_names) if not name or not name.strip()]
        if blanks:
            raise ValueError(
                f"ModelRouterConfig.model_names must contain non-empty strings; blank at indices: {blanks}",
            )
        if len(set(self.model_names)) != len(self.model_names):
            duplicates = sorted({n for n in self.model_names if self.model_names.count(n) > 1})
            raise ValueError(
                f"ModelRouterConfig.model_names must be unique; duplicates: {duplicates}",
            )
        return self

    def to_pool_entries(self) -> list[ModelPoolEntry]:
        """Expand the router into one ``ModelPoolEntry`` per model name.

        Every expanded entry shares ``api_key`` / ``api_base_url`` /
        ``api_provider`` and a deep-copied ``metadata`` dict so callers
        cannot accidentally cross-pollinate per-entry tweaks back into
        the router config or sibling entries.
        """
        return [
            ModelPoolEntry(
                model_name=name,
                api_key=self.api_key,
                api_base_url=self.api_base_url,
                api_provider=self.api_provider,
                metadata=copy.deepcopy(self.metadata),
            )
            for name in self.model_names
        ]


INTELLI_ROUTER_PROVIDER = "intelli_router"
"""``api_provider`` marking a pool entry as IntelliRouter-backed.

Matches ``ProviderType.IntelliRouter`` in the foundation layer, which is
what routes ``create_model_client`` to ``IntelliRouterModelClient``.
"""

INTELLI_ROUTER_UNIFIED_MODEL = "*"
"""Model name meaning "route across every deployment".

``IntelliRouterModelClient`` forwards this wildcard to the underlying
``ReliableRouter``, which then treats all deployments as equal peers and
fails over across model names and providers alike. It is the most
available option in the pool, which is why ``to_pool_entries`` puts it
first — ``IntelliRouterAllocator.allocate()`` with no hint returns the
first entry, so a leader without an explicit ``model_name`` gets the
broadest failover by default.
"""


class IntelliRouterDeployment(BaseModel):
    """One physical deployment behind an IntelliRouter.

    Mirrors ``intelli_router.Deployment``: a single model served by a
    single endpoint with its own credentials and rate limits. Unlike
    ``ModelPoolEntry`` — which the team allocator hands to exactly one
    member — deployments are never allocated individually. The whole
    list is handed to every member's client, and the router picks one
    per request, retrying on a different deployment when one fails.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    api_key: str
    api_base: str
    """Provider **root** URL — do NOT include the ``/v1`` suffix.

    This is the one field whose convention differs from the rest of
    openjiuwen. ``ModelClientConfig.api_base`` (and therefore
    ``ModelPoolEntry.api_base_url``) points at the OpenAI-compatible
    *API* base and normally ends in ``/v1``. IntelliRouter instead has
    each provider adapter append its own path — the OpenAI adapter
    builds ``f"{api_base}/v1/chat/completions"`` — so passing a
    ``/v1``-suffixed base here yields ``/v1/v1/chat/completions`` and
    every request 404s.

    The failure is worth spelling out because it does not look like a
    404: the router reports ``ResponseNotRead`` instead, since its error
    path reads the response body without first consuming the stream.

    Use ``https://api.deepseek.com``, not ``https://api.deepseek.com/v1``.
    """
    id: str | None = None
    """Stable deployment identifier surfaced in router stats and logs."""
    model_id: str | None = None
    """Business model identifier from the compiler snapshot."""
    provider: str
    """Upstream provider name interpreted by ``intelli_router``; required."""
    endpoint_profile: str | None = None
    custom_headers: dict[str, str] | None = None
    fallback_tag: str | None = None
    model_description: str | None = None
    request_defaults: dict[str, Any] = Field(default_factory=dict)
    """Deployment-specific request defaults compiled by the model layer."""
    tpm: int | None = None
    """Tokens-per-minute budget used by rate-aware routing strategies."""
    rpm: int | None = None
    """Requests-per-minute budget used by rate-aware routing strategies."""
    tags: list[str] = Field(default_factory=list)
    timeout: float | None = None
    """Per-deployment timeout; falls back to the router-level timeout when unset."""
    verify_ssl: bool | None = None
    """Per-deployment TLS verification; falls back to the router-level value when unset."""

    def to_deployment_dict(self) -> dict:
        """Render the wire dict consumed by ``IntelliRouterModelClient``.

        Optional fields are omitted rather than emitted as ``None`` so
        the client layer applies its own fallbacks — notably
        ``verify_ssl``, which falls back to the router-level value only
        when the key is absent.
        """
        payload: dict = {
            "model_name": self.model_name,
            "api_key": self.api_key,
            "api_base": self.api_base,
            "provider": self.provider,
        }
        if self.tags:
            payload["fallback_tag"] = self.tags[0]
        optional = {
            "id": self.id,
            "model_id": self.model_id,
            "endpoint_profile": self.endpoint_profile,
            "custom_headers": self.custom_headers,
            "fallback_tag": self.fallback_tag,
            "model_description": self.model_description,
            "request_defaults": self.request_defaults or None,
            "tpm": self.tpm,
            "rpm": self.rpm,
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload


class IntelliRouterConfig(BaseModel):
    """Multi-deployment reliable-routing configuration for a whole team.

    Where ``ModelRouterConfig`` describes one endpoint serving many model
    names, this describes many endpoints behind one *client-side* router:
    ``IntelliRouterModelClient`` wraps ``intelli_router.ReliableRouter``,
    which owns retry, failover, health checks, and rate-aware load
    balancing across the declared deployments.

    The two live at different layers and compose accordingly. Team-level
    allocation still decides *which model name* a member asks for; the
    router decides *which physical deployment* serves each request and
    what happens when it fails. So the pool this expands into never
    spreads members across endpoints the way ``round_robin`` does —
    every member shares the same deployment list, and availability is
    the router's job, not the allocator's.

    At ``TeamAgentSpec.build()`` time ``to_pool_entries`` expands this
    into one logical ``"*"`` entry carrying the complete deployment list in
    ``metadata.client.intelli_router`` and
    ``model_pool_strategy`` is set to ``"intelli_router"``, so every
    downstream path (``resolve_member_model``, ``inherit_pool_ids``,
    ``update_model_pool``) keeps working against the flat pool view with
    no IntelliRouter-specific branch.

    Mutually exclusive with both ``TeamAgentSpec.model_pool`` and
    ``TeamAgentSpec.model_router``.
    """

    model_config = ConfigDict(protected_namespaces=())

    deployments: list[IntelliRouterDeployment] = Field(min_length=1)
    """Physical deployments the router may route to. Never empty — a
    router with nothing to route to would fail every request."""
    model_names: list[str] | None = None
    """Optional logical Team name; when set it must be exactly ``["*"]``."""
    strategy: str = "simple-shuffle"
    """Routing strategy name passed through to ``ReliableRouter``."""
    num_retries: int = 3
    timeout: float = 30.0
    strategy_kwargs: dict = Field(default_factory=dict)
    """Strategy-specific tuning knobs forwarded verbatim to ``ReliableRouter``."""
    enable_health_check: bool = False
    health_check_interval: float = 300.0
    enable_observability: bool = False
    web_dashboard_port: int = 0
    verify_ssl: bool = True
    """Router-level TLS verification; per-deployment ``verify_ssl`` wins when set."""
    metadata: dict = Field(default_factory=dict)
    """Optional ``ModelPoolEntry.metadata`` payload copied into every
    expanded entry. Reserved ``client`` / ``request`` sub-keys apply as
    documented on ``ModelPoolEntry.metadata``. The generated structured
    ``client.intelli_router`` snapshot and router-level ``verify_ssl`` win
    over same-named values declared here; legacy flat ``intelli_router_*``
    metadata is discarded rather than written beside the canonical snapshot.
    """

    @model_validator(mode="after")
    def _validate_model_names(self) -> "IntelliRouterConfig":
        """Expose exactly one logical Team model name."""
        if self.model_names is None:
            return self
        if self.model_names != [INTELLI_ROUTER_UNIFIED_MODEL]:
            raise ValueError("IntelliRouterConfig exposes exactly one logical Team model name: '*'")
        return self

    def resolved_model_names(self) -> list[str]:
        """Return the sole logical Team model name."""
        return [INTELLI_ROUTER_UNIFIED_MODEL]

    def _client_extra(self) -> dict:
        """Build the canonical structured IntelliRouter client snapshot."""
        deployments = []
        for index, deployment in enumerate(self.deployments):
            payload = deployment.to_deployment_dict()
            route_id = payload.pop("id", None) or f"route_{index}"
            tags = payload.get("tags", [])
            if tags:
                payload["fallback_tag"] = tags[0]
            deployments.append({"route_id": route_id, **payload})
        result = {
            "intelli_router": {
                "deployments": deployments,
                "strategy": self.strategy,
                "num_retries": self.num_retries,
                "timeout": self.timeout,
                "strategy_kwargs": copy.deepcopy(self.strategy_kwargs),
                "enable_health_check": self.enable_health_check,
                "health_check_interval": self.health_check_interval,
                "enable_observability": self.enable_observability,
                "web_dashboard_port": self.web_dashboard_port,
            },
            "verify_ssl": self.verify_ssl,
        }
        return result

    def to_pool_entries(self) -> list[ModelPoolEntry]:
        """Expand into the single logical ``*`` Team model entry.

        The entry carries the complete deployment list. Physical deployment
        names remain inside the structured client snapshot and are never
        exposed to Team allocation:

        Every member receives this same logical entry, so the complete
        deployment list and router knobs produce one shared
        ``ReliableRouter`` cache key. Failover state, health checks, and
        per-deployment tpm/rpm budgets therefore stay global to the team;
        physical deployment names never become Team allocator entries.

        ``api_provider`` is fixed to ``"intelli_router"``, which is what
        routes materialization to ``IntelliRouterModelClient``. The entry's
        own ``api_key`` / ``api_base_url`` stay empty: credentials live
        per-deployment, and the foundation layer does not require
        top-level ones for this provider.
        """
        metadata = copy.deepcopy(self.metadata)
        client_metadata = dict(metadata.get("client") or {})
        for key in list(client_metadata):
            if key.startswith("intelli_router_"):
                client_metadata.pop(key)
        metadata["client"] = {**client_metadata, **self._client_extra()}
        return [ModelPoolEntry(
            model_name=INTELLI_ROUTER_UNIFIED_MODEL,
            api_key="",
            api_base_url="",
            api_provider=INTELLI_ROUTER_PROVIDER,
            description=f"IntelliRouter unified routing across {len(self.deployments)} deployment(s)",
            metadata=metadata,
        )]


def materialize_model_group(compiled: Any, selection: Any | None = None) -> list[ModelPoolEntry]:
    """Materialize a compiler result as the canonical Team model-group pool.

    A compiler-driven model group is intentionally represented by exactly one
    logical ``"*"`` entry.  Physical deployments remain nested in the
    structured ``metadata.client.intelli_router`` snapshot and are therefore
    available to the Foundation router without being mistaken for Team
    allocator endpoints.

    ``compiled`` is duck-typed to keep this helper usable across package
    boundaries; callers normally pass ``CompiledModelSelection`` and may pass
    the corresponding ``ModelSelection`` for an additional identity check.
    """
    if compiled is None:
        raise ValueError("compiled model selection is required")
    selected_type = getattr(compiled, "selected_type", None)
    selected_id = getattr(compiled, "selected_id", None)
    if selection is not None:
        if getattr(selection, "type", None) != selected_type or getattr(selection, "id", None) != selected_id:
            raise ValueError("model selection does not match compiled selection")
    if selected_type != "model_group":
        raise ValueError("materialize_model_group requires selected_type='model_group'")
    client = getattr(compiled, "model_client_config", None)
    client_provider = getattr(client, "client_provider", "") if client is not None else ""
    client_provider = getattr(client_provider, "value", client_provider)
    if client is None or str(client_provider) != INTELLI_ROUTER_PROVIDER:
        raise ValueError("model group must use client_provider='intelli_router'")
    router = getattr(client, "intelli_router", None)
    deployments = getattr(router, "deployments", None) if router is not None else None
    if router is None or not deployments:
        raise ValueError("model group IntelliRouter configuration must contain deployments")

    router_dump = router.model_dump(mode="json", exclude_none=True) if hasattr(router, "model_dump") else dict(router)
    request = getattr(compiled, "model_request_config", None)
    request_dump = (
        request.model_dump(mode="json", exclude_none=True, exclude_unset=True, by_alias=True)
        if request is not None and hasattr(request, "model_dump")
        else {}
    )
    # ``model`` is a logical Team field; the request model for a model group
    # must stay unset so deployment-level defaults are not overwritten.
    request_dump.pop("model", None)
    request_dump.pop("model_name", None)
    verify_ssl = getattr(client, "verify_ssl", None)
    client_metadata = {"intelli_router": router_dump}
    if verify_ssl is not None:
        client_metadata["verify_ssl"] = verify_ssl
    return [
        ModelPoolEntry(
            model_name=INTELLI_ROUTER_UNIFIED_MODEL,
            api_key="",
            api_base_url="",
            api_provider=INTELLI_ROUTER_PROVIDER,
            description=f"IntelliRouter model group {selected_id}",
            metadata={"client": client_metadata, "request": request_dump},
        )
    ]


def _entry_signature(entry: ModelPoolEntry) -> str:
    """Canonical signature of an entry's full config, excluding ``model_id``.

    Two entries with the same signature describe the same logical
    endpoint plus the same auth, request knobs, and metadata — i.e.
    a future foundation client cache could safely serve one client
    for both. Any difference (api_key rotation included) yields a
    different signature and forces a fresh ``model_id``.
    """
    payload = entry.model_dump(exclude={"model_id"})
    return json.dumps(payload, sort_keys=True, default=str)


def inherit_pool_ids(
    current_pool: list[ModelPoolEntry],
    new_pool: list[ModelPoolEntry],
) -> list[ModelPoolEntry]:
    """Carry ``model_id`` from ``current_pool`` into bit-exact entries of ``new_pool``.

    ``ModelPoolEntry.model_id`` surfaces as ``ModelClientConfig.client_id``,
    which a future foundation client cache may use to dedupe HTTP
    clients. Preserving it across a pool refresh is only safe when the
    new entry's full config is identical to the old one — otherwise a
    cached client built with the old api_key would silently service
    requests intended to use the new credentials.

    Alignment is therefore by **bit-exact signature**: every field
    other than ``model_id`` must match. When several entries in either
    pool share the same signature (e.g., genuine duplicates), they are
    paired in pool order, one-to-one. New entries that don't have an
    exact counterpart keep their own auto-generated ``model_id``;
    removed entries' ids are dropped.

    Side effects:

    * Order doesn't matter — reordered-but-otherwise-identical pools
      align fully.
    * Any value change (api_key rotation, base_url migration, timeout
      tweak, ...) breaks the match for that entry, forcing a fresh id.
    * Caller-supplied explicit ``model_id`` values are preserved when
      no signature match exists (no overwrite happens for unmatched
      new entries).

    Args:
        current_pool: The pool currently in session.
        new_pool: The replacement pool.

    Returns:
        A list parallel to ``new_pool`` with ``model_id`` inherited
        for bit-exact matches.
    """
    old_by_sig: dict[str, list[ModelPoolEntry]] = {}
    for entry in current_pool:
        old_by_sig.setdefault(_entry_signature(entry), []).append(entry)

    result: list[ModelPoolEntry] = []
    for new_entry in new_pool:
        bucket = old_by_sig.get(_entry_signature(new_entry))
        if bucket:
            inherited_id = bucket.pop(0).model_id
            result.append(new_entry.model_copy(update={"model_id": inherited_id}))
        else:
            result.append(new_entry)
    return result


__all__ = [
    "INTELLI_ROUTER_PROVIDER",
    "INTELLI_ROUTER_UNIFIED_MODEL",
    "IntelliRouterConfig",
    "IntelliRouterDeployment",
    "ModelPoolEntry",
    "ModelRouterConfig",
    "inherit_pool_ids",
    "materialize_model_group",
]
