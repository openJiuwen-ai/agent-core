# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution backends for one evaluation case."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.setup import get_config, init_observability
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.data_loader.case_files import copy_public_assets, task_input
from openjiuwen.rsi.harness_rsi.evaluator.controlled_skill_treatment_rail import (
    CONTROLLED_SKILL_TREATMENT_CASE_KEY,
    ControlledSkillTreatmentRail,
)
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.harness_input_rail import HarnessInputRail
from openjiuwen.rsi.harness_rsi.evaluator.judger import JudgeResult
from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import (
    RSISkillUseRail,
    RSISysOperationRail,
    run_agent_with_empty_response_recovery,
)
from openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime import prepare_swebench_workspace
from openjiuwen.rsi.harness_rsi.evaluator.terminal_bench_runtime import (
    TerminalBenchCommandRecorder,
    build_terminal_bench_sys_operation,
    remove_terminal_bench_container,
    run_docker,
    start_terminal_bench_solver_container,
    sync_container_git_patch_to_workspace,
)
from openjiuwen.rsi.harness_rsi.evaluator.trajectory_paths import (
    ROLE_TRAJECTORY_DIR_NAME,
    RoleFileTrajectoryStore,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)
from openjiuwen.rsi.harness_rsi.runtime_reliability import build_single_agent_reliability_rail


@dataclass(frozen=True, slots=True)
class CaseExecutionResult:
    """Result returned by a case execution backend."""

    response: Any
    execution_status: str
    error: str = ""
    judge_result: JudgeResult | None = None
    workspace_dir: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CaseExecutionBackend(Protocol):
    """Execution strategy for one evaluation case."""

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> CaseExecutionResult:
        """Run one case and return execution output only."""
        ...

    async def cleanup(self, team_name: str, session_id: str) -> None:
        """Release case-scoped runtime resources after artifacts are written."""
        ...


