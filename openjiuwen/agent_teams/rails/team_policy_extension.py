# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extension policy assembly layered over the rail's shared runtime behavior."""

from __future__ import annotations

import asyncio
from inspect import signature
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams import inbound_render
from openjiuwen.agent_teams.extensions.events import build_team_policy_sections
from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
from openjiuwen.agent_teams.extensions.policy import (
    build_default_policy_sections,
    get_member_capability_context,
    render_policy_sections,
    select_policy_disclosure_sections,
    select_policy_prefix_sections,
)
from openjiuwen.agent_teams.prompts.loader import TemplateLoader
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.core.events.protocol import ExtensionEventBusProtocol


def _framework_renderer_unavailable(**_kwargs: Any) -> str:
    """Reject framework delivery on releases without its renderer."""
    raise ValueError("Framework policy delivery requires a framework-capable prompt renderer")


class TeamPolicyExtensionMixin(DeepAgentRail):
    """Own policy construction while the shared base owns state delivery.

    The base constructor calls the virtual section builder. Its runtime,
    recovery and task-tracking changes can evolve independently of the
    extension entry that now owns that builder.
    """

    _language: str
    _loader: TemplateLoader
    _expose_human_agents_to_teammates: bool
    _tracker: TeamContextTracker
    _static_sections: list[PromptSection]
    _full_policy_sections: tuple[PromptSection, ...]
    _policy_build_input: TeamPolicyBuildInput
    _policy_context: TeamPolicyContext

    def __init__(self, *, extension_bus: ExtensionEventBusProtocol | None = None, **kwargs: Any) -> None:
        self._extension_bus = extension_bus
        self._policy_backend = kwargs.get("team_backend")
        self._policy_bootstrap_mode = kwargs.pop("bootstrap_mode", "leader")
        self._policy_bound_group_name = kwargs.pop("bound_group_name", None)
        if self._policy_bootstrap_mode == "framework" and kwargs.get("role") == TeamRole.LEADER:
            self._validate_framework_delivery()
        self._policy_sections_ready = extension_bus is None
        self._build_lock = asyncio.Lock() if extension_bus is not None else None

        base_parameters = signature(super().__init__).parameters
        if "bootstrap_mode" in base_parameters:
            # Policy delivery belongs to this mixin. Suppress the base's
            # eager framework-disclosure path; the real mode is in our context.
            kwargs["bootstrap_mode"] = "leader"
        if "bound_group_name" in base_parameters:
            kwargs["bound_group_name"] = self._policy_bound_group_name
        super().__init__(**kwargs)
        if self._policy_sections_ready:
            self._install_framework_policy(self._full_policy_sections)

    @staticmethod
    def _validate_framework_delivery() -> None:
        """Require both the renderer and the tracker's consuming delivery path."""
        if not callable(getattr(inbound_render, "render_team_policy_inline", None)):
            raise ValueError("Framework bootstrap requires an inline policy renderer")
        if "policy_text" not in signature(TeamContextTracker).parameters:
            raise ValueError("Framework bootstrap requires a tracker with policy_text delivery support")

    def _build_static_sections(self, **values: Any) -> list[PromptSection]:
        backend = self._policy_backend
        self._policy_build_input = TeamPolicyBuildInput(
            team_name=backend.team_name if backend is not None else "",
            role=values["role"],
            member_name=values.get("member_name"),
        )
        capabilities = get_member_capability_context(backend)
        for name in ("member_messaging", "member_subtasks"):
            if name in values:
                capabilities[name] = values[name]
        self._policy_context = TeamPolicyContext(
            language=self._language,
            lifecycle=values["lifecycle"],
            teammate_mode=values["teammate_mode"],
            team_mode=values["team_mode"],
            dispatch_mode=values["dispatch_mode"],
            hitt_enabled=values["hitt_enabled"],
            base_prompt=values.get("base_prompt"),
            swarmflow_enabled=values["swarmflow_enabled"],
            expose_human_agents_to_teammates=self._expose_human_agents_to_teammates,
            **capabilities,
            bootstrap_mode=self._policy_bootstrap_mode,
            bound_group_name=self._policy_bound_group_name,
        )
        if self._extension_bus is not None:
            return []
        self._full_policy_sections = build_default_policy_sections(
            self._policy_build_input,
            self._policy_context,
            loader=self._loader,
        )
        return list(self._prefix_view(self._full_policy_sections))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Finish the extension chain before the base injects sections or state."""
        await self._ensure_sections_built(ctx)
        await super().before_model_call(ctx)

    async def _ensure_sections_built(self, ctx: AgentCallbackContext) -> None:
        if self._policy_sections_ready or self._extension_bus is None or self._build_lock is None:
            return
        session = getattr(ctx, "session", None)
        session_id = session.get_session_id() if session is not None else None
        async with self._build_lock:
            if self._policy_sections_ready:
                return
            output = await build_team_policy_sections(
                self._policy_build_input,
                self._policy_context,
                bus=self._extension_bus,
                session_id=session_id,
                loader=self._loader,
            )
            self._static_sections = list(self._prefix_view(output.sections))
            self._install_framework_policy(output.sections)
            self._policy_sections_ready = True

    def _prefix_view(self, sections: tuple[PromptSection, ...]) -> tuple[PromptSection, ...]:
        if self._policy_build_input.role == TeamRole.LEADER:
            return select_policy_prefix_sections(sections)
        return sections

    def _install_framework_policy(self, sections: tuple[PromptSection, ...]) -> None:
        if self._policy_build_input.role != TeamRole.LEADER or self._policy_context.bootstrap_mode != "framework":
            return
        render_inline = getattr(inbound_render, "render_team_policy_inline", _framework_renderer_unavailable)
        if not callable(render_inline):
            raise ValueError("Framework policy delivery requires a framework-capable prompt renderer")
        self._tracker.set_policy_text(
            render_inline(
                body=render_policy_sections(select_policy_disclosure_sections(sections), language=self._language)
            ),
        )
