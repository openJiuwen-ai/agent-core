# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Executable action policy for current Auto Harness member optimization.

The current auto harness consumes member capabilities as ExpertHarness packages.
MemberOptimizer therefore only plans and executes local file changes within the
package surfaces that ExpertHarness loader can read today.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from openjiuwen.rsi.harness_rsi.schema import ActionDefinition

ALLOWED_ACTION_GROUPS: Final[frozenset[str]] = frozenset(
    {
        "prompt",
        "rail",
        "skill",
        "tool",
    }
)

ALLOWED_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "add",
        "modify",
        "remove",
        "search",
    }
)

ALLOWED_EXECUTOR_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
    }
)

GROUP_PATH_PREFIXES: Final[dict[str, tuple[str, ...]]] = {
    "prompt": (
        "identity.md",
        "soul.md",
        "prompt_sections/sections.yaml",
        "prompt_sections/files/",
    ),
    "tool": ("tools/",),
    "rail": ("rails/",),
    "skill": ("skills/",),
}


@dataclass(frozen=True, slots=True)
class ActionPolicyCheck:
    """Validation result for one member optimization action."""

    valid: bool
    errors: tuple[str, ...] = ()


def action_policy_prompt() -> str:
    """Return the current auto harness executable action policy for prompts."""
    path_lines = "\n".join(
        f"- {group}: {', '.join(prefixes)}" for group, prefixes in sorted(GROUP_PATH_PREFIXES.items())
    )
    return f"""## Current Auto Harness Action Policy

Allowed action_group values:
{chr(10).join(f"- {group}" for group in sorted(ALLOWED_ACTION_GROUPS))}

Allowed operation values:
{chr(10).join(f"- {operation}" for operation in sorted(ALLOWED_OPERATIONS))}

Allowed target_path and declared_write_paths by action_group:
{path_lines}

Current member optimizer actions are limited to local ExpertHarness package file
changes on prompt, skill, tool, and rail surfaces.

Operation rules:
- `search` is allowed only for `skill/search`.
- `skill/search` must set a non-empty English `candidate_query`, keep
  `install_ref` empty, target `skills/`, and declare `skills/` plus
  `skills/skills.yaml`. If search is unavailable or fails, use an explicit
  dependency-failed `skill/add` fallback.
- `skill/add` must target `skills/<snake_name>/SKILL.md`, declare that
  `SKILL.md` plus `skills/skills.yaml`, and keep `skills/skills.yaml`
  mounting the parent `skills` directory.
- For every non-search action, `candidate_query` and `install_ref` must be empty.
- Do not output config, mcp, dependency, test, documentation, memory,
  knowledge, context, workflow, install, or global environment actions.
Prompt rules:
- `identity.md` maps to the `identity` section with priority 10.
- `soul.md` maps to the `soul` section with priority 20.
- Extension sections must be declared in `prompt_sections/sections.yaml` and
  backed by `prompt_sections/files/*.md`.
- `prompt/remove` may delete only extension sections, never `identity.md` or
  `soul.md`.
- Surface selection:
  - `identity.md` is only for role identity and duty-boundary changes.
  - `soul.md` is only for a small number of durable operating principles.
  - Specific workflows, checklists, verification procedures, and task recovery
    routines belong in `prompt_sections/files/*.md`, not in `identity.md` or
    `soul.md`.
  - Skills hold reusable methodology or domain capability. A new Skill requires
    the same causal mechanism in at least two distinct cases and a trigger that
    is observable from the public task input or early runtime evidence. One
    verifier subitem, literal case IDs, fixed expected row counts, and known
    answer filenames are not reusable Skill content. Use a prompt section for a
    single-case instruction hypothesis.
  - Tools are only for deterministic executable capability.

Tool rules:
- `tool/add` must target a loadable Python file under `tools/*.py`, declare
  both that Python file and `tools/tools.yaml`, and set
  `constraints.class_name` to the Tool subclass name.
- `tools/tools.yaml` must register package-local tools as mappings like
  `{{"file": "tools/<name>.py", "class_name": "<ToolClass>"}}`.

Rail rules:
- `rail/add` must target a loadable Python file under `rails/*.py`, declare
  both that Python file and `rails/rails.yaml`, and set
  `constraints.class_name` to the AgentRail subclass name.
- `rails/rails.yaml` must register package-local rails as mappings like
  `{{"file": "rails/<name>.py", "class_name": "<AgentRailClass>"}}`.
- Rails are for lifecycle and action-transition control. Do not use them as a
  static Prompt or Skill hidden inside Python source.

"""


def filter_action_definitions(
    action_definitions: list[ActionDefinition],
) -> list[ActionDefinition]:
    """Keep config-provided definitions that match the current action policy."""
    filtered: list[ActionDefinition] = []
    for definition in action_definitions:
        if definition.group not in ALLOWED_ACTION_GROUPS:
            continue
        if _operation_allowed(definition.group, definition.operation):
            filtered.append(definition)
    return filtered


