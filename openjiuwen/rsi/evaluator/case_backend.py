# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution backends for one evaluation case."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
from openjiuwen.agent_teams.tools.database import TASK_TERMINAL_STATUSES
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator.controlled_skill_treatment_rail import (
    CONTROLLED_SKILL_TREATMENT_CASE_KEY,
    ControlledSkillTreatmentRail,
)
from openjiuwen.rsi.evaluator.judger import JudgeResult
from openjiuwen.rsi.evaluator.runtime_adapters import (
    RSISkillUseRail,
    RSISysOperationRail,
    run_agent_with_empty_response_recovery,
)
from openjiuwen.rsi.evaluator.team_factory import (
    TeamSkillTeamFactory,
    apply_team_spec_customizer_during_configure,
    clear_team_spec_customizer,
    resolve_team_skill_rail_config,
)
from openjiuwen.rsi.evaluator.trajectory_paths import (
    ROLE_TRAJECTORY_DIR_NAME,
    RoleFileTrajectoryStore,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)


@dataclass(frozen=True, slots=True)
class CaseExecutionResult:
    """Result returned by a case execution backend."""

    response: Any
    execution_status: str
    error: str = ""
    judge_result: JudgeResult | None = None
    team_spec: TeamAgentSpec | None = None
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
class LocalExecutionBackend:
    """Run an evaluation case through the local in-process Runner."""

    config: EvaluatorConfig
    team_factory: TeamSkillTeamFactory = field(init=False)

    def __post_init__(self) -> None:
        self.team_factory = TeamSkillTeamFactory(config=self.config)

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> CaseExecutionResult:
        """Run the Team locally and leave scoring to CaseRunner."""
        logger.info("[LocalExecutionBackend] begin to execute case: {}".format(case.get("case_id", "")))
        resolved_output_dir = str(Path(output_dir).expanduser().resolve())
        agent_team_spec = self._create_team_spec(
            output_dir=resolved_output_dir,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs=harness_refs or {},
        )
        status = "passed"
        response: Any = None
        error = ""

        await Runner.start()
        try:
            with apply_team_spec_customizer_during_configure(agent_team_spec):
                run_task = asyncio.create_task(
                    Runner.run_agent_team(
                        agent_team=agent_team_spec,
                        inputs=build_local_team_case_input(case),
                        session=session_id,
                    )
                )
                response, delivery_metadata = await _await_team_result_or_delivered_artifacts(
                    run_task=run_task,
                    case=case,
                    default_timeout_sec=self.config.case_lifecycle_timeout_sec,
                    team_name=agent_team_spec.team_name,
                    session_id=session_id,
                    db_config=agent_team_spec.resolve_db_config(),
                )
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            clear_team_spec_customizer(agent_team_spec)

        metadata = dict(delivery_metadata) if status == "passed" else {}
        logger.info("[LocalExecutionBackend] end to execute case: {}".format(case.get("case_id", "")))
        return CaseExecutionResult(
            response=response,
            execution_status=status,
            error=error,
            judge_result=None,
            team_spec=agent_team_spec,
            metadata=metadata,
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        """Release a case-scoped Team runtime after artifacts are written.

        Teardown order mirrors the proven path: delete the team (force=True
        stops any active runtime in-line) while the Runner is still up, then
        stop the Runner. ``Runner.stop()`` runs in ``finally`` so the global
        runtime is always released even if ``delete_agent_team`` fails.
        """
        try:
            try:
                await Runner.delete_agent_team(
                    team_name=team_name,
                    session_ids=[session_id],
                    force=True,
                )
            except AttributeError:
                await Runner.release(session_id, force=True)
            except RuntimeError as exc:
                if "Cannot resolve team session release info" not in str(exc):
                    raise
                await Runner.release(session_id, force=True)
        finally:
            await Runner.stop()

    def _create_team_spec(
        self,
        *,
        output_dir: str,
        team_skill_ref_path: str | Path | None,
        harness_refs: dict[str, str],
    ) -> TeamAgentSpec:
        return self.team_factory.create_team_spec(
            team_skill_ref_path=team_skill_ref_path,
            harness_refs=harness_refs,
            output_dir=output_dir,
        )


@dataclass(slots=True)
class SingleHarnessExecutionBackend:
    """Run an evaluation case through one DeepAgent bound to one ExpertHarness."""

    config: EvaluatorConfig

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
        started = False
        workspace_dir = Path(output_dir).expanduser().resolve() / "workspace"
        role_name = ""
        workspace_before: dict[str, dict[str, Any]] = {}
        workspace_after: dict[str, dict[str, Any]] = {}
        controlled_skill_treatment: ControlledSkillTreatmentRail | None = None
        skill_use_rails: list[Any] = []

        try:
            role_name, harness_path = _resolve_single_harness_ref(harness_refs or {})
            workspace_dir = _single_harness_workspace_dir(
                case=case,
                output_dir=Path(output_dir).expanduser().resolve(),
                session_id=session_id,
            )
            _prepare_single_harness_workspace(case, workspace_dir)
            workspace_before = _snapshot_workspace(workspace_dir)
            model = load_member_optimizer_model(self.config.model_config_ref)
            agent_rails = _single_harness_rails(
                team_skill_ref_path,
                controlled_skill_name=_controlled_skill_name(case),
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
                ),
                workspace=str(workspace_dir),
                rails=agent_rails,
                enable_task_loop=False,
                max_iterations=100,
                language="en",
                restrict_to_work_dir=False,
                auto_create_workspace=True,
                sys_operation=None,
            )
            await Runner.start()
            started = True
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
            _attach_single_harness_trajectory_rail(
                agent,
                output_dir=output_dir,
                role_name=role_name,
            )

            response = await run_agent_with_empty_response_recovery(
                agent,
                {"query": _case_inputs(case)},
                session=session_id,
            )
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            workspace_after = _snapshot_workspace(workspace_dir)
            if started:
                await Runner.stop()

        logger.info("[SingleHarnessExecutionBackend] end to execute case: {}".format(case.get("case_id", "")))
        return CaseExecutionResult(
            response=response,
            execution_status=status,
            error=error,
            judge_result=None,
            team_spec=None,
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
            ),
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        """No-op cleanup; this backend starts and stops Runner inside execute()."""


