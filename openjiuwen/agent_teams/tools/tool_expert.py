# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader tools over the installed experts: browse them, spawn one as a teammate.

* ``view_experts`` — single experts (``agent_template`` packages);
* ``view_expert_groups`` — AgentGroups, with their member roster on ``get``;
* ``spawn_expert`` — register a teammate that runs as an expert template.

The host only locates packages (``ExpertCatalog``); every manifest is parsed
here through agent-core: browsing reads cards only, spawning loads the full
package. Package reads run in a worker thread, never on the event loop. The
tools are wired only when the host injected a catalog.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.experts.agent_group import (
    LEADER_MEMBER,
    AgentGroupCard,
    AgentGroupMemberCard,
    aload_agent_group_package,
    read_agent_group_card,
)
from openjiuwen.agent_teams.experts.bound_group import BoundAgentGroup
from openjiuwen.agent_teams.experts.catalog import ExpertCatalog, ExpertRef, PackageLocation
from openjiuwen.agent_teams.experts.template_member import render_template_persona, template_snapshot
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_member import (
    _optional_text,
    _SpawnToolBase,
    check_model_selectable,
    external_cli_option_properties,
    model_name_description,
    register_external_cli_member,
    register_teammate,
    validate_member_name,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.resources import (
    AgentTemplateCard,
    aload_agent_template_package,
    read_agent_template_card,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.tools.member_options import MemberAgentTemplate
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
    from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec

_MANIFEST = "manifest.json"


def _first_line(text: str) -> str:
    """First non-empty line, for one-row listings."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _localized_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(text) for text in value.values()]
    return []


def _matches(query: str | None, *values: Any) -> bool:
    """Case-insensitive substring match over names, display names and descriptions."""
    if not query:
        return True
    needle = query.strip().lower()
    return any(needle in text.lower() for value in values for text in _localized_values(value))


def _runtime_label(card: AgentTemplateCard) -> str:
    """``native`` or the external runtime provider name."""
    if card.runtime == "native":
        return "native"
    return card.runtime_provider or "external"


async def _read_cards(
    locations: list[PackageLocation],
    reader: Callable[[PackageLocation], Any],
    *,
    kind: str,
) -> list[tuple[PackageLocation, Any]]:
    """Read every card concurrently; a package whose card cannot be read is skipped with a warning."""
    results = await asyncio.gather(*(reader(location) for location in locations), return_exceptions=True)
    cards: list[tuple[PackageLocation, Any]] = []
    for location, result in zip(locations, results):
        if isinstance(result, BaseException):
            team_logger.warning(
                "[expert] skip unreadable {} {!r} at {}: {}", kind, location.name, location.path, result
            )
            continue
        cards.append((location, result))
    return cards


async def _expert_card(location: PackageLocation) -> AgentTemplateCard:
    return await read_agent_template_card(location.path / _MANIFEST)


async def _group_card(location: PackageLocation) -> AgentGroupCard:
    return await read_agent_group_card(location.path)


def _expert_detail(name: str, card: AgentTemplateCard, language: str, *, source: str | None) -> dict[str, Any]:
    """Every card field of one expert, rendered in ``language``."""
    detail: dict[str, Any] = {
        "name": name,
        "display_name": card.display_label(language) or name,
        "description": card.description,
        "display_description": card.display_text(language),
        "category": card.category,
        "tags": list(card.tag_labels(language)),
        "version": card.version,
        "runtime": _runtime_label(card),
        "skills": list(card.skills),
    }
    if source is not None:
        detail["source"] = source
    return detail


def _render_expert_detail(detail: dict[str, Any], *, header: str) -> str:
    lines = [header]
    for key in ("name", "display_name", "source", "category", "version", "runtime"):
        if detail.get(key):
            lines.append(f"{key}: {detail[key]}")
    if detail.get("tags"):
        lines.append(f"tags: {', '.join(detail['tags'])}")
    if detail.get("skills"):
        lines.append(f"skills: {', '.join(detail['skills'])}")
    lines.append(f"description: {detail.get('description') or ''}")
    if detail.get("display_description") and detail["display_description"] != detail.get("description"):
        lines.append(f"display_description: {detail['display_description']}")
    return "\n".join(lines)


def _is_spawnable(member: AgentGroupMemberCard, declared_cli_kinds: frozenset[str]) -> bool:
    """Whether ``spawn_expert`` can run this member: native, or an external runtime the team declared."""
    from openjiuwen.agent_teams.external.manifest import cli_agent_for_provider

    if member.is_leader:
        return False
    if member.card.runtime == "native":
        return True
    return cli_agent_for_provider(member.card.runtime_provider) in declared_cli_kinds


class ViewExpertsTool(TeamTool):
    """List / inspect the single experts (``agent_template`` packages) installed on this machine."""

    def __init__(self, catalog: ExpertCatalog, t: Translator, *, language: str):
        super().__init__(ToolCard(id="team.view_experts", name="view_experts", description=t("view_experts")))
        self._catalog = catalog
        self._language = language
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get"],
                    "default": "list",
                    "description": t("view_experts", "action"),
                },
                "query": {"type": "string", "description": t("view_experts", "query")},
                "name": {"type": "string", "description": t("view_experts", "name")},
            },
            "required": [],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action") or "list"
        try:
            if action == "list":
                return await self._list(_optional_text(inputs.get("query")))
            if action == "get":
                return await self._get(_optional_text(inputs.get("name")))
        except Exception as exc:
            team_logger.error("view_experts failed: {}", exc)
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        return ToolOutput(success=False, error=f"Unknown action '{action}'; expected 'list' or 'get'")

    async def _list(self, query: str | None) -> ToolOutput:
        cards = await _read_cards(await self._catalog.list_expert_packages(), _expert_card, kind="expert")
        language = self._language
        items = [
            {
                "name": location.name,
                "display_name": card.display_label(language) or location.name,
                "category": card.category,
                "runtime": _runtime_label(card),
                "description": _first_line(card.display_text(language)),
            }
            for location, card in cards
            if _matches(query, location.name, card.display_name, card.description, card.display_description)
        ]
        return ToolOutput(success=True, data={"action": "list", "query": query, "experts": items})

    async def _get(self, name: str | None) -> ToolOutput:
        if name is None:
            return ToolOutput(success=False, error="'name' is required for action 'get'")
        try:
            location = await self._catalog.locate_expert(name)
        except ValueError:
            installed = [item.name for item in await self._catalog.list_expert_packages()]
            return ToolOutput(
                success=False,
                error=f"Expert '{name}' is not installed (installed: {', '.join(installed) or '<none>'})",
            )
        try:
            card = await _expert_card(location)
        except ValueError as exc:
            team_logger.warning("[expert] unreadable expert {!r} at {}: {}", name, location.path, exc)
            return ToolOutput(success=False, error=f"Expert '{name}' cannot be read: {exc}")
        detail = _expert_detail(location.name, card, self._language, source=location.source)
        return ToolOutput(success=True, data={"action": "get", "expert": detail})

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to view experts"
        data = output.data
        if data["action"] == "get":
            return _render_expert_detail(data["expert"], header="Expert:")
        experts = data["experts"]
        if not experts:
            return f"No installed expert matches '{data['query']}'" if data["query"] else "No experts installed"
        return "\n".join(
            f"name={item['name']} display_name={item['display_name']} category={item['category'] or '-'} "
            f"runtime={item['runtime']} — {item['description']}"
            for item in experts
        )


class ViewExpertGroupsTool(TeamTool):
    """List / inspect the AgentGroups installed on this machine, with their member rosters."""

    def __init__(
        self,
        catalog: ExpertCatalog,
        t: Translator,
        *,
        language: str,
        bound_group: BoundAgentGroup | None = None,
        declared_cli_kinds: frozenset[str] = frozenset(),
    ):
        super().__init__(
            ToolCard(id="team.view_expert_groups", name="view_expert_groups", description=t("view_expert_groups"))
        )
        self._catalog = catalog
        self._language = language
        self._bound_group = bound_group
        self._declared_cli_kinds = declared_cli_kinds
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get"],
                    "default": "list",
                    "description": t("view_expert_groups", "action"),
                },
                "query": {"type": "string", "description": t("view_expert_groups", "query")},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("view_expert_groups", "capabilities"),
                },
                "name": {"type": "string", "description": t("view_expert_groups", "name")},
                "member": {"type": "string", "description": t("view_expert_groups", "member")},
            },
            "required": [],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action") or "list"
        try:
            if action == "list":
                capabilities = inputs.get("capabilities") or []
                if not isinstance(capabilities, list):
                    return ToolOutput(success=False, error="'capabilities' must be a list of capability names")
                return await self._list(_optional_text(inputs.get("query")), [str(item) for item in capabilities])
            if action == "get":
                return await self._get(_optional_text(inputs.get("name")), _optional_text(inputs.get("member")))
        except Exception as exc:
            team_logger.error("view_expert_groups failed: {}", exc)
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        return ToolOutput(success=False, error=f"Unknown action '{action}'; expected 'list' or 'get'")

    def _bound_name(self) -> str | None:
        return self._bound_group.name if self._bound_group is not None else None

    async def _list(self, query: str | None, capabilities: list[str]) -> ToolOutput:
        cards = await _read_cards(await self._catalog.list_group_packages(), _group_card, kind="expert group")
        language = self._language
        items = []
        for location, card in cards:
            if not _matches(query, location.name, card.display_name, card.description, card.display_description):
                continue
            if not set(capabilities) <= set(card.capabilities):
                continue
            items.append(
                {
                    "name": location.name,
                    "display_name": card.display_label(language) or location.name,
                    "members": sum(1 for member in card.members if not member.is_leader),
                    "capabilities": list(card.capabilities),
                    "description": _first_line(card.display_text(language)),
                    "bound": location.name == self._bound_name(),
                }
            )
        return ToolOutput(
            success=True,
            data={"action": "list", "query": query, "capabilities": capabilities, "groups": items},
        )

    async def _get(self, name: str | None, member: str | None) -> ToolOutput:
        if name is None:
            return ToolOutput(success=False, error="'name' is required for action 'get'")
        try:
            location = await self._catalog.locate_group(name)
        except ValueError:
            installed = [item.name for item in await self._catalog.list_group_packages()]
            return ToolOutput(
                success=False,
                error=f"Expert group '{name}' is not installed (installed: {', '.join(installed) or '<none>'})",
            )
        try:
            card = await _group_card(location)
        except ValueError as exc:
            team_logger.warning("[expert] unreadable expert group {!r} at {}: {}", name, location.path, exc)
            return ToolOutput(success=False, error=f"Expert group '{name}' cannot be read: {exc}")
        language = self._language
        bound = location.name == self._bound_name()
        if member is not None:
            entry = card.member(member)
            if entry is None:
                names = ", ".join(item.name for item in card.members)
                return ToolOutput(
                    success=False,
                    error=f"Member '{member}' is not in expert group '{name}' (members: {names})",
                )
            detail = _expert_detail(entry.name, entry.card, language, source=None)
            detail.update(
                {
                    "group": location.name,
                    "display_name": entry.display_label(language),
                    "spawnable": _is_spawnable(entry, self._declared_cli_kinds),
                    "predefined": bound and self._is_predefined(entry.name),
                }
            )
            return ToolOutput(success=True, data={"action": "get", "member": detail})
        group = {
            "name": location.name,
            "display_name": card.display_label(language) or location.name,
            "description": card.display_text(language),
            "capabilities": list(card.capabilities),
            "version": card.version,
            "source": location.source,
            "bound": bound,
            "members": [
                {
                    "name": entry.name,
                    "display_name": entry.display_label(language),
                    "description": _first_line(entry.card.display_text(language)),
                    "runtime": _runtime_label(entry.card),
                    "spawnable": _is_spawnable(entry, self._declared_cli_kinds),
                    "predefined": bound and self._is_predefined(entry.name),
                }
                for entry in card.members
            ],
        }
        return ToolOutput(success=True, data={"action": "get", "group": group})

    def _is_predefined(self, member: str) -> bool:
        return self._bound_group is not None and self._bound_group.member(member) is not None

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to view expert groups"
        data = output.data
        if data["action"] == "get" and "member" in data:
            member = data["member"]
            text = _render_expert_detail(member, header=f"Expert group member {member['group']}/{member['name']}:")
            text += f"\nspawnable: {str(member['spawnable']).lower()}"
            if member["predefined"]:
                text += (
                    f"\nThis team already has the predefined member '{member['name']}'; "
                    "spawn_expert creates another instance of the same expert under a new member_name."
                )
            return text
        if data["action"] == "get":
            group = data["group"]
            lines = [f"Expert group: {group['name']} ({group['display_name']})"]
            if group["bound"]:
                lines.append("bound: true — this team was assembled from this group")
            if group["capabilities"]:
                lines.append(f"capabilities: {', '.join(group['capabilities'])}")
            if group["version"]:
                lines.append(f"version: {group['version']}")
            lines.append(f"description: {group['description']}")
            lines.append("members:")
            for member in group["members"]:
                note = " predefined=true" if member["predefined"] else ""
                lines.append(
                    f"- name={member['name']} display_name={member['display_name']} runtime={member['runtime']} "
                    f"spawnable={str(member['spawnable']).lower()}{note} — {member['description']}"
                )
            return "\n".join(lines)
        groups = data["groups"]
        if not groups:
            return "No installed expert group matches" if data["query"] or data["capabilities"] else (
                "No expert groups installed"
            )
        return "\n".join(
            f"name={item['name']} display_name={item['display_name']} members={item['members']} "
            f"capabilities={','.join(item['capabilities']) or '-'}{' bound=true' if item['bound'] else ''} "
            f"— {item['description']}"
            for item in groups
        )


@dataclass(frozen=True)
class ExpertTemplate:
    """What a spawn needs from an expert template, whatever its source."""

    ref: ExpertRef
    version: str
    display_name: str
    desc: str
    persona: str
    snapshot: dict[str, Any] | None
    external_cli: "ExternalCliAgentSpec | None"


@dataclass(frozen=True)
class _ExpertMemberIdentity:
    """Name and texts of the member one ``spawn_expert`` call registers."""

    member_name: str
    display_name: str
    desc: str
    prompt: str


class ExpertUnavailable(Exception):
    """The requested expert template cannot be resolved; the message is model-facing."""


class ExpertTemplateResolver:
    """Resolves an expert template for a new member: ``spawn_expert`` and scheduled ``create_task``'s inline member.

    The template comes from the team's own bound AgentGroup when ``group``
    names it (no disk access, same snapshot and persona as the predefined
    member), otherwise from the catalog + core loaders. Personas render in
    ``persona_language`` (the base spec's, as ``compose_template_member``
    does); display names in the leader's ``language``.
    """

    def __init__(
        self,
        *,
        catalog: ExpertCatalog,
        language: str,
        bound_group: BoundAgentGroup | None = None,
        persona_workspace: str | None = None,
        persona_language: str | None = None,
    ):
        self._catalog = catalog
        self._language = language
        self._bound_group = bound_group
        self._persona_workspace = persona_workspace
        self._persona_language = persona_language

    async def resolve(self, expert: str, group: str | None) -> ExpertTemplate:
        """Resolve one expert template; ``ExpertUnavailable`` (model-facing message) when it cannot be."""
        if group is not None and expert == LEADER_MEMBER:
            raise ExpertUnavailable(f"The leader of expert group '{group}' cannot be spawned as a teammate")
        bound = self._bound_group
        if group is not None and bound is not None and group == bound.name:
            member = bound.member(expert)
            if member is None:
                raise ExpertUnavailable(
                    f"'{expert}' is not a spawnable member of the bound expert group '{group}' "
                    f"(members: {', '.join(bound.member_names()) or '<none>'})"
                )
            return ExpertTemplate(
                ref=ExpertRef(name=expert, group=group),
                version="",
                display_name=member.display_name,
                desc=member.desc,
                persona=member.persona,
                snapshot=member.snapshot,
                external_cli=member.external_cli,
            )
        if group is not None:
            return await self._group_member_template(expert, group)
        return await self._single_expert_template(expert)

    async def _single_expert_template(self, expert: str) -> ExpertTemplate:
        try:
            location = await self._catalog.locate_expert(expert)
        except ValueError as exc:
            installed = [item.name for item in await self._catalog.list_expert_packages()]
            team_logger.warning("[expert] spawn_expert cannot locate expert {!r}: {}", expert, exc)
            raise ExpertUnavailable(
                f"Expert '{expert}' is not installed (installed: {', '.join(installed) or '<none>'})"
            ) from exc
        manifest = location.path / _MANIFEST
        try:
            template = await aload_agent_template_package(manifest)
            card = await read_agent_template_card(manifest)
        except (OSError, ValueError) as exc:
            team_logger.warning("[expert] spawn_expert cannot load expert {!r} at {}: {}", expert, location.path, exc)
            raise ExpertUnavailable(f"Expert '{expert}' cannot be loaded: {exc}") from exc
        return self._from_package(ExpertRef(name=expert), template, card)

    async def _group_member_template(self, expert: str, group: str) -> ExpertTemplate:
        try:
            location = await self._catalog.locate_group(group)
        except ValueError as exc:
            installed = [item.name for item in await self._catalog.list_group_packages()]
            team_logger.warning("[expert] spawn_expert cannot locate expert group {!r}: {}", group, exc)
            raise ExpertUnavailable(
                f"Expert group '{group}' is not installed (installed: {', '.join(installed) or '<none>'})"
            ) from exc
        try:
            package = await aload_agent_group_package(location.path)
        except (OSError, ValueError) as exc:
            team_logger.warning(
                "[expert] spawn_expert cannot load expert group {!r} at {}: {}", group, location.path, exc
            )
            raise ExpertUnavailable(f"Expert group '{group}' cannot be loaded: {exc}") from exc
        template = package.templates.get(expert)
        if template is None or expert == LEADER_MEMBER:
            members = [name for name in package.templates if name != LEADER_MEMBER]
            raise ExpertUnavailable(
                f"'{expert}' is not a member of expert group '{group}' (members: {', '.join(members)})"
            )
        try:
            card = await read_agent_template_card(package.package_dir / "agents" / expert / _MANIFEST)
        except ValueError as exc:
            raise ExpertUnavailable(f"Expert '{group}/{expert}' cannot be loaded: {exc}") from exc
        return self._from_package(ExpertRef(name=expert, group=group), template, card)

    def _from_package(self, ref: ExpertRef, template: "AgentTemplateSpec", card: AgentTemplateCard) -> ExpertTemplate:
        from openjiuwen.agent_teams.external.manifest import external_cli_agent_spec_from_template

        try:
            external_cli = external_cli_agent_spec_from_template(template)
        except ValueError as exc:
            raise ExpertUnavailable(f"Expert '{ref.label()}' has an unsupported runtime: {exc}") from exc
        return ExpertTemplate(
            ref=ref,
            version=card.version,
            display_name=card.display_label(self._language) or template.agent_card.name or ref.name,
            desc=card.description or template.agent_card.description,
            persona=render_template_persona(
                template, language=self._persona_language, workspace=self._persona_workspace
            ),
            snapshot=template_snapshot(template) if external_cli is None else None,
            external_cli=external_cli,
        )

    async def unique_display_name(
        self,
        team: TeamBackend,
        display_name: str,
        member_name: str,
        *,
        also_taken: frozenset[str] = frozenset(),
    ) -> str:
        """Template display name; suffixed with the member name when a teammate already uses it.

        ``also_taken`` adds display names of members being registered in the same call.
        """
        taken = {entry.display_name for entry in await team.list_member_roster()} | also_taken
        if display_name not in taken:
            return display_name
        suffix = f"（{member_name}）" if self._language == "cn" else f" ({member_name})"
        return f"{display_name}{suffix}"



def expert_member_texts(template: ExpertTemplate, *, desc: Any, prompt: Any) -> tuple[str, str]:
    """Member ``desc`` / ``prompt``: the template's own, then what the leader adds (never replaced)."""
    return (
        "\n\n".join(part for part in (template.desc, _optional_text(desc)) if part),
        "\n\n".join(part for part in (template.persona, _optional_text(prompt)) if part),
    )


def expert_member_record(template: ExpertTemplate) -> "MemberAgentTemplate":
    """The ``options.agent_template`` record of a member spawned from ``template``."""
    from openjiuwen.agent_teams.tools.member_options import MemberAgentTemplate

    return MemberAgentTemplate(ref=template.ref.to_dict(), version=template.version, snapshot=template.snapshot)


def expert_member_cli_config(team: TeamBackend, template: ExpertTemplate) -> "ExternalCliAgentSpec | None":
    """Launch config of an external-CLI member spawned from ``template``: the template's over the declared kind's.

    ``None`` when the kind is not declared; registration then rejects it.
    """
    from openjiuwen.agent_teams.external.manifest import merge_external_cli_config

    declared = team.external_cli_config(template.external_cli.cli_agent)
    if declared is None:
        return None
    return merge_external_cli_config(declared, template.external_cli)


class SpawnExpertTool(_SpawnToolBase):
    """Register a teammate that runs as an installed expert template (``role_type`` teammate / external_cli).

    The template comes from the team's own bound AgentGroup when ``group``
    names it (no disk access, same snapshot and persona as the predefined
    member), otherwise from the catalog + core loaders. The member row stores
    the persona in ``prompt`` and the capability snapshot (or the external
    CLI launch config) in ``options.agent_template``, so it is rebuilt from
    the row alone. ``desc`` / ``prompt`` only add to the template's own.
    """

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        catalog: ExpertCatalog,
        language: str,
        bound_group: BoundAgentGroup | None = None,
        persona_workspace: str | None = None,
        persona_language: str | None = None,
        model_config_allocator: Callable[..., "Allocation | None"] | None = None,
        resolver: "ExpertTemplateResolver | None" = None,
    ):
        super().__init__(team, t, "spawn_expert")
        self._resolver = resolver or ExpertTemplateResolver(
            catalog=catalog,
            language=language,
            bound_group=bound_group,
            persona_workspace=persona_workspace,
            persona_language=persona_language,
        )
        self._allocate_model_config = model_config_allocator
        external_cli_properties = external_cli_option_properties(
            team, t, builtin_model_description=t("spawn_expert", "external_cli.builtin_model")
        )
        model_description = t("spawn_expert", "model_name")
        if team.is_model_group_pool():
            # Inside a model group a native expert takes a route tag; list them.
            model_description = f"{model_description}\n\n{model_name_description(team, t)}"
        self.card.input_params = {
            "type": "object",
            "properties": {
                "expert": {"type": "string", "description": t("spawn_expert", "expert")},
                "group": {"type": "string", "description": t("spawn_expert", "group")},
                "member_name": {"type": "string", "description": t("spawn_expert", "member_name")},
                "display_name": {"type": "string", "description": t("spawn_expert", "display_name")},
                "desc": {"type": "string", "description": t("spawn_expert", "desc")},
                "prompt": {"type": "string", "description": t("spawn_expert", "prompt")},
                "model_name": {"type": "string", "description": model_description},
                "external_cli": {
                    "type": "object",
                    "description": t("spawn_expert", "external_cli"),
                    "properties": external_cli_properties,
                },
                "isolation": {
                    "type": "string",
                    "enum": ["worktree"],
                    "description": t("spawn_expert", "isolation"),
                },
                "permissions": {"type": "object", "description": t("spawn_expert", "permissions")},
            },
            "required": ["expert", "member_name"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            return await self._invoke(inputs)
        except Exception as exc:
            team_logger.error("spawn_expert failed: {}", exc)
            return self._fail(f"Internal error: {exc}")

    async def _invoke(self, inputs: dict[str, Any]) -> ToolOutput:
        err = validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)
        expert = _optional_text(inputs.get("expert"))
        if expert is None:
            return self._fail("'expert' is required: an installed expert package name, or a member name with 'group'")
        group = _optional_text(inputs.get("group"))
        external_args = inputs.get("external_cli")
        if external_args is not None and not isinstance(external_args, dict):
            return self._fail("'external_cli' must be an object")
        try:
            template = await self._resolver.resolve(expert, group)
        except ExpertUnavailable as exc:
            return self._fail(str(exc))
        if template.external_cli is None and external_args:
            return self._fail(
                f"Expert '{template.ref.label()}' runs natively; 'external_cli' applies only to experts with an "
                "external runtime — omit it"
            )

        member_name = inputs["member_name"]
        display_name = _optional_text(inputs.get("display_name")) or await self._resolver.unique_display_name(
            self.team, template.display_name, member_name
        )
        desc, prompt = expert_member_texts(template, desc=inputs.get("desc"), prompt=inputs.get("prompt"))
        identity = _ExpertMemberIdentity(member_name=member_name, display_name=display_name, desc=desc, prompt=prompt)
        if template.external_cli is not None:
            return await self._spawn_external(template, inputs, external_args or {}, identity)
        return await self._spawn_native(template, inputs, identity)

    async def _spawn_native(
        self,
        template: ExpertTemplate,
        inputs: dict[str, Any],
        identity: _ExpertMemberIdentity,
    ) -> ToolOutput:
        model_name = inputs.get("model_name")
        allocation = self._allocate_model_config(model_name) if self._allocate_model_config else None
        err = check_model_selectable(self.team, model_name, allocation)
        if err:
            return self._fail(err)
        result = await register_teammate(
            self.team,
            member_name=identity.member_name,
            display_name=identity.display_name,
            desc=identity.desc,
            prompt=identity.prompt,
            allocation=allocation,
            isolation=inputs.get("isolation"),
            permissions_override=inputs.get("permissions"),
            agent_template=expert_member_record(template),
        )
        return self._registered(result, template, identity.member_name, identity.display_name, role_type="teammate")

    async def _spawn_external(
        self,
        template: ExpertTemplate,
        inputs: dict[str, Any],
        external_args: dict[str, Any],
        identity: _ExpertMemberIdentity,
    ) -> ToolOutput:
        unsupported = [key for key in ("isolation", "permissions") if inputs.get(key) is not None]
        if unsupported:
            return self._fail(
                f"{', '.join(unsupported)} cannot be set for expert '{template.ref.label()}': it runs on an "
                "external CLI runtime"
            )
        cli_agent = template.external_cli.cli_agent
        result = await register_external_cli_member(
            self.team,
            tool_name="spawn_expert",
            allocate_model_config=self._allocate_model_config,
            cli_agent=cli_agent,
            member_name=identity.member_name,
            display_name=identity.display_name,
            desc=identity.desc,
            prompt=identity.prompt,
            model_name=inputs.get("model_name"),
            fallback_model_name=external_args.get("fallback_model_name"),
            builtin_model=external_args.get("builtin_model"),
            effort=external_args.get("effort"),
            member_config=expert_member_cli_config(self.team, template),
            agent_template=expert_member_record(template),
        )
        return self._registered(
            result,
            template,
            identity.member_name,
            identity.display_name,
            role_type="external_cli",
            cli_agent=cli_agent,
        )

    def _registered(
        self,
        result: Any,
        template: ExpertTemplate,
        member_name: str,
        display_name: str,
        **extra: Any,
    ) -> ToolOutput:
        if result.ok:
            team_logger.info(
                "[expert] spawn_expert registered member={} ref={} version={}",
                member_name,
                template.ref.label(),
                template.version or "-",
            )
        else:
            team_logger.warning(
                "[expert] spawn_expert rejected member={} ref={}: {}", member_name, template.ref.label(), result.reason
            )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            expert=template.ref.label(),
            **extra,
        )

    def render_for_llm(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to spawn expert"
        d = output.data
        cli_agent = d.get("cli_agent")
        suffix = f" cli_agent={cli_agent}" if cli_agent else ""
        return (
            f"Expert member registered: member_name={d['member_name']} display_name={d['display_name']} "
            f"expert={d['expert']} role={d['role_type']}{suffix}"
        )


__all__ = [
    "ExpertTemplate",
    "ExpertTemplateResolver",
    "ExpertUnavailable",
    "SpawnExpertTool",
    "ViewExpertGroupsTool",
    "ViewExpertsTool",
    "expert_member_cli_config",
    "expert_member_record",
    "expert_member_texts",
]
