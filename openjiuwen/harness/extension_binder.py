# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind / unbind ExtensionParts on a live DeepAgent; record ResourceRefs."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails import SkillUseRail, SubagentRail
from openjiuwen.harness.resources.extension_resolver import (
    ExtensionParts,
    ResolvedPromptSection,
    ResolvedSkill,
    ResourceKind,
    ResourceRef,
)
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig


async def apply_extension_hot(
    agent: DeepAgent,
    parts: ExtensionParts,
) -> list[ResourceRef]:
    """Bind resolved Plugin / AgentTemplate parts onto a live DeepAgent.

    Bind bookkeeping (hard-coded):
    - same-name / same-identity resource already present -> raise (batch fails)
    - AgentTemplate identity prompt section -> replace + snapshot (see module doc)
    - new resource -> bind and record a ref

    On failure, unapplies refs already bound in this call, then re-raises.
    """
    refs: list[ResourceRef] = []
    try:
        for tool in parts.tools:
            refs.append(_bind_tool(agent, tool))
        for mcp in parts.mcps:
            refs.append(await _bind_mcp(agent, mcp))
        for rail in parts.rails:
            refs.append(await _bind_rail(agent, rail))
        for resolved_section in parts.prompt_sections:
            refs.append(_bind_prompt_section(agent, resolved_section))
        for skill in parts.skills:
            refs.append(await _bind_skill(agent, skill))
        for subagent in parts.subagents:
            refs.append(_bind_subagent(agent, subagent))
        if parts.subagents:
            ensure_ref = await _ensure_subagent_rail_ready(agent)
            if ensure_ref is not None:
                refs.append(ensure_ref)
    except Exception:
        await unapply_extension_hot(agent, refs)
        raise
    return refs


async def unapply_extension_hot(
    agent: DeepAgent,
    refs: list[ResourceRef],
) -> list[str]:
    """Undo bindings recorded in ``refs`` (reverse order)."""
    unloaded: list[str] = []
    for ref in reversed(refs):
        await _unbind(agent, ref)
        unloaded.append(f"{ref.kind.value}:{ref.identity}")
    return unloaded


def _require_deep_config(agent: DeepAgent) -> DeepAgentConfig:
    config = agent.deep_config
    if config is None:
        raise ValueError("DeepAgentConfig is required. Call configure() first.")
    return config


def _tool_identity(agent: DeepAgent, card: ToolCard) -> str:
    if not card.stateless and agent.card.id:
        return AbilityManager.qualify_tool_id(card, agent.card.id)
    return card.id or card.name


def _bind_tool(agent: DeepAgent, resource: Tool | ToolCard) -> ResourceRef:
    if not isinstance(resource, (Tool, ToolCard)):
        raise TypeError(f"Unsupported tool resource: {type(resource).__name__}")
    card = resource.card if isinstance(resource, Tool) else resource
    if agent.ability_manager.get(card.name) is not None:
        raise ValueError(f"Tool already bound: {card.name}")
    identity = _tool_identity(agent, card)
    if isinstance(resource, Tool):
        agent.ability_manager.add_ability(card, resource)
    else:
        agent.ability_manager.add(card)
    config = _require_deep_config(agent)
    tools = list(config.tools or [])
    tools.append(card)
    config.tools = tools
    return ResourceRef(
        kind=ResourceKind.TOOL,
        identity=identity,
        extra={"ability_names": [card.name]},
    )


