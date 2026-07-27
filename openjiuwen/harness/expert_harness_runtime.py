# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExpertHarness hot apply / unapply on a live DeepAgent.

Resolve lives in ``resources/expert_harness_parts`` (Parts only, no live agent).
This module binds / unbinds those Parts and records ``ResourceRef``s for unload.
Kept outside ``resources/`` to avoid a resources → DeepAgent dependency.
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails import SkillUseRail
from openjiuwen.harness.rails.subagent import SubagentRail
from openjiuwen.harness.resources.expert_harness_parts import (
    ExpertHarnessParts,
    ResolvedFileSection,
    ResolvedSkill,
    ResourceKind,
    ResourceRef,
)
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig


async def apply_expert_harness_hot(
    agent: DeepAgent,
    parts: ExpertHarnessParts,
) -> list[ResourceRef]:
    """Bind resolved ExpertHarness parts onto a live DeepAgent.

    Idempotent bind / unload bookkeeping (hard-coded):
    - same-name / same-class resource already present → skip (no ref)
    - new resource → bind and record a ref

    On failure, unapplies refs already bound in this call, then re-raises.
    """
    refs: list[ResourceRef] = []
    try:
        for tool in parts.tools:
            ref = _hot_bind_tool(agent, tool)
            if ref is not None:
                refs.append(ref)
        for mcp in parts.mcps:
            ref = await _hot_bind_mcp(agent, mcp)
            if ref is not None:
                refs.append(ref)
        for rail in parts.rails:
            ref = await _hot_bind_rail(agent, rail)
            if ref is not None:
                refs.append(ref)
        for section in parts.prompt_sections:
            ref = _hot_bind_prompt_section(agent, section)
            if ref is not None:
                refs.append(ref)
        for section in parts.file_sections:
            ref = _hot_bind_file_section(agent, section)
            if ref is not None:
                refs.append(ref)
        for skill in parts.skills:
            ref = await _hot_bind_skill(agent, skill)
            if ref is not None:
                refs.append(ref)
        for subagent in parts.subagents:
            ref = _hot_bind_subagent(agent, subagent)
            if ref is not None:
                refs.append(ref)
        if parts.subagents:
            ensure_ref = await _ensure_subagent_rail_ready(agent)
            if ensure_ref is not None:
                refs.append(ensure_ref)
    except Exception:
        await unapply_expert_harness_hot(agent, refs)
        raise
    return refs


async def unapply_expert_harness_hot(
    agent: DeepAgent,
    refs: list[ResourceRef],
) -> list[str]:
    """Undo bindings recorded in ``refs`` (reverse order)."""
    unloaded: list[str] = []
    for ref in reversed(refs):
        await _hot_unbind(agent, ref)
        unloaded.append(f"{ref.kind.value}:{ref.identity}")
    return unloaded


def _require_deep_config(agent: DeepAgent) -> DeepAgentConfig:
    config = agent.deep_config
    if config is None:
        raise ValueError("DeepAgentConfig is required. Call configure() first.")
    return config


def _hot_tool_identity(agent: DeepAgent, card: ToolCard) -> str:
    if not card.stateless and agent.card.id:
        return AbilityManager.qualify_tool_id(card, agent.card.id)
    return card.id or card.name


def _hot_existing_tool_card(agent: DeepAgent, card: ToolCard) -> ToolCard | None:
    existing = agent.ability_manager.get(card.name)
    if not isinstance(existing, ToolCard):
        return None
    incoming_id = _hot_tool_identity(agent, card)
    return existing if (existing.id or existing.name) == incoming_id else None


def _hot_bind_tool(agent: DeepAgent, resource: Tool | ToolCard) -> ResourceRef | None:
    if isinstance(resource, list):
        raise TypeError("Tool part must be one instance, got a list")
    if not isinstance(resource, (Tool, ToolCard)):
        raise TypeError(f"Unsupported tool resource: {type(resource).__name__}")
    card = resource.card if isinstance(resource, Tool) else resource
    identity = _hot_tool_identity(agent, card)
    if _hot_existing_tool_card(agent, card) is not None:
        return None
    if agent.ability_manager.get(card.name) is not None:
        agent.ability_manager.remove(card.name)
    if isinstance(resource, Tool):
        agent.ability_manager.add_ability(card, resource)
    else:
        agent.ability_manager.add(card)
    config = _require_deep_config(agent)
    tools = list(config.tools or [])
    tools = [c for c in tools if (c.id or c.name) != identity and c.name != card.name]
    tools.append(card)
    config.tools = tools or None
    return ResourceRef(
        kind=ResourceKind.TOOL,
        identity=identity,
        extra={"ability_names": [card.name]},
    )


