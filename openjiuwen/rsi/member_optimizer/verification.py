# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verification and repair of candidate Expert Harness changes.

Per feat_009 rule.md Section 3.8 and design.md Section 4.10.
Verifier checks candidate Expert Harness code correctness, directory structure,
and action constraints. Does NOT execute evaluation or compute pass@k.
"""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.resources import (
    find_plugin_manifest,
    load_plugin_package,
    resolve_plugin_parts,
)
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.rsi.member_optimizer.action_groups import (
    validate_action_policy,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    create_verification_repair_agent,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    extract_agent_text,
)
from openjiuwen.rsi.member_optimizer.execution_contract import (
    evaluate_role_execution,
    role_execution_errors,
)
from openjiuwen.rsi.member_optimizer.schema import (
    MemberFixResult,
    MemberOptimizationPlan,
    MemberVerificationResult,
    RepairItem,
    RoleVerificationResult,
    VerificationCheck,
)
from openjiuwen.rsi.member_optimizer.worktree_coordinator import (
    resolve_integration_worktree_path,
)

_REPAIRABLE_CHECK_PREFIXES = (
    "integration_dir:",
    "yaml_parse:",
    "json_parse:",
    "python_compile:",
    "expert_harness_load:",
    "prompt_section_ref:",
    "skill_ref:",
    "skill_safety:",
    "skill_runtime_mount:",
    "tool_file_ref:",
    "tool_schema:",
    "tool_activation:",
    "rail_file_ref:",
    "expert_harness_resolve:",
)
_UNREPAIRABLE_CHECK_NAMES = {
    "plan_schema",
    "execution_schema",
    "execution_results_present",
    "execution_results_load",
}

_FORBIDDEN_SKILL_SUFFIXES = {".bat", ".cmd", ".dll", ".dylib", ".exe", ".ps1", ".sh", ".so"}
_SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS = {"httpx", "requests", "socket", "subprocess", "urllib"}
_SKILL_SCRIPT_FORBIDDEN_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "input",
    "os.popen",
    "os.spawn",
    "os.system",
    "shutil.move",
    "shutil.rmtree",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
_UNREPAIRABLE_CHECK_PREFIXES = (
    "action_policy:",
    "execution_action_policy:",
    "action_result:",
    "action_merge:",
    "role_action_bundle:",
    "verification_exception:",
)
_REPAIR_CONTEXT_FILE_LIMIT = 80
_REPAIR_CHECK_LIMIT = 12
_REPAIR_ERROR_LIMIT = 500
_REPAIR_RESPONSE_LIMIT = 800
_DANGEROUS_IMPORT_ROOTS = {
    "httpx",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "urllib",
}
_DANGEROUS_CALL_NAMES = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
}


def _check_yaml_parse(path: Path) -> VerificationCheck:
    """Check that a YAML file parses correctly."""
    try:
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f)
        return VerificationCheck(name=f"yaml_parse:{path.name}", status="passed")
    except Exception as e:
        return VerificationCheck(
            name=f"yaml_parse:{path.name}",
            status="failed",
            error=str(e),
        )


def _check_json_parse(path: Path) -> VerificationCheck:
    """Check that a JSON file parses correctly."""
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return VerificationCheck(name=f"json_parse:{path.name}", status="passed")
    except Exception as e:
        return VerificationCheck(
            name=f"json_parse:{path.name}",
            status="failed",
            error=str(e),
        )


def _check_python_compile(path: Path) -> VerificationCheck:
    """Check that a Python file compiles without syntax errors."""
    try:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
        return VerificationCheck(name=f"python_compile:{path.name}", status="passed")
    except Exception as e:
        return VerificationCheck(
            name=f"python_compile:{path.name}",
            status="failed",
            error=str(e),
        )


def _validate_package_python_source(source: str, *, path: str = "<source>") -> list[str]:
    """Return static safety errors for package-local Python resources."""
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [str(exc)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _DANGEROUS_IMPORT_ROOTS:
                    errors.append(f"dangerous import '{alias.name}' in {path}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _DANGEROUS_IMPORT_ROOTS:
                errors.append(f"dangerous import '{node.module}' in {path}")
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in _DANGEROUS_CALL_NAMES:
                errors.append(f"dangerous call '{call_name}' in {path}")
    return errors


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        # Attribute calls are not equivalent to the dangerous builtins with the
        # same leaf name (for example, re.compile). Keep blocking explicit access
        # through the builtins module without rejecting ordinary library APIs.
        if isinstance(node.value, ast.Name) and node.value.id in {
            "builtins",
            "__builtins__",
        }:
            return node.attr
    return ""


def _check_package_file_ref(
    *,
    role: str,
    integration_path: Path,
    resource_kind: str,
    file_path: str,
) -> VerificationCheck:
    try:
        raw = Path(str(file_path))
        if ".." in raw.parts:
            raise ValueError("file resource must not contain '..'")
        resolved = raw.resolve() if raw.is_absolute() else (integration_path / raw).resolve()
        resolved.relative_to(integration_path.resolve())
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        if resolved.suffix == ".py":
            errors = _validate_package_python_source(
                resolved.read_text(encoding="utf-8"),
                path=str(resolved.relative_to(integration_path)),
            )
            if errors:
                raise ValueError("; ".join(errors))
        return VerificationCheck(
            name=f"{resource_kind}_file_ref:{role}:{file_path}",
            status="passed",
        )
    except Exception as exc:
        return VerificationCheck(
            name=f"{resource_kind}_file_ref:{role}:{file_path}",
            status="failed",
            error=str(exc),
        )


def _check_prompt_sections_manifest(role: str, integration_path: Path) -> list[VerificationCheck]:
    manifest = integration_path / "prompt_sections" / "sections.yaml"
    if not manifest.is_file():
        return []
    checks: list[VerificationCheck] = []
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            sections = data.get("sections") or data.get("prompt_sections") or []
        elif isinstance(data, list):
            sections = data
        else:
            raise TypeError("prompt_sections/sections.yaml must be a mapping or list")
        if not isinstance(sections, list):
            sections = [sections]
        for index, section in enumerate(sections):
            try:
                if not isinstance(section, dict):
                    raise TypeError(f"section[{index}] must be a mapping")
                name = str(section.get("name", "") or f"section[{index}]")
                file_ref = section.get("file") or section.get("path")
                if not file_ref:
                    checks.append(
                        VerificationCheck(
                            name=f"prompt_section_ref:{role}:{name}",
                            status="passed",
                        )
                    )
                    continue
                resolved = _resolve_prompt_section_file(integration_path, str(file_ref))
                if not resolved.is_file():
                    raise FileNotFoundError(resolved)
                checks.append(
                    VerificationCheck(
                        name=f"prompt_section_ref:{role}:{name}",
                        status="passed",
                    )
                )
            except Exception as exc:
                checks.append(
                    VerificationCheck(
                        name=f"prompt_section_ref:{role}:{index}",
                        status="failed",
                        error=str(exc),
                    )
                )
    except Exception as exc:
        checks.append(
            VerificationCheck(
                name=f"prompt_section_ref:{role}:manifest",
                status="failed",
                error=str(exc),
            )
        )
    return checks


def _resolve_prompt_section_file(integration_path: Path, file_ref: str) -> Path:
    raw = Path(file_ref)
    if raw.is_absolute():
        raise ValueError("prompt section file must be package-relative")
    if ".." in raw.parts:
        raise ValueError("prompt section file must not contain '..'")
    direct = (integration_path / raw).resolve()
    root = integration_path.resolve()
    try:
        direct.relative_to(root)
    except ValueError as exc:
        raise ValueError("prompt section file escapes package") from exc
    if direct.is_file():
        return direct
    mounted = (integration_path / "prompt_sections" / "files" / raw).resolve()
    try:
        mounted.relative_to(root)
    except ValueError as exc:
        raise ValueError("prompt section file escapes package") from exc
    return mounted


def _check_skills_manifest(role: str, integration_path: Path) -> list[VerificationCheck]:
    manifest = integration_path / "skills" / "skills.yaml"
    if not manifest.is_file():
        return []
    checks: list[VerificationCheck] = []
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            skills = data.get("skills") or []
        elif isinstance(data, list):
            skills = data
        else:
            raise TypeError("skills/skills.yaml must be a mapping or list")
        if not isinstance(skills, list):
            skills = [skills]
        for index, skill_ref in enumerate(skills):
            try:
                mount_dir = _resolve_package_dir(integration_path, str(skill_ref))
                if not mount_dir.is_dir():
                    raise FileNotFoundError(mount_dir)
                skill_dirs = _discover_mounted_skill_dirs(mount_dir)
                if not skill_dirs:
                    raise FileNotFoundError(f"no SKILL.md found in {mount_dir} or its immediate skill children")
                checks.append(
                    VerificationCheck(
                        name=f"skill_ref:{role}:{skill_ref}",
                        status="passed",
                        notes=", ".join(skill_dir.name for skill_dir in skill_dirs),
                    )
                )
                for skill_dir in skill_dirs:
                    scan = scan_skill_directory(skill_dir)
                    check_name = f"skill_safety:{role}:{skill_ref}:{skill_dir.name}"
                    if scan["status"] != "passed":
                        checks.append(
                            VerificationCheck(
                                name=check_name,
                                status="failed",
                                error="; ".join(str(item) for item in scan["errors"]),
                            )
                        )
                        continue
                    checks.append(
                        VerificationCheck(
                            name=check_name,
                            status="passed",
                            notes=f"{scan.get('file_count', 0)} file(s), {scan.get('total_bytes', 0)} bytes",
                        )
                    )
            except Exception as exc:
                checks.append(
                    VerificationCheck(
                        name=f"skill_ref:{role}:{index}",
                        status="failed",
                        error=str(exc),
                    )
                )
    except Exception as exc:
        checks.append(
            VerificationCheck(
                name=f"skill_ref:{role}:manifest",
                status="failed",
                error=str(exc),
            )
        )
    return checks


def _discover_mounted_skill_dirs(mount_dir: Path) -> list[Path]:
    """Return skill directories discoverable from a manifest mount entry."""
    if (mount_dir / "SKILL.md").is_file():
        return [mount_dir]
    return [
        item
        for item in sorted(mount_dir.iterdir(), key=lambda path: path.name)
        if item.is_dir() and (item / "SKILL.md").is_file()
    ]


def _resolve_package_dir(integration_path: Path, path_ref: str) -> Path:
    raw = Path(path_ref)
    if raw.is_absolute():
        raise ValueError("package resource path must be relative")
    if ".." in raw.parts:
        raise ValueError("package resource path must not contain '..'")
    resolved = (integration_path / raw).resolve()
    resolved.relative_to(integration_path.resolve())
    return resolved


def _check_resolved_harness_resources(role: str, integration_path: Path, harness: Any) -> VerificationCheck:
    try:
        loaded = _resolve_harness_parts(harness)
        for tool in loaded.tools:
            if not tool.card.id or not tool.card.name:
                raise ValueError("resolved Tool produced an invalid ToolCard")
        return VerificationCheck(
            name=f"expert_harness_resolve:{role}",
            status="passed",
        )
    except Exception as exc:
        return VerificationCheck(
            name=f"expert_harness_resolve:{role}",
            status="failed",
            error=str(exc),
        )


def _check_resolved_tool_schemas(role: str, harness: Any) -> list[VerificationCheck]:
    """Ensure resolved tools can be registered as OpenAI-compatible functions."""
    try:
        loaded = _resolve_harness_parts(harness)
    except Exception as exc:
        return [
            VerificationCheck(
                name=f"tool_schema:{role}:resolve",
                status="failed",
                error=str(exc),
            )
        ]

    checks: list[VerificationCheck] = []
    for tool in loaded.tools:
        card = getattr(tool, "card", None)
        tool_name = str(getattr(card, "name", "") or getattr(card, "id", "") or "unknown")
        error = _tool_input_schema_error(getattr(card, "input_params", None))
        if error:
            checks.append(
                VerificationCheck(
                    name=f"tool_schema:{role}:{tool_name}",
                    status="failed",
                    error=error,
                )
            )
            continue
        checks.append(
            VerificationCheck(
                name=f"tool_schema:{role}:{tool_name}",
                status="passed",
            )
        )
    return checks


def _tool_input_schema_error(input_params: Any) -> str:
    """Return an error when input_params is not a top-level object JSON Schema."""
    schema = input_params
    if isinstance(schema, type) and hasattr(schema, "model_json_schema"):
        schema = schema.model_json_schema()
    if not isinstance(schema, dict):
        return "tool input_params must be a JSON Schema mapping with top-level type: object"
    if schema.get("type") != "object":
        return "tool input_params must be a JSON Schema with top-level type: object"
    properties = schema.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        return "tool input_params.properties must be a mapping"
    required = schema.get("required", [])
    if required is None:
        required = []
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return "tool input_params.required must be a list of strings"
    missing_required = [item for item in required if item not in properties]
    if missing_required:
        return f"tool input_params.required references unknown properties: {missing_required}"
    return ""


def _check_skill_runtime_mounts(role: str, harness: Any) -> VerificationCheck:
    """Check that declared ExpertHarness skills are discoverable by SkillUseRail."""
    try:
        resolved_skills = _resolve_harness_parts(harness).skills
        if not resolved_skills:
            return VerificationCheck(
                name=f"skill_runtime_mount:{role}",
                status="passed",
                notes="no skills declared",
            )

        discovered: list[str] = []
        for resolved_skill in resolved_skills:
            root = Path(resolved_skill.directory).expanduser().resolve()
            if not root.exists():
                raise FileNotFoundError(root)
            if not root.is_dir():
                raise NotADirectoryError(root)
            discovered.extend(skill_dir.name for skill_dir in _discover_mounted_skill_dirs(root))

        if not discovered:
            raise ValueError("no skills discoverable from declared skill roots")

        return VerificationCheck(
            name=f"skill_runtime_mount:{role}",
            status="passed",
            notes=", ".join(sorted(set(discovered))),
        )
    except Exception as exc:
        return VerificationCheck(
            name=f"skill_runtime_mount:{role}",
            status="failed",
            error=str(exc),
        )


def _resolve_harness_parts(harness: Any) -> Any:
    """Resolve a plugin package without mutating a live agent."""
    context = BuildContext(language="en")
    return resolve_plugin_parts(harness, context)


def _load_harness_plugin(integration_path: Path) -> Any:
    """Load a legacy RSI harness directory through the Agent Core plugin API."""
    return load_plugin_package(find_plugin_manifest(integration_path))


def _resource_file_path(spec: Any) -> str:
    params = getattr(spec, "params", None)
    if not isinstance(params, dict):
        return ""
    return str(params.get("file_path") or "")


def _validate_role_integration_worktree(
    role: str,
    integration_path: Path,
    expected_tool_names: set[str] | None = None,
) -> list[VerificationCheck]:
    """Run all static checks on a role's integration worktree."""
    checks: list[VerificationCheck] = []

    if not integration_path.is_dir():
        checks.append(
            VerificationCheck(
                name=f"integration_dir:{role}",
                status="failed",
                error=f"Integration worktree is not a directory: {integration_path}",
            )
        )
        return checks

    checks.append(
        VerificationCheck(
            name=f"integration_dir:{role}",
            status="passed",
        )
    )

    checks.extend(_check_prompt_sections_manifest(role, integration_path))
    checks.extend(_check_skills_manifest(role, integration_path))

    try:
        harness = _load_harness_plugin(integration_path)
        checks.append(
            VerificationCheck(
                name=f"expert_harness_load:{role}",
                status="passed",
            )
        )
        for spec in harness.tools:
            file_path = _resource_file_path(spec)
            if file_path:
                checks.append(
                    _check_package_file_ref(
                        role=role,
                        integration_path=integration_path,
                        resource_kind="tool",
                        file_path=str(file_path),
                    )
                )
        for spec in harness.rails:
            file_path = _resource_file_path(spec)
            if file_path:
                checks.append(
                    _check_package_file_ref(
                        role=role,
                        integration_path=integration_path,
                        resource_kind="rail",
                        file_path=str(file_path),
                    )
                )
        package_file_checks_failed = any(
            check.status == "failed"
            and (
                check.name.startswith(f"prompt_section_ref:{role}:")
                or check.name.startswith(f"skill_ref:{role}:")
                or check.name.startswith(f"skill_safety:{role}:")
                or check.name.startswith(f"skill_runtime_mount:{role}")
                or check.name.startswith(f"tool_file_ref:{role}:")
                or check.name.startswith(f"rail_file_ref:{role}:")
            )
            for check in checks
        )
        if not package_file_checks_failed:
            checks.append(_check_skill_runtime_mounts(role, harness))
            package_file_checks_failed = any(
                check.status == "failed" and check.name.startswith(f"skill_runtime_mount:{role}") for check in checks
            )
        if not package_file_checks_failed:
            checks.append(_check_resolved_harness_resources(role, integration_path, harness))
            checks.extend(_check_resolved_tool_schemas(role, harness))
            loaded = _resolve_harness_parts(harness)
            resolved_names = {
                str(tool.card.name or tool.card.id)
                for tool in loaded.tools
                if str(tool.card.name or tool.card.id).strip()
            }
            for expected_name in sorted(expected_tool_names or set()):
                matching_names = sorted(
                    name for name in resolved_names if _runtime_tool_names_match(expected_name, name)
                )
                if matching_names:
                    checks.append(
                        VerificationCheck(
                            name=f"tool_activation:{role}:{expected_name}",
                            status="passed",
                            notes=(f"tool resolves to runtime-visible ToolCard(s): {matching_names}"),
                        )
                    )
                else:
                    checks.append(
                        VerificationCheck(
                            name=f"tool_activation:{role}:{expected_name}",
                            status="failed",
                            error=(
                                f"planned runtime tool '{expected_name}' is not exposed; "
                                f"resolved tools: {sorted(resolved_names)}"
                            ),
                        )
                    )
    except Exception as exc:
        checks.append(
            VerificationCheck(
                name=f"expert_harness_load:{role}",
                status="failed",
                error=str(exc),
            )
        )

    for py_file in integration_path.rglob("*.py"):
        rel = py_file.relative_to(integration_path).as_posix()
        result = _check_python_compile(py_file)
        result = replace(result, name=f"python_compile:{role}/{rel}")
        checks.append(result)

    for yaml_file in integration_path.rglob("*.yaml"):
        rel = yaml_file.relative_to(integration_path).as_posix()
        result = _check_yaml_parse(yaml_file)
        result = replace(result, name=f"yaml_parse:{role}/{rel}")
        checks.append(result)

    for yml_file in integration_path.rglob("*.yml"):
        rel = yml_file.relative_to(integration_path).as_posix()
        result = _check_yaml_parse(yml_file)
        result = replace(result, name=f"yaml_parse:{role}/{rel}")
        checks.append(result)

    for json_file in integration_path.rglob("*.json"):
        rel = json_file.relative_to(integration_path).as_posix()
        result = _check_json_parse(json_file)
        result = replace(result, name=f"json_parse:{role}/{rel}")
        checks.append(result)

    harness_yaml = integration_path / "harness.yaml"
    if harness_yaml.exists():
        checks.append(
            VerificationCheck(
                name=f"harness_yaml:{role}",
                status="passed",
            )
        )

    return checks


