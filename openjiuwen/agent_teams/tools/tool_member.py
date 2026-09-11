# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member management tools: spawn, shutdown, approve, and list."""

import json
from abc import ABC
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_permissions import _MEMBER_NAME_PATTERN
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.models.pool import ModelPoolEntry
    from openjiuwen.agent_teams.schema.team import MemberOpResult


def _is_anthropic_provider(provider: str) -> bool:
    """Return whether a provider name is Anthropic-compatible."""
    return "anthropic" in provider.lower()


def _provider_filter_for_cli(cli_agent: str) -> Callable[[str], bool] | None:
    """Return a provider filter for the CLI kind, or None for unknown kinds."""
    if cli_agent == "claude":
        return _is_anthropic_provider
    if cli_agent == "codex":
        return lambda p: not _is_anthropic_provider(p)
    return None


def _model_api_protocol(provider: str) -> str:
    """Return the external CLI protocol label for a model provider."""
    return "Anthropic" if _is_anthropic_provider(provider) else "OpenAI"


# ========== Member Management ==========


class _SpawnToolBase(TeamTool, ABC):
    """Shared scaffolding for the role-specific spawn tools.

    Each concrete subclass owns exactly one ``role_type``: it declares its
    own flat ``input_params`` schema and implements a single straight-line
    ``invoke`` — no role branching anywhere. The cross-cutting concerns every
    spawn tool shares live here: ``member_name`` validation, ToolOutput
    construction, and model-facing result mapping.
    """

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        tool_name: str,
        *,
        omit_slots: frozenset[str] | None = None,
    ):
        """Build the spawn tool's card.

        Args:
            team: Backend every spawn tool talks to.
            t: Locale-bound translator.
            tool_name: Tool name, also the ``_desc`` key and card id suffix.
            omit_slots: Capability slots to drop from the Markdown
                description. Pass the same gate that shaped the subclass's
                schema, so a parameter and the prose describing it always
                appear together.
        """
        super().__init__(
            ToolCard(
                id=f"team.{tool_name}",
                name=tool_name,
                description=t(tool_name, omit=omit_slots),
            )
        )
        self.team = team

    @staticmethod
    def _validate_member_name(member_name: str | None) -> str | None:
        """Validate ``member_name`` at the tool boundary.

        Returns:
            An error message when the name is missing or malformed,
            otherwise ``None``.
        """
        if member_name and _MEMBER_NAME_PATTERN.match(member_name):
            return None
        return (
            f"Invalid member_name {member_name!r}: must start with a "
            "lowercase ASCII letter (a-z), followed by lowercase letters, "
            "digits (0-9) or hyphen (-); no uppercase, underscore, "
            "whitespace, or non-ASCII characters (including CJK) — "
            "member_name is reused as a routing token and a filesystem "
            "path segment"
        )

    @staticmethod
    def _fail(reason: str) -> ToolOutput:
        """Build a failed ToolOutput carrying a diagnostic reason."""
        return ToolOutput(success=False, error=reason)

    @staticmethod
    def _from_result(
        result: "MemberOpResult",
        *,
        member_name: str,
        display_name: str,
        role_type: str,
        **extra: Any,
    ) -> ToolOutput:
        """Wrap a backend ``MemberOpResult`` into a ToolOutput.

        Propagates ``result.reason`` into ``error`` on failure so the LLM
        can diagnose what the backend rejected.
        """
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "display_name": display_name,
                "role_type": role_type,
                **extra,
            },
            error=None if result.ok else result.reason,
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to spawn member"
        d = output.data
        role = d.get("role_type", "teammate")
        cli_agent = d.get("cli_agent")
        suffix = f" cli_agent={cli_agent}" if cli_agent else ""
        return (
            f"Member spawned: member_name={d['member_name']} "
            f"display_name={d['display_name']} role={role}{suffix}"
        )