def sanitize_allowed_tools(tools: list[str] | None) -> list[str]:
    """Keep only file read/write/edit tools supported by current execution."""
    return [tool for tool in tools or [] if tool in ALLOWED_EXECUTOR_TOOLS]


def validate_action_policy(
    action: Any,
    selected_roles: set[str] | None = None,
) -> ActionPolicyCheck:
    """Validate an action dict or MemberOptimizationAction against current policy."""
    errors: list[str] = []
    selected_roles = selected_roles or set()

    action_id = str(_get(action, "action_id", ""))
    role = str(_get(action, "role", ""))
    action_group = str(_get(action, "action_group", ""))
    operation = str(_get(action, "operation", ""))
    target_path = str(_get(action, "target_path", ""))
    declared_paths = _list_value(_get(action, "declared_write_paths", []))
    candidate_query = str(_get(action, "candidate_query", ""))
    install_ref = str(_get(action, "install_ref", ""))
    allowed_tools = _list_value(_get(action, "allowed_tools", []))
    constraints = _dict_value(_get(action, "constraints", {}))

    if not action_id:
        errors.append("missing action_id")
    if not role:
        errors.append("missing role")
    elif selected_roles and role not in selected_roles:
        errors.append(f"role '{role}' not in selected roles")

    if action_group not in ALLOWED_ACTION_GROUPS:
        errors.append(f"unsupported action_group '{action_group}'")
    if operation not in ALLOWED_OPERATIONS:
        errors.append(f"unsupported operation '{operation}'")
    elif action_group and not _operation_allowed(action_group, operation):
        errors.append(f"operation '{operation}' is not allowed for action_group '{action_group}'")

    if candidate_query and not (action_group == "skill" and operation == "search"):
        errors.append("candidate_query must be empty")
    if install_ref:
        errors.append("install_ref must be empty")

    if action_group == "skill" and operation == "search":
        if not candidate_query.strip():
            errors.append("skill/search requires a non-empty candidate_query")
        if _normalize_policy_path(target_path) != "skills":
            errors.append("skill/search target_path must be skills/")
        if "skills" not in {_normalize_policy_path(path) for path in declared_paths}:
            errors.append("skill/search must declare skills/")
        if "skills/skills.yaml" not in {_normalize_policy_path(path) for path in declared_paths}:
            errors.append("skill/search must declare skills/skills.yaml")

    unsupported_tools = [tool for tool in allowed_tools if tool not in ALLOWED_EXECUTOR_TOOLS]
    if unsupported_tools:
        errors.append(f"unsupported allowed_tools {unsupported_tools}")

    if not target_path:
        errors.append("target_path is empty")
    else:
        errors.extend(_validate_policy_path(target_path, action_group, "target_path"))

    normalized_declared_paths: set[str] = set()
    for declared_path in declared_paths:
        normalized_declared_paths.add(_normalize_policy_path(declared_path))
        errors.extend(_validate_policy_path(declared_path, action_group, "declared_write_path"))

    normalized_target_path = _normalize_policy_path(target_path)

    if action_group == "prompt":
        if operation == "add" and normalized_target_path in {"identity.md", "soul.md"}:
            errors.append("prompt/add may only create extension prompt sections")
        if operation == "remove" and normalized_target_path in {"identity.md", "soul.md"}:
            errors.append("prompt/remove cannot delete identity.md or soul.md")
        if normalized_target_path.startswith("prompt_sections/files/"):
            section_name = str(constraints.get("section_name", "") or "").strip()
            if not section_name:
                errors.append("prompt section actions require constraints.section_name")
            if "priority" in constraints:
                try:
                    int(constraints["priority"])
                except (TypeError, ValueError):
                    errors.append("constraints.priority must be an integer when provided")

    if action_group == "skill" and operation == "add":
        if not _is_skill_entry_path(normalized_target_path):
            errors.append("skill/add target_path must be skills/<snake_name>/SKILL.md")
        if normalized_target_path not in normalized_declared_paths:
            errors.append("skill/add must declare its target SKILL.md file")
        if "skills/skills.yaml" not in normalized_declared_paths:
            errors.append("skill/add must declare skills/skills.yaml")

    prompt_manifest_missing = (
        normalized_target_path.startswith("prompt_sections/files/")
        and "prompt_sections/sections.yaml" not in normalized_declared_paths
    )
    if action_group == "prompt" and prompt_manifest_missing:
        errors.append(
            "prompt section file changes must also declare prompt_sections/sections.yaml so the file can be mounted"
        )

    skill_manifest_missing = (
        normalized_target_path.startswith("skills/")
        and normalized_target_path != "skills/skills.yaml"
        and "skills/skills.yaml" not in normalized_declared_paths
    )
    if action_group == "skill" and skill_manifest_missing:
        errors.append("skill file changes must also declare skills/skills.yaml so the skill can be mounted")

    tool_manifest_missing = (
        normalized_target_path.startswith("tools/")
        and normalized_target_path != "tools/tools.yaml"
        and "tools/tools.yaml" not in normalized_declared_paths
    )
    if action_group == "tool" and tool_manifest_missing:
        errors.append("tool implementation changes must also declare tools/tools.yaml so the tool can be mounted")

    if action_group == "tool" and operation == "add":
        if not _is_python_resource_path(normalized_target_path, "tools"):
            errors.append("tool/add target_path must be a package-local tools/*.py file")
        if normalized_target_path not in normalized_declared_paths:
            errors.append("tool/add must declare its target Python file")
        if "tools/tools.yaml" not in normalized_declared_paths:
            errors.append("tool/add must declare tools/tools.yaml")
        if not str(constraints.get("class_name", "") or "").strip():
            errors.append("tool/add requires constraints.class_name")

    if action_group == "tool" and operation == "remove" and normalized_target_path == "tools/tools.yaml":
        errors.append("tool/remove must target a package-local tool, not tools/tools.yaml")

    rail_manifest_missing = (
        normalized_target_path.startswith("rails/")
        and normalized_target_path != "rails/rails.yaml"
        and "rails/rails.yaml" not in normalized_declared_paths
    )
    if action_group == "rail" and rail_manifest_missing:
        errors.append("rail implementation changes must also declare rails/rails.yaml so the rail can be mounted")

    if action_group == "rail" and operation == "add":
        if not _is_python_resource_path(normalized_target_path, "rails"):
            errors.append("rail/add target_path must be a package-local rails/*.py file")
        if normalized_target_path not in normalized_declared_paths:
            errors.append("rail/add must declare its target Python file")
        if "rails/rails.yaml" not in normalized_declared_paths:
            errors.append("rail/add must declare rails/rails.yaml")
        if not str(constraints.get("class_name", "") or "").strip():
            errors.append("rail/add requires constraints.class_name")

    if action_group == "rail" and operation == "remove" and normalized_target_path == "rails/rails.yaml":
        errors.append("rail/remove must target a package-local rail, not rails/rails.yaml")

    return ActionPolicyCheck(valid=not errors, errors=tuple(errors))