async def _bind_mcp(agent: DeepAgent, config: McpServerConfig) -> ResourceRef:
    deep_config = _require_deep_config(agent)
    if any(item.server_id == config.server_id for item in deep_config.mcps or []):
        raise ValueError(f"MCP server already bound: {config.server_id}")
    if agent.ability_manager.get(config.server_name) is not None:
        raise ValueError(f"MCP ability already bound: {config.server_name}")

    # Side effects first; commit deep_config last. On failure, unbind self so the
    # outer apply rollback does not need a ref for this incomplete bind.
    ref = ResourceRef(
        kind=ResourceKind.MCP,
        identity=config.server_id,
        extra={"server_name": config.server_name},
    )
    try:
        existing_config = Runner.resource_mgr.get_mcp_server_config(config.server_id)
        if existing_config is None:
            result = await Runner.resource_mgr.add_mcp_server(config, tag=agent.card.id)
            if result.is_err():
                raise result.msg()
        else:
            tag_result = Runner.resource_mgr.add_resource_tag(config.server_id, agent.card.id)
            if tag_result.is_err():
                raise tag_result.msg()
            for tool_id in Runner.resource_mgr.get_mcp_tool_ids(config.server_id):
                tool_tag_result = Runner.resource_mgr.add_resource_tag(tool_id, agent.card.id)
                if tool_tag_result.is_err():
                    raise tool_tag_result.msg()
        agent.ability_manager.add(config)

        mcps = list(deep_config.mcps or [])
        mcps.append(config)
        deep_config.mcps = mcps
    except Exception:
        await _unbind(agent, ref)
        raise
    return ref


async def _bind_rail(agent: DeepAgent, rail: AgentRail) -> ResourceRef:
    identity = type(rail).__name__
    if agent.find_rail_by_name(identity) is not None:
        raise ValueError(f"Rail already bound: {identity}")
    await agent.register_rail(rail)
    return ResourceRef(kind=ResourceKind.RAIL, identity=identity)


def _bind_prompt_section(agent: DeepAgent, resolved: ResolvedPromptSection) -> ResourceRef:
    if agent.system_prompt_builder is None:
        raise ValueError("Cannot bind prompt section before DeepAgent.configure()")
    section = resolved.section
    existing = agent.system_prompt_builder.get_section(section.name)
    if existing is not None and not resolved.replace_existing:
        raise ValueError(f"Prompt section already bound: {section.name}")

    extra: dict = {}
    if resolved.replace_existing:
        extra["previous_exists"] = existing is not None
        if existing is not None:
            extra["previous_snapshot"] = {
                "name": existing.name,
                "content": dict(existing.content),
                "priority": existing.priority,
            }
    agent.system_prompt_builder.add_section(section)
    agent.apply_prompt_builder_to_react_agent()
    return ResourceRef(kind=ResourceKind.PROMPT_SECTION, identity=section.name, extra=extra)


def _skill_paths_to_rail_mounts(skill_paths: list[str]) -> tuple[list[str], list[str]]:
    roots: list[str] = []
    enabled_names: list[str] = []
    for raw_path in skill_paths:
        path = Path(raw_path).expanduser().resolve()
        if (path / "SKILL.md").is_file():
            root = str(path.parent)
            name = path.name
        else:
            root = str(path)
            name = ""
        if root not in roots:
            roots.append(root)
        if name and name not in enabled_names:
            enabled_names.append(name)
    return roots, enabled_names