def _attach_single_harness_trajectory_rail(
    agent: Any,
    *,
    output_dir: str | Path,
    role_name: str,
) -> None:
    """Persist native tool calls so candidate capability gates can observe them."""
    from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail

    trace_root = Path(output_dir).expanduser().resolve() / ROLE_TRAJECTORY_DIR_NAME
    store = RoleFileTrajectoryStore(trace_root, role_name or "solver")
    agent.add_rail(TrajectoryRail(trajectory_store=store))


def _single_harness_rails(
    team_skill_ref_path: str | Path | None,
    *,
    shell_only: bool = False,
    controlled_skill_name: str = "",
) -> list[Any]:
    rails: list[Any] = [
        RSISysOperationRail(
            shell_only=shell_only,
            bash_pipefail=shell_only,
        )
    ]
    if controlled_skill_name:
        rails.append(ControlledSkillTreatmentRail(controlled_skill_name))
    if not team_skill_ref_path:
        return rails

    skill_rail_config = resolve_team_skill_rail_config(team_skill_ref_path)
    rails.append(
        RSISkillUseRail(
            skills_dir=skill_rail_config.skills_root,
            skill_mode=RSISkillUseRail.SKILL_MODE_ALL,
            enabled_skills=[skill_rail_config.enabled_skill],
            trigger_at_task_start=True,
        )
    )
    return rails


def _controlled_skill_name(case: dict[str, Any]) -> str:
    treatment = case.get(CONTROLLED_SKILL_TREATMENT_CASE_KEY)
    if isinstance(treatment, dict):
        return str(treatment.get("skill_name", "") or "").strip()
    if isinstance(treatment, str):
        return treatment.strip()
    return ""


def _case_inputs(case: dict[str, Any]) -> Any:
    for key in ("input", "inputs", "task_input", "query", "prompt"):
        if key in case:
            value = case[key]
            if key == "input" and isinstance(value, dict) and set(value) == {"user_message"}:
                return _normalize_case_input_for_backend(case, value["user_message"])
            return _normalize_case_input_for_backend(case, value)
    return case


