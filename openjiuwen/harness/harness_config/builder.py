# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HarnessConfigBuilder: convert ResolvedHarnessConfig → configured DeepAgent."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.harness.harness_config.loader import ResolvedFileSection, ResolvedHarnessConfig
from openjiuwen.harness.workspace.workspace import Workspace

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Builtin tool group registry
# ---------------------------------------------------------------------------

# Each entry: (dotted module path, class names, needs_sys_operation).
_BUILTIN_TOOL_GROUPS: Dict[str, Tuple[str, List[str], bool]] = {
    "filesystem": (
        "openjiuwen.harness.tools.filesystem",
        ["ReadFileTool", "WriteFileTool", "EditFileTool", "ListDirTool", "GlobTool", "GrepTool"],
        True,
    ),
    "shell": ("openjiuwen.harness.tools.bash", ["BashTool"], True),
    "code": ("openjiuwen.harness.tools.code", ["CodeTool"], True),
    "web_search": (
        "openjiuwen.harness.tools.web_tools",
        ["WebFreeSearchTool", "WebPaidSearchTool"],
        False,
    ),
    "web_fetch": ("openjiuwen.harness.tools.web_tools", ["WebFetchWebpageTool"], False),
}

_BUILTIN_RAIL_REGISTRY: Dict[str, str] = {
    "task_planning": "openjiuwen.harness.rails.task_planning_rail.TaskPlanningRail",
}

# Inverted registries for yaml generation (class dotted-path → group/name)
_TOOL_DOTTED_TO_GROUP: Dict[str, str] = {
    f"{module_path}.{cls_name}": group
    for group, (module_path, class_names, _needs_op) in _BUILTIN_TOOL_GROUPS.items()
    for cls_name in class_names
}
_RAIL_DOTTED_TO_NAME: Dict[str, str] = {v: k for k, v in _BUILTIN_RAIL_REGISTRY.items()}


def _resolve_builtin_tools(group_name: str, sys_operation: SysOperation) -> List[Any]:
    """Instantiate builtin tools by group name.

    Tools that operate on the host system (filesystem/bash/code) receive the
    shared ``sys_operation`` so they share the same sandbox policy as the agent.
    """
    entry = _BUILTIN_TOOL_GROUPS.get(group_name)
    if entry is None:
        raise ValueError(f"Unknown builtin tool group: '{group_name}'. Valid groups: {sorted(_BUILTIN_TOOL_GROUPS)}")
    module_path, class_names, needs_sys_op = entry
    mod = importlib.import_module(module_path)
    if needs_sys_op:
        return [getattr(mod, cls_name)(sys_operation) for cls_name in class_names]
    return [getattr(mod, cls_name)() for cls_name in class_names]


def _load_dotted_path(dotted: str) -> Any:
    """Load a class given 'module.path.ClassName'."""
    module_path, _, class_name = dotted.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    except (ImportError, AttributeError) as exc:
        raise ImportError(f"Cannot load '{dotted}': {exc}") from exc


def _load_from_entry_point(name: str, group: str) -> Any:
    """Load a class via Python entry_points."""
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group=group):
            if ep.name == name:
                return ep.load()
        raise ImportError(f"Entry point '{name}' not found in group '{group}'. Is the package installed?")
    except (ImportError, ValueError):
        raise
    except Exception as exc:
        raise ImportError(f"Failed to load entry point '{name}' from '{group}': {exc}") from exc


def _resolve_tools(resources_schema: Any, sys_operation: SysOperation) -> List[Any]:
    """Resolve all Tool instances from resources.tools.

    Builtin tool groups that need a ``SysOperation`` receive ``sys_operation``.
    Third-party (``package`` / ``entry_point``) tools are instantiated with no
    positional arguments — their class is responsible for acquiring whatever
    runtime services it needs.
    """
    tools: List[Any] = []
    for spec in resources_schema.tools:
        if spec.type == "builtin":
            names = spec.names or ([spec.name] if spec.name else [])
            for group in names:
                tools.extend(_resolve_builtin_tools(group, sys_operation))
        elif spec.type == "package":
            cls = _load_dotted_path(f"{spec.module}.{spec.class_name}")
            tools.append(cls())
        elif spec.type == "entry_point":
            cls = _load_from_entry_point(spec.name or "", "openjiuwen.tool")
            tools.append(cls())
    return tools