def _operation_allowed(action_group: str, operation: str) -> bool:
    if operation == "search":
        return action_group == "skill"
    return operation in {"add", "modify", "remove"}


def _get(action: Any, field: str, default: Any) -> Any:
    if isinstance(action, dict):
        return action.get(field, default)
    return getattr(action, field, default)


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _validate_policy_path(path: str, action_group: str, field_name: str) -> list[str]:
    errors: list[str] = []
    normalized = _normalize_policy_path(path)
    if not normalized:
        return [f"{field_name} is empty"]
    if Path(path).is_absolute():
        errors.append(f"{field_name} is absolute: {path}")
    if ".." in Path(path).parts:
        errors.append(f"{field_name} contains '..': {path}")

    allowed_prefixes = GROUP_PATH_PREFIXES.get(action_group)
    if allowed_prefixes and not _matches_allowed_prefix(normalized, allowed_prefixes):
        errors.append(f"{field_name} '{path}' is not allowed for action_group '{action_group}'")
    if action_group == "prompt" and normalized.startswith("prompt_sections/"):
        if normalized != "prompt_sections/sections.yaml" and not normalized.startswith("prompt_sections/files/"):
            errors.append(
                f"{field_name} '{path}' is not loadable by ExpertHarness; use "
                "prompt_sections/sections.yaml or prompt_sections/files/<name>.md"
            )
    return errors


def _normalize_policy_path(path: str) -> str:
    return Path(str(path).replace("\\", "/")).as_posix().strip("/")


def _matches_allowed_prefix(path: str, allowed_prefixes: tuple[str, ...]) -> bool:
    for prefix in allowed_prefixes:
        if prefix.endswith("/"):
            normalized_prefix = prefix.strip("/")
            if path == normalized_prefix or path.startswith(prefix):
                return True
            continue
        if path == prefix:
            return True
    return False


def _is_python_resource_path(path: str, folder: str) -> bool:
    parts = Path(path).parts
    return len(parts) == 2 and parts[0] == folder and parts[1].endswith(".py")


def _is_skill_entry_path(path: str) -> bool:
    parts = Path(path).parts
    name = parts[1] if len(parts) == 3 else ""
    return len(parts) == 3 and parts[0] == "skills" and _is_snake_name(name) and parts[2] == "SKILL.md"


def _is_snake_name(name: str) -> bool:
    if not name or name != name.lower():
        return False
    if name.startswith("_") or name.endswith("_") or "__" in name:
        return False
    compact = name.replace("_", "")
    return compact.isalnum() and not compact[0].isdigit()


__all__ = [
    "ALLOWED_ACTION_GROUPS",
    "ALLOWED_EXECUTOR_TOOLS",
    "ALLOWED_OPERATIONS",
    "GROUP_PATH_PREFIXES",
    "ActionPolicyCheck",
    "action_policy_prompt",
    "filter_action_definitions",
    "sanitize_allowed_tools",
    "validate_action_policy",
]
