# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared spec / build-context derivation for swarmflow member harnesses.

Both the single-shot :class:`TeamWorkerBackend` and the stateful
``AvatarSessionManager`` build a member ``DeepAgentSpec`` the same way: derive
from the team's base spec (teammate, or the leader / human_agent fallback), swap
in a unique per-member card plus the caller's system prompt and model, and append
any extra tools. Keeping that in one place stops the two backends from drifting.
"""
from __future__ import annotations

from typing import Any, Sequence

from openjiuwen.agent_teams.schema.team import TeamRole


def derive_member_spec(
    base_spec: Any,
    *,
    team_name: str,
    member_name: str,
    system_prompt: str,
    model: Any,
    extra_tools: Sequence[Any] = (),
    description: str = "swarmflow member",
) -> Any:
    """Derive a per-member ``DeepAgentSpec`` from a base spec (base left unmutated).

    Args:
        base_spec: The team's base spec (teammate, or leader / human_agent fallback).
        team_name: Team name used to namespace the member card id.
        member_name: The minted member identity (also the card name).
        system_prompt: The system prompt for this member's role.
        model: Per-member model config, or ``None`` to inherit the base spec's model.
        extra_tools: Tool instances to append to the base spec's tools.
        description: Card description.

    Returns:
        A new ``DeepAgentSpec`` copy with the derived card / model / prompt / tools.
    """
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    return base_spec.model_copy(
        update={
            "card": AgentCard(
                id=f"{team_name}_{member_name}",
                name=member_name,
                description=description,
            ),
            "model": model or base_spec.model,
            "system_prompt": system_prompt,
            "tools": list(base_spec.tools or []) + list(extra_tools),
        }
    )


def derive_member_build_context(
    build_context: Any,
    *,
    team_name: str,
    member_name: str,
    language: str,
) -> Any:
    """Derive a per-member ``BuildContext`` (shallow-copied extras), or ``None``.

    Args:
        build_context: The leader's build context, or ``None``.
        team_name: Team name for the member card id.
        member_name: The minted member identity.
        language: Prompt language for the member.

    Returns:
        A derived ``BuildContext`` with shallow-copied ``extras``, or ``None`` when
        no base build context was provided.
    """
    if build_context is None:
        return None
    derived = build_context.derive(
        member_name=member_name,
        role=TeamRole.WORKER.value,
        member_card_id=f"{team_name}_{member_name}",
        language=language,
    )
    derived.extras = dict(derived.extras)
    return derived


__all__ = ["derive_member_spec", "derive_member_build_context"]