def _runtime_tool_names_match(planned_name: str, resolved_name: str) -> bool:
    """Match a planned tool file stem to its runtime ToolCard name."""

    def canonical(value: str) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_")
        return normalized.removesuffix("_tool")

    return bool(canonical(planned_name)) and canonical(planned_name) == canonical(resolved_name)


def _validate_plan_schema(plan_path: Path) -> list[VerificationCheck]:
    """Validate that plan.yaml is parseable and has expected fields."""
    checks: list[VerificationCheck] = []
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_data = yaml.safe_load(f) or {}
        if "actions" in plan_data and "action_waves" in plan_data:
            checks.append(VerificationCheck(name="plan_schema", status="passed"))
        else:
            checks.append(
                VerificationCheck(
                    name="plan_schema",
                    status="failed",
                    error="Missing required fields: actions, action_waves",
                )
            )
    except Exception as e:
        checks.append(
            VerificationCheck(
                name="plan_schema",
                status="failed",
                error=str(e),
            )
        )
    return checks


def _validate_execution_results(exec_path: Path) -> list[VerificationCheck]:
    """Validate that execution_results.json is parseable."""
    checks: list[VerificationCheck] = []
    try:
        with open(exec_path, encoding="utf-8") as f:
            data = json.load(f)
        if "results" in data and isinstance(data["results"], list):
            checks.append(VerificationCheck(name="execution_schema", status="passed"))
        else:
            checks.append(
                VerificationCheck(
                    name="execution_schema",
                    status="failed",
                    error="Missing or invalid results list",
                )
            )
    except Exception as e:
        checks.append(
            VerificationCheck(
                name="execution_schema",
                status="failed",
                error=str(e),
            )
        )
    return checks


