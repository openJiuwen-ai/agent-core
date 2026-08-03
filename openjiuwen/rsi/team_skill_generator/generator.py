# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generate Team Skill directories by reusing the vendored swarmskill-creator skill."""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.rsi.member_optimizer.agents.factory import (
    _build_file_tools_for_workspace,
    load_member_optimizer_model,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    parse_json_object_response,
)
from openjiuwen.rsi.model_call import (
    DEFAULT_MODEL_CALL_MAX_RETRIES,
    is_retryable_model_call_failure,
    run_model_call_with_retries,
)

DEFAULT_SWARMSKILL_CREATOR_NAME = "swarmskill-creator"
DEFAULT_MAX_REPAIR_ATTEMPTS = DEFAULT_MODEL_CALL_MAX_RETRIES
SKILL_MD_FILENAME = "SKILL.md"
TeamSkillPlanRunner = Callable[[str, Path, str], Awaitable[Any]]


class TeamSkillGenerationError(RuntimeError):
    """Raised when Team Skill generation cannot produce a valid skill directory."""


class TeamSkillGenerator:
    """Create a Team Skill from a task using the vendored swarmskill-creator workflow.

    The deterministic boundary is path setup, creator skill mounting, validator
    execution, retry control, and final directory discovery. Authoring remains
    delegated to ``swarmskill-creator``.
    """

    def __init__(
        self,
        *,
        model_config_ref: str = "",
        creator_resource_dir: str | Path | None = None,
        max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
        plan_runner: TeamSkillPlanRunner | None = None,
        creator_agent_factory: Callable[..., Any] | None = None,
        validator_runner: Callable[[Path], tuple[bool, str]] | None = None,
    ) -> None:
        self.model_config_ref = str(model_config_ref or "")
        self.creator_resource_dir = (
            Path(creator_resource_dir).expanduser().resolve()
            if creator_resource_dir is not None
            else _default_creator_resource_dir()
        )
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self._plan_runner = plan_runner
        self._creator_agent_factory = creator_agent_factory
        self._validator_runner = validator_runner

    def can_generate(self) -> bool:
        """Return whether the default creator can be built from current config."""
        return bool(
            self.model_config_ref.strip() or self._creator_agent_factory is not None or self._plan_runner is not None
        )

    async def generate(
        self,
        task: str,
        output_dir: str | Path,
        model_config_ref: str | None = None,
    ) -> str:
        """Generate and validate one Team Skill directory under ``output_dir``."""
        normalized_task = str(task or "").strip()
        if not normalized_task:
            raise TeamSkillGenerationError("task is required for Team Skill generation")

        effective_model_config_ref = self.model_config_ref if model_config_ref is None else str(model_config_ref or "")
        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        self._ensure_creator_resources()

        if self._creator_agent_factory is None:
            return await self._generate_from_structured_plan(
                task=normalized_task,
                output_root=output_root,
                model_config_ref=effective_model_config_ref,
            )

        agent = self._create_creator_agent(
            workspace=output_root,
            model_config_ref=effective_model_config_ref,
        )
        selected_skill_dir: Path | None = None
        last_failure = "creator did not produce a Team Skill directory"
        for attempt in range(self.max_repair_attempts + 1):
            session = Session(
                session_id=f"team_skill_generator_{uuid.uuid4().hex}",
                card=getattr(agent, "card", None) or AgentCard(name="team_skill_generator"),
            )
            prompt = (
                _build_create_prompt(normalized_task, output_root)
                if attempt == 0
                else _build_repair_prompt(
                    task=normalized_task,
                    output_root=output_root,
                    team_skill_dir=selected_skill_dir,
                    failure_output=last_failure,
                )
            )
            try:
                await agent.invoke({"query": prompt}, session=session)
            except Exception as exc:
                if not is_retryable_model_call_failure(exc):
                    raise
                selected_skill_dir = None
                last_failure = f"creator invocation failed: {exc}"
                continue

            discovered_skill_dir, discovery_error = _discover_team_skill_dir(output_root)
            if discovered_skill_dir is None:
                selected_skill_dir = None
                last_failure = discovery_error
                continue

            selected_skill_dir = discovered_skill_dir
            validation_ok, validation_output = self._validate(selected_skill_dir)
            if validation_ok:
                return str(selected_skill_dir)
            last_failure = validation_output or "validator failed without output"

        raise TeamSkillGenerationError(
            f"Team Skill generation failed after {self.max_repair_attempts + 1} attempt(s): {last_failure}"
        )

    async def _generate_from_structured_plan(
        self,
        *,
        task: str,
        output_root: Path,
        model_config_ref: str,
    ) -> str:
        previous_error = ""
        last_failure = "structured Team Skill plan was not generated"
        for attempt in range(self.max_repair_attempts + 1):
            raw_plan = await self._call_plan_runner(
                task=task,
                output_root=output_root,
                model_config_ref=model_config_ref,
                previous_error=previous_error,
            )
            try:
                plan = _normalize_team_skill_plan(raw_plan, task=task)
                skill_dir = _write_team_skill_plan(output_root, plan)
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                last_failure = _format_plan_failure(
                    exc,
                    raw_debug_path=(
                        _write_raw_plan_output(output_root, raw_plan, attempt + 1)
                        if isinstance(raw_plan, str)
                        else None
                    ),
                )
                previous_error = last_failure
                continue

            validation_ok, validation_output = self._validate(skill_dir)
            if validation_ok:
                return str(skill_dir)
            last_failure = validation_output or "validator failed without output"
            previous_error = last_failure

        raise TeamSkillGenerationError(
            f"Team Skill generation failed after {self.max_repair_attempts + 1} attempt(s): {last_failure}"
        )

    async def _call_plan_runner(
        self,
        *,
        task: str,
        output_root: Path,
        model_config_ref: str,
        previous_error: str,
    ) -> Any:
        if self._plan_runner is not None:
            return await self._plan_runner(task, output_root, previous_error)
        if not model_config_ref.strip():
            raise TeamSkillGenerationError("model_config_ref is required to generate a Team Skill plan")

        async def call_once() -> str:
            model = load_member_optimizer_model(model_config_ref)
            response = await model.invoke(
                messages=[
                    {
                        "role": "system",
                        "content": _PLAN_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": _build_plan_prompt(
                            task=task,
                            previous_error=previous_error,
                        ),
                    },
                ],
                tools=None,
                temperature=0.2,
                max_tokens=3500,
            )
            return _extract_model_text(response)

        raw = await run_model_call_with_retries(
            call_once,
            operation_name="team skill plan generation",
            max_retries=self.max_repair_attempts,
        )
        return raw

    def _ensure_creator_resources(self) -> None:
        if not (self.creator_resource_dir / SKILL_MD_FILENAME).is_file():
            raise TeamSkillGenerationError(
                f"swarmskill-creator resource is missing SKILL.md: {self.creator_resource_dir}"
            )
        for relative in (
            "reference",
            "templates",
            "scripts/validate_swarmskill.py",
        ):
            if not (self.creator_resource_dir / relative).exists():
                raise TeamSkillGenerationError(
                    f"swarmskill-creator resource is missing {relative}: {self.creator_resource_dir}"
                )

    def _create_creator_agent(
        self,
        *,
        workspace: Path,
        model_config_ref: str,
    ) -> Any:
        if self._creator_agent_factory is not None:
            return self._creator_agent_factory(
                workspace=workspace,
                creator_resource_dir=self.creator_resource_dir,
                model_config_ref=model_config_ref,
            )
        if not model_config_ref.strip():
            raise TeamSkillGenerationError("model_config_ref is required to create a swarmskill-creator agent")

        model = load_member_optimizer_model(model_config_ref)
        file_tools, sys_operation = _build_file_tools_for_workspace(
            workspace=workspace,
            agent_name="team_skill_generator",
            sandbox_roots=[workspace, self.creator_resource_dir],
        )
        return create_deep_agent(
            model=model,
            card=AgentCard(
                name="team_skill_generator",
                description="Creates Team Skill directories via swarmskill-creator.",
            ),
            system_prompt=_CREATOR_SYSTEM_PROMPT,
            tools=file_tools,
            rails=[
                SkillUseRail(
                    skills_dir=str(self.creator_resource_dir.parent),
                    skill_mode="all",
                    enabled_skills=[self.creator_resource_dir.name],
                )
            ],
            workspace=str(workspace),
            sys_operation=sys_operation,
            enable_task_loop=False,
            max_iterations=80,
            language="en",
            restrict_to_work_dir=True,
            auto_create_workspace=True,
        )

    def _validate(self, team_skill_dir: Path) -> tuple[bool, str]:
        if self._validator_runner is not None:
            return self._validator_runner(team_skill_dir)
        return _run_validator(
            team_skill_dir,
            self.creator_resource_dir / "scripts" / "validate_swarmskill.py",
        )