def _skill_values(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _is_skill_leaf_dir(directory: Path) -> bool:
    return (directory / "SKILL.md").is_file()


async def _bind_skill(agent: DeepAgent, skill: ResolvedSkill) -> ResourceRef:
    """Load one skill onto the agent's existing ``SkillUseRail``.

    A ``SkillUseRail`` is not auto-created here: the agent is expected to
    already carry one (see ``factory._make_skill_rail`` / ``enable_skill_discovery``).
    Binding only adds this skill's root to the rail and reloads it; unbinding
    (``_unbind`` below) removes that root again.
    """
    config = _require_deep_config(agent)
    directory = Path(skill.directory).expanduser().resolve()
    is_leaf = _is_skill_leaf_dir(directory)
    mount_root = str(directory.parent) if is_leaf else str(directory)

    rails = agent.find_rails_by_type((SkillUseRail,))
    if not rails:
        raise ValueError("No SkillUseRail registered on the agent; cannot bind a skill.")
    target = rails[0]

    roots, _ = _skill_paths_to_rail_mounts([skill.directory])

    previous_dirs = _skill_values(target.skills_dir)
    current_dirs = {str(Path(item).expanduser().resolve()) for item in previous_dirs}

    if mount_root in current_dirs and not is_leaf:
        raise ValueError(f"Skill already bound: {mount_root}")

    previous_enabled = None if target.enabled_skills is None else set(target.enabled_skills)

    # Append after host roots. SkillUseRail keeps the first loaded name, so
    # workspace/skills wins over a same-named package skill. Do not write leaf
    # names into enabled_skills: that global allow-list would hide host skills.
    target.skills_dir = [*previous_dirs, *(root for root in roots if root not in current_dirs)]
    target.enable_cache = False
    target.clear_skills()
    try:
        await target.reload_skills()
    except Exception:
        target.skills_dir = previous_dirs
        target.enabled_skills = previous_enabled
        target.enable_cache = False
        target.clear_skills()
        raise

    # Commit config only after rail side effects succeed.
    skills = list(_skill_values(config.skills))
    for root in roots:
        if root not in skills:
            skills.append(root)
    config.skills = skills

    return ResourceRef(
        kind=ResourceKind.SKILL,
        identity=skill.directory,
        extra={"mode": skill.mode, "directory": skill.directory},
    )


def _subagent_key(subagent: SubAgentConfig) -> str:
    card = subagent.agent_card
    return str(card.name or card.id)


def _bind_subagent(agent: DeepAgent, subagent: SubAgentConfig) -> ResourceRef:
    identity = _subagent_key(subagent)
    config = _require_deep_config(agent)
    if any(_subagent_key(item) == identity for item in config.subagents or []):
        raise ValueError(f"Subagent already bound: {identity}")
    name = subagent.agent_card.name
    if name and agent.ability_manager.get(name) is not None:
        raise ValueError(f"Subagent ability already bound: {identity}")
    subagents = list(config.subagents or [])
    subagents.append(subagent)
    config.subagents = subagents
    agent.ability_manager.add(subagent.agent_card)
    return ResourceRef(kind=ResourceKind.SUBAGENT, identity=identity)


async def _ensure_subagent_rail_ready(agent: DeepAgent) -> ResourceRef | None:
    """After subagent binds: ensure SubagentRail exists and has task/session tools.

    Not a same-name conflict scenario: shared bootstrap infrastructure is
    idempotently reused/created regardless of which Extension load triggered it.
    """
    config = _require_deep_config(agent)
    if not config.subagents:
        return None

    rails = agent.find_rails_by_type((SubagentRail,))
    rail = rails[0] if rails else None
    created = False
    if rail is None:
        rail = SubagentRail(
            enable_async_subagent=bool(config.enable_async_subagent),
            enable_subagent_runtime=bool(config.enable_subagent_runtime),
        )
        await agent.register_rail(rail)
        created = True

    if not getattr(rail, "tools", None):
        rail.init(agent)
    else:
        rail.refresh_available_agents(agent)

    if created:
        return ResourceRef(kind=ResourceKind.RAIL, identity=type(rail).__name__)
    return None


async def _unbind(agent: DeepAgent, ref: ResourceRef) -> None:
    if ref.kind == ResourceKind.TOOL:
        names = ref.extra.get("ability_names") or []
        tool_name = names[0] if names else ref.identity
        if tool_name:
            agent.ability_manager.remove_ability(tool_name)
            config = _require_deep_config(agent)
            if config.tools:
                config.tools = [card for card in config.tools if card.name != tool_name] or None
        return
    if ref.kind == ResourceKind.MCP:
        server_id = ref.identity
        server_name = ref.extra.get("server_name")
        config = _require_deep_config(agent)
        if config.mcps:
            config.mcps = [item for item in config.mcps if item.server_id != server_id] or None
        if server_name and agent.ability_manager.get(server_name) is not None:
            agent.ability_manager.remove(server_name)
        if Runner.resource_mgr.get_mcp_server_config(server_id) is None:
            return
        tag_result = Runner.resource_mgr.remove_resource_tag(
            server_id,
            agent.card.id,
            skip_if_tag_not_exists=True,
        )
        if tag_result.is_err():
            raise tag_result.msg()
        tags = Runner.resource_mgr.get_resource_tag(server_id) or []
        if not tags:
            result = await Runner.resource_mgr.remove_mcp_server(
                server_id,
                skip_if_tag_not_exists=True,
                ignore_exception=True,
            )
            if isinstance(result, list):
                failed = [item for item in result if item.is_err()]
                if failed:
                    raise failed[0].msg()
            elif result.is_err():
                raise result.msg()
        return
    if ref.kind == ResourceKind.RAIL:
        rail = agent.find_rail_by_name(ref.identity)
        if rail is not None and agent.is_registered_rail(rail):
            await agent.unregister_rail(rail)
        elif rail is not None:
            agent.remove_pending_rail(rail)
        return
    if ref.kind == ResourceKind.PROMPT_SECTION:
        if agent.system_prompt_builder is None:
            return
        previous_exists = ref.extra.get("previous_exists")
        if previous_exists:
            snapshot = ref.extra.get("previous_snapshot") or {}
            agent.system_prompt_builder.add_section(
                PromptSection(
                    name=snapshot.get("name", ref.identity),
                    content=dict(snapshot.get("content") or {}),
                    priority=snapshot.get("priority", 100),
                )
            )
        else:
            agent.system_prompt_builder.remove_section(ref.identity)
        agent.apply_prompt_builder_to_react_agent()
        return
    if ref.kind == ResourceKind.SKILL:
        directory = str(ref.extra.get("directory") or ref.identity)
        path = Path(directory).expanduser().resolve()
        is_leaf = _is_skill_leaf_dir(path)
        root = str(path.parent) if is_leaf else str(path)
        config = _require_deep_config(agent)
        for rail in agent.find_rails_by_type((SkillUseRail,)):
            current = _skill_values(rail.skills_dir)
            mounted = {str(Path(item).expanduser().resolve()) for item in current}
            if root not in mounted:
                continue

            # Sibling leaf unbind: drop only this skill name while others remain.
            if is_leaf and rail.enabled_skills is not None:
                rail.enabled_skills.discard(path.name)
                if rail.enabled_skills:
                    if agent.is_registered_rail(rail):
                        rail.enable_cache = False
                        rail.clear_skills()
                        await rail.reload_skills()
                    break

            if config.skills:
                config.skills = [
                    item
                    for item in _skill_values(config.skills)
                    if str(Path(item).expanduser().resolve()) != root
                ] or None
            rail.skills_dir = [
                item for item in current if str(Path(item).expanduser().resolve()) != root
            ]
            if not rail.skills_dir:
                # Nothing left to mount; reload_skills() requires a non-empty
                # skills_dir, so just drop the cached skills instead.
                rail.clear_skills()
            elif agent.is_registered_rail(rail):
                rail.enable_cache = False
                rail.clear_skills()
                await rail.reload_skills()
            break
        return
    if ref.kind == ResourceKind.SUBAGENT:
        config = _require_deep_config(agent)
        subagent = next(
            (item for item in config.subagents or [] if _subagent_key(item) == ref.identity),
            None,
        )
        if subagent is None:
            return
        if config.subagents:
            config.subagents = [
                item for item in config.subagents if _subagent_key(item) != ref.identity
            ] or None
        name = subagent.agent_card.name
        if name and agent.ability_manager.get(name) is not None:
            agent.ability_manager.remove(name)
        for rail in agent.find_rails_by_type((SubagentRail,)):
            rail.refresh_available_agents(agent)
        return
    raise ValueError(f"Unsupported hot unbind: kind={ref.kind.value}")


__all__ = [
    "apply_extension_hot",
    "unapply_extension_hot",
]