class SpawnTeammateTool(_SpawnToolBase):
    """Spawn an ordinary LLM teammate (``role_type='teammate'``).

    Context inheritance is a gated capability: when ``fork_enabled`` is
    False the ``fork`` / ``fork_source`` / ``compact`` properties are absent
    from the schema *and* the fork section is dropped from the description,
    both off the one flag. See ``TeamAgentSpec.enable_fork``.
    """

    #: Fork properties and the description slot that documents them. Schema
    #: and prose are gated together — the model must never read about an
    #: argument it has no way to pass.
    _FORK_PARAMS = ("fork", "fork_source", "fork_mode")
    _FORK_SLOTS = frozenset({"fork_usage", "fork_param_rows"})

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        model_config_allocator: Callable[[str | None], "Allocation | None"] | None = None,
        fork_enabled: bool = False,
    ):
        """Build the spawn_teammate tool.

        Args:
            team: Backend that registers the member row.
            t: Locale-bound translator.
            model_config_allocator: Callback returning the next ``Allocation``
                for the spawned teammate; receives the ``model_name`` hint.
            fork_enabled: Whether context inheritance is open for this team
                (``TeamBackend.fork_enabled()``). Gates the fork properties
                and the fork section of the description as one unit.
        """
        super().__init__(
            team,
            t,
            "spawn_teammate",
            omit_slots=None if fork_enabled else frozenset(self._FORK_SLOTS),
        )
        self._allocate_model_config = model_config_allocator
        self._fork_enabled = fork_enabled
        properties: dict[str, Any] = {
            "member_name": {
                "type": "string",
                "description": t("spawn_teammate", "member_name"),
            },
            "display_name": {
                "type": "string",
                "description": t("spawn_teammate", "display_name"),
            },
            "desc": {"type": "string", "description": t("spawn_teammate", "desc")},
            "prompt": {"type": "string", "description": t("spawn_teammate", "prompt")},
            "model_name": {
                "type": "string",
                "description": t("spawn_teammate", "model_name"),
            },
            "isolation": {
                "type": "string",
                "enum": ["worktree"],
                "description": (
                    "Optional isolation mode. Set 'worktree' only when the "
                    "user explicitly requests worktree isolation, or when "
                    "the teammate must modify repository files in an "
                    "isolated checkout. Omit this field for read-only, "
                    "game, discussion, research, or standby tasks."
                ),
            },
            "permissions": {
                "type": "object",
                "description": t("spawn_teammate", "permissions"),
            },
        }
        if fork_enabled:
            properties.update({
                "fork": {
                    "anyOf": [{"type": "boolean"}, {"type": "string"}],
                    "description": t("spawn_teammate", "fork"),
                },
                "fork_source": {
                    "type": "string",
                    "description": t("spawn_teammate", "fork_source"),
                },
                "fork_mode": {
                    "type": "string",
                    "enum": [
                        "full",
                        "before",
                        "after",
                        "keep_before_compact_after",
                        "keep_after_compact_before",
                    ],
                    "description": t("spawn_teammate", "fork_mode"),
                },
            })
        self.card.input_params = {
            "type": "object",
            "properties": properties,
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        # Schema omission binds the hosting LLM; this rejects the same
        # arguments coming from an MCP client, which calls ``invoke``
        # directly without validating against ``input_params``. Checked
        # before the member row is written so a rejected call spawns
        # nothing.
        if not self._fork_enabled:
            passed = [key for key in self._FORK_PARAMS if inputs.get(key) is not None]
            if passed:
                return self._fail(
                    f"Cannot use {', '.join(passed)}: context inheritance (fork) is "
                    "disabled (enable_fork=False on TeamAgentSpec). Spawn the "
                    "teammate without these arguments, or enable fork in the team spec."
                )

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        desc = inputs.get("desc", "")
        permissions_override = inputs.get("permissions")
        allocation = (
            self._allocate_model_config(inputs.get("model_name")) if self._allocate_model_config else None
        )
        agent_card = AgentCard(
            id=f"{self.team.team_name}_{member_name}",
            name=display_name,
            description=desc,
        )
        result = await self.team.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=agent_card,
            desc=desc,
            prompt=inputs.get("prompt"),
            mode=MemberMode(self.team.teammate_mode.value),
            allocation=allocation,
            isolation=inputs.get("isolation"),
            permissions_override=permissions_override,
        )
        fork_value = inputs.get("fork")
        if fork_value and fork_value not in ("false", False):
            self.team.mark_fork_on_spawn(
                member_name,
                fork_value,
                fork_source=inputs.get("fork_source"),
                fork_mode=inputs.get("fork_mode") or "",
            )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="teammate",
            isolation=inputs.get("isolation"),
        )