_CREATOR_SYSTEM_PROMPT = """You are an internal Team Skill generator.
Use the mounted swarmskill-creator skill as the source of truth.
Always route requests through swarmskill-creator CREATE and Markdown spec authoring.
Write only inside the provided output workspace.
Do not hand-author a shortcut structure outside the swarmskill-creator workflow.
"""


_PLAN_SYSTEM_PROMPT = """You design Team Skill plans for an agent team runtime.
Return one compact JSON object. Design teammate roles only; the leader is implicit.
The plan must describe role responsibilities, handoff workflow, and acceptance gates.
"""


def _build_plan_prompt(*, task: str, previous_error: str) -> str:
    retry_context = f"\nPrevious plan validation error:\n{previous_error}\n" if previous_error.strip() else ""
    return f"""Create a structured Team Skill plan for this task.

Task:
{task}
{retry_context}
Return JSON only with this shape:
{{
  "team_skill_plan": {{
    "team_name": "short-kebab-case-team-name",
    "description": "one sentence describing the team",
    "roles": [
      {{
        "id": "short-kebab-case-role-id",
        "kind": "ai_agent",
        "purpose": "one-line role purpose",
        "motto": "first-person role motto",
        "responsibilities": ["concrete responsibility"],
        "success_criteria": ["observable success criterion"],
        "forbidden": ["role boundary the role should not cross"],
        "mandatory": ["role discipline the role must follow"],
        "output_sections": ["stable_snake_case_artifact_name"],
        "skills": [],
        "tools": []
      }}
    ],
    "workflow_steps": [
      {{
        "name": "step name",
        "executor": "leader or one role id",
        "action": "what happens",
        "output": "artifact path or decision produced",
        "quality_gate": "observable pass condition"
      }}
    ],
    "acceptance_criteria": ["whole-team completion criterion"]
  }}
}}

Planning requirements:
- Include 2 to 6 teammate roles.
- Each role owns a distinct slice of the task.
- Each role output section is a stable artifact identifier. A section named
  `decision_summary` means the role writes exactly the flat handoff path
  `artifacts/decision_summary.md`.
- Each workflow step names a concrete executor and output. When the output is
  consumed by another role, name the same flat `artifacts/<output_section>.md`
  path that the producing role writes.
- Acceptance criteria judge the final task deliverable, not internal narration.
"""


