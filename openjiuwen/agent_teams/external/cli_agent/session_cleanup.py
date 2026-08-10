# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Best-effort external CLI backend session cleanup."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.runtime.metadata import read_team_namespace
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.agent_teams.tools.member_options import get_member_cli_agent
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import create_agent_team_session


async def cleanup_external_cli_backend_sessions(
    *,
    session_id: str,
    team_names: list[str],
    db: Any,
) -> None:
    """Delete persisted backend-native sessions for external CLI members."""
    session = create_agent_team_session(session_id=session_id, source_metadata_enabled=False)
    try:
        await session.pre_run()
    except Exception as exc:
        team_logger.warning("Failed to restore session {} for external CLI cleanup: {}", session_id, exc)
        return

    for team_name in team_names:
        backend_configs = _external_cli_configs_for_team(session, team_name)
        if not backend_configs:
            continue
        try:
            members = await db.member.get_team_members(team_name)
        except Exception as exc:
            team_logger.warning("Failed to list members for external CLI cleanup team={}: {}", team_name, exc)
            continue
        for member in members:
            cli_agent = get_member_cli_agent(member)
            if not cli_agent:
                continue
            config = backend_configs.get(cli_agent)
            if config is None:
                continue
            await cleanup_external_cli_backend_session(
                cli_agent=cli_agent,
                team_session_id=session_id,
                member_name=member.member_name,
                config=config,
            )


async def cleanup_external_cli_backend_session(
    *,
    cli_agent: str,
    team_session_id: str,
    member_name: str,
    config: ExternalCliAgentSpec,
) -> None:
    """Delete one backend-native session when the backend supports it."""
    if cli_agent != "claude":
        return
    from openjiuwen.agent_teams.external.cli_agent.claude.options import delete_claude_session

    try:
        deleted = delete_claude_session(
            team_session_id=team_session_id,
            member_name=member_name,
            cwd=config.cwd,
        )
    except FileNotFoundError:
        team_logger.debug(
            "External CLI backend session already absent backend={} session={} member={}",
            cli_agent,
            team_session_id,
            member_name,
        )
        return
    except Exception as exc:
        team_logger.warning(
            "Failed to delete external CLI backend session backend={} session={} member={}: {}",
            cli_agent,
            team_session_id,
            member_name,
            exc,
        )
        return
    if deleted:
        team_logger.info(
            "Deleted external CLI backend session backend={} session={} member={}",
            cli_agent,
            team_session_id,
            member_name,
        )


def _external_cli_configs_for_team(session: Any, team_name: str) -> dict[str, ExternalCliAgentSpec]:
    bucket = read_team_namespace(session, team_name)
    if bucket is None:
        return {}
    spec_data = bucket.get("spec")
    if spec_data is None:
        return {}
    try:
        spec = TeamAgentSpec.model_validate(spec_data)
    except Exception as exc:
        team_logger.warning("Failed to parse team spec for external CLI cleanup team={}: {}", team_name, exc)
        return {}
    return {config.cli_agent: config for config in spec.external_cli_agents}


__all__ = ["cleanup_external_cli_backend_session", "cleanup_external_cli_backend_sessions"]
