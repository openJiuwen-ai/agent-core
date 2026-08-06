# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Member action execution — three-layer parallelism with isolated worktrees and serial merge.

Per feat_009 rule.md Section 3.7 and design.md Section 4.9.
- Global waves: serial
- Same-wave role workers: bounded parallel
- Role-local subwaves: path-overlap-aware, serial per subwave
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.rsi.member_optimizer.action_groups import (
    ALLOWED_EXECUTOR_TOOLS,
    build_action_waves,
    build_role_subwaves,
    resolve_declared_paths,
    sanitize_allowed_tools,
    validate_action_policy,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    create_action_execution_agent,
    load_member_optimizer_model,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    extract_agent_text,
    parse_json_object_response,
)
from openjiuwen.rsi.member_optimizer.schema import (
    MemberActionExecutionResult,
    MemberActionMergeResult,
    MemberOptimizationAction,
    MemberOptimizationPlan,
)
from openjiuwen.rsi.member_optimizer.verification import (
    _validate_package_python_source,
)
from openjiuwen.rsi.member_optimizer.worktree_coordinator import (
    MEMBER_WORKTREES_DIR_NAME,
    MemberWorktreeCoordinator,
)
from openjiuwen.rsi.model_call import (
    run_model_call_with_retries,
)


@dataclass(frozen=True)
class _ActionExecutionContext:
    integration_dir: Path | None
    run_dir: Path
    worktrees_dir: str | None
    wave_index: int
    model_config_ref: str
    plan: MemberOptimizationPlan


@dataclass(frozen=True)
class _SubwaveMergeRequest:
    role: str
    wave_index: int
    subwave_index: int
    results: list[MemberActionExecutionResult]
    integration_dir: Path
    run_dir: Path


_ACTION_EXECUTION_PROMPT = (Path(__file__).resolve().parent / "agents" / "prompts" / "action_execution.md").read_text(
    encoding="utf-8"
)


def _normalize_rel_path(path: str | Path) -> str:
    return Path(path).as_posix().strip("/")


def _snapshot_files(root: Path, rel_paths: list[str] | None = None) -> dict[str, str]:
    """Snapshot files below root or below selected relative paths."""
    roots = rel_paths or ["."]
    snapshot: dict[str, str] = {}

    for rel_path in roots:
        candidate = root / rel_path
        if candidate.is_file():
            rel = _normalize_rel_path(candidate.relative_to(root))
            snapshot[rel] = _hash_file(candidate)
            continue
        if candidate.is_dir():
            for file_path in sorted(p for p in candidate.rglob("*") if p.is_file()):
                rel = _normalize_rel_path(file_path.relative_to(root))
                snapshot[rel] = _hash_file(file_path)

    return snapshot


def _short_token(value: str) -> str:
    return hashlib.sha1(str(value or "default").encode("utf-8")).hexdigest()[:8]


def _canonical_skill_identifier(value: str) -> str:
    """Return the package-local skill identifier used by dirs and frontmatter."""
    text = str(value or "").replace("\\", "/").strip().split("/")[-1]
    chars: list[str] = []
    previous_underscore = False
    for char in text:
        if char.isalnum():
            chars.append(char.lower())
            previous_underscore = False
            continue
        if char in {"_", "-", " ", "."} and not previous_underscore:
            chars.append("_")
            previous_underscore = True
    return "".join(chars).strip("_") or "member_skill"


def _skill_name_from_skill_md_path(path: str | Path) -> str:
    normalized = _normalize_rel_path(path)
    parts = Path(normalized).parts
    if len(parts) >= 3 and parts[0] == "skills" and parts[-1] == "SKILL.md":
        return _canonical_skill_identifier(parts[-2])
    return ""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    all_paths = set(before) | set(after)
    return sorted(path for path in all_paths if before.get(path) != after.get(path))


def _sync_skill_registry_for_written_files(
    *,
    action_worktree: Path,
    action: MemberOptimizationAction,
    declared_paths: list[str],
    written_files: list[str],
) -> list[str]:
    if action.action_group != "skill":
        return written_files

    additions: list[str] = []
    for path in written_files:
        normalized = _normalize_rel_path(path)
        parts = Path(normalized).parts
        if len(parts) >= 3 and parts[0] == "skills" and parts[-1] == "SKILL.md":
            additions.append(Path(*parts[:-1]).as_posix())

    if not additions:
        return written_files

    registry_rel = "skills/skills.yaml"
    if not _path_allowed_by_declared(registry_rel, declared_paths):
        raise ValueError(f"skill action must declare {registry_rel} so the skill can be mounted")

    registry_path = action_worktree.resolve() / registry_rel
    existing = _load_registry_values(registry_path, "skills")
    merged = list(existing)
    for addition in additions:
        if addition not in merged:
            merged.append(addition)

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        yaml.safe_dump({"skills": merged}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return sorted(set([*written_files, registry_rel]))


def _normalize_skill_frontmatter_name_for_written_files(
    *,
    action_worktree: Path,
    action: MemberOptimizationAction,
    written_files: list[str],
) -> list[str]:
    if action.action_group != "skill":
        return written_files

    changed = list(written_files)
    for rel_path in written_files:
        normalized = _normalize_rel_path(rel_path)
        skill_name = _skill_name_from_skill_md_path(normalized)
        if not skill_name:
            continue
        path = action_worktree / normalized
        if not path.is_file():
            continue
        current = path.read_text(encoding="utf-8")
        updated = _normalize_skill_frontmatter_name(
            current,
            skill_name=skill_name,
            fallback_description=_skill_trigger_description(action),
        )
        if updated != current:
            path.write_text(updated, encoding="utf-8")
            changed.append(normalized)
    return sorted(set(changed))


def _normalize_skill_frontmatter_name(
    content: str,
    *,
    skill_name: str,
    fallback_description: str = "",
) -> str:
    canonical_name = _canonical_skill_identifier(skill_name)
    description = str(fallback_description or "").strip() or "Reusable member harness skill."
    lines = content.splitlines()
    body_lines = lines
    frontmatter: dict[str, Any] = {}

    if lines and lines[0].strip() == "---":
        end_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            -1,
        )
        if end_index > 0:
            raw_frontmatter = "\n".join(lines[1:end_index])
            try:
                loaded = yaml.safe_load(raw_frontmatter) if raw_frontmatter.strip() else {}
            except yaml.YAMLError:
                loaded = _salvage_skill_frontmatter(raw_frontmatter)
            if isinstance(loaded, dict):
                frontmatter = dict(loaded)
            body_start = end_index + 1
            body_lines = lines[body_start:]

    frontmatter["name"] = canonical_name
    if not str(frontmatter.get("description") or "").strip():
        frontmatter["description"] = description

    frontmatter_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    body = "\n".join(body_lines).strip("\n")
    if body:
        return f"---\n{frontmatter_text}\n---\n\n{body}\n"
    return f"---\n{frontmatter_text}\n---\n"


