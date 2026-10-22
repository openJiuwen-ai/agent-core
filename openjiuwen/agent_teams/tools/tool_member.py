# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member management tools: spawn, shutdown, approve, and list."""

import json
from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_permissions import _MEMBER_NAME_PATTERN
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.models.pool import ModelPoolEntry
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, MemberOpResult
    from openjiuwen.agent_teams.tools.member_options import MemberAgentTemplate, MemberBuiltinModel


def validate_member_name(member_name: str | None) -> str | None:
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


def _is_anthropic_provider(provider: str) -> bool:
    """Return whether a provider name is Anthropic-compatible."""
    from openjiuwen.agent_teams.external.cli_agent.model_compat import is_anthropic_provider

    return is_anthropic_provider(provider)


def _provider_filter_for_cli(cli_agent: str) -> Callable[[str], bool] | None:
    """Return a provider filter for the CLI kind, or None for unknown kinds."""
    # Imported lazily: model_compat lives under agent_teams.external, whose
    # package __init__ eagerly imports tool_gateway -> team_workspace.tools.
    # tool_member is itself imported while team_workspace.tools is still
    # mid-initialization, so a top-level import would re-enter it and deadlock.
    from openjiuwen.agent_teams.external.cli_agent.model_compat import provider_filter_for_cli

    return provider_filter_for_cli(cli_agent)


def _takes_auth_fallback(cli_agent: str) -> bool:
    """Return whether ``cli_agent`` switches to a team model when its own login fails."""
    from openjiuwen.agent_teams.external.cli_agent import backends

    return backends.takes_auth_fallback(cli_agent)


def _model_api_protocol(provider: str) -> str:
    """Return the external CLI protocol label for a model provider."""
    from openjiuwen.agent_teams.external.cli_agent.model_compat import model_api_protocol

    return model_api_protocol(provider)


def _builtin_model_catalog(team: TeamBackend) -> str:
    """Render the built-in models each CLI kind declares, for the model to pick from."""
    catalog: dict[str, list[dict[str, Any]]] = {}
    for cli_agent in sorted(team.external_cli_kinds()):
        config = team.external_cli_config(cli_agent)
        if config is None or not config.builtin_models:
            continue
        catalog[cli_agent] = [
            {
                "name": model.name,
                "description": model.description,
                "efforts": model.efforts,
                "default_effort": model.default_effort,
            }
            for model in config.builtin_models
        ]
    return f"<builtin_model_catalog>{json.dumps(catalog, ensure_ascii=False)}</builtin_model_catalog>"