async def _hot_bind_mcp(agent: DeepAgent, config: McpServerConfig) -> ResourceRef | None:
    deep_config = _require_deep_config(agent)
    agent_local = next(
        (item for item in deep_config.mcps or [] if item.server_id == config.server_id),
        None,
    )
    if agent_local is not None:
        return None

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
        existing_ability = agent.ability_manager.get(config.server_name)
        if existing_ability is not None:
            agent.ability_manager.remove(config.server_name)
        agent.ability_manager.add(config)

        mcps = list(deep_config.mcps or [])
        mcps = [item for item in mcps if item.server_id != config.server_id]
        mcps.append(config)
        deep_config.mcps = mcps or None
    except Exception:
        await _hot_unbind(agent, ref)
        raise
    return ref


async def _hot_bind_rail(agent: DeepAgent, rail: AgentRail) -> ResourceRef | None:
    identity = type(rail).__name__
    if agent.find_rail_by_name(identity) is not None:
        return None
    await agent.register_rail(rail)
    return ResourceRef(kind=ResourceKind.RAIL, identity=identity)


def _hot_bind_prompt_section(agent: DeepAgent, section: PromptSection) -> ResourceRef | None:
    if agent.system_prompt_builder is None:
        raise ValueError("Cannot bind prompt section before DeepAgent.configure()")
    if agent.system_prompt_builder.get_section(section.name) is not None:
        return None
    agent.system_prompt_builder.add_section(section)
    agent.apply_prompt_builder_to_react_agent()
    return ResourceRef(kind=ResourceKind.PROMPT_SECTION, identity=section.name)


def _hot_bind_file_section(agent: DeepAgent, section: ResolvedFileSection) -> ResourceRef | None:
    previous_exists = section.target.is_file()
    previous_content = section.target.read_text(encoding="utf-8") if previous_exists else None
    if previous_exists and previous_content == section.content:
        return None
    section.target.parent.mkdir(parents=True, exist_ok=True)
    section.target.write_text(section.content, encoding="utf-8")
    return ResourceRef(
        kind=ResourceKind.FILE_SECTION,
        identity=section.filename,
        extra={
            "path": str(section.target),
            "previous_content": previous_content,
            "previous_exists": previous_exists,
        },
    )


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


def _hot_skill_mounted(agent: DeepAgent, skill: ResolvedSkill) -> bool:
    config = _require_deep_config(agent)
    directory = str(Path(skill.directory).expanduser().resolve())
    skill_path = Path(directory)
    mount_root = str(skill_path.parent) if (skill_path / "SKILL.md").is_file() else directory
    mounted = {str(Path(item).expanduser().resolve()) for item in _skill_values(config.skills)}
    return mount_root in mounted


async def _hot_bind_skill(agent: DeepAgent, skill: ResolvedSkill) -> ResourceRef | None:
    if _hot_skill_mounted(agent, skill):
        return None
    roots, enabled_names = _skill_paths_to_rail_mounts([skill.directory])
    if skill.enabled_skills:
        for name in skill.enabled_skills:
            if name not in enabled_names:
                enabled_names.append(name)
    config = _require_deep_config(agent)
    rails = agent.find_rails_by_type((SkillUseRail,))
    target = rails[0] if rails else None
    created_rail = False
    previous_dirs: list[str] | None = None
    previous_mode = None
    previous_enabled: set[str] | None = None
    previous_enabled_was_none = False

    try:
        if target is None:
            target = SkillUseRail(
                skills_dir=roots,
                skill_mode=skill.mode,
                enabled_skills=enabled_names or None,
            )
            await agent.register_rail(target)
            created_rail = True
            await target.reload_skills()
        else:
            current_dirs = _skill_values(target.skills_dir)
            previous_dirs = [
                str(Path(item).expanduser().resolve())
                for item in current_dirs
                if str(item).strip()
            ]
            previous_mode = target.skill_mode
            if target.enabled_skills is None:
                previous_enabled_was_none = True
            else:
                previous_enabled = set(target.enabled_skills)

            target.skills_dir = [*roots, *(item for item in previous_dirs if item not in roots)]
            target.skill_mode = skill.mode
            if enabled_names:
                if target.enabled_skills:
                    target.enabled_skills.update(enabled_names)
                else:
                    target.enabled_skills = set(enabled_names)
            target.enable_cache = False
            target.clear_skills()
            if agent.is_pending_rail(target):
                agent.remove_pending_rail(target)
                await agent.register_rail(target)
            await target.reload_skills()

        # Commit config only after rail side effects succeed.
        skills = list(_skill_values(config.skills))
        for root in roots:
            if root not in skills:
                skills.append(root)
        config.skills = skills or None
    except Exception:
        if created_rail and target is not None:
            if agent.is_registered_rail(target):
                await agent.unregister_rail(target)
            elif agent.is_pending_rail(target):
                agent.remove_pending_rail(target)
        elif target is not None and previous_dirs is not None:
            target.skills_dir = previous_dirs
            target.skill_mode = previous_mode
            target.enabled_skills = None if previous_enabled_was_none else previous_enabled
            target.enable_cache = False
            target.clear_skills()
            await target.reload_skills()
        raise

    return ResourceRef(
        kind=ResourceKind.SKILL,
        identity=skill.directory,
        extra={"mode": skill.mode, "directory": skill.directory},
    )


