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
from typing import Optional

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
    provider: str = "openai"
    """Upstream provider name interpreted by ``intelli_router``, not by openjiuwen."""
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
            "tags": list(self.tags),
        }
        optional = {
            "id": self.id,
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
    into a flat ``model_pool`` (one entry per logical model name, each
    carrying the full deployment list in ``metadata.client``) and
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
    """Logical model names offered to team members for allocation.

    ``None`` (default) derives the list as ``"*"`` followed by each
    distinct deployment ``model_name`` in declaration order, so members
    can either take unified routing or pin a specific model.

    When set explicitly, every name must be either ``"*"`` or the
    ``model_name`` of a declared deployment — a name no deployment
    serves would allocate a member a model that cannot be routed.
    Order matters: the first name is the team default returned by
    ``IntelliRouterAllocator.allocate()`` with no hint.
    """
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
    documented on ``ModelPoolEntry.metadata``; the ``intelli_router_*``
    keys this config generates are merged into ``client`` and win over
    same-named keys declared here.
    """

    @model_validator(mode="after")
    def _validate_model_names(self) -> "IntelliRouterConfig":
        """Reject model names that are blank, duplicated, or unroutable.

        A name no deployment serves (and that isn't the ``"*"`` wildcard)
        would expand into a pool entry the router cannot resolve, so the
        member allocated to it fails at request time rather than here.
        """
        if self.model_names is None:
            return self
        if not self.model_names:
            raise ValueError("IntelliRouterConfig.model_names must not be empty when set")
        blanks = [i for i, name in enumerate(self.model_names) if not name or not name.strip()]
        if blanks:
            raise ValueError(
                f"IntelliRouterConfig.model_names must contain non-empty strings; blank at indices: {blanks}",
            )
        if len(set(self.model_names)) != len(self.model_names):
            duplicates = sorted({n for n in self.model_names if self.model_names.count(n) > 1})
            raise ValueError(
                f"IntelliRouterConfig.model_names must be unique; duplicates: {duplicates}",
            )
        served = {dep.model_name for dep in self.deployments}
        unknown = [n for n in self.model_names if n != INTELLI_ROUTER_UNIFIED_MODEL and n not in served]
        if unknown:
            raise ValueError(
                f"IntelliRouterConfig.model_names entries must be '{INTELLI_ROUTER_UNIFIED_MODEL}' or a declared "
                f"deployment model_name; unserved: {unknown} (declared: {sorted(served)})",
            )
        return self

    def resolved_model_names(self) -> list[str]:
        """Return the logical model names this router offers, in order.

        Falls back to ``"*"`` plus each distinct deployment model name
        when ``model_names`` is unset. The first element is the team
        default.
        """
        if self.model_names is not None:
            return list(self.model_names)
        names = [INTELLI_ROUTER_UNIFIED_MODEL]
        for dep in self.deployments:
            if dep.model_name not in names:
                names.append(dep.model_name)
        return names

    def _client_extra(self) -> dict:
        """Build the ``intelli_router_*`` client kwargs for one pool entry."""
        return {
            "intelli_router_deployments": [dep.to_deployment_dict() for dep in self.deployments],
            "intelli_router_strategy": self.strategy,
            "intelli_router_num_retries": self.num_retries,
            "intelli_router_timeout": self.timeout,
            "intelli_router_strategy_kwargs": copy.deepcopy(self.strategy_kwargs),
            "intelli_router_enable_health_check": self.enable_health_check,
            "intelli_router_health_check_interval": self.health_check_interval,
            "intelli_router_enable_observability": self.enable_observability,
            "intelli_router_web_dashboard_port": self.web_dashboard_port,
            "verify_ssl": self.verify_ssl,
        }

    def to_pool_entries(self) -> list[ModelPoolEntry]:
        """Expand the router into one ``ModelPoolEntry`` per logical model name.

        **Every entry carries the identical, complete deployment list.**
        That looks redundant — each member is handed the whole fleet just
        to ask for one model name — but it is exactly what makes the fleet
        shared rather than duplicated:

        ``IntelliRouterModelClient`` caches one ``ReliableRouter`` per
        client-config key, and that key is derived from the deployment
        list plus the router knobs, never from ``model_name`` or
        ``client_id``. Identical deployments across entries therefore
        collapse to a **single** router instance shared by every member,
        which is what keeps failover state, health checks, and the
        per-deployment tpm / rpm budgets global to the team.

        Narrowing each entry to "just the deployments serving my model"
        would be the intuitive optimization and is precisely the bug: the
        keys would diverge, every member would build its own router, and
        each would then count rpm / tpm on its own — a 4-member team
        silently spending 4x its declared quota, with failover knowledge
        never shared. ``test_intelli_router_all_entries_share_one_router_cache_key``
        pins this down.

        Members still differ where they should: the pinned ``model_name``
        rides on ``ModelRequestConfig``, not on the client config, so the
        per-member client wrapper is thin and the heavy machinery is not.

        ``api_provider`` is fixed to ``"intelli_router"``, which is what
        routes materialization to ``IntelliRouterModelClient``. The entry's
        own ``api_key`` / ``api_base_url`` stay empty: credentials live
        per-deployment, and the foundation layer does not require
        top-level ones for this provider.
        """
        client_extra = self._client_extra()
        entries: list[ModelPoolEntry] = []
        for name in self.resolved_model_names():
            metadata = copy.deepcopy(self.metadata)
            metadata["client"] = {**(metadata.get("client") or {}), **client_extra}
            unified = name == INTELLI_ROUTER_UNIFIED_MODEL
            description = (
                f"IntelliRouter unified routing across {len(self.deployments)} deployment(s)"
                if unified
                else f"IntelliRouter routing pinned to model '{name}'"
            )
            entries.append(
                ModelPoolEntry(
                    model_name=name,
                    api_key="",
                    api_base_url="",
                    api_provider=INTELLI_ROUTER_PROVIDER,
                    description=description,
                    metadata=metadata,
                )
            )
        return entries


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
]