def _validate_generated_skill_contract(content: str) -> None:
    """Validate transport-independent properties of a native ``SKILL.md``.

    Skill usefulness is established by target-case evaluation, not by forcing a
    fixed documentation template. Optimizer provenance stays in optimization
    artifacts instead of becoming runtime instructions.
    """
    lines = content.splitlines()
    body_start = 0
    frontmatter: dict[str, Any] = {}
    if lines and lines[0].strip() == "---":
        body_start = next(
            (index + 1 for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            len(lines),
        )
        frontmatter_end = body_start - 1
        raw_frontmatter = "\n".join(lines[1:frontmatter_end])
        loaded = yaml.safe_load(raw_frontmatter) if raw_frontmatter.strip() else {}
        if isinstance(loaded, dict):
            frontmatter = loaded
    if frontmatter:
        description = str(frontmatter.get("description", "") or "").strip()
        if not description:
            raise ValueError("generated SKILL.md frontmatter description is required")
    body = "\n".join(lines[body_start:]).strip()
    if not body:
        raise ValueError("generated SKILL.md must contain runtime instructions")


def _salvage_skill_frontmatter(raw_frontmatter: str) -> dict[str, str]:
    """Recover scalar skill metadata before re-serializing malformed YAML."""
    salvaged: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if line[:1].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in {"name", "description"}:
            continue
        value = value.strip().strip("\"'")
        if value and value not in {">", ">-", "|", "|-"}:
            salvaged[key] = value
    return salvaged


def _should_run_action(
    action: MemberOptimizationAction,
    action_status_by_id: dict[str, str],
) -> tuple[bool, str]:
    """Return whether an action should run under its dependency condition."""
    if not action.depends_on or action.run_if == "always":
        return True, ""

    dependency_statuses = {dep: action_status_by_id.get(dep, "") for dep in action.depends_on}
    missing = [dep for dep, status in dependency_statuses.items() if not status]
    if missing:
        return False, f"dependency status unavailable: {missing}"

    failed = [dep for dep, status in dependency_statuses.items() if status == "failed"]
    if action.run_if == "dependency_failed":
        if failed:
            return True, ""
        return False, "dependency did not fail"

    if failed:
        return False, f"dependency failed: {failed}"
    return True, ""


def _path_allowed_by_declared(path: str, declared_paths: list[str]) -> bool:
    normalized = _normalize_rel_path(path)
    for declared in declared_paths:
        declared_norm = _normalize_rel_path(declared)
        if not declared_norm:
            continue
        if normalized == declared_norm or normalized.startswith(f"{declared_norm}/"):
            return True
    return False


def _is_runtime_workspace_metadata(path: str) -> bool:
    normalized = _normalize_rel_path(path)
    if normalized in {"AGENT.md", "SOUL.md", "IDENTITY.md", "USER.md", "HEARTBEAT.md"}:
        return True
    parts = Path(normalized).parts
    return bool(
        parts
        and parts[0] in {"agents", "context", "memory", "messages", "skills", "todo"}
        and parts[-1] == ".workspace"
    ) or bool(parts and parts[0] in {"context", "memory"} and len(parts) > 1)


def _extract_model_response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", item.get("content", "")) or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _action_resource_guidance(action: MemberOptimizationAction) -> str:
    class_name = str(action.constraints.get("class_name", "") or "").strip()
    target_path = _normalize_rel_path(action.target_path)

    if action.action_group == "tool" and (action.operation == "add" or "add" in str(action.action_type or "")):
        class_hint = class_name or "a descriptive Tool subclass"
        return (
            f"Create `{target_path}` with a loadable `{class_hint}` class that "
            "inherits from `openjiuwen.core.foundation.tool.Tool`. Also update "
            "`tools/tools.yaml` with a `tools` entry containing `file` and "
            "`class_name`. The ToolCard must define `input_params` as a JSON "
            "Schema with top-level `type: object`. Keep the implementation "
            "compact and package-local, with small helper methods and explicit "
            "structured results. The generated tool must pass package-local "
            "Python safety verification: do not import httpx, os, pathlib, "
            "requests, shutil, socket, subprocess, sys, or urllib, and do not "
            "call __import__, compile, eval, exec, input, or open. Design tools "
            "to operate on explicit input payloads supplied by the agent, such "
            "as file contents, selectors, rules, or structured observations, "
            "rather than reading files, launching processes, installing "
            "packages, or contacting external services. Give the ToolCard a "
            "specific `name` and `description` that state when the role should "
            "search for or load this tool, the concrete defect it detects or "
            "repairs, and the kind of input payload it expects. Implement "
            "`invoke` and `stream` so the tool can be mounted, discovered by "
            "progressive tool search, and called by the role. It must return a "
            "machine-checkable result that changes the caller's next action. Do not "
            "approximate an available parser, compiler, linter, or test command "
            "with a weaker string heuristic, and do not create a passive validator."
        )
    if action.action_group == "skill" and action.operation == "add":
        return (
            f"Create `{target_path}` as a package-local skill. It must be a "
            "`SKILL.md` file with YAML frontmatter containing a non-empty "
            "`description`; frontmatter `name` must exactly equal the "
            "`skills/<snake_name>` directory name and use underscores, not "
            "hyphens. Base the description on the decision contract's causal "
            "distinction, using the public task only for broad task-area vocabulary. "
            "The trigger must not broaden to a sibling mechanism listed only as a "
            "scope boundary. "
            "Write the description as a direct runtime trigger such as `Use when ...`; "
            "never say to create, add, update, generate, or write a Skill. "
            "Author a native reusable Skill, not an "
            "optimizer audit report. Teach only the procedural knowledge needed to "
            "preserve required_behavior and avoid forbidden_behavior. Treat the "
            "decision_contract as directional causal knowledge: teach its "
            "required_action, use its acceptance_observable to stop investigation, "
            "and do not reintroduce the wrong_decision as an optional branch under "
            "the same trigger. Preserve the meaning and direction of the causal "
            "distinction, required action, acceptance observable, and scope boundary, "
            "but rewrite, combine, or shorten them into the clearest native Skill. "
            "Do not promote syntax from the failed patch into a "
            "general rule or prescribe a concrete patch recipe unless the contract's "
            "observable established it. Include the "
            "decisive distinction, the observable that ends investigation, the smallest "
            "justified action, and a concrete non-tautological acceptance probe that "
            "checks the positive case and nearest boundary. Merely avoiding an exception "
            "does not establish value, ownership, ordering, or lifecycle semantics. "
            "Use any Markdown "
            "structure and amount of detail that makes that procedure effective; do not "
            "pad the Skill for a fixed template or optimize for character count. Keep "
            "case ids, optimizer provenance, evaluator-only names, and manifest fields "
            "out of runtime instructions. Move optional long reference material into Skill "
            "resources only when it is genuinely needed. Return the complete canonical "
            "`SKILL.md` as one `file_writes` item; the executor updates "
            "`skills/skills.yaml` after validating it. Do not clone, "
            "search, or install external resources for skill/add."
        )
    if action.action_group == "skill":
        return (
            f"Keep `{target_path}` a valid package-local skill resource. If "
            "`skills/skills.yaml` is declared, keep it valid YAML and mount the "
            "parent `skills` directory."
        )
    if action.action_group == "tool":
        manifest = "tools/tools.yaml"
        return (
            f"Keep `{target_path}` loadable and keep `{manifest}` valid YAML "
            "if the manifest is one of the declared write paths."
        )
    return "Apply the requested local ExpertHarness package change directly."


def _runtime_contract_projection(
    action: MemberOptimizationAction,
) -> list[dict[str, Any]]:
    """Project optimizer hypotheses onto the public runtime authoring boundary.

    Analyzer evidence remains available to candidate selection and evaluation,
    but an authored Skill, Tool, or Prompt Section may only learn the public
    trigger, generalized behavior, and sanitized decision change. In particular,
    case IDs, verifier identifiers, trajectory pointers, and optimizer rationale
    never cross this boundary.
    """
    raw_contracts = action.constraints.get("optimization_contracts", [])
    if not isinstance(raw_contracts, list):
        return []

    projected: list[dict[str, Any]] = []
    for raw in raw_contracts:
        if not isinstance(raw, dict):
            continue
        tasks: list[str] = []
        for trigger in raw.get("public_trigger", []):
            if not isinstance(trigger, dict):
                continue
            cleaned = _sanitize_runtime_semantic_text(trigger.get("task", ""))
            if cleaned:
                tasks.append(cleaned)
        forbidden = raw.get("forbidden_behavior", [])
        if isinstance(forbidden, str):
            forbidden = [forbidden]
        elif not isinstance(forbidden, list):
            forbidden = []
        required_behavior = _sanitize_runtime_semantic_text(raw.get("required_behavior", ""))
        forbidden_behavior: list[str] = []
        for item in forbidden:
            cleaned = _sanitize_runtime_semantic_text(item)
            if cleaned:
                forbidden_behavior.append(cleaned)
        decision_contract = _runtime_decision_contract_projection(raw.get("decision_contract", {}))
        projected_contract: dict[str, Any] = {
            "public_tasks": tasks,
            "required_behavior": required_behavior,
            "forbidden_behavior": forbidden_behavior,
        }
        has_decision_contract = False
        for value in decision_contract.values():
            if value not in (None, "", []):
                has_decision_contract = True
                break
        if has_decision_contract:
            projected_contract["decision_contract"] = decision_contract
        projected.append(projected_contract)
    return projected


_PRIVATE_RUNTIME_MARKER_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_.:\[\]-]+\b"),
    re.compile(r"\btest_[A-Za-z0-9_]+\b"),
    re.compile(r"\b[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+\b"),
    re.compile(r"\b(?:step|trace)_\d+\b"),
)