def _create_sys_operation(card: AgentCard) -> SysOperation:
    """Create and register a local ``SysOperation`` keyed by the agent card."""
    sysop_id = f"{card.name}_{card.id}" if card.id else f"{card.name}_harness_config"
    sysop_card = SysOperationCard(
        id=sysop_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
    return Runner.resource_mgr.get_sys_operation(sysop_id)


def _resolve_rails(resources_schema: Any) -> List[Any]:
    """Resolve all Rail instances from resources.rails."""
    rails: List[Any] = []
    for spec in resources_schema.rails:
        if spec.type == "builtin":
            dotted = _BUILTIN_RAIL_REGISTRY.get(spec.name or "")
            if dotted is None:
                raise ValueError(f"Unknown builtin rail: '{spec.name}'. Valid names: {sorted(_BUILTIN_RAIL_REGISTRY)}")
            cls = _load_dotted_path(dotted)
            rails.append(cls())
        elif spec.type == "package":
            cls = _load_dotted_path(f"{spec.module}.{spec.class_name}")
            rails.append(cls())
        elif spec.type == "entry_point":
            cls = _load_from_entry_point(spec.name or "", "openjiuwen.rail")
            rails.append(cls())
    return rails


def _resolve_mcps(resources_schema: Any) -> List[Any]:
    """Convert harness config MCP specs to McpServerConfig objects."""
    from openjiuwen.core.foundation.tool import McpServerConfig

    mcps = []
    for spec in resources_schema.mcps:
        cmd_parts = spec.command.split() if spec.command else []
        all_parts = cmd_parts + list(spec.args)
        server_path = " ".join(all_parts)
        mcps.append(
            McpServerConfig(
                server_name=spec.command or "mcp_server",
                server_path=server_path,
                client_type=spec.type,
                params=dict(spec.env) if spec.env else {},
            )
        )
    return mcps


def _write_file_sections(
    file_sections: List[ResolvedFileSection],
    workspace_root: Path,
    language: str,
) -> None:
    """Write file-backed prompt sections to the workspace directory."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    for fs in file_sections:
        content = fs.content.get(language) or fs.content.get("cn") or fs.content.get("en") or ""
        if not content.strip():
            continue
        (workspace_root / fs.filename).write_text(content, encoding="utf-8")


def _tools_to_yaml_specs(tools: List[Any]) -> List[Dict[str, Any]]:
    """Reverse-map tool instances to YAML ToolResourceSchema dicts."""
    builtin_groups: List[str] = []
    unknown_specs: List[Dict[str, Any]] = []
    for tool in tools:
        cls = type(tool)
        key = f"{cls.__module__}.{cls.__name__}"
        group = _TOOL_DOTTED_TO_GROUP.get(key)
        if group is not None:
            if group not in builtin_groups:
                builtin_groups.append(group)
        else:
            unknown_specs.append({"type": "package", "module": cls.__module__, "class": cls.__name__})
    specs: List[Dict[str, Any]] = []
    if builtin_groups:
        specs.append({"type": "builtin", "names": builtin_groups})
    specs.extend(unknown_specs)
    return specs


def _rails_to_yaml_specs(rails: List[Any]) -> List[Dict[str, Any]]:
    """Reverse-map rail instances to YAML RailResourceSchema dicts."""
    specs: List[Dict[str, Any]] = []
    for rail in rails:
        cls = type(rail)
        dotted = f"{cls.__module__}.{cls.__name__}"
        name = _RAIL_DOTTED_TO_NAME.get(dotted)
        if name is not None:
            specs.append({"type": "builtin", "name": name})
        else:
            specs.append({"type": "package", "module": cls.__module__, "class": cls.__name__})
    return specs


def generate_harness_config_yaml(
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[Union[str, Dict[str, str]]] = None,
    tools: Optional[List[Any]] = None,
    rails: Optional[List[Any]] = None,
    language: str = "cn",
    max_iterations: Optional[int] = None,
    completion_timeout: Optional[float] = None,
    extra_sections: Optional[List[Dict[str, Any]]] = None,
    output_path: Optional[Union[str, Path]] = None,
) -> str:
    """Generate a ``harness_config.yaml`` string from ``create_deep_agent``-style arguments.

    Pass the same values you would pass to ``create_deep_agent()`` and receive
    a ready-to-save YAML string.

    Example::

        yaml_str = generate_harness_config_yaml(
            card=AgentCard(id="my-agent", name="My Agent"),
            system_prompt="你是一个编码助手",
            tools=[bash_tool, read_tool],
            rails=[TaskPlanningRail()],
            language="cn",
            output_path="harness_config.yaml",
        )

    Args:
        card:               AgentCard with id/name/description.
        system_prompt:      System prompt string or ``{lang: text}`` dict.
        tools:              Tool instances (same list passed to ``create_deep_agent``).
        rails:              AgentRail instances.
        language:           Default language (``"cn"`` or ``"en"``).
        max_iterations:     Max ReAct iterations.
        completion_timeout: Timeout in seconds.
        extra_sections:     Additional prompt sections — each a dict with keys
                            ``name``, ``priority``, ``content``.
        output_path:        If given, also write the yaml to this file path.

    Returns:
        YAML string.
    """
    import yaml

    data: Dict[str, Any] = {"schema_version": "harness_config.v0.1"}

    if card is not None:
        if card.id:
            data["id"] = card.id
        if card.name:
            data["name"] = card.name
        if getattr(card, "description", None):
            data["description"] = card.description

    data["language"] = language
    if max_iterations is not None:
        data["max_iterations"] = max_iterations
    if completion_timeout is not None:
        data["completion_timeout"] = completion_timeout

    sections: List[Dict[str, Any]] = []
    if system_prompt is not None:
        if isinstance(system_prompt, str):
            content: Dict[str, str] = {"cn": system_prompt, "en": system_prompt}
        else:
            content = dict(system_prompt)
        sections.append({"name": "identity", "priority": 10, "content": content})
    for sec in extra_sections or []:
        sections.append(dict(sec))
    if sections:
        data["prompts"] = {"sections": sections}

    resources: Dict[str, Any] = {}
    if tools:
        tool_specs = _tools_to_yaml_specs(tools)
        if tool_specs:
            resources["tools"] = tool_specs
    if rails:
        rail_specs = _rails_to_yaml_specs(rails)
        if rail_specs:
            resources["rails"] = rail_specs
    if resources:
        data["resources"] = resources

    yaml_str: str = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    if output_path is not None:
        Path(output_path).write_text(yaml_str, encoding="utf-8")

    return yaml_str


class HarnessConfigBuilder:
    """Convert a ``ResolvedHarnessConfig`` into a configured ``DeepAgent``."""

    @classmethod
    def build(
        cls,
        resolved: ResolvedHarnessConfig,
        model: Model,
        workspace_root: Optional[Union[str, Path]] = None,
    ) -> "DeepAgent":
        """Assemble and return a ``DeepAgent`` from *resolved*.

        Execution order:
          1. Build AgentCard (needed to key the shared SysOperation)
          2. Create shared SysOperation (reused by tools and factory)
          3. Resolve tools (builtin / package / entry_point)
          4. Resolve extra rails from resources.rails
          5. Resolve MCPs
          6. Build Workspace
          7. Call create_deep_agent() (handles SecurityRail, SubagentRail, etc.)
          8. Inject extra_sections into agent.system_prompt_builder
          9. Write file_sections to workspace directory
         10. Add SkillUseRail with resolved absolute paths

        Args:
            resolved:       Output of ``HarnessConfigLoader.load()``.
            model:          Pre-constructed ``Model`` instance for LLM calls.
            workspace_root: Override the workspace root path.

        Raises:
            ValueError:  Unknown builtin name.
            ImportError:     package / entry_point load failure.
            HarnessConfigSubConfigError:   Sub-config build failure.
        """
        from openjiuwen.harness.factory import create_deep_agent
        from openjiuwen.harness.prompts.builder import PromptSection

        config = resolved.config
        resources = config.resources
        language = config.language

        # ── 1. Agent card
        card = AgentCard(
            id=config.id or "",
            name=config.name or "harness_agent",
            description=config.description or "",
        )

        # ── 2. Shared SysOperation
        sys_operation = _create_sys_operation(card)

        # ── 3. Tools
        tools: List[Any] = []
        if resources and resources.tools:
            tools = _resolve_tools(resources, sys_operation)

        # ── 4. Extra rails
        extra_rails: List[Any] = []
        if resources and resources.rails:
            extra_rails = _resolve_rails(resources)

        # ── 5. MCPs
        mcps: List[Any] = []
        if resources and resources.mcps:
            mcps = _resolve_mcps(resources)

        # ── 6. Workspace root
        if workspace_root is not None:
            ws_root = Path(workspace_root).resolve()
        elif config.workspace and config.workspace.root_path:
            ws_root = (resolved.source_path.parent / config.workspace.root_path).resolve()
        else:
            ws_root = resolved.source_path.parent

        workspace = Workspace(root_path=str(ws_root), language=language)

        # ── 7. Create agent via factory. ``completion_timeout`` is not an
        # explicit ``create_deep_agent`` parameter in the current API; the
        # factory forwards unknown kwargs onto ``DeepAgentConfig`` via setattr.
        factory_kwargs: Dict[str, Any] = {}
        if config.completion_timeout is not None:
            factory_kwargs["completion_timeout"] = config.completion_timeout

        agent = create_deep_agent(
            model=model,
            card=card,
            system_prompt=resolved.system_prompt,
            tools=tools or None,
            mcps=mcps or None,
            rails=extra_rails or None,
            workspace=workspace,
            language=language,
            max_iterations=config.max_iterations or 15,
            sys_operation=sys_operation,
            **factory_kwargs,
        )

        # ── 8. Inject extra_sections into system_prompt_builder
        if agent.system_prompt_builder is not None:
            for sec in resolved.extra_sections:
                agent.system_prompt_builder.add_section(
                    PromptSection(
                        name=sec.name,
                        content=sec.content,
                        priority=sec.priority,
                    )
                )

        # ── 9. Write file sections to workspace
        if resolved.file_sections:
            _write_file_sections(resolved.file_sections, ws_root, language)

        # ── 10. Skills → SkillUseRail with resolved absolute paths
        if resources and resources.skills and resources.skills.dirs:
            from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

            skill_dirs = [str((resolved.source_path.parent / d).resolve()) for d in resources.skills.dirs]
            mode = resources.skills.mode or "all"
            agent.add_rail(SkillUseRail(skills_dir=skill_dirs, skill_mode=mode))

        return agent


__all__ = ["HarnessConfigBuilder", "generate_harness_config_yaml"]