class CheckpointTool(TeamTool):
    """Save a named snapshot of current conversation context.

    Half of the fork capability: the snapshot this records is what a later
    ``spawn_teammate(fork="<name>")`` inherits from. Gated on
    ``TeamAgentSpec.enable_fork`` — the tool is not wired at all when fork
    is off (see ``create_team_tools``); the check in ``invoke`` is the
    backstop for MCP clients that call it directly.
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.checkpoint",
                name="checkpoint",
                description=t("checkpoint"),
            )
        )
        self.team = team
        self.t = t
        self.card.input_params = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": t("checkpoint", "name")},
                "description": {"type": "string", "description": t("checkpoint", "description")},
            },
            "required": ["name"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        if not self.team.fork_enabled():
            return ToolOutput(
                success=False,
                error=(
                    "Cannot save a checkpoint: context inheritance (fork) is "
                    "disabled (enable_fork=False on TeamAgentSpec). Checkpoints "
                    "exist only to be forked from."
                ),
            )
        name = inputs["name"]
        count = self.team.snapshot_context_length()
        description = inputs.get("description") or ""
        conflict = self.team.store_checkpoint(
            name,
            count,
            description=description,
            created_by=self.team.member_name,
        )
        if conflict is not None:
            return ToolOutput(
                success=False,
                error=self.t(
                    "checkpoint", "duplicate",
                    name=name,
                    created_by=conflict.get("created_by") or "?",
                    description=conflict.get("description") or "",
                ),
            )
        # Notify the leader as a framework event (not a member message): the
        # name reaches the leader's context as an announcement-only note, so
        # the leader is never prompted to reply. The leader's own checkpoints
        # need no self-notification.
        if not self.team.is_leader:
            await self.team.publish_checkpoint_created(name, count, description)
        return ToolOutput(
            success=True,
            data={"name": name, "message_count": count},
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to save checkpoint"
        d = output.data
        return f"Checkpoint '{d['name']}' saved at message {d['message_count']}"


class ListCheckpointsTool(TeamTool):
    """List all named checkpoints available for fork inheritance."""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.list_checkpoints",
                name="list_checkpoints",
                description=t("list_checkpoints"),
            )
        )
        self.team = team
        self.card.input_params = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        checkpoints = self.team.list_checkpoints()
        items = [
            {
                "name": name,
                "message_count": record.get("count"),
                "description": record.get("description", ""),
                "created_by": record.get("created_by", ""),
            }
            for name, record in sorted(checkpoints.items())
        ]
        return ToolOutput(
            success=True,
            data={"checkpoints": items, "count": len(items)},
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list checkpoints"
        checkpoints = output.data["checkpoints"]
        if not checkpoints:
            return "No checkpoints"
        lines = []
        for item in checkpoints:
            line = (
                f"name={item['name']} message_count={item['message_count']} "
                f"created_by={item['created_by']}"
            )
            if item.get("description"):
                line += f' description="{item["description"]}"'
            lines.append(line)
        return "\n".join(lines)


class SpawnHumanAgentTool(_SpawnToolBase):
    """Spawn a human member driven by the real user (``role_type='human_agent'``).

    The schema deliberately omits ``model_name`` / ``prompt`` — human members
    run on the framework template, so there is no field to reject at runtime.
    The HITT capability check below is a defensive backstop; the tool is not
    even wired when HITT is disabled (see ``create_team_tools``).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_human_agent")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_human_agent", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_human_agent", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_human_agent", "desc")},
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        if not self.team.hitt_enabled():
            return self._fail(
                "Cannot spawn human agent: HITT capability is disabled "
                "(enable_hitt=False on TeamAgentSpec or build_team). "
                "Enable HITT in the team spec or use spawn_teammate instead."
            )

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        result = await self.team.spawn_human_agent(
            member_name=member_name,
            display_name=display_name,
            desc=inputs.get("desc", ""),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="human_agent",
        )