def _load_execution_result_data(exec_path: Path) -> list[dict[str, Any]]:
    if not exec_path.is_file():
        return []
    with open(exec_path, encoding="utf-8") as file:
        data = json.load(file)
    results = data.get("results", [])
    return [item for item in results if isinstance(item, dict)]


def _validate_action_results_by_role(
    plan: MemberOptimizationPlan,
    exec_path: Path,
) -> dict[str, list[VerificationCheck]]:
    """Validate planned action execution/merge outcomes grouped by role."""
    checks_by_role: dict[str, list[VerificationCheck]] = {target.role: [] for target in plan.targets}
    checks: list[VerificationCheck] = []
    try:
        result_rows = _load_execution_result_data(exec_path)
    except Exception as exc:
        checks = [
            VerificationCheck(
                name="execution_results_load",
                status="failed",
                error=str(exc),
            )
        ]
        for role in checks_by_role:
            checks_by_role[role].extend(checks)
        return checks_by_role

    outcomes_by_role = {role: evaluate_role_execution(plan, result_rows, role) for role in checks_by_role}

    for action in plan.actions:
        role_checks = checks_by_role.setdefault(action.role, [])
        policy_check = validate_action_policy(action, {target.role for target in plan.targets})
        if not policy_check.valid:
            role_checks.append(
                VerificationCheck(
                    name=f"action_policy:{action.action_id}",
                    status="failed",
                    error="; ".join(policy_check.errors),
                )
            )

        matching_results = [row for row in result_rows if str(row.get("action_id", "")) == action.action_id]
        result = matching_results[0] if len(matching_results) == 1 else None
        outcome = outcomes_by_role.get(action.role, {}).get(action.action_id)
        if result is None or outcome is None:
            error = outcome.reason if outcome is not None else "missing execution result"
            role_checks.append(
                VerificationCheck(
                    name=f"action_result:{action.action_id}",
                    status="failed",
                    error=error,
                )
            )
            continue

        result_action = {
            "action_id": action.action_id,
            "role": action.role,
            "action_group": action.action_group,
            "operation": action.operation,
            "target_path": action.target_path,
            "declared_write_paths": result.get(
                "declared_write_paths",
                action.declared_write_paths,
            ),
            "candidate_query": action.candidate_query,
            "install_ref": action.install_ref,
            "allowed_tools": action.allowed_tools,
            "constraints": action.constraints,
        }
        result_policy_check = validate_action_policy(
            result_action,
            {target.role for target in plan.targets},
        )
        if not result_policy_check.valid:
            role_checks.append(
                VerificationCheck(
                    name=f"execution_action_policy:{action.action_id}",
                    status="failed",
                    error="; ".join(result_policy_check.errors),
                )
            )
            continue

        if not outcome.satisfied:
            role_checks.append(
                VerificationCheck(
                    name=f"action_result:{action.action_id}",
                    status="failed",
                    error=outcome.reason,
                )
            )
            continue

        role_checks.append(
            VerificationCheck(
                name=f"action_result:{action.action_id}",
                status="passed",
            )
        )

    for role, role_checks in checks_by_role.items():
        execution_errors = role_execution_errors(plan, result_rows, role)
        role_checks.append(
            VerificationCheck(
                name=f"role_action_bundle:{role}",
                status="failed" if execution_errors else "passed",
                error="; ".join(execution_errors),
            )
        )

    return checks_by_role