def build_local_team_case_input(case: dict[str, Any]) -> str:
    """Build the positive execution contract for local Team evaluation."""
    raw_input = _case_inputs(case)
    if isinstance(raw_input, str):
        task_text = raw_input
    else:
        task_text = json.dumps(raw_input, ensure_ascii=False, indent=2)
    artifact_hint = _artifact_contract_hint(case, task_text)

    return "\n".join(
        [
            "这是一个自动评测 case。请按以下顺序完成团队执行：",
            "1. 调用 build_team 组建临时团队，并让团队目标对齐本 case 的交付物。",
            "2. 调用 create_task 创建面向交付物的任务 DAG，每个任务写清验收标准和产物路径。",
            "3. 使用 team skill 中的预置成员；当任务需要新增能力时，调用 spawn_member 添加成员。",
            "4. 调用 send_message 启动成员执行任务，并让成员在共享工作区产出可评测交付物。",
            "5. 检查共享工作区中的交付物，确保最终文件位于 .team/<team_name>/artifacts/。",
            "6. 质量闭环最多包含一次修复与一次复审；复审完成后写入最终结论。",
            "7. 调用 shutdown_member 关闭已完成工作的成员。",
            "8. 调用 clean_team 结束临时团队，让本次 evaluation case 返回给评测器。",
            "",
            "交付物路径契约：",
            "- 最终交付物必须直接写入 .team/<team_name>/artifacts/ 根目录；"
            "artifacts/code、artifacts/docs、artifacts/reports 仅作为辅助目录，"
            "最终交付物验收路径以 artifacts 根目录文件为准。",
            artifact_hint,
            "",
            "原始任务：",
            task_text,
        ]
    )


def _artifact_contract_hint(case: dict[str, Any], task_text: str) -> str:
    """Return a case-specific artifact hint without hard-coding one domain."""
    expected_files = _artifact_files_from_case(case, task_text)
    if expected_files:
        root_paths = "、".join(f".team/<team_name>/artifacts/{filename}" for filename in expected_files)
        return (
            "本 case 期望的文件包括："
            + "、".join(expected_files)
            + "。创建任务 DAG 时必须把这些路径写成最终产物验收路径："
            + root_paths
            + "。"
        )
    return "具体文件名以原始任务和 case reference 中声明的交付物为准。"


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


async def _await_team_result_or_delivered_artifacts(
    *,
    run_task: asyncio.Task[Any],
    case: dict[str, Any],
    team_name: str,
    session_id: str,
    db_config: Any,
    default_timeout_sec: float = 3600.0,
) -> tuple[Any, dict[str, Any]]:
    """Wait for the Team to finish its delivery and lifecycle naturally.

    Files becoming stable is not equivalent to completion: after the final
    artifact write the leader still has to collect evidence, summarize the
    result, shut members down, and clean the temporary team.  Stopping the Team
    from this evaluator path races that final round and turns a valid delivery
    into ``round_aborted``.  Lifecycle ownership therefore remains entirely
    with Team; evaluator cleanup runs only after this task returns.
    """

    _ = case, team_name, session_id, db_config, default_timeout_sec
    return await run_task, {}


async def _team_task_board_terminal_for_delivery(
    *,
    db_config: Any,
    team_name: str,
    session_id: str,
) -> bool:
    """Return True only when artifact cleanup is waiting on lifecycle cleanup.

    Stable expected artifacts are not enough to stop a temporary team: the
    team may still be running QA, revisions, or final integration. Forced
    cleanup is only safe once at least one task exists and every task is in a
    terminal status, which means any remaining wait is shutdown/clean debt.
    """
    token = set_session_id(session_id)
    try:
        db = get_shared_db(db_config)
        await db.initialize()
        tasks = await db.task.get_team_tasks(team_name)
    except Exception as exc:
        logger.warning(
            "failed to inspect task board before artifact delivery cleanup for {} / {}: {}",
            team_name,
            session_id,
            exc,
        )
        return False
    finally:
        reset_session_id(token)

    if not tasks:
        return False
    return all(getattr(task, "status", None) in TASK_TERMINAL_STATUSES for task in tasks)


def _expected_artifact_files_for_delivery(case: dict[str, Any]) -> list[str]:
    raw_input = _case_inputs(case)
    task_text = raw_input if isinstance(raw_input, str) else json.dumps(raw_input, ensure_ascii=False)
    return _artifact_files_from_case(case, task_text)


def _artifact_delivery_grace_sec(case: dict[str, Any]) -> float:
    return _positive_float(case.get("artifact_delivery_grace_sec", 90.0), default=90.0)


def _artifact_delivery_poll_sec(case: dict[str, Any]) -> float:
    return _positive_float(case.get("artifact_delivery_poll_sec", 5.0), default=5.0)


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 0.001)


