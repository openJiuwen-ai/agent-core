# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spawn payload construction.

Centralizes the cross-process wire format for spawning teammates.
Output of ``build_spawn_payload`` is the public contract consumed by
``TeamAgent.from_spawn_payload`` in the spawned process — any change
here must preserve all output keys.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Optional,
)

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRuntimeContext,
)
from openjiuwen.core.common.logging.log_config import get_log_config_snapshot
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.spawn.agent_config import (
    SpawnAgentConfig,
    SpawnAgentKind,
    serialize_runner_config,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager import MessagerTransportConfig


class SpawnPayloadBuilder:
    """Builds spawn payloads, member contexts, and spawn configs.

    Stateful only in ``_member_port_map`` and ``_teammate_port_counter``,
    which together act as an incremental port allocator: each member name
    gets a stable port assignment for the lifetime of this builder.
    """

    def __init__(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        self._spec = spec
        self._ctx = ctx
        self._member_port_map: dict[str, int] = {}
        self._teammate_port_counter: int = 0

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Produce the cross-process spawn payload.

        Output schema is the public wire contract — preserve every key.
        """
        team_spec = ctx.team_spec
        member_transport = self.build_member_messager_config(ctx.member_name)
        return {
            "coordination": {
                "team_name": team_spec.team_name if team_spec else "",
                "display_name": team_spec.display_name if team_spec else "",
                "leader_member_name": team_spec.leader_member_name if team_spec else None,
                "member_name": ctx.member_name,
                "role": ctx.role.value,
                "persona": ctx.persona,
                "transport": (member_transport.model_dump(mode="json") if member_transport is not None else None),
            },
            "query": initial_message or "Join the team and wait for your first assignment.",
        }

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        """Construct a runtime context for spawning a teammate."""
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_name=member_spec.member_name,
            persona=member_spec.persona,
            team_spec=self._ctx.team_spec,
            messager_config=self.build_member_messager_config(member_spec.member_name),
            db_config=self._ctx.db_config,
        )

    def build_member_messager_config(self, member_name: str) -> Optional["MessagerTransportConfig"]:
        """Allocate a stable transport config for the given member name."""
        leader_cfg = self._ctx.messager_config
        if leader_cfg is None:
            return None

        meta = self._spec.metadata or {}
        base_port = meta.get("teammate_base_port", 16000)
        port_offset = meta.get("teammate_port_offset", 10)

        if member_name in self._member_port_map:
            port_base = self._member_port_map[member_name]
        else:
            port_base = base_port + self._teammate_port_counter * port_offset
            self._teammate_port_counter += 1
            self._member_port_map[member_name] = port_base

        updates: Dict[str, Any] = {
            "node_id": member_name,
            "direct_addr": f"tcp://127.0.0.1:{port_base}",
            "pubsub_publish_addr": leader_cfg.pubsub_publish_addr,
            "pubsub_subscribe_addr": leader_cfg.pubsub_subscribe_addr,
        }
        metadata = dict(leader_cfg.metadata)
        metadata.pop("pubsub_bind", None)
        updates["metadata"] = metadata
        return leader_cfg.model_copy(update=updates)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        """Build the SpawnAgentConfig that wraps the payload for Runner.spawn_agent."""
        member_tag = ctx.member_name or ""
        return SpawnAgentConfig(
            agent_kind=SpawnAgentKind.TEAM_AGENT,
            runner_config=serialize_runner_config(Runner.get_config()),
            logging_config=_build_member_logging_config(member_tag),
            session_id=None,
            payload={
                "spec": self._spec.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
            },
        )


def _build_member_logging_config(member_tag: str) -> dict[str, Any]:
    """Redirect file-based log sinks into a per-member subdirectory.

    Snapshots the active log config and rewrites every file sink target
    to ``<dir>/teammates/<member_tag>/<filename>`` so each spawned member
    writes to its own log files.
    """
    config = get_log_config_snapshot()
    sinks = config.get("sinks", {})
    for sink in sinks.values():
        target = sink.get("target")
        if not isinstance(target, str) or target in ("stdout", "stderr"):
            continue
        parts = target.rsplit("/", 1)
        if len(parts) == 2:
            sink["target"] = f"{parts[0]}/teammates/{member_tag}/{parts[1]}"
        else:
            sink["target"] = f"teammates/{member_tag}/{target}"
    return config


__all__ = ["SpawnPayloadBuilder"]