_TRACE_PROVENANCE_SUFFIX = re.compile(
    r"\s*\((?:trace_id|role|message_index|step(?:_pointer)?)=.*?\)\. ?",
    re.IGNORECASE,
)


def _sanitize_runtime_semantic_text(value: Any) -> str:
    """Remove evaluator identifiers without discarding their semantic lesson."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^(?:member_harness|team_skill)\.[^.\s]+\.[^:]+:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _TRACE_PROVENANCE_SUFFIX.sub(". ", text)
    text = _PRIVATE_RUNTIME_MARKER_PATTERNS[0].sub("the observed check", text)
    text = re.sub(
        r"\bverifier\s+(?:test|check)\s+test_[A-Za-z0-9_]+\b",
        "the observed contract",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:test|check)\s+test_[A-Za-z0-9_]+\b",
        "the observed check",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\btest[_ -]patch\b",
        "supplied acceptance-test contract",
        text,
        flags=re.IGNORECASE,
    )
    text = _PRIVATE_RUNTIME_MARKER_PATTERNS[1].sub("the observed check", text)
    text = _PRIVATE_RUNTIME_MARKER_PATTERNS[2].sub("the task", text)
    text = _PRIVATE_RUNTIME_MARKER_PATTERNS[3].sub("", text)
    replacements = {
        "SWE-bench": "authoritative evaluation",
        "FAIL_TO_PASS": "required checks",
        "PASS_TO_PASS": "regression checks",
    }
    for source, replacement in replacements.items():
        text = re.sub(re.escape(source), replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:hyp|issue)_[A-Za-z0-9_]+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bhidden[- ]tests?\b", "unseen checks", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bverifier[_ -]result\b",
        "evaluation result",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bverifier\s+(?:test|check)\b",
        "observed contract",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:(?:official|authoritative)\s+)?verifier\b",
        "acceptance evaluation",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split()).strip(" ,;.")


def _runtime_decision_contract_projection(value: Any) -> dict[str, Any]:
    """Project the analyzer's decision change without optimizer provenance."""
    raw = value if isinstance(value, dict) else {}
    boundaries = raw.get("scope_boundary", [])
    if isinstance(boundaries, str):
        boundaries = [boundaries]
    elif not isinstance(boundaries, list):
        boundaries = []
    return {
        "wrong_decision": _sanitize_runtime_semantic_text(raw.get("wrong_decision")),
        "causal_distinction": _sanitize_runtime_semantic_text(raw.get("causal_distinction")),
        "required_action": _sanitize_runtime_semantic_text(raw.get("required_action")),
        "acceptance_observable": _sanitize_runtime_semantic_text(raw.get("acceptance_observable")),
        "scope_boundary": _sanitized_runtime_semantic_items(boundaries),
        "activation_phase": str(raw.get("activation_phase", "") or ""),
    }


def _sanitized_runtime_semantic_items(values: list[Any]) -> list[str]:
    """Return non-empty runtime-safe semantic strings."""
    items: list[str] = []
    for value in values:
        cleaned = _sanitize_runtime_semantic_text(value)
        if cleaned:
            items.append(cleaned)
    return items


def _build_declared_file_context(
    action_worktree: Path | None,
    declared_paths: list[str],
    *,
    max_chars_per_file: int = 12000,
) -> str:
    """Render current declared file contents for the execution model."""
    if action_worktree is None:
        return "No action worktree was provided."

    root = action_worktree.resolve()
    sections: list[str] = []
    for rel_path in declared_paths:
        normalized = _normalize_rel_path(rel_path)
        if not normalized:
            continue
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            sections.append(f"### {normalized}\n\nPath is outside the action worktree and cannot be read.")
            continue

        if candidate.is_dir():
            files = sorted(p for p in candidate.rglob("*") if p.is_file())
            if not files:
                sections.append(f"### {normalized}/\n\nDirectory exists and is empty.")
                continue
            for file_path in files[:20]:
                rel = _normalize_rel_path(file_path.relative_to(root))
                sections.append(_render_file_context(file_path, rel, max_chars_per_file))
            if len(files) > 20:
                sections.append(f"### {normalized}/\n\nDirectory listing truncated at 20 files.")
            continue

        if not candidate.exists():
            sections.append(f"### {normalized}\n\nFile does not exist.")
            continue
        sections.append(_render_file_context(candidate, normalized, max_chars_per_file))

    return "\n\n".join(sections) if sections else "No declared files were found."