def _subagent_key(subagent: SubAgentConfig) -> str:
    card = subagent.agent_card
    return str(card.name or card.id)


def _hot_bind_subagent(agent: DeepAgent, subagent: SubAgentConfig) -> ResourceRef | None:
    identity = _subagent_key(subagent)
    config = _require_deep_config(agent)
    existing = next(
        (item for item in config.subagents or [] if _subagent_key(item) == identity),
        None,
    )
    if existing is not None:
        return None
    subagents = list(config.subagents or [])
    subagents.append(subagent)
    config.subagents = subagents or None
    agent.ability_manager.add(subagent.agent_card)
    return ResourceRef(kind=ResourceKind.SUBAGENT, identity=identity)


async def _ensure_subagent_rail_ready(agent: DeepAgent) -> ResourceRef | None:
    """After subagent binds: ensure SubagentRail exists and has task/session tools.

    Covers two hot-load races:
    - SubagentRail was registered earlier while ``config.subagents`` was still
      empty, so ``init`` skipped and ``tools`` stayed ``None``.
    - Package declared subagents without a ``core.subagent`` rail; mount one.
    """
    config = _require_deep_config(agent)
    if not config.subagents:
        return None

    rails = agent.find_rails_by_type((SubagentRail,))
    rail = rails[0] if rails else None
    created = False
    if rail is None:
        rail = SubagentRail(enable_async_subagent=bool(config.enable_async_subagent))
        await agent.register_rail(rail)
        created = True

    if not getattr(rail, "tools", None):
        rail.init(agent)
    else:
        rail.refresh_available_agents(agent)

    if created:
        return ResourceRef(kind=ResourceKind.RAIL, identity=type(rail).__name__)
    return None


async def _hot_unbind(agent: DeepAgent, ref: ResourceRef) -> None:
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
        if agent.system_prompt_builder is not None:
            agent.system_prompt_builder.remove_section(ref.identity)
            agent.apply_prompt_builder_to_react_agent()
        return
    if ref.kind == ResourceKind.FILE_SECTION:
        target = Path(ref.extra.get("path") or ref.identity)
        previous_exists = bool(ref.extra.get("previous_exists"))
        previous_content = ref.extra.get("previous_content")
        if previous_exists:
            target.write_text(previous_content or "", encoding="utf-8")
        elif target.is_file():
            target.unlink()
        return
    if ref.kind == ResourceKind.SKILL:
        directory = str(ref.extra.get("directory") or ref.identity)
        path = Path(directory).expanduser().resolve()
        root = str(path.parent) if (path / "SKILL.md").is_file() else str(path)
        config = _require_deep_config(agent)
        if config.skills:
            config.skills = [item for item in _skill_values(config.skills) if item != root] or None
        for rail in agent.find_rails_by_type((SkillUseRail,)):
            current = _skill_values(rail.skills_dir)
            if root not in {str(Path(item).expanduser().resolve()) for item in current}:
                continue
            rail.skills_dir = [item for item in current if str(Path(item).expanduser().resolve()) != root]
            if not rail.skills_dir:
                rail.clear_skills()
            if agent.is_registered_rail(rail):
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
        return
    raise ValueError(f"Unsupported hot unbind: kind={ref.kind.value}")