def _expected_artifact_snapshot(
    artifacts_dir: Path,
    expected_files: list[str],
) -> tuple[tuple[str, int, int], ...] | None:
    snapshot: list[tuple[str, int, int]] = []
    for filename in expected_files:
        path = _find_expected_artifact_file(artifacts_dir, filename)
        if path is None:
            return None
        stat = path.stat()
        snapshot.append((filename, stat.st_size, stat.st_mtime_ns))
    return tuple(snapshot)


def _find_expected_artifact_file(artifacts_dir: Path, filename: str) -> Path | None:
    direct = artifacts_dir / filename
    if direct.is_file():
        return direct
    candidates = [path for path in sorted(artifacts_dir.rglob(filename)) if path.is_file() and path.name == filename]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


async def _stop_delivered_temporary_team(*, team_name: str, session_id: str) -> bool:
    try:
        return await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
    except AttributeError:
        return False
    except Exception as exc:
        logger.warning(
            "temporary team stop after artifact delivery failed for {} / {}: {}",
            team_name,
            session_id,
            exc,
        )
        return False


async def _finish_or_cancel_team_task(run_task: asyncio.Task[Any]) -> Any:
    if run_task.done():
        return run_task.result()
    try:
        return await asyncio.wait_for(asyncio.shield(run_task), timeout=10.0)
    except asyncio.CancelledError:
        return None
    except TimeoutError:
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        return None
    except Exception as exc:
        logger.warning("team task ended after delivered artifact cleanup with error: {}", exc)
        return None


def _normalize_case_input_for_backend(case: dict[str, Any], value: Any) -> Any:
    """Return case input unchanged; external adapters own environment hints."""
    del case
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
    has_external_workspace = bool(str(case.get("workspace_source_dir", "") or "").strip())
    if os.name != "nt" or not has_external_workspace:
        return output_dir / "workspace"
    runtime_root = Path(".local/rsi/single_harness_runtime").resolve()
    safe_session = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in session_id)
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


def _single_harness_system_prompt(role_name: str) -> str:
    return (
        f"You are the standalone evaluation agent for role `{role_name}`. "
        "Solve the given task with the bound expert harness and available local tools. "
        "Write any produced files under the current workspace unless the task explicitly "
        "names another working directory. Preserve existing line endings when editing files; "
        "Terminal-Bench verifiers may compare exact file hashes."
    )


def _single_harness_metadata(
    *,
    role_name: str,
    workspace_before: dict[str, dict[str, Any]],
    workspace_after: dict[str, dict[str, Any]],
    team_skill_ref_path: str | Path | None,
    controlled_skill_treatment: dict[str, Any] | None = None,
    skill_triggers: list[dict[str, Any]] | None = None,
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
    return metadata


def _single_harness_team_skill_metadata(
    team_skill_ref_path: str | Path | None,
) -> dict[str, str]:
    if not team_skill_ref_path:
        return {}

    skill_rail_config = resolve_team_skill_rail_config(team_skill_ref_path)
    skill_dir = Path(skill_rail_config.skills_root) / skill_rail_config.enabled_skill
    skill_md_path = skill_dir / "SKILL.md"
    metadata = {
        "ref_path": str(Path(team_skill_ref_path)),
        "skills_root": str(skill_rail_config.skills_root),
        "enabled_skill": skill_rail_config.enabled_skill,
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
    "local": LocalExecutionBackend,
    "single_harness": SingleHarnessExecutionBackend,
}


def build_backend(config: EvaluatorConfig) -> CaseExecutionBackend:
    """Instantiate the execution backend named by ``config.backend``.

    Each backend constructs its own ``TeamSkillTeamFactory`` from ``config``.
    Callers must not pass a separate ``team_factory`` (avoids duplicate
    construction and redundant parameter threading).
    """
    factory_cls = _BACKEND_REGISTRY.get(config.backend)
    if factory_cls is None:
        raise ValueError(f"unknown backend type: {config.backend!r}; supported values: {list(_BACKEND_REGISTRY)}")
    return factory_cls(config=config)


__all__ = [
    "CaseExecutionBackend",
    "CaseExecutionResult",
    "LocalExecutionBackend",
    "SingleHarnessExecutionBackend",
    "build_local_team_case_input",
    "build_backend",
]