def _is_worktree_repairable_check(name: str) -> bool:
    return name.startswith(_REPAIRABLE_CHECK_PREFIXES)


def _is_blocking_unrepairable_check(name: str) -> bool:
    if name in _UNREPAIRABLE_CHECK_NAMES:
        return True
    return name.startswith(_UNREPAIRABLE_CHECK_PREFIXES)


def _repairable_failed_checks(
    checks: list[VerificationCheck],
) -> list[VerificationCheck]:
    return [check for check in checks if check.status == "failed" and _is_worktree_repairable_check(check.name)]


def _role_is_repairable(checks: list[VerificationCheck]) -> bool:
    failed = [check for check in checks if check.status == "failed"]
    if not failed:
        return False
    if any(_is_blocking_unrepairable_check(check.name) for check in failed):
        return False
    return all(_is_worktree_repairable_check(check.name) for check in failed)


def _summarize_failed_checks(
    checks: list[dict[str, Any]] | list[VerificationCheck],
    *,
    limit: int = _REPAIR_CHECK_LIMIT,
) -> str:
    lines: list[str] = []
    for raw_check in checks[:limit]:
        check = raw_check if isinstance(raw_check, VerificationCheck) else VerificationCheck(**raw_check)
        error = (check.error or check.notes or "").replace("\r", " ").strip()
        if len(error) > _REPAIR_ERROR_LIMIT:
            error = f"{error[: _REPAIR_ERROR_LIMIT - 3].rstrip()}..."
        line = f"- {check.name}: {check.status}"
        if error:
            line = f"{line} | {error}"
        lines.append(line)
    if len(checks) > limit:
        lines.append(f"- ... {len(checks) - limit} more failed checks omitted")
    return "\n".join(lines) if lines else "- no failed checks"