@dataclass(slots=True)
class SingleHarnessExecutionBackend:
    """Run an evaluation case through one DeepAgent bound to one ExpertHarness."""

    config: EvaluatorConfig
    _trajectory_span_processor: TrajectorySpanProcessor = field(
        default_factory=TrajectorySpanProcessor,
        init=False,
        repr=False,
    )

    @property
    def trajectory_span_processor(self) -> TrajectorySpanProcessor:
        """Return the context-isolated processor shared by case runners."""
        return self._trajectory_span_processor

    def reuse_trajectory_processor(self, source: SingleHarnessExecutionBackend) -> None:
        """Share context-isolated tracing without registering another processor."""
        self._trajectory_span_processor = source.trajectory_span_processor

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> CaseExecutionResult:
        """Run one standalone harness and leave scoring to CaseRunner."""
        logger.info("[SingleHarnessExecutionBackend] begin to execute case: {}".format(case.get("case_id", "")))
        status = "passed"
        response: Any = None
        error = ""
        agent = None
        workspace_dir = Path(output_dir).expanduser().resolve() / "workspace"
        role_name = ""
        workspace_before: dict[str, dict[str, Any]] = {}
        workspace_after: dict[str, dict[str, Any]] = {}
        controlled_skill_treatment: ControlledSkillTreatmentRail | None = None
        skill_use_rails: list[Any] = []
        solver_container_name = ""
        command_recorder: TerminalBenchCommandRecorder | None = None
        swebench_model_patch_path = ""

        try:
            role_name, harness_path = _resolve_single_harness_ref(harness_refs or {})
            workspace_dir = _single_harness_workspace_dir(
                case=case,
                output_dir=Path(output_dir).expanduser().resolve(),
                session_id=session_id,
            )
            swebench = case.get("swebench")
            sys_operation = None
            if isinstance(swebench, dict):
                timeout_sec = int(float(swebench.get("timeout_sec") or 1800))
                image = prepare_swebench_workspace(
                    case=case,
                    workspace_dir=workspace_dir,
                    timeout_sec=timeout_sec,
                )
                command_recorder = TerminalBenchCommandRecorder()
                solver_container_name = start_terminal_bench_solver_container(
                    docker_image=image,
                    case_id=str(case.get("case_id") or session_id),
                    workspace_dir=workspace_dir,
                    timeout_sec=timeout_sec,
                    container_workspace_dir="/testbed",
                    mount_workspace=False,
                )
                _stage_case_assets(case, workspace_dir, solver_container_name)
                sys_operation = build_terminal_bench_sys_operation(
                    sys_operation_id=f"sweb_single_{session_id}",
                    container_name=solver_container_name,
                    workspace_dir=workspace_dir,
                    recorder=command_recorder,
                    container_workspace_dir="/testbed",
                    clean_shell=True,
                    runtime_environment=_swebench_testbed_environment(),
                    enforce_in_container_timeout=True,
                )
            else:
                _prepare_single_harness_workspace(case, workspace_dir)
                _stage_case_assets(case, workspace_dir)
            workspace_before = _snapshot_workspace(workspace_dir)
            model = load_member_optimizer_model(self.config.model_config_ref)
            agent_rails = _single_harness_rails(
                team_skill_ref_path,
                harness_path=harness_path,
                shell_only=bool(solver_container_name),
                controlled_skill_name=_controlled_skill_name(case),
                workspace="/testbed" if solver_container_name else workspace_dir,
            )
            controlled_skill_treatment = next(
                (rail for rail in agent_rails if isinstance(rail, ControlledSkillTreatmentRail)),
                None,
            )
            agent = create_deep_agent(
                model=model,
                card=AgentCard(
                    name=role_name,
                    description=f"Single harness evaluator role: {role_name}",
                ),
                system_prompt=_single_harness_system_prompt(
                    role_name,
                    workspace="/testbed" if solver_container_name else str(workspace_dir),
                ),
                workspace=str(workspace_dir),
                rails=[rail for rail in agent_rails if not isinstance(rail, RSISkillUseRail)],
                enable_task_loop=False,
                max_iterations=100,
                language="en",
                restrict_to_work_dir=False,
                auto_create_workspace=True,
                sys_operation=sys_operation,
            )
            await Runner.start()
            # Register through the native API before plugin discovery so the
            # rail has its filesystem operation when reading Skill descriptions.
            for rail in agent_rails:
                if isinstance(rail, RSISkillUseRail):
                    await agent.register_rail(rail)
            await agent.load_plugin(harness_path)
            find_rails = getattr(agent, "find_rails_by_type", None)
            skill_use_rails = (
                list(find_rails((RSISkillUseRail,)))
                if callable(find_rails)
                else [rail for rail in agent_rails if isinstance(rail, RSISkillUseRail)]
            )
            for skill_rail in skill_use_rails:
                # Single-Harness execution uses the same task-start trigger in
                # source, candidate, replay, and published evaluations.  The
                # selector receives only task text and Skill metadata.
                skill_rail.list_skill_model = model
                skill_rail.trigger_at_task_start = True
            if solver_container_name:
                _enforce_container_sys_operation_rail(agent)
            _attach_single_harness_trajectory_rail(
                agent,
                output_dir=output_dir,
                role_name=role_name,
                trajectory_span_processor=self._trajectory_span_processor,
            )

            response = await run_agent_with_empty_response_recovery(
                agent,
                {"query": _case_inputs(case)},
                session=session_id,
            )
        except EvaluationInfrastructureError:
            status = "failed"
            raise
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            if solver_container_name:
                try:
                    model_patch = sync_container_git_patch_to_workspace(
                        container_name=solver_container_name,
                        workspace_dir=workspace_dir,
                        container_workspace_dir="/testbed",
                    )
                    patch_path = Path(output_dir).expanduser().resolve() / "verifier" / "container_model.patch"
                    patch_path.parent.mkdir(parents=True, exist_ok=True)
                    patch_path.write_text(model_patch, encoding="utf-8")
                    swebench_model_patch_path = str(patch_path)
                except Exception as exc:
                    status = "failed"
                    error = f"failed to sync SWE-bench solver workspace: {exc}"
            workspace_after = _snapshot_workspace(workspace_dir)
            try:
                if agent is not None:
                    # Runner is process-global: another case or Judge may still
                    # be using it. Release only this execution's owned resources.
                    try:
                        await agent.cleanup_task_resources()
                    finally:
                        agent.ability_manager.teardown_tools()
                        if sys_operation is None:
                            Runner.resource_mgr.remove_sys_operation(f"{agent.card.name}_{agent.card.id}")
            finally:
                if solver_container_name:
                    remove_terminal_bench_container(solver_container_name)

        logger.info("[SingleHarnessExecutionBackend] end to execute case: {}".format(case.get("case_id", "")))
        return CaseExecutionResult(
            response=response,
            execution_status=status,
            error=error,
            judge_result=None,
            workspace_dir=str(workspace_dir),
            metadata=_single_harness_metadata(
                role_name=role_name,
                workspace_before=workspace_before,
                workspace_after=workspace_after,
                team_skill_ref_path=team_skill_ref_path,
                controlled_skill_treatment=(
                    controlled_skill_treatment.evidence() if controlled_skill_treatment is not None else None
                ),
                skill_triggers=[rail.task_trigger_evidence() for rail in skill_use_rails],
                command_recorder=command_recorder,
                swebench_model_patch_path=swebench_model_patch_path,
            ),
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        """No-op; execute releases case resources, not the host-owned Runner."""


def _enforce_container_sys_operation_rail(agent: Any) -> None:
    """Keep workspace edits in the solver container after Plugin loading."""
    from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
    from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail

    for skill_rail in agent.find_rails_by_type((SkillUseRail,)):
        skill_rail.include_tools = False
    agent.strip_rails_by_type((SysOperationRail,))
    agent.add_rail(RSISysOperationRail(shell_only=True, bash_pipefail=True))


def _attach_single_harness_trajectory_rail(
    agent: Any,
    *,
    output_dir: str | Path,
    role_name: str,
    trajectory_span_processor: TrajectorySpanProcessor,
) -> None:
    """Persist native tool calls so candidate capability gates can observe them."""
    from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail

    trace_root = Path(output_dir).expanduser().resolve() / ROLE_TRAJECTORY_DIR_NAME
    config = get_config() or ObservabilityConfig(exporter="file", traces_dir=str(trace_root))
    init_observability(config, additional_span_processors=(trajectory_span_processor,))
    store = RoleFileTrajectoryStore(trace_root, role_name or "solver")
    agent.add_rail(
        TrajectoryRail(
            trajectory_store=store,
            trajectory_span_processor=trajectory_span_processor,
        )
    )


def _single_harness_rails(
    team_skill_ref_path: str | Path | None,
    *,
    harness_path: str | Path,
    shell_only: bool = False,
    controlled_skill_name: str = "",
    workspace: str | Path | None = None,
) -> list[Any]:
    rails: list[Any] = [
        RSISysOperationRail(
            shell_only=shell_only,
            bash_pipefail=shell_only,
        ),
        build_single_agent_reliability_rail(),
    ]
    if controlled_skill_name:
        rails.append(ControlledSkillTreatmentRail(controlled_skill_name))
    if workspace is not None:
        rails.append(HarnessInputRail(harness_path, workspace))
    skill_dir = _resolve_skill_dir(team_skill_ref_path) if team_skill_ref_path else None
    # load_plugin binds skills to an existing native rail. Register the RSI
    # delivery adapter even for an empty H0; do not add any baseline skill.
    rails.append(
        RSISkillUseRail(
            skills_dir=str(skill_dir.parent if skill_dir else Path(harness_path) / "skills"),
            skill_mode=RSISkillUseRail.SKILL_MODE_ALL,
            enabled_skills=[skill_dir.name] if skill_dir else None,
            include_tools=not shell_only,
            trigger_at_task_start=True,
        )
    )
    return rails


def _resolve_skill_dir(skill_ref_path: str | Path) -> Path:
    ref_path = Path(skill_ref_path).expanduser().resolve()
    if ref_path.name.lower() == "skill.md":
        return ref_path.parent
    return ref_path if ref_path.is_dir() else ref_path.parent


def _controlled_skill_name(case: dict[str, Any]) -> str:
    treatment = case.get(CONTROLLED_SKILL_TREATMENT_CASE_KEY)
    if isinstance(treatment, dict):
        return str(treatment.get("skill_name", "") or "").strip()
    if isinstance(treatment, str):
        return treatment.strip()
    return ""


def _stage_case_assets(case: dict[str, Any], workspace: Path, container_name: str = "") -> None:
    try:
        for relative, path in copy_public_assets(case, workspace):
            if container_name:
                target = PurePosixPath("/testbed") / relative
                run_docker(["docker", "exec", container_name, "mkdir", "-p", "--", str(target.parent)], timeout=120)
                run_docker(["docker", "cp", str(path), f"{container_name}:{target}"], timeout=120)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise EvaluationInfrastructureError(f"dataset assets could not be prepared: {exc}") from exc


def _case_inputs(case: dict[str, Any]) -> Any:
    return _normalize_case_input_for_backend(case, task_input(case))


def _artifact_files_from_case(case: dict[str, Any], task_text: str) -> list[str]:
    candidates: list[Any] = [task_text]
    for mapping_key in ("reference", "expected_output", "metadata"):
        value = case.get(mapping_key)
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, str):
            candidates.append(value)
    files: list[str] = []
    for value in candidates:
        files.extend(_artifact_files_from_value(value))
    return list(dict.fromkeys(files))