class SpawnBridgeAgentTool(_SpawnToolBase):
    """Spawn a bridge agent to a remote independent agent (``role_type='bridge_agent'``).

    A bridge agent is a full local teammate paired with a remote agent reached
    over a pure-text protocol. ``prompt`` is required: it is the private system
    prompt the remote adopts (via ``adapter.connect``) to act as this member.
    ``desc`` is the public roster description peers see. The Bridge capability
    check is a defensive backstop; the tool is not wired when Bridge is disabled
    (see ``create_team_tools``).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_bridge_agent")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_bridge_agent", "desc")},
                "prompt": {"type": "string", "description": t("spawn_bridge_agent", "prompt")},
                "mailbox_inject_mode": {
                    "type": "string",
                    "enum": ["passthrough", "rephrase"],
                    "default": "passthrough",
                    "description": t("spawn_bridge_agent", "mailbox_inject_mode"),
                },
                "protocol": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "protocol"),
                },
                "adapter_config": {
                    "type": "object",
                    "description": t("spawn_bridge_agent", "adapter_config"),
                },
                "model_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "model_name"),
                },
            },
            "required": ["member_name", "display_name", "prompt"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.agent_teams.schema.team import BridgeMailboxInjectMode

        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        if not self.team.bridge_enabled():
            return self._fail(
                "Cannot spawn bridge agent: Bridge capability is disabled "
                "(enable_bridge=False on TeamAgentSpec or build_team). "
                "Enable Bridge in the team spec or use spawn_teammate instead."
            )

        desc = inputs.get("desc") or ""
        prompt = inputs.get("prompt") or ""
        if not prompt:
            return self._fail(
                "spawn_bridge_agent requires a non-empty 'prompt' — it is the "
                "briefing the remote agent adopts via adapter.connect"
            )

        mode_raw = (inputs.get("mailbox_inject_mode") or "passthrough").lower()
        try:
            inject_mode = BridgeMailboxInjectMode(mode_raw)
        except ValueError:
            return self._fail(
                f"Invalid mailbox_inject_mode '{mode_raw}'; expected 'passthrough' or 'rephrase'"
            )

        adapter_config = inputs.get("adapter_config") or {}
        if not isinstance(adapter_config, dict):
            return self._fail("adapter_config must be an object/dict")

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        protocol = inputs.get("protocol") or ""
        result = await self.team.spawn_bridge_agent(
            member_name=member_name,
            display_name=display_name,
            desc=desc,
            prompt=prompt,
            model_name=inputs.get("model_name"),
            mailbox_inject_mode=inject_mode,
            protocol=protocol,
            adapter_config=adapter_config,
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="bridge_agent",
            mailbox_inject_mode=inject_mode.value,
            protocol=protocol,
        )


class SpawnExternalCliTool(_SpawnToolBase):
    """Spawn a third-party CLI agent as a teammate (``role_type='external_cli'``).

    The teammate's brain is a CLI subprocess (claudecode / codex / ...) driven
    by an ``ExternalCliRuntime``. ``cli_agent`` names a CLI kind pre-declared in
    ``TeamAgentSpec.external_cli_agents`` — all launch knowledge lives there, so
    this call carries only the identifier. ``prompt`` is the private system
    prompt the CLI adopts to act as this member; ``desc`` is the public roster
    description peers see. The tool is not wired when no CLI kinds are declared
    (see ``create_team_tools``).
    """

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        model_config_allocator: Callable[[str | None], "Allocation | None"] | None = None,
    ):
        super().__init__(team, t, "spawn_external_cli")
        self._allocate_model_config = model_config_allocator
        fallback_description = t("spawn_external_cli", "fallback_model_name")
        fallback_description = f"{fallback_description}\n\n{self._model_catalog_context()}"
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_external_cli", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_external_cli", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_external_cli", "desc")},
                "prompt": {"type": "string", "description": t("spawn_external_cli", "prompt")},
                "cli_agent": {
                    "type": "string",
                    "description": t("spawn_external_cli", "cli_agent"),
                },
                "model_name": {
                    "type": "string",
                    "description": t("spawn_external_cli", "model_name"),
                },
                "fallback_model_name": {
                    "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
                    "description": fallback_description,
                },
            },
            "required": ["member_name", "display_name", "prompt", "cli_agent", "fallback_model_name"],
        }

    def _compatible_pool_entries(self, cli_agent: str) -> list["ModelPoolEntry"]:
        """Return protocol-compatible pool entries for one CLI kind."""
        provider_filter = _provider_filter_for_cli(cli_agent)
        return [
            entry
            for entry in self.team.get_model_pool()
            if provider_filter is None or provider_filter(entry.api_provider)
        ]

    def _preferred_current_model(self, cli_agent: str, entries: list["ModelPoolEntry"]) -> str | None:
        """Return the current model when the pool can allocate it compatibly."""
        current_name = self.team.current_model_name
        current_provider = self.team.current_model_provider
        if current_name is None or current_provider is None:
            return None
        current_protocol = _model_api_protocol(current_provider)
        for entry in entries:
            if entry.model_name != current_name:
                continue
            if _model_api_protocol(entry.api_provider) == current_protocol:
                return current_name
        return None

    def _model_catalog_context(self) -> str:
        """Render safe current-model and pool protocol data for the model."""
        current_model = None
        if self.team.current_model_name is not None and self.team.current_model_provider is not None:
            current_model = {
                "model_name": self.team.current_model_name,
                "protocol": _model_api_protocol(self.team.current_model_provider),
            }
        compatible_by_cli: dict[str, list[dict[str, str]]] = {}
        for cli_agent in sorted(self.team.external_cli_kinds()):
            options = {
                (entry.model_name, _model_api_protocol(entry.api_provider))
                for entry in self._compatible_pool_entries(cli_agent)
            }
            compatible_by_cli[cli_agent] = [
                {"model_name": model_name, "protocol": protocol}
                for model_name, protocol in sorted(options)
            ]
        catalog = {
            "current_model": current_model,
            "compatible_fallback_models": compatible_by_cli,
        }
        return f"<fallback_model_catalog>{json.dumps(catalog, ensure_ascii=False)}</fallback_model_catalog>"

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        cli_agent = (inputs.get("cli_agent") or "").strip()
        if not cli_agent:
            return self._fail(
                "spawn_external_cli requires 'cli_agent' naming a CLI kind "
                "declared in TeamAgentSpec.external_cli_agents (e.g. 'claude' or 'codex')"
            )
        if self.team.external_cli_config(cli_agent) is None:
            declared = ", ".join(sorted(self.team.external_cli_kinds())) or "<none>"
            return self._fail(
                f"cli_agent '{cli_agent}' is not declared in TeamAgentSpec.external_cli_agents "
                f"(declared: {declared})"
            )

        desc = inputs.get("desc") or ""
        prompt = inputs.get("prompt") or ""
        if not prompt:
            return self._fail("spawn_external_cli requires a non-empty 'prompt' (the member's private system prompt)")

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        model_name = inputs.get("model_name")
        if "fallback_model_name" not in inputs:
            return self._fail("spawn_external_cli requires 'fallback_model_name' to be a model name or null")
        raw_fallback_model_name = inputs["fallback_model_name"]
        if raw_fallback_model_name is not None and not isinstance(raw_fallback_model_name, str):
            return self._fail("spawn_external_cli requires 'fallback_model_name' to be a model name or null")
        fallback_model_name = (
            raw_fallback_model_name.strip()
            if isinstance(raw_fallback_model_name, str)
            else None
        )
        if fallback_model_name == "":
            return self._fail("spawn_external_cli requires 'fallback_model_name' to be a non-empty model name or null")
        allocation = None
        fallback_allocation = None
        provider_filter = _provider_filter_for_cli(cli_agent)
        compatible_entries = self._compatible_pool_entries(cli_agent)
        compatible_model_names = sorted(
            {
                entry.model_name
                for entry in compatible_entries
            }
        )
        preferred_current_model = self._preferred_current_model(cli_agent, compatible_entries)
        if model_name:
            if self._allocate_model_config is None:
                return self._fail("spawn_external_cli requires a team model pool when 'model_name' is specified")
            if provider_filter is not None:
                allocation = self._allocate_model_config(model_name, provider_filter=provider_filter)
            else:
                allocation = self._allocate_model_config(model_name)
            if allocation is None:
                return self._fail(
                    f"model_name '{model_name}' is unavailable or incompatible with cli_agent '{cli_agent}'"
                )
        if fallback_model_name is None:
            if compatible_model_names:
                return self._fail(
                    "fallback_model_name cannot be null while compatible models are available for "
                    f"cli_agent '{cli_agent}': {', '.join(compatible_model_names)}"
                )
        elif preferred_current_model is not None and fallback_model_name != preferred_current_model:
            return self._fail(
                f"fallback_model_name must use current model '{preferred_current_model}' because it is present "
                f"in the team model pool and compatible with cli_agent '{cli_agent}'"
            )
        elif self._allocate_model_config is None:
            return self._fail("spawn_external_cli requires a team model pool when 'fallback_model_name' is specified")
        else:
            if provider_filter is not None:
                fallback_allocation = self._allocate_model_config(
                    fallback_model_name,
                    provider_filter=provider_filter,
                )
            else:
                fallback_allocation = self._allocate_model_config(fallback_model_name)
            if fallback_allocation is None:
                compatible_hint = ", ".join(compatible_model_names) if compatible_model_names else "none"
                return self._fail(
                    f"fallback_model_name '{fallback_model_name}' is unavailable or incompatible with "
                    f"cli_agent '{cli_agent}' (compatible models: {compatible_hint}); use null only when no "
                    "compatible model is available"
                )
        result = await self.team.spawn_external_cli_agent(
            member_name=member_name,
            display_name=display_name,
            cli_agent=cli_agent,
            desc=desc,
            prompt=prompt,
            model_name=model_name,
            allocation=allocation,
            fallback_allocation=fallback_allocation,
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="external_cli",
            cli_agent=cli_agent,
        )


class ShutdownMemberTool(TeamTool):
    """Shutdown a team member"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.shutdown_member",
                name="shutdown_member",
                description=t("shutdown_member"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("shutdown_member", "member_name"),
                },
                "force": {"type": "boolean", "description": t("shutdown_member", "force")},
            },
            "required": ["member_name"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        result = await self.team.shutdown_member(
            member_name=member_name,
            force=inputs.get("force", False),
        )
        return ToolOutput(
            success=result.ok,
            data={"member_name": member_name},
            error=None if result.ok else result.reason,
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to shutdown member"
        return f"Member shutdown: member_name={output.data['member_name']}"


class ApprovePlanTool(TeamTool):
    """Approve or reject a member's plan"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.approve_plan",
                name="approve_plan",
                description=t("approve_plan"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": t("approve_plan", "plan_id"),
                },
                "approved": {"type": "boolean", "description": t("approve_plan", "approved")},
                "feedback": {"type": "string", "description": t("approve_plan", "feedback")},
            },
            "required": ["plan_id", "approved"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        approved = inputs.get("approved")
        plan_id = inputs.get("plan_id")
        success = await self.team.approve_plan(
            plan_id=plan_id,
            approved=approved,
            feedback=inputs.get("feedback"),
        )
        return ToolOutput(
            success=success,
            data={
                "plan_id": plan_id,
                "approved": approved,
            },
            error=None if success else "Failed to approve/reject plan",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject plan"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return f"Plan {decision}: plan_id={d['plan_id']} decision={decision}"


class ApproveToolCallTool(TeamTool):
    """Approve or reject one teammate tool call."""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.approve_tool",
                name="approve_tool",
                description=t("approve_tool"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("approve_tool", "member_name"),
                },
                "tool_call_id": {"type": "string", "description": t("approve_tool", "tool_call_id")},
                "approved": {"type": "boolean", "description": t("approve_tool", "approved")},
                "feedback": {"type": "string", "description": t("approve_tool", "feedback")},
                "auto_confirm": {"type": "boolean", "description": t("approve_tool", "auto_confirm")},
            },
            "required": ["member_name", "tool_call_id", "approved"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        tool_call_id = inputs.get("tool_call_id")
        approved = inputs.get("approved")
        success = await self.team.approve_tool(
            member_name=member_name,
            tool_call_id=tool_call_id,
            approved=approved,
            feedback=inputs.get("feedback"),
            auto_confirm=inputs.get("auto_confirm", False),
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name, "tool_call_id": tool_call_id, "approved": approved},
            error=None if success else "Failed to approve/reject tool call",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject tool call"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return (
            f"Tool call {decision}: tool_call_id={d['tool_call_id']} member_name={d['member_name']} decision={decision}"
        )


class ListMembersTool(TeamTool):
    """List all team members"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.list_members",
                name="list_members",
                description=t("list_members"),
            )
        )
        self.team = team
        self.card.input_params = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        members = await self.team.list_member_roster()
        return ToolOutput(
            success=True, data={"members": [member.model_dump() for member in members], "count": len(members)}
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list members"
        members = output.data["members"]
        if not members:
            return "No members"
        lines = [
            f"member_name={m['member_name']} display_name={m['display_name']} status={m['status']}" for m in members
        ]
        return "\n".join(lines)
