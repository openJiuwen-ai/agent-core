# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory for internal Member Optimizer Agents."""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.rg_binary import resolve_rg_binary
from openjiuwen.harness.tools.filesystem import (
    EditFileTool, GlobTool, GrepTool, ListDirTool, ReadFileTool, WriteFileTool,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.action_groups import (
    filter_action_definitions,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.profiles import (
    ACTION_EXECUTION,
    ACTION_PLANNING,
    MECHANISM_ATTRIBUTION,
    ROLE_ATTRIBUTION,
    VERIFICATION_REPAIR,
    MemberOptimizerAgentProfile,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import (
    with_rsi_output_budget,
    with_rsi_reasoning_policy,
    without_inner_sdk_retries,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    ActionDefinition,
    FailureSignature,
    MechanismType,
    OptimizationSurface,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROMPTS_DIR = _PACKAGE_DIR / "prompts"
_SKILLS_DIR = _PACKAGE_DIR / "skills"
_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


@asynccontextmanager
async def optimizer_agent_resources(agent: Any) -> AsyncIterator[Any]:
    """Release this invocation's native resources without stopping shared Runner."""
    operation = getattr(getattr(agent, "deep_config", None), "sys_operation", None)
    operation_id = getattr(operation, "id", None)
    try:
        yield agent
    finally:
        try:
            cleanup = getattr(agent, "cleanup_task_resources", None)
            if callable(cleanup):
                await cleanup()
        finally:
            manager = getattr(agent, "ability_manager", None)
            if manager is not None:
                manager.teardown_tools()
            if operation_id:
                Runner.resource_mgr.remove_sys_operation(operation_id)


def load_member_optimizer_model(model_config_ref: str) -> Model:
    """Load a standalone member-optimizer model config file and build a Model.

    Expected YAML/JSON shape:

    ``model_client_config`` is required and maps to ModelClientConfig.
    ``model_request_config`` is optional and maps to ModelRequestConfig.
    """
    if not str(model_config_ref or "").strip():
        raise RuntimeError("model_config_ref is required for member optimization")

    path = Path(model_config_ref).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"model_config_ref not found: {model_config_ref}")

    try:
        with open(path, encoding="utf-8") as file:
            if path.suffix.lower() in {".yaml", ".yml"}:
                raw = yaml.safe_load(file) or {}
            else:
                raw = json.load(file)
    except Exception as exc:
        raise RuntimeError(f"failed to load model_config_ref {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"model_config_ref must decode to a mapping: {path}")

    model_data = raw.get("model", raw)
    if not isinstance(model_data, dict):
        raise RuntimeError(f"model_config_ref model block must be a mapping: {path}")

    data = without_inner_sdk_retries(with_rsi_reasoning_policy(with_rsi_output_budget(_expand_env_vars(model_data))))
    client_data = data.get("model_client_config")
    if not isinstance(client_data, dict):
        raise RuntimeError("model_config_ref must contain a mapping field: model_client_config")

    request_data = data.get("model_request_config")
    if request_data is not None and not isinstance(request_data, dict):
        raise RuntimeError("model_request_config must be a mapping when provided")

    try:
        client_config = ModelClientConfig.model_validate(client_data)
        request_config = ModelRequestConfig.model_validate(request_data) if request_data is not None else None
        return BudgetedRsiModel(
            model_client_config=client_config,
            model_config=request_config,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to initialize model from {path}: {exc}") from exc


def create_member_optimizer_agent(
    *,
    profile: MemberOptimizerAgentProfile,
    model_config_ref: str,
    workspace: str | Path,
    prompt_values: dict[str, str] | None = None,
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
    enabled_skills_override: tuple[str, ...] | None = None,
    tools: list[Any] | None = None,
    sys_operation: Any | None = None,
) -> Any:
    """Create a configured DeepAgent for one internal Member Optimizer Agent."""
    model = load_member_optimizer_model(model_config_ref)
    prompt = _render_prompt(profile.prompt_file, prompt_values or {})
    card = AgentCard(
        name=profile.agent_name,
        description=profile.description,
    )
    rails = _build_rails(
        profile=profile,
        agent_skills_dirs=agent_skills_dirs or [],
        extra_rails=extra_rails,
        enabled_skills_override=enabled_skills_override,
    )
    return create_deep_agent(
        model=model,
        card=card,
        system_prompt=prompt,
        tools=tools,
        rails=rails or None,
        workspace=str(workspace),
        sys_operation=sys_operation,
        enable_task_loop=False,
        enable_skill_discovery=profile.key not in {ACTION_EXECUTION.key, VERIFICATION_REPAIR.key},
        max_iterations=profile.max_iterations,
        invocation_log_dir=str(Path(workspace).resolve().parent / "agent_logs" / Path(workspace).name),
        language="en",
        restrict_to_work_dir=True,
        auto_create_workspace=True,
    )


def create_role_attribution_agent(
    *,
    model_config_ref: str,
    workspace: str | Path,
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
) -> Any:
    """Create the role attribution Member Optimizer Agent."""
    return create_member_optimizer_agent(
        profile=ROLE_ATTRIBUTION,
        model_config_ref=model_config_ref,
        workspace=workspace,
        agent_skills_dirs=agent_skills_dirs,
        extra_rails=extra_rails,
    )


def create_mechanism_attribution_agent(
    *,
    model_config_ref: str,
    workspace: str | Path,
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
) -> Any:
    """Create the mechanism attribution Member Optimizer Agent."""
    return create_member_optimizer_agent(
        profile=MECHANISM_ATTRIBUTION,
        model_config_ref=model_config_ref,
        workspace=workspace,
        prompt_values={
            "MECHANISM_TYPE_VALUES": "\n".join(f"- {value}" for value in _mechanism_type_values()),
            "FAILURE_SIGNATURE_VALUES": "\n".join(f"- {value}" for value in _failure_signature_values()),
            "OPTIMIZATION_SURFACE_VALUES": "\n".join(f"- {value}" for value in _optimization_surface_values()),
        },
        agent_skills_dirs=agent_skills_dirs,
        extra_rails=extra_rails,
    )


def create_action_planning_agent(
    *,
    model_config_ref: str,
    workspace: str | Path,
    action_definitions: list[ActionDefinition],
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
    evidence_rail: Any | None = None,
) -> Any:
    """Create the member action planning Member Optimizer Agent."""
    from openjiuwen.rsi.harness_rsi.member_optimizer.agents.rails.planner_evidence import PlannerEvidenceRail

    return create_member_optimizer_agent(
        profile=ACTION_PLANNING,
        model_config_ref=model_config_ref,
        workspace=workspace,
        prompt_values={},
        agent_skills_dirs=agent_skills_dirs,
        extra_rails=[*(extra_rails or []), evidence_rail or PlannerEvidenceRail(ACTION_PLANNING.max_iterations)],
    )


def create_action_execution_agent(
    *,
    model_config_ref: str,
    workspace: str | Path,
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
    enable_skill_creator: bool = True,
    declared_write_paths: list[str] | None = None,
) -> Any:
    """Create the member action execution Member Optimizer Agent."""
    tools, sys_operation = _build_file_tools_for_workspace(
        workspace=workspace, agent_name=ACTION_EXECUTION.agent_name,
        declared_write_paths=declared_write_paths,
    )
    return create_member_optimizer_agent(
        profile=ACTION_EXECUTION,
        model_config_ref=model_config_ref,
        workspace=workspace,
        agent_skills_dirs=agent_skills_dirs,
        extra_rails=extra_rails,
        enabled_skills_override=(ACTION_EXECUTION.enabled_skills if enable_skill_creator else ()),
        tools=tools,
        sys_operation=sys_operation,
    )


def create_verification_repair_agent(
    *,
    model_config_ref: str,
    workspace: str | Path,
    agent_skills_dirs: list[str] | None = None,
    extra_rails: list[Any] | None = None,
) -> Any:
    """Create the verification repair Member Optimizer Agent."""
    tools, sys_operation = _build_file_tools_for_workspace(
        workspace=workspace, agent_name=VERIFICATION_REPAIR.agent_name, repair=True,
    )
    return create_member_optimizer_agent(
        profile=VERIFICATION_REPAIR,
        tools=tools,
        sys_operation=sys_operation,
        model_config_ref=model_config_ref,
        workspace=workspace,
        agent_skills_dirs=agent_skills_dirs,
        extra_rails=extra_rails,
    )


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders using the current environment."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def _render_prompt(filename: str, values: dict[str, str]) -> str:
    prompt = _load_prompt(filename)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"missing prompt placeholder value: {key}")
        return values[key]

    return _PLACEHOLDER_RE.sub(replace, prompt)


def _build_rails(
    *,
    profile: MemberOptimizerAgentProfile,
    agent_skills_dirs: list[str],
    extra_rails: list[Any] | None,
    enabled_skills_override: tuple[str, ...] | None = None,
) -> list[Any]:
    rails: list[Any] = []
    enabled_skills = profile.enabled_skills if enabled_skills_override is None else enabled_skills_override
    if enabled_skills:
        skill_rail = _build_skill_rail(enabled_skills, agent_skills_dirs)
        if skill_rail is not None:
            rails.append(skill_rail)
    if extra_rails:
        rails.extend(extra_rails)
    return rails


def _build_file_tools_for_workspace(
    *,
    workspace: str | Path,
    agent_name: str,
    sandbox_roots: list[str | Path] | None = None,
    declared_write_paths: list[str] | None = None,
    repair: bool = False,
) -> tuple[list[Any], Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    read_roots = [workspace_path, *supported_host_read_roots()]
    resolved_sandbox_roots = [str(Path(root).expanduser().resolve()) for root in (sandbox_roots or read_roots)]
    operation_roots = list(resolved_sandbox_roots)
    rg_binary = resolve_rg_binary()
    if rg_binary:
        operation_roots.append(str(Path(rg_binary).resolve()))
    sysop_card = SysOperationCard(
        id=f"{agent_name}_{workspace_path.name}_{os.urandom(4).hex()}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(
            sandbox_root=operation_roots,
            restrict_to_sandbox=True,
            shell_allowlist=[],
        ),
    )
    result = Runner.resource_mgr.add_sys_operation(sysop_card)
    if result.is_err():
        raise RuntimeError(f"add action executor sys_operation failed: {result.msg()}")
    sys_operation = Runner.resource_mgr.get_sys_operation(sysop_card.id)
    if sys_operation is None:
        raise RuntimeError(f"action executor sys_operation not found: {sysop_card.id}")
    native_tools = [
        ReadFileTool(sys_operation, language="en", agent_id=sysop_card.id),
        GlobTool(sys_operation, language="en", agent_id=sysop_card.id),
        GrepTool(sys_operation, language="en", agent_id=sysop_card.id),
        ListDirTool(sys_operation, language="en", agent_id=sysop_card.id),
        WriteFileTool(sys_operation, language="en", agent_id=sysop_card.id),
        EditFileTool(sys_operation, language="en", agent_id=sysop_card.id),
    ]
    return [
        _ScopedPackageTool(
            tool, workspace_path, [Path(root) for root in resolved_sandbox_roots],
            declared_write_paths or [], repair=repair,
        )
        for tool in native_tools
    ], sys_operation


_PROTECTED_PACKAGE_NAMES = {
    "plan.yaml", "execution_results.json", "verification.json", "fix_result.json",
    "current_harness_refs.yaml", "member_optimization_ref.yaml",
}


def package_write_allowed(path: str, declared_paths: list[str] | None = None, *, repair: bool = False) -> bool:
    """Check package-relative mutation scope, independently of model claims."""
    candidate = Path(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        return False
    if any(part.startswith(".") or part in _PROTECTED_PACKAGE_NAMES for part in candidate.parts):
        return False
    if candidate.parts[0] in {"current_harnesses", "evaluation", "evaluations", "audit", "checkpoints"}:
        return False
    if repair:
        return (
            candidate.parts[0] in {"prompt_sections", "skills", "tools", "rails", "subagents"}
            or candidate.as_posix() in {"harness.yaml", "identity.md", "soul.md"}
            or candidate.suffix.lower() in {".yaml", ".yml", ".json", ".py"}
        )
    return any(
        candidate == Path(declared) or Path(declared) in candidate.parents
        for declared in (declared_paths or [])
        if not Path(declared).is_absolute() and ".." not in Path(declared).parts
    )


def supported_host_read_roots() -> list[Path]:
    """Expose implementation contracts as read-only context to package authors."""
    package = Path(__file__).resolve().parents[4]
    return [
        package / "core" / "single_agent" / "rail",
        package / "core" / "foundation" / "tool",
        package / "harness" / "rails",
        package / "harness" / "docs",
    ]


class _ScopedPackageTool(Tool):
    """Keep native filesystem semantics while enforcing the candidate boundary."""

    def __init__(self, tool, workspace: Path, read_roots: list[Path], declared_paths: list[str], *, repair: bool):
        super().__init__(tool.card)
        self._tool = tool
        self._workspace = workspace
        self._read_roots = read_roots
        self._declared_paths = declared_paths
        self._repair = repair

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        if not isinstance(inputs, dict):
            return ToolOutput(success=False, error="tool inputs must be an object")
        writing = isinstance(self._tool, (WriteFileTool, EditFileTool))
        key = "file_path" if isinstance(self._tool, (ReadFileTool, WriteFileTool, EditFileTool)) else "path"
        value = inputs.get(key, ".")
        if not isinstance(value, str) or not value.strip():
            return ToolOutput(success=False, error=f"{key} must be a path string")
        try:
            raw_path = Path(value).expanduser()
            path = (raw_path if raw_path.is_absolute() else self._workspace / raw_path).resolve()
            if writing:
                relative = path.relative_to(self._workspace)
                if not package_write_allowed(relative.as_posix(), self._declared_paths, repair=self._repair):
                    raise ValueError("write is outside the permitted package paths")
                lexical = raw_path if raw_path.is_absolute() else self._workspace / raw_path
                if any(parent.is_symlink() for parent in [lexical, *lexical.parents] if parent != self._workspace):
                    raise ValueError("writes through symlinks are forbidden")
            elif not any(path.is_relative_to(root) for root in self._read_roots):
                raise ValueError("read is outside the package and supported host contracts")
            if isinstance(self._tool, GlobTool):
                pattern = str(inputs.get("pattern", ""))
                if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                    raise ValueError("glob pattern must stay within the search path")
        except (OSError, ValueError) as exc:
            return ToolOutput(success=False, error=str(exc))
        return await self._tool.invoke({**inputs, key: str(path)}, **kwargs)

    def render_for_llm(self, output: ToolOutput) -> str:
        return self._tool.render_for_llm(output)

    async def stream(self, inputs: dict[str, Any], **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _build_skill_rail(
    requested_skills: tuple[str, ...],
    agent_skills_dirs: list[str],
) -> Any | None:
    from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

    roots = [_SKILLS_DIR, *(Path(path) for path in agent_skills_dirs)]
    skills_dir: list[str] = []
    enabled_skills: list[str] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        if not any((root_path / skill_name).is_dir() for skill_name in requested_skills):
            continue
        skills_dir.append(str(root_path))
    for skill_name in requested_skills:
        for root in skills_dir:
            if (Path(root) / skill_name).is_dir():
                enabled_skills.append(skill_name)
                break
    if not enabled_skills:
        return None
    return SkillUseRail(
        skills_dir=skills_dir,
        skill_mode="all",
        enabled_skills=enabled_skills,
        include_tools=False,
    )


def _format_action_definitions(action_definitions: list[ActionDefinition]) -> str:
    action_definitions = filter_action_definitions(action_definitions)
    if not action_definitions:
        return ""

    lines = ["## Action Definitions"]
    for definition in action_definitions:
        lines.append(
            f"- {definition.group}/{definition.operation}: {definition.purpose} (function={definition.function})"
        )
    return "\n".join(lines)


def _mechanism_type_values() -> list[str]:
    return [item.value for item in MechanismType if item is not MechanismType.RAIL]


def _failure_signature_values() -> list[str]:
    return [item.value for item in FailureSignature if item is not FailureSignature.CONFIG_OR_RAIL_MISMATCH]


def _optimization_surface_values() -> list[str]:
    return [item.value for item in OptimizationSurface if item is not OptimizationSurface.RAIL]


__all__ = [
    "create_action_execution_agent",
    "create_action_planning_agent",
    "create_mechanism_attribution_agent",
    "create_member_optimizer_agent",
    "create_role_attribution_agent",
    "create_verification_repair_agent",
    "load_member_optimizer_model",
]