def _artifact_files_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return re.findall(r"(?<![\w/.-])[\w.-]+\.(?:html|css|js|md|json|ya?ml|py|txt)(?![\w/.-])", value)
    if isinstance(value, dict):
        files: list[str] = []
        for key in (
            "expected_artifacts",
            "required_files",
            "output_files",
            "final_files",
            "expected_files",
            "deliverables",
            "gold_answer_or_expected_artifact",
        ):
            if key in value:
                files.extend(_artifact_files_from_value(value[key]))
        return files
    if isinstance(value, list):
        files: list[str] = []
        for item in value:
            files.extend(_artifact_files_from_value(item))
        return files
    return []


def _normalize_case_input_for_backend(case: dict[str, Any], value: Any) -> Any:
    """Expose the container workspace without altering the stored task input."""
    if isinstance(case.get("swebench"), dict) and isinstance(value, str):
        return (
            "Execution environment: the repository is mounted at "
            "`/testbed` inside the task container. Shell commands already run there. "
            "Use `bash` for repository file inspection and edits; do not guess another path or "
            "use host filesystem tools.\n\n"
            f"{value}"
        )
    return value


def _resolve_single_harness_ref(harness_refs: dict[str, str]) -> tuple[str, str]:
    refs = {str(role): str(path) for role, path in harness_refs.items() if str(path).strip()}
    if len(refs) != 1:
        raise ValueError(f"single_harness backend requires exactly one harness ref; got {len(refs)}")
    return next(iter(refs.items()))