def _list_repair_context_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    files: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel.endswith(".pyc") or "/__pycache__/" in f"/{rel}":
            continue
        files.append(rel)
        if len(files) >= _REPAIR_CONTEXT_FILE_LIMIT:
            files.append("...")
            break
    return files


def _repair_failed_file_context(root: Path, failed_checks: list[dict[str, Any]]) -> str:
    """Render bounded contents for files directly referenced by failed checks."""
    rel_paths: list[str] = []
    for check in failed_checks:
        name = str(check.get("name", "") or "")
        rel_path = _failed_check_relative_path(name)
        if rel_path and rel_path not in rel_paths:
            rel_paths.append(rel_path)
    if not rel_paths:
        return "No failed check pointed to a concrete local file."

    sections: list[str] = []
    for rel_path in rel_paths[:12]:
        path = (root / rel_path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            sections.append(f"### {rel_path}\n\nFile is missing.")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        rendered_lines = [f"{index:04d}: {line}" for index, line in enumerate(lines[:220], start=1)]
        if len(lines) > 220:
            rendered_lines.append("... content truncated ...")
        sections.append(f"### {rel_path}\n\n```text\n{chr(10).join(rendered_lines)}\n```")
    return "\n\n".join(sections) if sections else "No failed check pointed to a readable local file."


def _failed_check_relative_path(check_name: str) -> str:
    for prefix in ("python_compile:", "yaml_parse:", "json_parse:"):
        if check_name.startswith(prefix):
            value = check_name.removeprefix(prefix)
            if "/" in value:
                return value.split("/", 1)[1]
            return value
    for prefix in ("tool_file_ref:", "rail_file_ref:"):
        if check_name.startswith(prefix):
            value = check_name.removeprefix(prefix)
            parts = value.split(":", 1)
            if len(parts) == 2:
                return parts[1].replace("\\", "/")
    return ""


def _summarize_agent_response(response_text: str) -> str:
    lines = [line.strip() for line in response_text.splitlines() if line.strip()]
    summary = "\n".join(lines[:8]).strip()
    if len(summary) > _REPAIR_RESPONSE_LIMIT:
        return f"{summary[: _REPAIR_RESPONSE_LIMIT - 3].rstrip()}..."
    return summary or "DeepAgent repair turn completed."


class HarnessRepairAgent:
    """DeepAgent adapter for repairing failed static verification checks."""

    def __init__(
        self,
        model_config_ref: str,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._model_config_ref = model_config_ref
        self._agent_skills_dirs = list(agent_skills_dirs or [])

    async def repair_role(
        self,
        role_integration_worktree: Path,
        role: str,
        failed_checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Invoke DeepAgent to repair one role integration worktree."""
        repairable_checks = [
            check for check in failed_checks if _is_worktree_repairable_check(str(check.get("name", "")))
        ]
        if not repairable_checks:
            repair = RepairItem(
                role=role,
                action="deepagent_repair",
                description="No worktree-repairable failed checks were provided.",
                status="failed",
                error="no repairable checks",
            )
            return {
                "status": "failed",
                "repairs": [asdict(repair)],
            }

        agent = create_verification_repair_agent(
            model_config_ref=self._model_config_ref,
            workspace=role_integration_worktree,
            agent_skills_dirs=self._agent_skills_dirs,
        )
        session = Session(
            session_id=f"member_verifier_repair_{role}",
            card=getattr(agent, "card", None) or AgentCard(name="member_verifier_repair"),
        )
        response = await agent.invoke(
            inputs={
                "query": self._build_repair_user_message(
                    role=role,
                    role_integration_worktree=role_integration_worktree,
                    failed_checks=repairable_checks,
                )
            },
            session=session,
        )
        repair = RepairItem(
            role=role,
            action="deepagent_repair",
            description=_summarize_agent_response(extract_agent_text(response)),
            status="attempted",
            error="",
        )
        return {
            "status": "attempted",
            "repairs": [asdict(repair)],
        }

    @staticmethod
    def _build_repair_user_message(
        *,
        role: str,
        role_integration_worktree: Path,
        failed_checks: list[dict[str, Any]],
    ) -> str:
        files = _list_repair_context_files(role_integration_worktree)
        files_text = "\n".join(f"- {path}" for path in files) or "- no files found"
        failed_summary = _summarize_failed_checks(failed_checks)
        failed_file_context = _repair_failed_file_context(
            role_integration_worktree,
            failed_checks,
        )
        return f"""## Role Expert Harness Repair

Role: {role}
Integration worktree: current workspace

Repair only the failed static checks below. Do not modify audit artifacts such
as plan.yaml, execution_results.json, verification.json, fix_result.json,
current_harness_refs.yaml, or member_optimization_ref.yaml.

## Failed Checks

{failed_summary}

## Current Workspace Files

{files_text}

## Failed File Contents

{failed_file_context}

## Output

After editing files, briefly report which files you changed, why, and any
limits you hit. The verifier will rerun deterministic checks to decide success.
"""


class HarnessChangeVerifier:
    """Verify and optionally repair candidate Expert Harness changes."""

    def __init__(
        self,
        repair_agent: HarnessRepairAgent | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._repair_agent = repair_agent
        self._agent_skills_dirs = list(agent_skills_dirs or [])

    def _get_repair_agent(self, model_config_ref: str) -> HarnessRepairAgent:
        if self._repair_agent is not None:
            return self._repair_agent
        return HarnessRepairAgent(
            model_config_ref=model_config_ref,
            agent_skills_dirs=self._agent_skills_dirs,
        )

    async def verify(
        self,
        plan: MemberOptimizationPlan,
        worktrees_dir: Path,
        output_path: str,
    ) -> MemberVerificationResult:
        """Verify all selected role integration worktrees.

        Verifies against worktrees/{role}/integration per spec Section 4.10.
        Concurrent per-role checking with serial shared-artifact writes.
        """
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        selected_roles = {t.role for t in plan.targets}
        expected_tool_names_by_role: dict[str, set[str]] = {role: set() for role in selected_roles}
        for action in plan.actions:
            if action.action_group != "tool" or action.operation not in {"add", "modify"}:
                continue
            runtime_name = Path(action.target_path).stem
            if runtime_name:
                expected_tool_names_by_role.setdefault(action.role, set()).add(runtime_name)
        all_checks: list[VerificationCheck] = []
        role_results: dict[str, RoleVerificationResult] = {}
        repairable = False

        plan_path = worktrees_dir.parent / "plan.yaml"
        if plan_path.is_file():
            all_checks.extend(_validate_plan_schema(plan_path))

        exec_path = worktrees_dir.parent / "execution_results.json"
        action_checks_by_role: dict[str, list[VerificationCheck]] = {role: [] for role in selected_roles}
        if exec_path.is_file():
            all_checks.extend(_validate_execution_results(exec_path))
            action_checks_by_role = _validate_action_results_by_role(plan, exec_path)
        elif plan.actions:
            missing_exec_check = VerificationCheck(
                name="execution_results_present",
                status="failed",
                error=f"execution_results.json not found: {exec_path}",
            )
            all_checks.append(missing_exec_check)
            for role in selected_roles:
                action_checks_by_role.setdefault(role, []).append(missing_exec_check)

        async def verify_role(role: str) -> tuple[str, RoleVerificationResult]:
            integration_dir = resolve_integration_worktree_path(worktrees_dir, role)
            try:
                checks = await asyncio.to_thread(
                    _validate_role_integration_worktree,
                    role,
                    integration_dir,
                    expected_tool_names_by_role.get(role, set()),
                )
                failed_static_checks = [c for c in checks if c.status == "failed"]
                error = f"{len(failed_static_checks)} static check(s) failed" if failed_static_checks else ""
            except Exception as e:
                checks = [
                    VerificationCheck(
                        name=f"verification_exception:{role}",
                        status="failed",
                        error=str(e),
                    )
                ]
                error = str(e)

            checks.extend(action_checks_by_role.get(role, []))
            failed_role_checks = [c for c in checks if c.status == "failed"]
            status = "failed" if failed_role_checks else "passed"
            repairable_role = _role_is_repairable(checks)

            return role, RoleVerificationResult(
                role=role,
                status=status,
                checks=checks,
                repairable=repairable_role,
                error=error,
            )

        role_tasks = [verify_role(role) for role in selected_roles]
        role_outcomes = await asyncio.gather(*role_tasks, return_exceptions=True)

        for outcome in role_outcomes:
            if isinstance(outcome, Exception):
                continue
            role, rvr = outcome
            role_results[role] = rvr
            all_checks.extend(rvr.checks)
            if rvr.repairable:
                repairable = True

        failed_checks = [c for c in all_checks if c.status == "failed"]
        overall_status = "passed" if not failed_checks else "failed"

        verification_result = MemberVerificationResult(
            status=overall_status,
            checked_roles=list(selected_roles),
            checks=all_checks,
            role_results=role_results,
            repairable=repairable,
            metadata={"worktrees_dir": str(worktrees_dir)},
        )

        self._write_verification_result(output, verification_result)
        return verification_result

    @staticmethod
    def _write_verification_result(
        output: Path,
        result: MemberVerificationResult,
    ) -> None:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": result.status,
                    "checked_roles": result.checked_roles,
                    "checks": [asdict(c) for c in result.checks],
                    "role_results": {role: asdict(rvr) for role, rvr in result.role_results.items()},
                    "repairable": result.repairable,
                    "metadata": result.metadata,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    async def repair(
        self,
        verification_result: MemberVerificationResult,
        worktrees_dir: Path,
        output_path: str,
        model_config_ref: str,
        stage_retry_limit: int = 2,
    ) -> MemberFixResult:
        """Attempt repair of failed verification checks."""
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        if verification_result.status == "passed":
            fix_result = MemberFixResult(
                status="not_needed",
                verification_path=str(worktrees_dir.parent / "verification.json"),
                final_verification_status="passed",
                metadata={"note": "Verification already passed, no repair needed"},
            )
            self._write_fix_result(output, fix_result)
            return fix_result

        repairs: list[RepairItem] = []
        role_attempts: dict[str, int] = {}
        remaining_failed_checks: dict[str, list[str]] = {}
        any_role_passed = False
        max_attempts = max(stage_retry_limit, 0)

        for role, rvr in verification_result.role_results.items():
            if not rvr.repairable:
                failed_names = [c.name for c in rvr.checks if c.status == "failed"]
                if failed_names:
                    remaining_failed_checks[role] = failed_names
                continue
            integration_dir = resolve_integration_worktree_path(worktrees_dir, role)
            agent = self._get_repair_agent(model_config_ref)
            failed_checks = _repairable_failed_checks(rvr.checks)
            failed = [asdict(c) for c in failed_checks]
            if not failed or max_attempts == 0:
                remaining_failed_checks[role] = [c.name for c in rvr.checks if c.status == "failed"]
                continue

            for _attempt in range(max_attempts):
                role_attempts[role] = role_attempts.get(role, 0) + 1
                try:
                    repair_result = await agent.repair_role(
                        role_integration_worktree=integration_dir,
                        role=role,
                        failed_checks=failed,
                    )
                    repair_items = [
                        RepairItem(**r) if isinstance(r, dict) else r for r in repair_result.get("repairs", [])
                    ]

                    expected_tool_names = {
                        check.name.rsplit(":", 1)[-1]
                        for check in rvr.checks
                        if check.name.startswith(f"tool_activation:{role}:")
                    }
                    re_checks = _validate_role_integration_worktree(
                        role,
                        integration_dir,
                        expected_tool_names,
                    )
                    if not any(c.status == "failed" for c in re_checks):
                        repairs.extend(
                            [
                                replace(item, status="succeeded") if item.status == "attempted" else item
                                for item in repair_items
                            ]
                        )
                        any_role_passed = True
                        remaining_failed_checks.pop(role, None)
                        break
                    repairs.extend(
                        [
                            replace(item, status="failed")
                            if _attempt == max_attempts - 1 and item.status == "attempted"
                            else item
                            for item in repair_items
                        ]
                    )
                    failed_checks = _repairable_failed_checks(re_checks)
                    failed = [asdict(c) for c in failed_checks]
                    remaining_failed_checks[role] = [c.name for c in re_checks if c.status == "failed"]
                    if not failed:
                        break
                except Exception as exc:
                    repair = RepairItem(
                        role=role,
                        action="deepagent_repair",
                        description="DeepAgent repair invocation failed.",
                        status="failed",
                        error=str(exc),
                    )
                    repairs.append(repair)
                    remaining_failed_checks[role] = [c.name for c in rvr.checks if c.status == "failed"]
                    break

        for role, rvr in verification_result.role_results.items():
            if role not in remaining_failed_checks:
                failed_names = [c.name for c in rvr.checks if c.status == "failed"]
                if failed_names and role_attempts.get(role, 0) == 0:
                    remaining_failed_checks[role] = failed_names

        role_failed_check_names: set[str] = set()
        for role_result in verification_result.role_results.values():
            for check in role_result.checks:
                if check.status == "failed":
                    role_failed_check_names.add(check.name)
        run_level_failed = [
            check.name
            for check in verification_result.checks
            if check.status == "failed" and check.name not in role_failed_check_names
        ]
        if run_level_failed:
            remaining_failed_checks["__run__"] = run_level_failed

        final_status = "passed" if not remaining_failed_checks else "failed"
        if repairs:
            status = "completed"
        elif verification_result.repairable:
            status = "failed"
        else:
            status = "failed"

        fix_result = MemberFixResult(
            status=status,
            repairs=repairs,
            verification_path=str(worktrees_dir.parent / "verification.json"),
            final_verification_status=final_status,
            metadata={
                "repair_attempts": len(repairs),
                "role_attempts": role_attempts,
                "remaining_failed_checks": remaining_failed_checks,
                "any_role_repaired": any_role_passed,
                "worktrees_dir": str(worktrees_dir),
            },
        )

        self._write_fix_result(output, fix_result)
        return fix_result

    @staticmethod
    def _write_fix_result(output: Path, result: MemberFixResult) -> None:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(asdict(result), f, ensure_ascii=False, indent=2)


def scan_skill_directory(
    skill_dir: Path,
    *,
    max_files: int = 200,
    max_total_bytes: int = 10 * 1024 * 1024,
) -> dict[str, object]:
    """Statically reject unsafe or malformed generated Skill packages."""
    errors: list[str] = []
    files: list[str] = []
    total_bytes = 0
    root = skill_dir.resolve()
    if not skill_dir.is_dir():
        return {"status": "failed", "errors": [f"skill directory not found: {skill_dir}"], "files": []}
    if not (skill_dir / "SKILL.md").is_file():
        errors.append("missing SKILL.md")

    for path in sorted(skill_dir.rglob("*")):
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            errors.append(f"path escapes skill directory: {path}")
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts):
            errors.append(f"hidden path not allowed: {rel}")
        if path.is_symlink():
            errors.append(f"symlink not allowed: {rel}")
            continue
        if path.is_dir():
            continue
        files.append(rel)
        if path.suffix.lower() in _FORBIDDEN_SKILL_SUFFIXES:
            errors.append(f"forbidden executable file type: {rel}")
        try:
            total_bytes += path.stat().st_size
        except OSError as exc:
            errors.append(f"cannot stat {rel}: {exc}")
        if path.suffix == ".py":
            errors.extend(_scan_python_skill_script(path, rel))

    if len(files) > max_files:
        errors.append(f"file count {len(files)} exceeds limit {max_files}")
    if total_bytes > max_total_bytes:
        errors.append(f"total size {total_bytes} exceeds limit {max_total_bytes}")
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "files": files,
        "file_count": len(files),
        "total_bytes": total_bytes,
    }


def _scan_python_skill_script(path: Path, rel: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError) as exc:
        return [f"python parse failed in {rel}: {exc}"]
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".", 1)[0] for alias in node.names]
            errors.extend(
                f"dangerous import '{root}' in {rel}" for root in roots if root in _SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS
            )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS:
                errors.append(f"dangerous import '{node.module}' in {rel}")
        elif isinstance(node, ast.Call):
            call_name = _qualified_call_name(node.func)
            if call_name in _SKILL_SCRIPT_FORBIDDEN_CALLS:
                errors.append(f"dangerous call '{call_name}' in {rel}")
    return errors


def _qualified_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


__all__ = [
    "HarnessChangeVerifier",
    "HarnessRepairAgent",
]