def _render_file_context(path: Path, rel_path: str, max_chars: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"### {rel_path}\n\nCould not read file: {exc}"

    truncated = content[:max_chars]
    suffix = "\n\n[content truncated]" if len(content) > max_chars else ""
    return f"### {rel_path}\n\n```text\n{truncated}{suffix}\n```"


class MemberActionExecutorAgent:
    """DeepAgent-based executor for a single action in an isolated worktree."""

    def __init__(
        self,
        model_config_ref: str,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._model_config_ref = model_config_ref
        self._agent_skills_dirs = list(agent_skills_dirs or [])

    async def execute_action(
        self,
        action_worktree: Path,
        action: MemberOptimizationAction,
        plan_summary: str,
        allowed_skills: list[str],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        """Execute a single action in the given worktree.

        Returns a dict with: status, changed_files, declared_write_paths, error.
        """
        from openjiuwen.core.session.agent import Session

        user_message = self._build_user_message(
            action,
            plan_summary,
            allowed_skills,
            allowed_tools,
            action_worktree=action_worktree,
        )
        declared_paths = resolve_declared_paths(action)

        message = user_message
        last_response_text = ""
        last_error = ""
        # Skill and Tool artifacts are authored in one model call. They do not
        # need a ReAct agent or a meta-skill; the immutable hypothesis already
        # contains the semantic contract and the executor owns validation.
        direct_model_execution = action.action_group in {"skill", "tool"}
        agent = None
        if not direct_model_execution:
            agent = create_action_execution_agent(
                model_config_ref=self._model_config_ref,
                workspace=action_worktree,
                agent_skills_dirs=self._agent_skills_dirs,
                enable_skill_creator=False,
            )
        for attempt in range(3):
            try:
                if direct_model_execution:
                    response_text = await (
                        self._invoke_direct_skill_action(message)
                        if action.action_group == "skill"
                        else self._invoke_direct_tool_action(message)
                    )
                else:
                    session = Session(
                        session_id=f"action_exec_{action.action_id}_{attempt}",
                        card=getattr(agent, "card", None) or AgentCard(name="member_action_executor"),
                    )
                    response = await agent.invoke(
                        inputs={"query": message},
                        session=session,
                    )
                    response_text = extract_agent_text(response)
                last_response_text = response_text
                parsed = self._parse_action_response(response_text)
                status = str(parsed.get("status", "failed"))
                errors = [str(error) for error in parsed.get("errors", []) if str(error).strip()]
                if status != "succeeded":
                    return {
                        "status": "failed",
                        "declared_write_paths": declared_paths,
                        "response_text": response_text,
                        "error": "; ".join(errors) or "agent reported failed status",
                    }

                file_writes = parsed.get("file_writes")
                written_files = self._apply_structured_file_writes(
                    action_worktree=action_worktree,
                    action=action,
                    declared_paths=declared_paths,
                    file_writes=file_writes,
                )
                written_files = self._sync_action_registries(
                    action_worktree=action_worktree,
                    action=action,
                    declared_paths=declared_paths,
                    written_files=written_files,
                )
                validation_errors = _validate_generated_action_resources(
                    action_worktree,
                    action,
                    written_files,
                )
                if validation_errors:
                    raise ValueError("; ".join(validation_errors))
                return {
                    "status": "succeeded",
                    "declared_write_paths": declared_paths,
                    "changed_files": written_files,
                    "response_text": response_text,
                    "error": "",
                }
            except ValueError as exc:
                last_error = str(exc)
                message = self._build_retry_message(
                    original_message=user_message,
                    previous_response=last_response_text,
                    error=last_error,
                )
            except Exception as e:
                return {
                    "status": "failed",
                    "declared_write_paths": declared_paths,
                    "response_text": last_response_text,
                    "error": str(e),
                }

        return {
            "status": "failed",
            "declared_write_paths": declared_paths,
            "response_text": last_response_text,
            "error": last_error or "agent response must be a structured file_writes JSON object",
        }

    async def _invoke_direct_action(self, message: str) -> str:
        async def call_once() -> str:
            model = load_member_optimizer_model(self._model_config_ref)
            response = await model.invoke(
                messages=[
                    {"role": "system", "content": _ACTION_EXECUTION_PROMPT},
                    {"role": "user", "content": message},
                ],
                tools=None,
                temperature=0.0,
                max_tokens=8192,
                extra_body={"enable_thinking": False},
            )
            return _extract_model_response_text(response)

        return await run_model_call_with_retries(
            call_once,
            operation_name="member action artifact execution",
            max_retries=0,
        )

    async def _invoke_direct_skill_action(self, message: str) -> str:
        return await self._invoke_direct_action(message)

    async def _invoke_direct_tool_action(self, message: str) -> str:
        return await self._invoke_direct_action(message)

    @staticmethod
    def _parse_action_response(response_text: str) -> dict[str, Any]:
        try:
            parsed = parse_json_object_response(response_text)
        except Exception as exc:
            raise ValueError(f"agent response must be a structured file_writes JSON object: {exc}") from exc
        if "status" not in parsed or "file_writes" not in parsed:
            raise ValueError("agent response must be a structured file_writes JSON object")
        file_writes = parsed.get("file_writes")
        if str(parsed.get("status")) == "succeeded" and (not isinstance(file_writes, list) or not file_writes):
            raise ValueError("agent response must include non-empty structured file_writes")
        return parsed

    @staticmethod
    def _apply_structured_file_writes(
        *,
        action_worktree: Path,
        action: MemberOptimizationAction,
        declared_paths: list[str],
        file_writes: Any,
    ) -> list[str]:
        if not isinstance(file_writes, list):
            raise ValueError("file_writes must be a list")
        written_files: list[str] = []
        root = action_worktree.resolve()
        for index, item in enumerate(file_writes):
            if not isinstance(item, dict):
                raise ValueError(f"file_writes[{index}] must be an object")

            rel_path = str(item.get("path", "")).strip()
            if not rel_path:
                raise ValueError(f"file_writes[{index}].path is required")
            normalized = _normalize_rel_path(rel_path)
            if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
                raise ValueError(f"file_writes[{index}].path must be a safe relative path")
            if not _path_allowed_by_declared(normalized, declared_paths):
                raise ValueError(
                    f"file_writes[{index}].path {normalized} outside declared_write_paths {declared_paths}"
                )

            if "content" not in item:
                raise ValueError(f"file_writes[{index}].content is required")
            content = item["content"]
            if not isinstance(content, str):
                raise ValueError(f"file_writes[{index}].content must be a string")
            skill_name = _skill_name_from_skill_md_path(normalized)
            if action.action_group == "skill" and skill_name:
                content = _normalize_skill_frontmatter_name(
                    content,
                    skill_name=skill_name,
                    fallback_description=_skill_trigger_description(action),
                )
                if action.operation == "add":
                    _validate_generated_skill_contract(content)

            target = (root / normalized).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"file_writes[{index}].path {normalized} escapes action worktree") from exc

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written_files.append(normalized)

        return sorted(set(written_files))

    @staticmethod
    def _sync_action_registries(
        *,
        action_worktree: Path,
        action: MemberOptimizationAction,
        declared_paths: list[str],
        written_files: list[str],
    ) -> list[str]:
        root = action_worktree.resolve()
        written_files = _normalize_skill_frontmatter_name_for_written_files(
            action_worktree=root,
            action=action,
            written_files=written_files,
        )
        return _sync_skill_registry_for_written_files(
            action_worktree=root,
            action=action,
            declared_paths=declared_paths,
            written_files=written_files,
        )

    @staticmethod
    def _load_registry_list(path: Path, key: str) -> list[str]:
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            data = data.get(key, [])
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a list or '{key}' mapping")
        return [str(item) for item in data]

    @staticmethod
    def _build_retry_message(
        *,
        original_message: str,
        previous_response: str,
        error: str,
    ) -> str:
        return f"""{original_message}

## Previous Invalid Output

{previous_response}

## Validation Error

{error}

Return ONLY a valid JSON object with this exact shape. Do not emit `<tool_call>`,
do not describe tool usage, and do not include text outside the JSON object.

```json
{{
  "action_id": "<action_id>",
  "status": "succeeded",
  "file_writes": [
    {{
      "path": "<relative path inside declared_write_paths>",
      "content": "<complete replacement file content>"
    }}
  ],
  "errors": []
}}
```
"""

    @staticmethod
    def _build_user_message(
        action: MemberOptimizationAction,
        plan_summary: str,
        allowed_skills: list[str],
        allowed_tools: list[str],
        action_worktree: Path | None = None,
    ) -> str:
        del allowed_tools
        resource_guidance = _action_resource_guidance(action)
        declared_paths = resolve_declared_paths(action)
        file_context = _build_declared_file_context(action_worktree, declared_paths)
        runtime_contracts = _runtime_contract_projection(action)
        if runtime_contracts:
            semantic_context = f"""## Public Runtime Contract

The optimizer's case identifiers, trajectory pointers, evaluator identifiers,
rationale, and plan summary are intentionally withheld. Author the runtime
artifact only from this public semantic projection. The `decision_contract`
records one evidence-selected change in behavior; preserve its direction:

{json.dumps(runtime_contracts, ensure_ascii=False, indent=2)}"""
        else:
            semantic_context = f"""## Requested Change

Description: {action.description}
Rationale: {action.rationale}
Risk Notes: {", ".join(action.risk_notes) if action.risk_notes else "none"}

## Plan Summary

{plan_summary}"""
        write_constraint = (
            "Do not call write_file or edit_file to apply the change. Return the "
            "complete replacement content in `file_writes`; the executor applies it "
            "after validation."
        )
        output_contract = """Return ONLY a JSON object. Do not call file tools in the response. Put the
complete desired file contents in file_writes; the executor will write them
after path validation.

```json
{
  "action_id": "<action_id>",
  "status": "succeeded",
  "file_writes": [
    {
      "path": "<relative path inside declared_write_paths>",
      "content": "<complete replacement file content>"
    }
  ],
  "errors": []
}
```"""
        return f"""## Action to Execute

You are executing the following action in your workspace.

Action ID: {action.action_id}
Role: {action.role}
Action Group: {action.action_group}
Operation: {action.operation}
Action Type: {action.action_type}
Target Path: {action.target_path}
Declared Write Paths: {action.declared_write_paths or [action.target_path]}
Allowed Skills: {allowed_skills}
Allowed Tools: []

{semantic_context}

## Current Declared File Contents

{file_context}

## Action-Specific Guidance

{resource_guidance}

## Constraints

1. You may ONLY write files within the declared_write_paths.
2. Do NOT modify files outside the role worktree.
3. Do NOT modify current_harnesses, current_harness_refs.yaml, or orchestrator artifacts.
4. Only use the allowed_skills and allowed_tools listed above.
5. Do NOT search for external resources or install dependencies.
6. This action may only modify local ExpertHarness package files supported by current auto harness.
7. {write_constraint}
8. Use the current file contents above as the source of truth. If the file
   content does not support the requested change, return failed instead of
   inventing fields or paths.

## Output

{output_contract}

If you cannot produce a valid change, return:

```json
{{
  "action_id": "{action.action_id}",
  "status": "failed",
  "file_writes": [],
  "errors": ["<specific reason>"]
}}
```
"""


class MemberActionExecutor:
    """Execute optimization actions in three-layer parallelism with isolated worktrees."""

    def __init__(
        self,
        worktree_coordinator: MemberWorktreeCoordinator | None = None,
        executor_agent: MemberActionExecutorAgent | None = None,
        execution_concurrency: int = 2,
        role_execution_concurrency: int = 2,
        action_execution_concurrency_per_role: int = 2,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._coordinator = worktree_coordinator or MemberWorktreeCoordinator()
        self._executor_agent = executor_agent
        self._agent_skills_dirs = list(agent_skills_dirs or [])
        self._global_sem = asyncio.Semaphore(execution_concurrency)
        self._role_sem = asyncio.Semaphore(role_execution_concurrency)
        self._action_sem = asyncio.Semaphore(action_execution_concurrency_per_role)

    def _get_executor_agent(self, model_config_ref: str) -> MemberActionExecutorAgent:
        if self._executor_agent is not None:
            return self._executor_agent
        return MemberActionExecutorAgent(
            model_config_ref=model_config_ref,
            agent_skills_dirs=self._agent_skills_dirs,
        )

    async def execute(
        self,
        plan: MemberOptimizationPlan,
        output_dir: str,
        model_config_ref: str,
        worktrees_dir: str | None = None,
    ) -> list[MemberActionExecutionResult]:
        """Execute the plan in waves with role-level and action-level parallelism."""
        run_dir = Path(output_dir).expanduser().resolve()
        worktrees_root = (
            Path(worktrees_dir).expanduser().resolve()
            if worktrees_dir is not None
            else run_dir / MEMBER_WORKTREES_DIR_NAME
        )

        action_by_id: dict[str, MemberOptimizationAction] = {a.action_id: a for a in plan.actions}

        integration_worktrees: dict[str, Path] = {}
        for target in plan.targets:
            role = target.role
            integration_dir = self._coordinator.prepare_integration_worktree(
                role=role,
                harness_ref_path=target.harness_ref_path,
                worktrees_dir=str(worktrees_root),
            )
            integration_worktrees[role] = integration_dir

        results: list[MemberActionExecutionResult] = []
        failed_roles: set[str] = set()
        action_status_by_id: dict[str, str] = {}

        # Plans can be loaded from persisted or model-authored artifacts. Rebuild
        # dependency waves here so execution never trusts stale wave metadata.
        execution_waves = build_action_waves(plan.actions)
        for wave_idx, wave in enumerate(execution_waves):
            wave_actions = [action_by_id[aid] for aid in wave if aid in action_by_id]
            grouped: dict[str, list[MemberOptimizationAction]] = {}
            for action in wave_actions:
                should_run, skip_reason = _should_run_action(action, action_status_by_id)
                if not should_run:
                    results.append(
                        MemberActionExecutionResult(
                            action_id=action.action_id,
                            role=action.role,
                            status="skipped",
                            worktree_path="",
                            artifact_path="",
                            error=skip_reason,
                        )
                    )
                    action_status_by_id[action.action_id] = "skipped"
                    continue
                if action.role in failed_roles:
                    results.append(
                        MemberActionExecutionResult(
                            action_id=action.action_id,
                            role=action.role,
                            status="skipped",
                            worktree_path="",
                            artifact_path="",
                            error="role integration already failed",
                        )
                    )
                    action_status_by_id[action.action_id] = "skipped"
                    continue
                grouped.setdefault(action.role, []).append(action)

            async def execute_role_group(
                role: str,
                role_actions: list[MemberOptimizationAction],
                wave_idx: int,
            ) -> tuple[str, list[MemberActionExecutionResult], list[MemberActionMergeResult], bool]:
                async with self._role_sem:
                    integration_dir = integration_worktrees.get(role)
                    role_failed = False
                    role_results: list[MemberActionExecutionResult] = []
                    role_merge_results: list[MemberActionMergeResult] = []

                    subwaves = build_role_subwaves(role_actions)

                    for subwave_idx, subwave in enumerate(subwaves):
                        subwave_results: list[MemberActionExecutionResult] = []

                        async def execute_one_action(
                            action: MemberOptimizationAction,
                            wave_idx: int,
                        ) -> MemberActionExecutionResult:
                            async with self._action_sem:
                                async with self._global_sem:
                                    try:
                                        return await self._execute_one_action(
                                            action,
                                            _ActionExecutionContext(
                                                integration_dir=integration_dir,
                                                run_dir=run_dir,
                                                worktrees_dir=str(worktrees_root),
                                                wave_index=wave_idx,
                                                model_config_ref=model_config_ref,
                                                plan=plan,
                                            ),
                                        )
                                    except Exception as exc:
                                        return self._write_failed_action_result(
                                            action=action,
                                            run_dir=run_dir,
                                            error=f"task exception: {exc}",
                                        )

                        subwave_task_results = await asyncio.gather(
                            *[execute_one_action(action, wave_idx) for action in subwave],
                            return_exceptions=True,
                        )

                        for r in subwave_task_results:
                            if isinstance(r, Exception):
                                subwave_results.append(
                                    MemberActionExecutionResult(
                                        action_id="unknown",
                                        role=role,
                                        status="failed",
                                        worktree_path="",
                                        artifact_path="",
                                        error=f"task exception: {r}",
                                    )
                                )
                            else:
                                subwave_results.append(r)

                        successful_results = [r for r in subwave_results if r.status == "succeeded"]
                        if successful_results:
                            merge_result = self._merge_subwave(
                                _SubwaveMergeRequest(
                                    role=role,
                                    wave_index=wave_idx,
                                    subwave_index=subwave_idx,
                                    results=successful_results,
                                    integration_dir=integration_dir,
                                    run_dir=run_dir,
                                )
                            )
                            role_merge_results.append(merge_result)
                            merged_action_ids = set(merge_result.action_ids)
                        else:
                            merge_result = None
                            merged_action_ids = set()

                        for r in subwave_results:
                            if r.action_id in merged_action_ids and r.status == "succeeded":
                                r = replace(
                                    r,
                                    merge_status=merge_result.status,
                                    merge_artifact_path=merge_result.artifact_path,
                                )
                            role_results.append(r)

                        if merge_result and merge_result.status == "failed":
                            role_failed = True
                            break

                    return role, role_results, role_merge_results, role_failed

            role_tasks = [
                execute_role_group(role, actions, wave_idx)
                for role, actions in grouped.items()
                if role not in failed_roles
            ]

            if not role_tasks:
                continue

            wave_results = await asyncio.gather(*role_tasks, return_exceptions=True)
            for wave_result in wave_results:
                if isinstance(wave_result, Exception):
                    continue
                role, role_results, _role_merge_results, role_failed = wave_result
                results.extend(role_results)
                for result in role_results:
                    action_status_by_id[result.action_id] = result.status
                if role_failed:
                    failed_roles.add(role)

        self._write_execution_results(run_dir, results)

        return results

    async def _execute_one_action(
        self,
        action: MemberOptimizationAction,
        context: _ActionExecutionContext,
    ) -> MemberActionExecutionResult:
        """Execute a single action in an isolated worktree."""
        integration_dir = context.integration_dir
        run_dir = context.run_dir
        if integration_dir is None:
            raise RuntimeError(f"integration worktree not found for role {action.role}")

        policy_check = validate_action_policy(action)
        if not policy_check.valid:
            return self._write_failed_action_result(
                action=action,
                run_dir=run_dir,
                error="; ".join(policy_check.errors),
            )

        worktrees_root = (
            Path(context.worktrees_dir).expanduser().resolve()
            if context.worktrees_dir is not None
            else run_dir / MEMBER_WORKTREES_DIR_NAME
        )
        worktree_dir = self._coordinator.prepare_action_worktree(
            action=action,
            integration_worktree=integration_dir,
            worktrees_dir=str(worktrees_root),
            wave_index=context.wave_index,
        )
        declared_paths = resolve_declared_paths(action)
        before_all = _snapshot_files(worktree_dir)
        before_declared = _snapshot_files(worktree_dir, declared_paths)

        plan_summary = (
            f"Plan {context.plan.plan_id} with {len(context.plan.actions)} actions "
            f"in {len(context.plan.action_waves)} waves. "
            f"Targets: {[t.role for t in context.plan.targets]}."
        )

        try:
            if action.operation == "remove":
                exec_result = await asyncio.to_thread(
                    _execute_deterministic_remove,
                    worktree_dir,
                    action,
                    declared_paths,
                )
            else:
                preseed_scaffold = None
                if _is_add_like_scaffold_action(worktree_dir, action):
                    preseed_result = await asyncio.to_thread(
                        _execute_deterministic_add_scaffold,
                        worktree_dir,
                        action,
                        declared_paths,
                    )
                    if preseed_result["status"] == "succeeded":
                        preseed_scaffold = preseed_result["scaffold"]
                agent = self._get_executor_agent(context.model_config_ref)
                exec_result = await agent.execute_action(
                    action_worktree=worktree_dir,
                    action=action,
                    plan_summary=plan_summary,
                    allowed_skills=action.allowed_skills,
                    allowed_tools=sanitize_allowed_tools(action.allowed_tools),
                )
                if preseed_scaffold:
                    exec_result.setdefault("deterministic_preseed", preseed_scaffold)
        except Exception as e:
            exec_result = {
                "status": "failed",
                "declared_write_paths": declared_paths,
                "error": str(e),
            }

        after_declared = _snapshot_files(worktree_dir, declared_paths)
        changed_files = _changed_files(before_declared, after_declared)
        actual_changed_files = _changed_files(before_all, _snapshot_files(worktree_dir))
        if exec_result.get("status", "failed") == "succeeded":
            changed_files = _normalize_skill_frontmatter_name_for_written_files(
                action_worktree=worktree_dir,
                action=action,
                written_files=changed_files,
            )
            actual_changed_files = _changed_files(before_all, _snapshot_files(worktree_dir))
        needs_add_scaffold = (
            exec_result.get("status", "failed") != "succeeded"
            and not changed_files
            and action.operation == "add"
            and action.action_group in {"prompt", "skill"}
        )
        if needs_add_scaffold:
            scaffold_result = _execute_deterministic_add_scaffold(
                worktree_dir,
                action,
                declared_paths,
            )
            if scaffold_result["status"] == "succeeded":
                exec_result["status"] = "succeeded"
                exec_result["error"] = ""
                exec_result.setdefault("deterministic_scaffold", scaffold_result["scaffold"])
                after_declared = _snapshot_files(worktree_dir, declared_paths)
                changed_files = _changed_files(before_declared, after_declared)
                actual_changed_files = _changed_files(before_all, _snapshot_files(worktree_dir))
        if exec_result.get("status", "failed") == "succeeded" and not changed_files:
            scaffold_result = _execute_deterministic_add_scaffold(
                worktree_dir,
                action,
                declared_paths,
            )
            if scaffold_result["status"] == "succeeded":
                exec_result.setdefault("deterministic_scaffold", scaffold_result["scaffold"])
                after_declared = _snapshot_files(worktree_dir, declared_paths)
                changed_files = _changed_files(before_declared, after_declared)
                actual_changed_files = _changed_files(before_all, _snapshot_files(worktree_dir))

        artifact_dir = run_dir / "act" / _short_token(action.role) / _short_token(action.action_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "execution.json"

        status = exec_result.get("status", "failed")
        declared_paths = exec_result.get("declared_write_paths", resolve_declared_paths(action))
        out_of_bounds = []
        for path in actual_changed_files:
            if not _path_allowed_by_declared(path, declared_paths) and not _is_runtime_workspace_metadata(path):
                out_of_bounds.append(path)
        if out_of_bounds and status == "succeeded":
            status = "failed"
            exec_result["error"] = f"changed_files {out_of_bounds} outside declared_write_paths {declared_paths}"
        elif status == "succeeded" and not changed_files:
            status = "failed"
            exec_result["error"] = "agent reported success but no declared_write_paths changed"
        elif status == "succeeded":
            validation_errors = _validate_generated_action_resources(
                worktree_dir,
                action,
                changed_files,
            )
            if validation_errors:
                status = "failed"
                exec_result["error"] = "; ".join(validation_errors)

        payload = {
            "action_id": action.action_id,
            "role": action.role,
            "action": asdict(action),
            "status": status,
            "worktree_path": str(worktree_dir),
            "changed_files": changed_files,
            "actual_changed_files": actual_changed_files,
            "declared_write_paths": declared_paths,
            "response_text": exec_result.get("response_text", ""),
            "error": exec_result.get("error", ""),
        }
        if "remove" in exec_result:
            payload["remove"] = exec_result["remove"]
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return MemberActionExecutionResult(
            action_id=action.action_id,
            role=action.role,
            member_name=action.member_name,
            status=status,
            worktree_path=str(worktree_dir),
            artifact_path=str(artifact_path),
            changed_files=changed_files,
            declared_write_paths=declared_paths,
            merge_status="pending",
            error=exec_result.get("error", ""),
        )

    @staticmethod
    def _write_failed_action_result(
        action: MemberOptimizationAction,
        run_dir: Path,
        error: str,
    ) -> MemberActionExecutionResult:
        """Write a failed execution artifact for an action rejected before execution."""
        declared_paths = resolve_declared_paths(action)
        artifact_dir = run_dir / "act" / _short_token(action.role) / _short_token(action.action_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "execution.json"
        payload = {
            "action_id": action.action_id,
            "role": action.role,
            "action": asdict(action),
            "status": "failed",
            "worktree_path": "",
            "changed_files": [],
            "actual_changed_files": [],
            "declared_write_paths": declared_paths,
            "allowed_executor_tools": sorted(ALLOWED_EXECUTOR_TOOLS),
            "response_text": "",
            "error": error,
        }
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return MemberActionExecutionResult(
            action_id=action.action_id,
            role=action.role,
            member_name=action.member_name,
            status="failed",
            worktree_path="",
            artifact_path=str(artifact_path),
            changed_files=[],
            declared_write_paths=declared_paths,
            merge_status="",
            error=error,
        )

    @staticmethod
    def _merge_subwave(
        request: _SubwaveMergeRequest,
    ) -> MemberActionMergeResult:
        """Merge successful action worktrees into the integration worktree."""
        merge_dir = request.run_dir / "m" / _short_token(request.role)
        merge_dir.mkdir(parents=True, exist_ok=True)
        merge_artifact = merge_dir / (f"wave_{request.wave_index:03d}_subwave_{request.subwave_index:03d}.json")

        merged_files: list[str] = []
        conflicts: list[str] = []

        for result in sorted(request.results, key=lambda r: r.action_id):
            if result.status != "succeeded":
                continue
            action_worktree = Path(result.worktree_path)
            if not action_worktree.exists():
                continue

            for changed_file in result.changed_files:
                src = action_worktree / changed_file
                dst = request.integration_dir / changed_file
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    merged_files.append(changed_file)
                elif dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                    _remove_empty_parent_dirs(dst.parent, request.integration_dir)
                    merged_files.append(changed_file)

        merge_status = "merged" if merged_files else "failed"
        if not merged_files and request.results:
            conflicts.append("no files merged from successful actions")

        payload = {
            "role": request.role,
            "wave_index": request.wave_index,
            "subwave_index": request.subwave_index,
            "action_ids": [r.action_id for r in request.results],
            "status": merge_status,
            "merged_files": merged_files,
            "conflicts": conflicts,
            "artifact_path": str(merge_artifact),
        }
        with open(merge_artifact, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return MemberActionMergeResult(
            role=request.role,
            wave_index=request.wave_index,
            subwave_index=request.subwave_index,
            action_ids=[r.action_id for r in request.results],
            status=merge_status,
            merged_files=merged_files,
            conflicts=conflicts,
            artifact_path=str(merge_artifact),
        )

    @staticmethod
    def _write_execution_results(
        run_dir: Path,
        results: list[MemberActionExecutionResult],
    ) -> None:
        path = run_dir / "execution_results.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"results": [asdict(r) for r in results]},
                f,
                ensure_ascii=False,
                indent=2,
            )


def _execute_deterministic_remove(
    worktree_dir: Path,
    action: MemberOptimizationAction,
    declared_paths: list[str],
) -> dict[str, object]:
    target_rel = _normalize_rel_path(action.target_path)
    if not _path_allowed_by_declared(target_rel, declared_paths):
        return {
            "status": "failed",
            "declared_write_paths": declared_paths,
            "error": f"target_path {target_rel!r} is outside declared_write_paths",
        }

    removed_paths: list[str] = []
    manifest_updates: list[str] = []

    if action.action_group == "prompt":
        section_name = str(action.constraints.get("section_name", "") or "")
        removed_paths.extend(_remove_path(worktree_dir, target_rel))
        manifest = worktree_dir / "prompt_sections" / "sections.yaml"
        if manifest.is_file() and _remove_prompt_section_entry(
            manifest,
            target_rel,
            section_name,
        ):
            manifest_updates.append("prompt_sections/sections.yaml")
    elif action.action_group == "skill":
        skill_root_rel = _skill_root_from_target(target_rel)
        removed_paths.extend(_remove_path(worktree_dir, skill_root_rel))
        manifest = worktree_dir / "skills" / "skills.yaml"
        if manifest.is_file() and _remove_manifest_entries(
            manifest,
            list_key="skills",
            target_paths={skill_root_rel},
            exact_only=True,
        ):
            manifest_updates.append("skills/skills.yaml")
    elif action.action_group == "tool":
        removed_paths.extend(_remove_path(worktree_dir, target_rel))
        manifest = worktree_dir / "tools" / "tools.yaml"
        if manifest.is_file() and _remove_manifest_entries(
            manifest,
            list_key="tools",
            target_paths={target_rel},
        ):
            manifest_updates.append("tools/tools.yaml")
    else:
        return {
            "status": "failed",
            "declared_write_paths": declared_paths,
            "error": f"deterministic remove unsupported for {action.action_group}",
        }

    if not removed_paths and not manifest_updates:
        return {
            "status": "failed",
            "declared_write_paths": declared_paths,
            "error": f"nothing removed for target_path {target_rel}",
        }

    return {
        "status": "succeeded",
        "declared_write_paths": declared_paths,
        "error": "",
        "remove": {
            "target_path": target_rel,
            "removed_paths": removed_paths,
            "manifest_updates": manifest_updates,
        },
    }


def _validate_generated_action_resources(
    worktree_dir: Path,
    action: MemberOptimizationAction,
    changed_files: list[str],
) -> list[str]:
    """Run cheap action-local checks before merging generated resources."""
    errors: list[str] = []
    if action.action_group == "skill":
        for rel_path in changed_files:
            normalized = _normalize_rel_path(rel_path)
            expected_name = _skill_name_from_skill_md_path(normalized)
            if not expected_name:
                continue
            path = worktree_dir / normalized
            if not path.is_file():
                continue
            frontmatter = _read_skill_frontmatter(path)
            actual_name = str(frontmatter.get("name") or "").strip()
            if actual_name != expected_name:
                errors.append(f"skill_frontmatter:{normalized}: name must be {expected_name!r}, got {actual_name!r}")
            if not str(frontmatter.get("description") or "").strip():
                errors.append(f"skill_frontmatter:{normalized}: description is required")
        return errors

    if action.action_group != "tool":
        return errors

    for rel_path in changed_files:
        normalized = _normalize_rel_path(rel_path)
        path = worktree_dir / normalized
        if not path.is_file():
            continue
        if path.suffix == ".py":
            try:
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")
            except Exception as exc:
                errors.append(f"python_compile:{normalized}: {exc}")
                continue
            safety_errors = _validate_package_python_source(source, path=normalized)
            errors.extend(safety_errors)
        elif normalized == "tools/tools.yaml":
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"yaml_parse:{normalized}: {exc}")
        elif path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"json_parse:{normalized}: {exc}")
    return errors


def _read_skill_frontmatter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        -1,
    )
    if end_index <= 0:
        return {}
    loaded = yaml.safe_load("\n".join(lines[1:end_index])) or {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _execute_deterministic_add_scaffold(
    worktree_dir: Path,
    action: MemberOptimizationAction,
    declared_paths: list[str],
) -> dict[str, object]:
    target_rel = _normalize_rel_path(action.target_path)
    if not _is_add_like_scaffold_action(worktree_dir, action):
        return {
            "status": "skipped",
            "scaffold": {},
        }

    if not _path_allowed_by_declared(target_rel, declared_paths):
        return {
            "status": "failed",
            "scaffold": {},
            "error": f"target_path {target_rel!r} is outside declared_write_paths",
        }

    if action.action_group == "prompt":
        if not target_rel.startswith("prompt_sections/files/") or not target_rel.endswith(".md"):
            return {
                "status": "failed",
                "scaffold": {},
                "error": "prompt/add scaffold target must be prompt_sections/files/*.md",
            }
        manifest_rel = "prompt_sections/sections.yaml"
        if manifest_rel not in {_normalize_rel_path(path) for path in declared_paths}:
            return {
                "status": "failed",
                "scaffold": {},
                "error": f"add scaffold requires declared manifest {manifest_rel}",
            }
        section_name = str(action.constraints.get("section_name", "") or Path(target_rel).stem).strip()
        priority = _int_or_default(action.constraints.get("priority"), 30)
        content = _prompt_section_scaffold(action)
        target = worktree_dir / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        manifest = worktree_dir / manifest_rel
        manifest.parent.mkdir(parents=True, exist_ok=True)
        _append_manifest_entry(
            manifest,
            list_key="sections",
            entry={"name": section_name, "file": target_rel, "priority": priority},
        )
        return {
            "status": "succeeded",
            "scaffold": {
                "target_path": target_rel,
                "manifest_path": manifest_rel,
                "section_name": section_name,
            },
        }

    if action.action_group == "skill":
        if not target_rel.startswith("skills/") or not target_rel.endswith("/SKILL.md"):
            return {
                "status": "failed",
                "scaffold": {},
                "error": "skill/add scaffold target must be skills/<name>/SKILL.md",
            }
        manifest_rel = "skills/skills.yaml"
        if manifest_rel not in {_normalize_rel_path(path) for path in declared_paths}:
            return {
                "status": "failed",
                "scaffold": {},
                "error": f"add scaffold requires declared manifest {manifest_rel}",
            }
        skill_root = Path(*Path(target_rel).parts[:-1]).as_posix()
        skill_name = (
            _skill_name_from_skill_md_path(target_rel)
            or str(action.constraints.get("skill_name", "") or Path(skill_root).name).strip()
        )
        target = worktree_dir / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_skill_scaffold(skill_name, action), encoding="utf-8")
        manifest = worktree_dir / manifest_rel
        existing = _load_registry_values(manifest, "skills")
        if skill_root not in existing:
            existing.append(skill_root)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            yaml.safe_dump({"skills": existing}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "scaffold": {
                "target_path": target_rel,
                "manifest_path": manifest_rel,
                "skill_root": skill_root,
            },
        }

    class_name = str(action.constraints.get("class_name", "") or "").strip()
    if not class_name:
        return {
            "status": "failed",
            "scaffold": {},
            "error": "add scaffold requires constraints.class_name",
        }

    if action.action_group != "tool" or not target_rel.startswith("tools/") or not target_rel.endswith(".py"):
        return {"status": "failed", "scaffold": {}, "error": "tool/add scaffold target must be tools/*.py"}
    manifest_rel = "tools/tools.yaml"
    list_key = "tools"
    content = _tool_scaffold(class_name, action.description)

    if manifest_rel not in {_normalize_rel_path(path) for path in declared_paths}:
        return {
            "status": "failed",
            "scaffold": {},
            "error": f"add scaffold requires declared manifest {manifest_rel}",
        }

    target = worktree_dir / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    manifest = worktree_dir / manifest_rel
    manifest.parent.mkdir(parents=True, exist_ok=True)
    _append_manifest_entry(
        manifest,
        list_key=list_key,
        entry={"file": target_rel, "class_name": class_name},
    )

    return {
        "status": "succeeded",
        "scaffold": {
            "target_path": target_rel,
            "manifest_path": manifest_rel,
            "class_name": class_name,
        },
    }


def _is_add_like_scaffold_action(
    worktree_dir: Path,
    action: MemberOptimizationAction,
) -> bool:
    if action.action_group not in {"prompt", "skill", "tool"}:
        return False
    if action.operation == "add":
        return True
    if action.operation != "modify" or action.action_group not in {"skill", "tool"}:
        return False
    target_rel = _normalize_rel_path(action.target_path)
    return not (worktree_dir / target_rel).exists()


def _tool_scaffold(class_name: str, description: str) -> str:
    tool_name = _snake_case(class_name)
    desc = (description or "Package-local member optimizer tool.").replace("'", "\\'")
    return "\n".join(
        [
            "from openjiuwen.core.foundation.tool import Tool, ToolCard",
            "",
            "",
            f"class {class_name}(Tool):",
            "    def __init__(self):",
            "        super().__init__(",
            "            ToolCard(",
            f"                id='{tool_name}',",
            f"                name='{tool_name}',",
            f"                description='{desc}',",
            "                input_params={",
            "                    'type': 'object',",
            "                    'properties': {},",
            "                    'required': [],",
            "                },",
            "            )",
            "        )",
            "",
            "    async def invoke(self, inputs, **kwargs):",
            "        return {",
            "            'checklist': [",
            "                'confirm bounded concurrency before launching tasks',",
            "                'let already-started tasks run cleanup on cancellation',",
            "                'verify cancellation paths with focused tests',",
            "            ]",
            "        }",
            "",
            "    async def stream(self, inputs, **kwargs):",
            "        if False:",
            "            yield inputs",
            "",
        ]
    )


def _prompt_section_scaffold(action: MemberOptimizationAction) -> str:
    title = str(action.constraints.get("section_name", "") or Path(action.target_path).stem)
    description = action.description.strip() or "Apply the requested prompt improvement."
    rationale = action.rationale.strip()
    expected_effect = action.expected_effect.strip()
    lines = [
        f"# {title.replace('_', ' ').title()}",
        "",
        description,
    ]
    if rationale:
        lines.extend(["", "## Why This Matters", "", rationale])
    if expected_effect:
        lines.extend(["", "## Expected Behavior", "", expected_effect])
    lines.extend(
        [
            "",
            "## Operating Rule",
            "",
            (
                "Before finalizing the answer, check this rule against the current task and revise the output "
                "when it is violated."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _skill_scaffold(skill_name: str, action: MemberOptimizationAction) -> str:
    safe_name = _canonical_skill_identifier(skill_name)
    description = _skill_trigger_description(action)
    rationale = action.rationale.strip()
    expected_effect = action.expected_effect.strip()
    lines = [
        "---",
        f"name: {safe_name}",
        f"description: {description}",
        "---",
        "",
        f"# {safe_name.replace('_', ' ').title()}",
        "",
        f"Skill ID: `{safe_name}`",
        "",
        description,
    ]
    if rationale:
        lines.extend(["", "## When To Use", "", rationale])
    lines.extend(
        [
            "",
            "## Procedure",
            "",
            "1. Identify the task requirements and the evaluation behaviors that apply.",
            "2. Map each requirement to a concrete output element before drafting.",
            "3. Produce the artifact with explicit coverage of every mapped requirement.",
            "4. Review the artifact against the requirements and repair missing elements before finishing.",
        ]
    )
    if expected_effect:
        lines.extend(["", "## Success Signal", "", expected_effect])
    lines.append("")
    return "\n".join(lines)


def _skill_trigger_description(action: MemberOptimizationAction) -> str:
    """Derive discoverable runtime metadata instead of copying planner prose."""
    contracts = action.constraints.get("optimization_contracts", [])
    if not isinstance(contracts, list):
        contracts = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        decision = contract.get("decision_contract", {})
        if isinstance(decision, dict):
            distinction = _sanitize_runtime_semantic_text(decision.get("causal_distinction"))
            if distinction:
                return f"Use when a task requires deciding whether {distinction.rstrip('.')}."
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        triggers = contract.get("public_trigger", [])
        if isinstance(triggers, str):
            triggers = [triggers]
        if isinstance(triggers, list):
            for trigger in triggers:
                cleaned = _sanitize_runtime_semantic_text(trigger)
                if cleaned:
                    return f"Use when {cleaned.rstrip('.')}."
    expected = _sanitize_runtime_semantic_text(action.expected_effect)
    if expected:
        return f"Use when a task requires {expected.rstrip('.')}."
    return "Use when the current task matches this reusable procedure."


def _load_registry_values(manifest: Path, key: str) -> list[str]:
    if not manifest.is_file():
        return []
    data = _load_yaml_manifest(manifest)
    if isinstance(data, dict):
        values = data.get(key) or []
    elif isinstance(data, list):
        values = data
    else:
        values = []
    if not isinstance(values, list):
        values = [values]
    return [str(value) for value in values]


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _append_manifest_entry(
    manifest: Path,
    *,
    list_key: str,
    entry: dict[str, object],
) -> None:
    data = _load_yaml_manifest(manifest) if manifest.is_file() else {}
    if isinstance(data, dict):
        entries = data.get(list_key) or []
    elif isinstance(data, list):
        entries = data
        data = {list_key: entries}
    else:
        entries = []
        data = {list_key: entries}
    if not isinstance(entries, list):
        entries = [entries]
    normalized_entry_file = _normalize_rel_path(entry["file"])
    kept = []
    for item in entries:
        if isinstance(item, dict):
            item_file = str(item.get("file") or item.get("file_path") or "")
            if _normalize_rel_path(item_file) == normalized_entry_file:
                continue
        kept.append(item)
    kept.append(entry)
    data[list_key] = kept
    _write_yaml_manifest(manifest, data)


def _snake_case(value: str) -> str:
    chars: list[str] = []
    for idx, char in enumerate(value):
        if char.isupper() and idx > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars).strip("_") or "member_optimizer_tool"


def _remove_path(worktree_dir: Path, rel_path: str) -> list[str]:
    target = worktree_dir / rel_path
    if not target.exists():
        return []
    if target.is_dir():
        removed = [path.relative_to(worktree_dir).as_posix() for path in sorted(target.rglob("*")) if path.is_file()]
        shutil.rmtree(target)
        return removed
    target.unlink()
    _remove_empty_parent_dirs(target.parent, worktree_dir)
    return [rel_path]


def _remove_empty_parent_dirs(path: Path, stop_at: Path) -> None:
    stop_at = stop_at.resolve()
    current = path
    while current.resolve() != stop_at:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _skill_root_from_target(target_rel: str) -> str:
    parts = Path(target_rel).parts
    if len(parts) >= 2 and parts[0] == "skills":
        return Path(parts[0], parts[1]).as_posix()
    return target_rel


def _remove_prompt_section_entry(
    manifest: Path,
    target_rel: str,
    section_name: str,
) -> bool:
    data = _load_yaml_manifest(manifest)
    if isinstance(data, dict):
        entries = data.get("sections") or data.get("prompt_sections") or []
        key = "sections" if "sections" in data else "prompt_sections"
    elif isinstance(data, list):
        entries = data
        data = {"sections": entries}
        key = "sections"
    else:
        entries = []
        data = {"sections": entries}
        key = "sections"
    if not isinstance(entries, list):
        entries = [entries]

    kept: list[object] = []
    removed = False
    for entry in entries:
        if _prompt_entry_matches(entry, target_rel, section_name):
            removed = True
            continue
        kept.append(entry)
    if not removed:
        return False
    data[key] = kept
    _write_yaml_manifest(manifest, data)
    return True


def _prompt_entry_matches(entry: object, target_rel: str, section_name: str) -> bool:
    if not isinstance(entry, dict):
        return False
    name = str(entry.get("name", "") or "")
    if section_name and name == section_name:
        return True
    file_value = entry.get("file") or entry.get("path")
    if not file_value:
        return False
    return _manifest_path_matches(str(file_value), target_rel)


def _remove_manifest_entries(
    manifest: Path,
    *,
    list_key: str,
    target_paths: set[str],
    exact_only: bool = False,
) -> bool:
    data = _load_yaml_manifest(manifest)
    if isinstance(data, dict):
        entries = data.get(list_key) or []
    elif isinstance(data, list):
        entries = data
        data = {list_key: entries}
    else:
        entries = []
        data = {list_key: entries}
    if not isinstance(entries, list):
        entries = [entries]

    kept: list[object] = []
    removed = False
    for entry in entries:
        if _manifest_entry_matches(entry, target_paths, exact_only=exact_only):
            removed = True
            continue
        kept.append(entry)
    if not removed:
        return False
    data[list_key] = kept
    _write_yaml_manifest(manifest, data)
    return True


def _manifest_entry_matches(
    entry: object,
    target_paths: set[str],
    *,
    exact_only: bool = False,
) -> bool:
    if isinstance(entry, str):
        return any(_manifest_path_matches(entry, target, exact_only=exact_only) for target in target_paths)
    if not isinstance(entry, dict):
        return False
    for key in ("file", "file_path", "path"):
        value = entry.get(key)
        if value and any(_manifest_path_matches(str(value), target, exact_only=exact_only) for target in target_paths):
            return True
    return False


def _manifest_path_matches(raw_path: str, target_rel: str, *, exact_only: bool = False) -> bool:
    normalized = _normalize_rel_path(raw_path)
    target = _normalize_rel_path(target_rel)
    if normalized == target:
        return True
    if exact_only:
        return False
    if not normalized.startswith(("skills/", "tools/", "prompt_sections/")):
        prefixed = f"prompt_sections/files/{normalized}"
        if prefixed == target:
            return True
    return target.startswith(f"{normalized}/")


def _load_yaml_manifest(path: Path) -> object:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _write_yaml_manifest(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


__all__ = [
    "MemberActionExecutor",
    "MemberActionExecutorAgent",
]