def _optional_text(value: Any) -> str | None:
    """Return a stripped non-empty string, or ``None`` for absent/blank input."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def model_name_description(team: TeamBackend, t: Translator) -> str:
    """Describe the ``model_name`` argument of a tool that registers a teammate.

    Inside a model group the field takes a route tag rather than a pool
    model, so the wording switches and the selectable tags are listed.
    """
    if not team.is_model_group_pool():
        return t("spawn_teammate", "model_name")
    catalog = {"selectable_models": [str(tag) for tag in team.list_route_tags()]}
    context = f"<selectable_model_catalog>{json.dumps(catalog, ensure_ascii=False)}</selectable_model_catalog>"
    return f"{t('spawn_teammate', 'model_name_group')}\n\n{context}"


def check_model_selectable(team: TeamBackend, model_name: Any, allocation: "Allocation | None") -> str | None:
    """Reject an explicit ``model_name`` the current model group cannot honour.

    In a model group an explicit model is a hard requirement: running the
    member on the whole group instead would silently ignore the user.

    Returns:
        An error message when the model is not selectable, otherwise ``None``.
    """
    model_name = _optional_text(model_name)
    if allocation is not None or not model_name or not team.is_model_group_pool():
        return None
    selectable = ", ".join(team.list_route_tags()) or "<none>"
    return (
        f"model_name '{model_name}' is not selectable in the current model group "
        f"(selectable: {selectable}). Do not pick a substitute yourself: ask the user "
        "which model to use, or omit model_name to let the model group choose."
    )


# ========== External CLI model rules ==========
#
# One implementation of "may this team launch this CLI kind, and on which
# model" shared by every tool that registers an external CLI member
# (``spawn_teammate`` with ``cli_agent`` and the external path of
# ``spawn_expert``), so the same inputs always reach the same allocation or
# the same error.


def check_external_cli_declared(team: TeamBackend, cli_agent: str) -> str | None:
    """Reject a CLI kind the team has not declared (the capability ceiling).

    Returns:
        An error message when ``cli_agent`` is not declared, otherwise ``None``.
    """
    if team.external_cli_config(cli_agent) is not None:
        return None
    declared = ", ".join(sorted(team.external_cli_kinds())) or "<none>"
    return (
        f"cli_agent '{cli_agent}' is not declared in TeamAgentSpec.external_cli_agents "
        f"(declared: {declared})"
    )


def external_cli_model_catalog_context(team: TeamBackend) -> str:
    """Render safe current-model and pool protocol data for the model."""
    rules = ExternalCliModelRules(team, None)
    current_model = None
    if (
        not team.is_model_group_pool()
        and team.current_model_name is not None
        and team.current_model_provider is not None
    ):
        current_model = {
            "model_name": team.current_model_name,
            "protocol": _model_api_protocol(team.current_model_provider),
        }
    compatible_by_cli: dict[str, list[dict[str, str]]] = {}
    for cli_agent in sorted(team.external_cli_kinds()):
        if not _takes_auth_fallback(cli_agent):
            continue
        provider_filter = _provider_filter_for_cli(cli_agent)
        if team.is_model_group_pool():
            options = {
                (item.model, _model_api_protocol(item.provider))
                for item in team.list_cli_models(provider_filter=provider_filter)
                if item.model
            }
        else:
            options = {
                (entry.model_name, _model_api_protocol(entry.api_provider))
                for entry in rules.compatible_pool_entries(cli_agent)
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


@dataclass(frozen=True)
class ExternalCliModels:
    """Model choice of one external CLI member."""

    allocation: "Allocation | None"
    fallback_allocation: "Allocation | None"
    builtin_model: "MemberBuiltinModel | None"


class ExternalCliModelRules:
    """Pool model / authentication fallback / built-in model rules of an external CLI member."""

    def __init__(
        self,
        team: TeamBackend,
        allocate_model_config: Callable[..., "Allocation | None"] | None,
    ):
        self.team = team
        self._allocate_model_config = allocate_model_config

    def resolve(
        self,
        cli_agent: str,
        *,
        model_name: str | None,
        fallback_model_name: Any,
        builtin_model: Any,
        effort: Any,
        tool_name: str,
    ) -> tuple[ExternalCliModels | None, str]:
        """Resolve the member's models; ``(None, reason)`` when the inputs are rejected.

        Args:
            cli_agent: A CLI kind already checked with ``check_external_cli_declared``.
            model_name: Team model pool endpoint for the CLI.
            fallback_model_name: Raw ``fallback_model_name`` argument.
            builtin_model: Raw ``builtin_model`` argument.
            effort: Raw ``effort`` argument.
            tool_name: Calling tool, named in error messages.
        """
        raw_fallback_model_name = fallback_model_name
        if raw_fallback_model_name is not None and not isinstance(raw_fallback_model_name, str):
            return None, f"{tool_name} requires 'fallback_model_name' to be a model name when given"
        # Omitted, null and blank all mean "no fallback model".
        fallback_model_name = (raw_fallback_model_name or "").strip() or None
        has_auth_fallback = _takes_auth_fallback(cli_agent)
        if fallback_model_name is not None and not has_auth_fallback:
            return None, f"cli_agent '{cli_agent}' has no authentication fallback; omit 'fallback_model_name'"
        allocation = None
        fallback_allocation = None
        provider_filter = _provider_filter_for_cli(cli_agent)
        compatible_entries = self.compatible_pool_entries(cli_agent)
        compatible_model_names = self._compatible_model_names(cli_agent)
        preferred_current_model = self._preferred_current_model(cli_agent, compatible_entries)
        builtin, builtin_error = self._resolve_builtin_model(
            builtin_model, effort, cli_agent=cli_agent, model_name=model_name
        )
        if builtin_error:
            return None, builtin_error
        if model_name == "*" or fallback_model_name == "*":
            return None, "model_name '*' is a Team logical entry and cannot be passed to an external CLI"
        if model_name:
            if self.team.is_model_group_pool():
                candidates = self.team.resolve_cli_models(model_name, provider_filter=provider_filter)
                logical = self._allocate_model_config(None) if self._allocate_model_config else None
                if candidates and logical is not None:
                    from openjiuwen.agent_teams.models.allocator import Allocation

                    allocation = Allocation(logical.entry, logical.group_index, persistence_model_name=model_name)
            elif self._allocate_model_config is None:
                return None, self._model_name_failure(
                    cli_agent=cli_agent,
                    model_name=model_name,
                    compatible=compatible_model_names,
                    cause="cannot be resolved without a team model pool",
                )
            elif provider_filter is not None:
                allocation = self._allocate_model_config(model_name, provider_filter=provider_filter)
            elif allocation is None:
                allocation = self._allocate_model_config(model_name)
            if allocation is None:
                return None, self._model_name_failure(
                    cli_agent=cli_agent,
                    model_name=model_name,
                    compatible=compatible_model_names,
                    cause="is unavailable or incompatible",
                )
        if fallback_model_name is None:
            if compatible_model_names and has_auth_fallback:
                return None, (
                    "fallback_model_name is required while compatible models are available for "
                    f"cli_agent '{cli_agent}': {', '.join(compatible_model_names)}"
                )
        elif preferred_current_model is not None and fallback_model_name != preferred_current_model:
            return None, (
                f"fallback_model_name must use current model '{preferred_current_model}' because it is present "
                f"in the team model pool and compatible with cli_agent '{cli_agent}'"
            )
        elif self._allocate_model_config is None:
            return None, f"{tool_name} requires a team model pool when 'fallback_model_name' is specified"
        else:
            if self.team.is_model_group_pool():
                candidates = self.team.resolve_cli_models(fallback_model_name, provider_filter=provider_filter)
                logical = self._allocate_model_config(None)
                if candidates and logical is not None:
                    from openjiuwen.agent_teams.models.allocator import Allocation

                    fallback_allocation = Allocation(
                        logical.entry,
                        logical.group_index,
                        persistence_model_name=fallback_model_name,
                    )
            elif provider_filter is not None:
                fallback_allocation = self._allocate_model_config(
                    fallback_model_name,
                    provider_filter=provider_filter,
                )
            else:
                fallback_allocation = self._allocate_model_config(fallback_model_name)
            if fallback_allocation is None:
                compatible_hint = ", ".join(compatible_model_names) if compatible_model_names else "none"
                return None, (
                    f"fallback_model_name '{fallback_model_name}' is unavailable or incompatible with "
                    f"cli_agent '{cli_agent}' (compatible models: {compatible_hint}); omit 'fallback_model_name' "
                    "only when no compatible model is available"
                )
        return ExternalCliModels(allocation, fallback_allocation, builtin), ""

    def compatible_pool_entries(self, cli_agent: str) -> list["ModelPoolEntry"]:
        """Return protocol-compatible pool entries for one CLI kind."""
        provider_filter = _provider_filter_for_cli(cli_agent)
        return [
            entry
            for entry in self.team.get_model_pool()
            if provider_filter is None or provider_filter(entry.api_provider)
        ]

    def _compatible_model_names(self, cli_agent: str) -> list[str]:
        """Return safe model names visible to one CLI kind.

        IntelliRouter/model-group pools contain one logical ``*`` entry while
        their real deployment names live in the allocator catalog.  Project
        that catalog here so fallback validation does not accidentally treat
        a model group as an empty pool.
        """
        provider_filter = _provider_filter_for_cli(cli_agent)
        if self.team.is_model_group_pool():
            return sorted(
                {
                    item.model
                    for item in self.team.list_cli_models(provider_filter=provider_filter)
                    if item.model
                }
            )
        return sorted({entry.model_name for entry in self.compatible_pool_entries(cli_agent)})

    def _preferred_current_model(self, cli_agent: str, entries: list["ModelPoolEntry"]) -> str | None:
        """Return the current model when the pool can allocate it compatibly."""
        # In a model group the current model is the logical "*" entry, not a
        # concrete model an external CLI can run; the fallback must be one of
        # the real deployments instead.
        if self.team.is_model_group_pool():
            return None
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

    def _model_name_failure(
        self,
        *,
        cli_agent: str,
        model_name: str,
        compatible: list[str],
        cause: str,
    ) -> str:
        """Explain a rejected ``model_name`` and point at the built-in catalog.

        A model the CLI offers on its own login (``"sonnet"``) is a natural
        value to put in ``model_name``, but that field only resolves team
        model pool endpoints — so the redirect belongs in the error itself.
        """
        config = self.team.external_cli_config(cli_agent)
        builtin_names = [item.name for item in config.builtin_models] if config is not None else []
        if model_name in builtin_names:
            return (
                f"model_name '{model_name}' is a built-in model of cli_agent '{cli_agent}', not a team model pool "
                "endpoint; pass it as 'builtin_model' instead (optionally with 'effort')"
            )
        message = f"model_name '{model_name}' {cause} for cli_agent '{cli_agent}'"
        if compatible:
            message += f"; the team model pool offers: {', '.join(compatible)}"
        else:
            message += "; the team model pool has no compatible model"
        if builtin_names:
            message += f". Built-in models of this cli_agent, via 'builtin_model': {', '.join(builtin_names)}"
        return message

    def _resolve_builtin_model(
        self,
        builtin_model: Any,
        effort: Any,
        *,
        cli_agent: str,
        model_name: str | None,
    ) -> tuple["MemberBuiltinModel | None", str]:
        """Validate the optional built-in model choice.

        MCP clients do not validate against the schema, so arguments the gate
        removed are rejected here instead of being silently ignored.
        """
        builtin_name = _optional_text(builtin_model)
        effort = _optional_text(effort)
        if builtin_name is None and effort is None:
            return None, ""
        if not self.team.builtin_models_enabled():
            return None, "builtin_model / effort are unavailable: no CLI kind declares builtin_models"
        if builtin_name is None:
            return None, "'effort' requires 'builtin_model'"
        if model_name:
            return None, (
                "'builtin_model' and 'model_name' are mutually exclusive: a built-in model runs on the CLI's "
                "own login, 'model_name' on a team model pool endpoint"
            )
        return self.team.resolve_builtin_model(cli_agent, builtin_name, effort)


def external_cli_option_properties(
    team: TeamBackend,
    t: Translator,
    *,
    builtin_model_description: str,
) -> dict[str, Any]:
    """Properties of an ``external_cli`` object (``spawn_teammate`` / ``spawn_expert`` / scheduled
    ``create_task``'s inline member).

    ``builtin_model`` / ``effort`` exist only when some declared CLI kind
    offers a built-in model catalog (``builtin_models_enabled()``).
    """
    properties: dict[str, Any] = {
        "fallback_model_name": {
            "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
            "description": (
                f"{t('spawn_teammate', 'external_cli.fallback_model_name')}\n\n"
                f"{external_cli_model_catalog_context(team)}"
            ),
        },
    }
    if team.builtin_models_enabled():
        properties["builtin_model"] = {
            "type": "string",
            "description": f"{builtin_model_description}\n\n{_builtin_model_catalog(team)}",
        }
        properties["effort"] = {"type": "string", "description": t("spawn_teammate", "external_cli.effort")}
    return properties


async def register_external_cli_member(
    team: TeamBackend,
    *,
    tool_name: str,
    allocate_model_config: Callable[..., "Allocation | None"] | None,
    cli_agent: str,
    member_name: str,
    display_name: str,
    desc: str,
    prompt: str,
    model_name: str | None,
    fallback_model_name: Any = None,
    builtin_model: Any = None,
    effort: Any = None,
    member_config: "ExternalCliAgentSpec | None" = None,
    agent_template: "MemberAgentTemplate | None" = None,
    lifetime: str | None = None,
) -> "MemberOpResult":
    """Register one external CLI member row (UNSTARTED): declared-kind check, model rules, registration.

    Shared by ``spawn_teammate`` with ``cli_agent``, the external path of ``spawn_expert``
    and scheduled ``create_task``'s inline member, so the same inputs always
    reach the same row or the same error (only ``tool_name`` differs).
    """
    from openjiuwen.agent_teams.schema.team import MemberOpResult

    err = check_external_cli_declared(team, cli_agent)
    if err:
        return MemberOpResult.fail(err)
    models, err = ExternalCliModelRules(team, allocate_model_config).resolve(
        cli_agent,
        model_name=model_name,
        fallback_model_name=fallback_model_name,
        builtin_model=builtin_model,
        effort=effort,
        tool_name=tool_name,
    )
    if models is None:
        return MemberOpResult.fail(err)
    return await team.spawn_external_cli_agent(
        member_name=member_name,
        display_name=display_name,
        cli_agent=cli_agent,
        desc=desc,
        prompt=prompt,
        model_name=model_name,
        allocation=models.allocation,
        fallback_allocation=models.fallback_allocation,
        builtin_model=models.builtin_model,
        member_config=member_config,
        agent_template=agent_template,
        lifetime=lifetime,
    )


# ========== Member Management ==========


class _SpawnToolBase(TeamTool, ABC):
    """Shared scaffolding for the spawn tools.

    Each concrete subclass owns exactly one kind of member — an AI teammate
    (local or on an external CLI), a human, or a bridge: it declares its own
    flat ``input_params`` schema and implements one ``invoke``. The
    cross-cutting concerns every
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

    def render_for_llm(self, output: ToolOutput) -> str:
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


async def register_teammate(
    team: TeamBackend,
    *,
    member_name: str,
    display_name: str,
    desc: str,
    prompt: str | None,
    allocation: "Allocation | None",
    isolation: str | None = None,
    permissions_override: dict[str, str] | None = None,
    lifetime: str | None = None,
    agent_template: "MemberAgentTemplate | None" = None,
) -> "MemberOpResult":
    """Register one ordinary LLM teammate row (UNSTARTED) — the single place that
    knows how a teammate's card and mode are derived.

    Used by ``spawn_teammate``, by ``create_task``'s inline ``member``
    (F_118) and by ``spawn_expert`` (with ``agent_template``), so they can
    never drift on card id, mode or options.
    """
    from openjiuwen.agent_teams.schema.status import MemberMode
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    agent_card = AgentCard(
        id=f"{team.team_name}_{member_name}",
        name=display_name,
        description=desc,
    )
    return await team.spawn_member(
        member_name=member_name,
        display_name=display_name,
        agent_card=agent_card,
        desc=desc,
        prompt=prompt,
        mode=MemberMode(team.teammate_mode.value),
        allocation=allocation,
        isolation=isolation,
        permissions_override=permissions_override,
        lifetime=lifetime,
        agent_template=agent_template,
    )


class SpawnTeammateTool(_SpawnToolBase):
    """Spawn an AI teammate: a local LLM (``role_type='teammate'``) or, with
    ``cli_agent``, a third-party CLI agent (``role_type='external_cli'``).

    Both are the same kind of member to the leader — only the runtime
    differs — so one tool registers them. Each optional capability is gated
    per attribute, schema and description slot off the same signal:

    - context inheritance (``fork`` / ``fork_source`` / ``fork_mode``):
      ``fork_enabled`` (``TeamAgentSpec.enable_fork``);
    - running on an external CLI (``cli_agent`` / ``external_cli``):
      ``team.external_cli_kinds()`` is non-empty;
    - the CLI's built-in models (``external_cli.builtin_model`` / ``effort``):
      ``team.builtin_models_enabled()``.

    The shape matches scheduled ``create_task``'s inline ``member`` object.
    """

    #: Fork properties and the description slot that documents them. Schema
    #: and prose are gated together — the model must never read about an
    #: argument it has no way to pass.
    _FORK_PARAMS = ("fork", "fork_source", "fork_mode")
    _FORK_SLOTS = frozenset({"fork_usage", "fork_param_rows"})
    #: Properties that only a local teammate can take.
    _LOCAL_ONLY_PARAMS = ("isolation", "permissions", *_FORK_PARAMS)
    _EXTERNAL_CLI_SLOTS = frozenset({"external_cli_usage"})
    _BUILTIN_MODEL_SLOTS = frozenset({"builtin_model_param_rows", "builtin_model_usage"})

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
        external_cli_enabled = bool(team.external_cli_kinds())
        builtin_models_enabled = external_cli_enabled and team.builtin_models_enabled()
        omit_slots = frozenset().union(
            () if fork_enabled else self._FORK_SLOTS,
            () if external_cli_enabled else self._EXTERNAL_CLI_SLOTS,
            () if builtin_models_enabled else self._BUILTIN_MODEL_SLOTS,
        )
        super().__init__(team, t, "spawn_teammate", omit_slots=omit_slots or None)
        self._allocate_model_config = model_config_allocator
        self._fork_enabled = fork_enabled
        self._external_cli_enabled = external_cli_enabled
        model_description = model_name_description(team, t)
        if external_cli_enabled:
            model_description = f"{model_description}\n\n{t('spawn_teammate', 'model_name_external_cli')}"
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
                "description": model_description,
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
        if external_cli_enabled:
            properties.update({
                "cli_agent": {"type": "string", "description": t("spawn_teammate", "cli_agent")},
                "external_cli": {
                    "type": "object",
                    "description": t("spawn_teammate", "external_cli"),
                    "properties": external_cli_option_properties(
                        team, t, builtin_model_description=t("spawn_teammate", "external_cli.builtin_model")
                    ),
                },
            })
        self.card.input_params = {
            "type": "object",
            "properties": properties,
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        err = validate_member_name(inputs.get("member_name"))
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
        err = self._check_external_cli_args(inputs)
        if err:
            return self._fail(err)
        cli_agent = _optional_text(inputs.get("cli_agent"))
        if cli_agent is not None:
            return await self._spawn_external_cli(inputs, cli_agent)

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        desc = inputs.get("desc", "")
        allocation = (
            self._allocate_model_config(inputs.get("model_name")) if self._allocate_model_config else None
        )
        err = check_model_selectable(self.team, inputs.get("model_name"), allocation)
        if err:
            return self._fail(err)
        result = await register_teammate(
            self.team,
            member_name=member_name,
            display_name=display_name,
            desc=desc,
            prompt=inputs.get("prompt"),
            allocation=allocation,
            isolation=inputs.get("isolation"),
            permissions_override=inputs.get("permissions"),
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

    def _check_external_cli_args(self, inputs: dict[str, Any]) -> str | None:
        """Reject ``cli_agent`` / ``external_cli`` arguments this call cannot take, before any write.

        Same rules as scheduled ``create_task``'s inline ``member``.
        """
        cli_agent = _optional_text(inputs.get("cli_agent"))
        external_args = inputs.get("external_cli")
        if (cli_agent or external_args is not None) and not self._external_cli_enabled:
            return "cli_agent / external_cli are unavailable: this team declares no external CLI kinds"
        if external_args is not None and not isinstance(external_args, dict):
            return "'external_cli' must be an object"
        if external_args and not cli_agent:
            return "'external_cli' applies only to an external CLI member: set 'cli_agent', or omit it"
        if cli_agent:
            local_only = [key for key in self._LOCAL_ONLY_PARAMS if inputs.get(key) is not None]
            if local_only:
                return (
                    f"{', '.join(local_only)} cannot be set with cli_agent '{cli_agent}': they apply only to a "
                    "local teammate; omit them, or omit 'cli_agent'"
                )
        return None

    async def _spawn_external_cli(self, inputs: dict[str, Any], cli_agent: str) -> ToolOutput:
        """Register the teammate as a third-party CLI member (``role_type='external_cli'``)."""
        external_args = inputs.get("external_cli") or {}
        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        result = await register_external_cli_member(
            self.team,
            tool_name="spawn_teammate",
            allocate_model_config=self._allocate_model_config,
            cli_agent=cli_agent,
            member_name=member_name,
            display_name=display_name,
            desc=inputs.get("desc") or "",
            prompt=inputs.get("prompt") or "",
            model_name=inputs.get("model_name"),
            fallback_model_name=external_args.get("fallback_model_name"),
            builtin_model=external_args.get("builtin_model"),
            effort=external_args.get("effort"),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="external_cli",
            cli_agent=cli_agent,
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

    def render_for_llm(self, output: ToolOutput) -> str:
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

    def render_for_llm(self, output: ToolOutput) -> str:
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
        err = validate_member_name(inputs.get("member_name"))
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


class SpawnPassiveHumanTool(_SpawnToolBase):
    """Spawn a passive human member (``role_type='passive_human'``).

    A passive human has no avatar — no harness, no LLM, no prompt — just
    a READY roster identity plus a message-bus address. The controlling
    human speaks through the interact channel (messages and tool-call
    passthrough), so the member can be assigned tasks like any human
    collaborator. Schema omits ``model_name`` / ``prompt`` for the same
    reason as ``SpawnHumanAgentTool``; the HITT check below is a
    defensive backstop (the tool is not wired when HITT is disabled).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_passive_human")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_passive_human", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_passive_human", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_passive_human", "desc")},
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        if getattr(self.team, "conversation_mode", "team") != "group_chat":
            return self._fail("spawn_passive_human requires a group_chat runtime")

        err = validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        if not self.team.hitt_enabled():
            return self._fail(
                "Cannot spawn passive human: HITT capability is disabled "
                "(enable_hitt=False on TeamAgentSpec or build_team). "
                "Enable HITT in the team spec or use spawn_teammate instead."
            )

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        result = await self.team.spawn_passive_human(
            member_name=member_name,
            display_name=display_name,
            desc=inputs.get("desc", ""),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="passive_human",
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

        err = validate_member_name(inputs.get("member_name"))
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


class ShutdownMemberTool(TeamTool):
    """Shutdown a team member.

    The lifetime / DORMANT note is a gated capability slot: only the
    scheduled variant of ``create_task`` can declare a member with a
    ``lifetime``, so under autonomous dispatch the prose about it is dropped
    (F_118 / B_04) — the model must never read about a path it cannot take.
    """

    #: Description slot documenting lifetime-driven terminal states.
    _LIFETIME_SLOT = "shutdown_member_lifetime"

    def __init__(self, team: TeamBackend, t: Translator, *, dispatch_mode: str = "autonomous"):
        """Build the shutdown_member tool.

        Args:
            team: Backend the shutdown request goes through.
            t: Locale-bound translator.
            dispatch_mode: How tasks reach members. ``"scheduled"`` keeps the
                lifetime note; ``"autonomous"`` omits it.
        """
        lifetime_enabled = dispatch_mode == "scheduled"
        super().__init__(
            ToolCard(
                id="team.shutdown_member",
                name="shutdown_member",
                description=t("shutdown_member", omit=None if lifetime_enabled else frozenset({self._LIFETIME_SLOT})),
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

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to shutdown member"
        return f"Member shutdown: member_name={output.data['member_name']}"


class SetMemberModelTool(TeamTool):
    """Switch an external-CLI member's built-in model and/or reasoning effort.

    Only wired when some declared CLI kind offers ``builtin_models``. The
    choice is persisted, so it also survives restarts; a running member
    switches before its next turn.
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.set_member_model",
                name="set_member_model",
                description=t("set_member_model"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {"type": "string", "description": t("set_member_model", "member_name")},
                "model": {
                    "type": "string",
                    "description": f"{t('set_member_model', 'model')}\n\n{_builtin_model_catalog(team)}",
                },
                "effort": {"type": "string", "description": t("set_member_model", "effort")},
            },
            "required": ["member_name"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        member_name = _optional_text(inputs.get("member_name"))
        if member_name is None:
            return ToolOutput(success=False, error="'member_name' is required")
        try:
            result = await self.team.set_member_model(
                member_name,
                model=_optional_text(inputs.get("model")),
                effort=_optional_text(inputs.get("effort")),
            )
        except Exception as e:
            team_logger.error("set_member_model failed for {}: {}", member_name, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "model": result.model,
                "effort": result.effort,
                "applied_live": result.applied_live,
            },
            error=None if result.ok else result.reason,
        )

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to set member model"
        d = output.data
        timing = "applies before its next turn" if d["applied_live"] else "applies when the member next starts"
        return f"Member model set: member_name={d['member_name']}, model={d['model']}, effort={d['effort']} ({timing})"


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

    def render_for_llm(self, output: ToolOutput) -> str:
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

    def render_for_llm(self, output: ToolOutput) -> str:
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

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list members"
        members = output.data["members"]
        if not members:
            return "No members"
        lines = [
            f"member_name={m['member_name']} display_name={m['display_name']} status={m['status']}" for m in members
        ]
        return "\n".join(lines)