def _default_creator_resource_dir() -> Path:
    return Path(__file__).resolve().parent / "resources" / DEFAULT_SWARMSKILL_CREATOR_NAME


def _build_create_prompt(task: str, output_root: Path) -> str:
    return f"""CREATE a Team Skill from this task using the swarmskill-creator workflow.

Task:
{task}

Output root:
{output_root}

Hard requirements:
- Use swarmskill-creator CREATE route, not CONVERT or MODIFY.
- Use the Markdown spec route and generate exactly one skill directory under the output root.
- Output shape must be <output_root>/<team-name>/ with SKILL.md, workflow.md, bind.md,
  dependencies.yaml, and roles/<role-id>.md files.
- SKILL.md frontmatter kind must be team-skill.
- SKILL.md frontmatter name must equal the generated directory name.
- SKILL.md roles[] entries must include id, kind, purpose, skills, and tools.
- Role kind values must be ai_agent or human_agent.
- Do not generate scripts/workflow.py; the current evaluator consumes the Markdown spec.
- Use the vendored swarmskill-creator templates, reference material, and validator rules.
"""


def _build_repair_prompt(
    *,
    task: str,
    output_root: Path,
    team_skill_dir: Path | None,
    failure_output: str,
) -> str:
    target = str(team_skill_dir) if team_skill_dir is not None else "<not discovered>"
    return f"""Repair the generated Team Skill using swarmskill-creator's validator feedback.

Original task:
{task}

Output root:
{output_root}

Current Team Skill directory:
{target}

Previous failure output:
{failure_output or "<no failure output>"}

Fix the files in place so the Team Skill remains a Markdown spec Team Skill
with kind: team-skill and no scripts/workflow.py. Keep exactly one generated
Team Skill directory under the output root.
"""


