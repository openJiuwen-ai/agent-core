# coding: utf-8
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

import json
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

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
    "ModelPoolEntry",
    "inherit_pool_ids",
]