def _prepare_single_harness_workspace(case: dict[str, Any], workspace_dir: Path) -> None:
    source_value = str(case.get("workspace_source_dir", "") or "").strip()
    if not source_value:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return

    source_dir = Path(source_value).expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"workspace_source_dir not found: {source_dir}")
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    _copy_workspace_tree(source_dir, workspace_dir)
    _configure_git_lf_line_endings(workspace_dir)


def _single_harness_workspace_dir(
    *,
    case: dict[str, Any],
    output_dir: Path,
    session_id: str,
) -> Path:
    has_external_workspace = bool(
        str(case.get("workspace_source_dir", "") or "").strip() or isinstance(case.get("swebench"), dict)
    )
    if os.name != "nt" or not has_external_workspace:
        return output_dir / "workspace"
    runtime_root = Path(".local/w").resolve()
    workspace_key = f"{output_dir.resolve()}\0{session_id}"
    safe_session = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()[:16]
    return runtime_root / safe_session


def _copy_workspace_tree(source_dir: Path, workspace_dir: Path) -> None:
    if os.name == "nt":
        robocopy = shutil.which("robocopy")
        if robocopy is None:
            raise RuntimeError("robocopy is required to prepare a Windows evaluation workspace")
        workspace_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                robocopy,
                str(source_dir),
                str(workspace_dir),
                "/E",
                "/R:1",
                "/W:1",
                "/NFL",
                "/NDL",
                "/NJH",
                "/NJS",
                "/NP",
            ],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode > 7:
            raise RuntimeError(
                "robocopy failed while preparing single harness workspace: "
                f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return
    shutil.copytree(source_dir, workspace_dir)


def _configure_git_lf_line_endings(workspace_dir: Path) -> None:
    """Keep Terminal-Bench git workspaces byte-stable on Windows.

    Some Terminal-Bench tests compare exact file hashes. If the local git
    config inherits Windows CRLF conversion, a correct merge can still fail
    hash-based verification because LF files become CRLF.
    """
    git_executable = shutil.which("git")
    if git_executable is None:
        return
    git_dirs = [path for path in workspace_dir.rglob(".git") if path.is_dir()]
    if (workspace_dir / ".git").is_dir():
        git_dirs.insert(0, workspace_dir / ".git")
    seen: set[Path] = set()
    for git_dir in git_dirs:
        repo_dir = git_dir.parent.resolve()
        if repo_dir in seen:
            continue
        seen.add(repo_dir)
        for key, value in (
            ("core.autocrlf", "false"),
            ("core.eol", "lf"),
            ("core.safecrlf", "false"),
        ):
            subprocess.run(
                [git_executable, "-C", str(repo_dir), "config", key, value],
                check=False,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )


def _single_harness_system_prompt(role_name: str, *, workspace: str = "") -> str:
    return (
        f"You are the standalone evaluation agent for role `{role_name}`. "
        "Solve the given task with the bound expert harness and available local tools. "
        "Write any produced files under the current workspace unless the task explicitly "
        "names another working directory. Preserve existing line endings when editing files; "
        "Terminal-Bench verifiers may compare exact file hashes."
        + (f" Your task workspace and default output directory is `{workspace}`. "
           "Loaded plugin and skill directories are read-only capability sources, not task "
           "workspaces. Do not write deliverables, temporary files or validation reports there."
           if workspace else "")
    )


def _single_harness_metadata(
    *,
    role_name: str,
    workspace_before: dict[str, dict[str, Any]],
    workspace_after: dict[str, dict[str, Any]],
    team_skill_ref_path: str | Path | None,
    controlled_skill_treatment: dict[str, Any] | None = None,
    skill_triggers: list[dict[str, Any]] | None = None,
    command_recorder: TerminalBenchCommandRecorder | None = None,
    swebench_model_patch_path: str = "",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "member_id": role_name,
        "member_role": role_name,
        "workspace_changes": _workspace_changes(workspace_before, workspace_after),
    }
    team_skill_metadata = _single_harness_team_skill_metadata(team_skill_ref_path)
    if team_skill_metadata:
        metadata["team_skill"] = team_skill_metadata
    if controlled_skill_treatment is not None:
        metadata["controlled_skill_treatment"] = controlled_skill_treatment
    if skill_triggers:
        metadata["skill_triggers"] = [dict(item) for item in skill_triggers]
    if command_recorder is not None:
        metadata["command_log"] = command_recorder.to_list()
    if swebench_model_patch_path:
        metadata["swebench_model_patch_path"] = swebench_model_patch_path
    return metadata


def _single_harness_team_skill_metadata(
    team_skill_ref_path: str | Path | None,
) -> dict[str, str]:
    if not team_skill_ref_path:
        return {}

    skill_dir = _resolve_skill_dir(team_skill_ref_path)
    skill_md_path = skill_dir / "SKILL.md"
    metadata = {
        "ref_path": str(Path(team_skill_ref_path)),
        "skills_root": str(skill_dir.parent),
        "enabled_skill": skill_dir.name,
        "skill_mode": "all",
    }
    try:
        skill_md = skill_md_path.read_bytes()
    except OSError:
        return metadata
    metadata["skill_md_sha256"] = hashlib.sha256(skill_md).hexdigest()
    return metadata


def _snapshot_workspace(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    """Create a bounded workspace file snapshot for behavior traces."""
    if not workspace_dir.is_dir():
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    file_paths: list[Path] = []
    pruned_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [name for name in dirs if name not in pruned_dirs]
        file_paths.extend(Path(root) / name for name in files)
    for file_path in sorted(file_paths):
        try:
            rel = file_path.relative_to(workspace_dir).as_posix()
        except ValueError:
            continue
        if _skip_trace_snapshot_path(rel):
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        snapshot[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return snapshot


def _workspace_changes(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "added": sorted(after_paths - before_paths)[:200],
        "modified": sorted(path for path in before_paths & after_paths if before[path] != after[path])[:200],
        "removed": sorted(before_paths - after_paths)[:200],
    }


def _skip_trace_snapshot_path(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return True
    if _is_runtime_workspace_metadata(path):
        return True
    if parts[0] in {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
        return True
    if any(part in {"node_modules", ".venv", "venv"} for part in parts):
        return True
    return Path(path).suffix.lower() in {".pyc", ".pyo", ".log"}


def _is_runtime_workspace_metadata(path: str) -> bool:
    """Return whether ``path`` is created and owned by the agent runtime."""
    normalized = Path(path).as_posix().lstrip("./")
    if normalized in {"AGENT.md", "SOUL.md", "IDENTITY.md", "USER.md", "HEARTBEAT.md"}:
        return True
    parts = Path(normalized).parts
    if not parts:
        return True
    if parts[0] in {"context", "memory", "messages", "todo"}:
        return True
    return parts[0] in {"agents", "skills"} and parts[-1] == ".workspace"


_BACKEND_REGISTRY: dict[str, type] = {
    "single_harness": SingleHarnessExecutionBackend,
}


def build_backend(config: EvaluatorConfig) -> CaseExecutionBackend:
    """Instantiate the execution backend named by ``config.backend``.

    RSI deliberately exposes only the standalone Harness backend.
    """
    factory_cls = _BACKEND_REGISTRY.get(config.backend)
    if factory_cls is None:
        raise ValueError(f"unknown backend type: {config.backend!r}; supported values: {list(_BACKEND_REGISTRY)}")
    return factory_cls(config=config)


__all__ = [
    "CaseExecutionBackend",
    "CaseExecutionResult",
    "SingleHarnessExecutionBackend",
    "build_backend",
]


def _swebench_testbed_environment() -> dict[str, str]:
    """Use the image's testbed interpreter without running conda activation.

    SWE-bench images activate this environment from login-shell profiles.
    Replaying that profile for every tool call is both expensive and capable
    of leaving conda activation processes behind when a docker exec times out.
    """
    return {
        "CONDA_DEFAULT_ENV": "testbed",
        "CONDA_PREFIX": "/opt/miniconda3/envs/testbed",
        "PATH": (
            "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "PYTHONNOUSERSITE": "1",
    }