def _discover_team_skill_dir(output_root: Path) -> tuple[Path | None, str]:
    candidates = [
        child.resolve()
        for child in sorted(output_root.iterdir(), key=lambda path: path.name)
        if child.is_dir() and (child / SKILL_MD_FILENAME).is_file()
    ]
    if len(candidates) == 1:
        return candidates[0], ""
    if not candidates:
        return (
            None,
            f"no Team Skill directory containing {SKILL_MD_FILENAME} under {output_root}",
        )
    return (
        None,
        "expected exactly one Team Skill directory under "
        f"{output_root}, found: {', '.join(path.name for path in candidates)}",
    )


def _run_validator(team_skill_dir: Path, validator_path: Path) -> tuple[bool, str]:
    validator = validator_path.expanduser().resolve()
    if not validator.is_file():
        return False, f"validator not found: {validator}"
    process = subprocess.run(
        [sys.executable, str(validator), str(team_skill_dir.expanduser().resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part for part in (process.stdout.strip(), process.stderr.strip()) if part)
    if process.returncode != 0 and not output:
        output = f"validator failed with exit code {process.returncode}"
    return process.returncode == 0, output


def _extract_model_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", item.get("content", ""))))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _write_raw_plan_output(output_root: Path, raw_plan: str, attempt: int) -> Path:
    artifacts_dir = output_root / "_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifacts_dir / f"failed_team_skill_plan_attempt_{attempt:03d}.raw.txt"
    raw_path.write_text(raw_plan, encoding="utf-8")
    return raw_path


def _format_plan_failure(exc: BaseException, *, raw_debug_path: Path | None) -> str:
    message = str(exc)
    if raw_debug_path is not None:
        message = f"{message}; raw_debug_path={raw_debug_path}"
    return message[:2000]


def _normalize_team_skill_plan(raw_plan: Any, *, task: str) -> dict[str, Any]:
    if isinstance(raw_plan, str):
        parsed = parse_json_object_response(raw_plan)
    elif isinstance(raw_plan, dict):
        parsed = raw_plan
    else:
        raise ValueError("Team Skill plan response must be a JSON object")

    plan = parsed.get("team_skill_plan", parsed)
    if not isinstance(plan, dict):
        raise ValueError("team_skill_plan must be a JSON object")

    team_name = _slugify(
        str(plan.get("team_name") or plan.get("name") or ""),
        default=_slugify(task, default="generated-team") + "-team",
    )
    description = _single_line(
        str(plan.get("description") or f"Team Skill for {task}"),
        default=f"Team Skill for {task}",
    )

    roles = _normalize_roles(plan.get("roles"))
    if len(roles) < 2:
        raise ValueError("Team Skill plan must include at least two teammate roles")

    workflow_steps = _normalize_workflow_steps(
        plan.get("workflow_steps"),
        role_ids={str(role["id"]) for role in roles},
    )
    acceptance = _string_list(plan.get("acceptance_criteria")) or [
        "The final deliverable satisfies the original task request.",
        "All role outputs needed by the workflow are integrated.",
    ]
    return {
        "team_name": team_name,
        "description": description,
        "roles": roles,
        "workflow_steps": workflow_steps,
        "acceptance_criteria": acceptance,
    }


def _normalize_roles(raw_roles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_roles, list):
        raise ValueError("team_skill_plan.roles must be a non-empty list")

    roles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_role in enumerate(raw_roles, 1):
        if not isinstance(raw_role, dict):
            continue
        role_id = _slugify(
            str(raw_role.get("id") or raw_role.get("role_id") or raw_role.get("name") or ""),
            default=f"role-{index}",
        )
        if role_id in seen:
            raise ValueError(f"duplicate Team Skill role id: {role_id}")
        seen.add(role_id)
        kind = str(raw_role.get("kind") or "ai_agent").strip()
        if kind not in {"ai_agent", "human_agent"}:
            kind = "ai_agent"
        purpose = _single_line(
            str(raw_role.get("purpose") or ""),
            default=f"Perform the {role_id} role.",
        )
        motto = _single_line(
            str(raw_role.get("motto") or ""),
            default=f"I own the {role_id} slice and make it concrete.",
        ).strip("\"'")
        roles.append(
            {
                "id": role_id,
                "kind": kind,
                "purpose": purpose,
                "motto": motto,
                "responsibilities": _string_list(raw_role.get("responsibilities")) or [purpose],
                "success_criteria": _string_list(raw_role.get("success_criteria"))
                or [f"{role_id} produces an inspectable output."],
                "forbidden": _string_list(raw_role.get("forbidden"))
                or ["Do not take over another role's assigned output."],
                "mandatory": _string_list(raw_role.get("mandatory"))
                or [f"You MUST produce concrete evidence for the {role_id} output."],
                "output_sections": _string_list(raw_role.get("output_sections")) or ["Findings", "Evidence", "Output"],
                "skills": _string_list(raw_role.get("skills")),
                "tools": _string_list(raw_role.get("tools")),
            }
        )
    if not roles:
        raise ValueError("team_skill_plan.roles did not contain any valid roles")
    return roles


def _normalize_workflow_steps(
    raw_steps: Any,
    *,
    role_ids: set[str],
) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    if isinstance(raw_steps, list):
        for index, raw_step in enumerate(raw_steps, 1):
            if not isinstance(raw_step, dict):
                continue
            executor = _single_line(str(raw_step.get("executor") or "leader"), default="leader")
            if executor != "leader" and executor not in role_ids:
                executor = "leader"
            steps.append(
                {
                    "name": _single_line(
                        str(raw_step.get("name") or ""),
                        default=f"Step {index}",
                    ),
                    "executor": executor,
                    "action": _single_line(
                        str(raw_step.get("action") or ""),
                        default="Execute the assigned team workflow step.",
                    ),
                    "output": _single_line(
                        str(raw_step.get("output") or ""),
                        default="Structured step output",
                    ),
                    "quality_gate": _single_line(
                        str(raw_step.get("quality_gate") or ""),
                        default="Output is concrete and can be inspected by the next step.",
                    ),
                }
            )
    if steps:
        return steps
    return [
        {
            "name": "Plan role work",
            "executor": "leader",
            "action": "Dispatch teammate roles with the task context and expected outputs.",
            "output": "Role assignments",
            "quality_gate": "Each role receives a distinct responsibility and output contract.",
        },
        {
            "name": "Integrate role outputs",
            "executor": "leader",
            "action": "Combine role outputs into the final task deliverable.",
            "output": "Final deliverable",
            "quality_gate": "The final deliverable satisfies the task request.",
        },
    ]


def _write_team_skill_plan(output_root: Path, plan: dict[str, Any]) -> Path:
    team_name = str(plan["team_name"])
    skill_dir = (output_root / team_name).resolve()
    if output_root.resolve() not in skill_dir.parents:
        raise RuntimeError(f"generated Team Skill path escapes output root: {skill_dir}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    roles_dir = skill_dir / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)

    roles = list(plan["roles"])
    role_ids = {str(role["id"]) for role in roles}
    for stale_role in roles_dir.glob("*.md"):
        if stale_role.stem not in role_ids:
            stale_role.unlink()

    _write_skill_md(skill_dir / SKILL_MD_FILENAME, plan)
    _write_workflow_md(skill_dir / "workflow.md", plan)
    _write_bind_md(skill_dir / "bind.md", plan)
    _write_dependencies_yaml(skill_dir / "dependencies.yaml", roles)
    for role in roles:
        _write_role_md(roles_dir / f"{role['id']}.md", role)
    return skill_dir


def _write_skill_md(path: Path, plan: dict[str, Any]) -> None:
    roles = [
        {
            "id": role["id"],
            "kind": role["kind"],
            "purpose": role["purpose"],
            "skills": role["skills"],
            "tools": role["tools"],
        }
        for role in plan["roles"]
    ]
    frontmatter = {
        "name": plan["team_name"],
        "description": _frontmatter_description(str(plan["description"])),
        "version": "0.1",
        "kind": "team-skill",
        "roles": roles,
    }
    role_rows = "\n".join(
        (
            "| {id} | {purpose} | every run when its slice is needed | task context and upstream outputs | "
            "{deps} | [roles/{id}.md](roles/{id}.md) |"
        ).format(
            id=role["id"],
            purpose=role["purpose"],
            deps=", ".join([*role["skills"], *role["tools"]]) or "inline persona",
        )
        for role in plan["roles"]
    )
    workflow_items = []
    for index, step in enumerate(plan["workflow_steps"], 1):
        workflow_items.append(
            "".join(
                (
                    f"{index}. **{step['name']}**: `{step['executor']}` produces ",
                    f"{step['output']}. Gate: {step['quality_gate']}",
                )
            )
        )
    workflow_lines = "\n".join(workflow_items)
    preflight_line = "".join(
        (
            "0. **Pre-flight**: read [dependencies.yaml](dependencies.yaml), ",
            "verify required dependencies, and report missing items before dispatch.",
        )
    )
    bind_row = "".join(
        (
            "| [bind.md](bind.md) | Resource limits, behavior rules, and failure handling | ",
            "During pre-flight and exception handling |",
        )
    )
    body = f"""# {plan["team_name"]}

{plan["description"]}

## Workflow

{preflight_line}
{workflow_lines}

## Roles

| id | Purpose | When dispatched | Input | Key dependencies | Role file |
|---|---|---|---|---|---|
{role_rows}

Before dispatching each teammate, read the matching role file and paste the
`## Inline Persona for Teammate` block into the teammate prompt.

## Files

| File | What it contains | When to read |
|---|---|---|
| [workflow.md](workflow.md) | Complete team protocol, quality gates, and final report shape | Before first dispatch |
{bind_row}
| [roles/*.md](roles/) | Per-role persona, boundaries, and output schema | Before dispatching each teammate |
| [dependencies.yaml](dependencies.yaml) | Required skills and tools | Startup dependency check |
"""
    _write_markdown_with_frontmatter(path, frontmatter, body)


def _write_workflow_md(path: Path, plan: dict[str, Any]) -> None:
    role_nodes = "\n".join(f'  L --> R{index}["{role["id"]}"]' for index, role in enumerate(plan["roles"], 1))
    role_edges = "\n".join(
        f'  R{index} --> I["Leader: integrate outputs"]' for index, _role in enumerate(plan["roles"], 1)
    )
    steps = "\n\n".join(
        f"""### Step {index} - {step["name"]}

- **Executor**: {step["executor"]}
- **Input**: task context and available upstream outputs
- **Action**: {step["action"]}
- **Output**: {step["output"]}
- **Serial / Parallel**: follows the mermaid workflow and role dependencies
- **Quality gate**: {step["quality_gate"]}"""
        for index, step in enumerate(plan["workflow_steps"], 1)
    )
    acceptance = "\n".join(f"- {item}" for item in plan["acceptance_criteria"])
    text = f"""# Workflow: {plan["team_name"]}

## Overview

```mermaid
graph LR
  L["Leader: pre-flight and dispatch"]
{role_nodes}
{role_edges}
  I --> F["Final deliverable"]
```

The leader owns orchestration, dispatch, and integration. Teammates own their
declared role slices and return structured outputs for integration.

## Detailed Steps

### Step 0 - Pre-flight dependency check

- **Executor**: leader
- **Input**: [dependencies.yaml](dependencies.yaml)
- **Action**: verify required skills and tools, then dispatch roles with clear output contracts.
- **Output**: role assignments and dependency status
- **Serial / Parallel**: serial setup before teammate work
- **Quality gate**: every dispatched role has a distinct responsibility and expected output.

{steps}

### Step N - Final integration

- **Executor**: leader
- **Input**: all teammate outputs
- **Action**: integrate outputs into the requested deliverable and record unresolved gaps.
- **Output**: final task deliverable
- **Serial / Parallel**: serial integration after role outputs
- **Quality gate**: final output satisfies the acceptance criteria below.

#### Final Report Format

```markdown
# Final Deliverable Summary

## Completed Work
- <deliverable or artifact>

## Role Contributions
- <role>: <contribution>

## Checks
- <acceptance criterion>: <status>

## Open Gaps
- <gap or none>
```

## Acceptance Criteria

{acceptance}
"""
    path.write_text(text, encoding="utf-8")


def _write_bind_md(path: Path, plan: dict[str, Any]) -> None:
    max_parallel = max(1, min(len(plan["roles"]), 6))
    timeout_row = "".join(
        (
            "| Teammate timeout | Retry once if the runtime permits it, then mark the role output as missing ",
            "and integrate remaining evidence. |",
        )
    )
    text = f"""# Execution Guardrails

## Resource Constraints

| Item | Limit | Reason |
|---|---|---|
| `max_parallel_teammates` | {max_parallel} | matches the generated teammate role count |
| `total_wall_clock_budget` | one evaluation case budget | keeps the team inside evaluator timeout |
| `total_token_budget` | configured model budget | prevents one role from exhausting the run |

## Behavioral Constraints

- **Leader orchestration**: the leader dispatches roles, passes task context, and integrates role outputs.
- **Role ownership**: each teammate works inside its declared responsibility boundary.
- **Evidence and handoff**: each teammate returns concrete output that the leader can inspect and integrate.
- **Completion discipline**: the leader checks the final deliverable against the acceptance criteria before finishing.

## Failure Handling

### Teammate failure

| Failure mode | Response |
|---|---|
{timeout_row}
| Malformed teammate output | Re-dispatch once with the role output schema inlined. |
| Missing role evidence | Record the missing evidence in the final summary and avoid claiming that slice is complete. |

### Input over-scale degradation

| Trigger condition | Degraded mode |
|---|---|
| Task exceeds available runtime budget | Prioritize roles that directly affect final deliverable correctness. |
| Required dependency unavailable | Continue with inline-persona mode and record the limitation. |

### Escalation rules

- If most role outputs are missing, emit an incomplete final summary with explicit missing roles.
- If the time budget is reached, stop dispatching new work and finalize with available evidence.
"""
    path.write_text(text, encoding="utf-8")


def _write_dependencies_yaml(path: Path, roles: list[dict[str, Any]]) -> None:
    skills = sorted({item for role in roles for item in role["skills"]})
    tools = sorted({item for role in roles for item in role["tools"]})
    payload = {
        "skills": [
            {
                "name": skill,
                "source": "local",
                "required": False,
                "purpose": f"Supports roles that declared the {skill} skill.",
            }
            for skill in skills
        ],
        "tools": [
            {
                "name": tool,
                "required": False,
                "purpose": f"Supports roles that declared the {tool} tool.",
            }
            for tool in tools
        ],
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_role_md(path: Path, role: dict[str, Any]) -> None:
    section_names = role["output_sections"][:4]
    schema_sections = "\n\n".join(f"### {section}\n- <concrete {section.lower()} item>" for section in section_names)
    artifact_paths = [_artifact_path_for_section(section) for section in section_names]
    artifact_contract = "\n".join(
        f"- Use exactly `{artifact_path}` for the `{section}` handoff output."
        for section, artifact_path in zip(section_names, artifact_paths, strict=True)
    )
    success = "\n".join(f"- {item}" for item in role["success_criteria"])
    responsibilities = "\n".join(f"- {item}" for item in role["responsibilities"])
    forbidden = "\n".join(f"- {item}" for item in role["forbidden"])
    mandatory = "\n".join(f"- {item}" for item in role["mandatory"])
    output_format = "\n\n".join(f"### {section}\n- <{section.lower()} item>" for section in section_names)
    artifact_output_format = "\n".join(f"- {artifact_path}" for artifact_path in artifact_paths)
    write_instruction = "".join(
        (
            "You MUST write each handoff output to its exact listed flat `artifacts/*.md` path ",
            "before reporting READY.",
        )
    )
    claim_instruction = "".join(
        (
            "You MUST call claim_task(status=completed) after those artifacts satisfy the task; ",
            "reporting READY alone does not complete the Team task.",
        )
    )
    text = f"""# Role: {role["id"]}

## Identity

> *"{role["motto"]}"*

This role owns a distinct part of the team workflow. It turns the task context
into concrete evidence, decisions, or deliverables for the leader to integrate.

## Success Criteria

{success}

**Focus areas**: {", ".join(role["responsibilities"])}.

## Boundary

**Forbidden**:
{forbidden}

**Mandatory**:
{mandatory}

## Handoff Artifacts

Write these files when this role completes its output:
{artifact_contract}

These are flat paths under `artifacts/`; downstream roles read the exact same
paths.

## Output Schema

```markdown
## Role: {role["id"]}

{schema_sections}

### Artifact Files
{artifact_output_format}

### Verdict
- <READY | BLOCKED | NEEDS_REVISION>
```

## Inline Persona for Teammate

```
ROLE: {role["id"]} in a Team Skill.

You own this slice of the task:
{responsibilities}

You MUST produce concrete, inspectable output for the leader.
{write_instruction}
{claim_instruction}
You MUST follow the role boundary and output schema.
You MUST surface uncertainty or missing inputs explicitly.
You MUST NOT take over another role's assigned slice.

INPUTS YOU WILL RECEIVE:
- task: the original user request
- context: relevant upstream role outputs, files, or constraints
- output_contract: the section of the final deliverable this role owns

OUTPUT FORMAT:

## Role: {role["id"]}

{output_format}

### Artifact Files
{artifact_output_format}

### Verdict
- <READY | BLOCKED | NEEDS_REVISION>
```
"""
    path.write_text(text, encoding="utf-8")


def _artifact_path_for_section(section: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", str(section or "").strip()).strip("_")
    safe_name = re.sub(r"_{2,}", "_", safe_name).lower() or "output"
    return f"artifacts/{safe_name}.md"


def _write_markdown_with_frontmatter(
    path: Path,
    frontmatter: dict[str, Any],
    body: str,
) -> None:
    path.write_text(
        "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n\n" + body.lstrip(),
        encoding="utf-8",
    )


def _frontmatter_description(description: str) -> str:
    what = _single_line(description, default="Team Skill for multi-role task execution.")
    return (
        f"{what}\n"
        "Use when the task benefits from distinct teammate roles and leader integration.\n"
        "Do NOT use for a task that is clearly single-step and single-role."
    )


def _slugify(value: str, *, default: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:80].strip("-") or default


def _single_line(value: str, *, default: str) -> str:
    text = " ".join(str(value or "").split())
    return text or default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _single_line(str(item or ""), default="")
        if text:
            result.append(text)
    return result
