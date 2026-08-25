# coding: utf-8
"""Benchmark-owned execution and scoring adapters for WorkBuddy Office."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts import PromptSection
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
    AtomicChecksSignalExtractor,
    register_signal_extractor,
)
from openjiuwen.rsi.evaluator.case_backend import (
    CaseExecutionResult,
    _attach_single_harness_trajectory_rail,
    _case_inputs,
    _controlled_skill_name,
    _resolve_single_harness_ref,
    _single_harness_metadata,
    _single_harness_rails,
    _single_harness_system_prompt,
    _snapshot_workspace,
)
from openjiuwen.rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.evaluator.controlled_skill_treatment_rail import (
    ControlledSkillTreatmentRail,
)
from openjiuwen.rsi.evaluator.judger import EvaluationJudger, JudgeResult
from openjiuwen.rsi.evaluator.runtime_adapters import (
    RSISkillUseRail,
    RSISysOperationRail,
    run_agent_with_empty_response_recovery,
)
from openjiuwen.rsi.evaluator.team_evaluator import TeamEvaluator
from openjiuwen.rsi.member_optimizer.agents.factory import load_member_optimizer_model

from examples.rsi.workbuddy_office.container_runtime import (
    TerminalBenchCommandRecorder,
    build_terminal_bench_sys_operation,
    remove_terminal_bench_container,
    run_docker,
    start_terminal_bench_solver_container,
)
from examples.rsi.workbuddy_office.runtime import (
    WorkBuddyInfrastructureError,
    prepare_workbuddy_office_workspace,
    run_workbuddy_office_verifier,
)


_SHARED_JIUWENSWARM_RUNTIME = "/opt/rsi-jiuwenswarm"


@dataclass(slots=True)
class WorkBuddyOfficeBackend:
    """Run a single Expert Harness inside the official task container."""

    config: EvaluatorConfig
    _containers: dict[str, str] = field(default_factory=dict, init=False)

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> CaseExecutionResult:
        if not isinstance(case.get("workbuddy_office"), dict):
            raise ValueError("WorkBuddyOfficeBackend requires case.workbuddy_office")

        status = "passed"
        response: Any = None
        error = ""
        runner_started = False
        container_name = ""
        role_name = ""
        workspace_dir = Path(output_dir).expanduser().resolve() / "workspace"
        workspace_before: dict[str, dict[str, Any]] = {}
        workspace_after: dict[str, dict[str, Any]] = {}
        command_recorder = TerminalBenchCommandRecorder()
        controlled_skill_treatment: ControlledSkillTreatmentRail | None = None
        skill_use_rails: list[Any] = []
        solver_metadata: dict[str, Any] = {}

        try:
            role_name, harness_path = _resolve_single_harness_ref(harness_refs or {})
            workspace_dir = _workbuddy_workspace_dir(
                output_dir=Path(output_dir).expanduser().resolve(),
                session_id=session_id,
            )
            workbuddy_config = case["workbuddy_office"]
            timeout_sec = int(float(workbuddy_config.get("timeout_sec") or 1800))
            image = prepare_workbuddy_office_workspace(
                case=case,
                workspace_dir=workspace_dir,
                timeout_sec=timeout_sec,
            )
            workspace_before = _snapshot_workspace(workspace_dir)
            extra_bind_mounts: list[tuple[Path, str]] = []
            extra_volume_mounts: list[tuple[str, str]] = []
            if self.config.solver_backend == "jiuwenswarm":
                if not str(self.config.jiuwenswarm_python or "").strip():
                    version = str(self.config.jiuwenswarm_expected_version or "").strip() or "0.2.3"
                    extra_volume_mounts.append(
                        (
                            _jiuwenswarm_runtime_volume_name(version),
                            _SHARED_JIUWENSWARM_RUNTIME,
                        )
                    )
            try:
                container_name = start_terminal_bench_solver_container(
                    docker_image=image,
                    case_id=str(case.get("case_id") or session_id),
                    workspace_dir=workspace_dir,
                    timeout_sec=timeout_sec,
                    container_workspace_dir="/workspace",
                    extra_bind_mounts=extra_bind_mounts,
                    extra_volume_mounts=extra_volume_mounts,
                )
                self._containers[session_id] = container_name
                sys_operation = build_terminal_bench_sys_operation(
                    sys_operation_id=f"rsi_workbuddy_{_safe_id(session_id)}",
                    container_name=container_name,
                    workspace_dir=workspace_dir,
                    recorder=command_recorder,
                    container_workspace_dir="/workspace",
                    enforce_in_container_timeout=True,
                )
            except Exception as exc:
                raise WorkBuddyInfrastructureError(f"failed to start the WorkBuddy solver container: {exc}") from exc

            if self.config.solver_backend == "jiuwenswarm":
                response, solver_metadata = await _run_jiuwenswarm_solver(
                    config=self.config,
                    container_name=container_name,
                    output_dir=Path(output_dir).expanduser().resolve(),
                    harness_path=Path(harness_path).expanduser().resolve(),
                    role_name=role_name,
                    instruction=_case_inputs(case),
                    session_id=session_id,
                    required_artifact_paths=_required_workbuddy_artifact_paths(case),
                )
            else:
                model = load_member_optimizer_model(self.config.model_config_ref)
                rails = _single_harness_rails(
                    team_skill_ref_path,
                    shell_only=True,
                    controlled_skill_name=_controlled_skill_name(case),
                )
                controlled_skill_treatment = next(
                    (rail for rail in rails if isinstance(rail, ControlledSkillTreatmentRail)),
                    None,
                )
                agent = create_deep_agent(
                    model=model,
                    card=AgentCard(
                        name=role_name,
                        description=f"WorkBuddy Office evaluator role: {role_name}",
                    ),
                    system_prompt=_single_harness_system_prompt(role_name),
                    workspace=str(workspace_dir),
                    rails=rails,
                    enable_task_loop=False,
                    max_iterations=100,
                    language="en",
                    restrict_to_work_dir=False,
                    auto_create_workspace=True,
                    sys_operation=sys_operation,
                )
                await Runner.start()
                runner_started = True
                _prepare_expert_harness_prompt_overlay(
                    agent,
                    evaluation_prompt=_single_harness_system_prompt(role_name),
                )
                await agent.load_plugin(harness_path)
                self._enforce_container_tools(agent)

                find_rails = getattr(agent, "find_rails_by_type", None)
                skill_use_rails = (
                    list(find_rails((RSISkillUseRail,)))
                    if callable(find_rails)
                    else [rail for rail in rails if isinstance(rail, RSISkillUseRail)]
                )
                for skill_rail in skill_use_rails:
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
        except WorkBuddyInfrastructureError:
            raise
        except Exception as exc:  # The verifier distinguishes semantic failure later.
            status = "failed"
            error = str(exc)
            logger.exception("WorkBuddy Office case execution failed: %s", case.get("case_id", ""))
        finally:
            workspace_after = _snapshot_workspace(workspace_dir)
            if runner_started:
                await Runner.stop()
            if status == "failed" and container_name:
                remove_terminal_bench_container(container_name)
                self._containers.pop(session_id, None)

        return CaseExecutionResult(
            response=response,
            execution_status=status,
            error=error,
            workspace_dir=str(workspace_dir),
            metadata={
                **_single_harness_metadata(
                    role_name=role_name,
                    workspace_before=workspace_before,
                    workspace_after=workspace_after,
                    team_skill_ref_path=team_skill_ref_path,
                    controlled_skill_treatment=(
                        controlled_skill_treatment.evidence() if controlled_skill_treatment is not None else None
                    ),
                    skill_triggers=[rail.task_trigger_evidence() for rail in skill_use_rails],
                ),
                "workbuddy_office_solver_container": container_name,
                "solver_backend": self.config.solver_backend,
                "solver": solver_metadata,
                "command_log": command_recorder.to_list(),
                "command_records": command_recorder.to_list(),
            },
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        del team_name
        container_name = self._containers.pop(session_id, "")
        if container_name:
            remove_terminal_bench_container(container_name)

    @staticmethod
    def _enforce_container_tools(agent: Any) -> None:
        """Remove plugin-added host filesystem rails after loading the Harness."""
        find_rails = getattr(agent, "find_rails_by_type", None)
        if callable(find_rails):
            for skill_rail in find_rails((SkillUseRail,)):
                skill_rail.include_tools = False
        agent.strip_rails_by_type((SysOperationRail,))
        agent.add_rail(RSISysOperationRail(shell_only=True, bash_pipefail=True))


async def _run_jiuwenswarm_solver(
    *,
    config: EvaluatorConfig,
    container_name: str,
    output_dir: Path,
    harness_path: Path,
    role_name: str,
    instruction: str,
    session_id: str,
    required_artifact_paths: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Run the pinned JiuwenSwarm backend inside the WorkBuddy task container."""
    if not harness_path.is_dir():
        raise WorkBuddyInfrastructureError(f"Expert Harness directory not found: {harness_path}")
    harness_config = harness_path / "harness_config.yaml"
    if not harness_config.is_file():
        raise WorkBuddyInfrastructureError(f"Expert Harness config not found: {harness_config}")

    profile = str(config.jiuwenswarm_runtime_profile or "task86").strip()
    if profile != "task86":
        raise WorkBuddyInfrastructureError(f"unsupported JiuwenSwarm runtime profile: {profile}")
    expected_version = str(config.jiuwenswarm_expected_version or "").strip() or "0.2.3"
    configured_python = str(config.jiuwenswarm_python or "").strip()
    container_python = configured_python or f"{_SHARED_JIUWENSWARM_RUNTIME}/bin/python"
    bootstrap_python = "" if configured_python else "python3"
    startup_timeout = int(config.jiuwenswarm_startup_timeout_sec)
    runtime_timeout = int(config.jiuwenswarm_runtime_timeout_sec)
    runtime_key = _safe_id(session_id).lower() or "case"
    runtime_parent = f"/tmp/rsi_jiuwenswarm/{runtime_key}"
    data_dir = f"{runtime_parent}/home/.jiuwenswarm"
    runtime_harness = f"{data_dir}/auto-harness/runtime_extensions/rsi/{_safe_id(role_name)}"
    package_id = f"rsi_{_safe_id(role_name).lower()}_{runtime_key[-16:]}"
    bridge_path = _jiuwenswarm_bridge_path(config)
    container_bridge = f"{runtime_parent}/jiuwenswarm_solver.py"

    await asyncio.to_thread(
        _ensure_container_jiuwenswarm,
        container_name=container_name,
        python_executable=container_python,
        bootstrap_python_executable=bootstrap_python,
        expected_version=expected_version,
        timeout_sec=max(startup_timeout, 600),
    )
    model_profile = _load_jiuwenswarm_model_profile(config.model_config_ref)

    with tempfile.TemporaryDirectory(prefix="rsi_jws_stage_") as temp_value:
        temp_dir = Path(temp_value)
        staged_harness = temp_dir / "candidate_harness"
        harness_compilation = _compile_jiuwenswarm_runtime_harness(
            source_dir=harness_path,
            target_dir=staged_harness,
            extension_name=_safe_id(role_name),
        )
        (temp_dir / "task86-overlay.yaml").write_text(
            yaml.safe_dump(
                _task86_jiuwenswarm_config(
                    model_profile,
                    runtime_timeout_sec=runtime_timeout,
                ),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (temp_dir / ".env").write_text(
            _task86_jiuwenswarm_env(model_profile),
            encoding="utf-8",
        )
        (temp_dir / "harness-packages.json").write_text(
            json.dumps(
                _jiuwenswarm_package_manifest(
                    package_id=package_id,
                    role_name=role_name,
                    runtime_harness=runtime_harness,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        init_environment = [
            "-e",
            f"JIUWENSWARM_HOME={runtime_parent}/home",
            "-e",
            f"JIUWENSWARM_DATA_DIR={data_dir}",
        ]
        await asyncio.to_thread(
            run_docker,
            [
                "docker",
                "exec",
                *init_environment,
                container_name,
                container_python,
                "-m",
                "jiuwenswarm.init_workspace",
            ],
            timeout=max(startup_timeout, 180),
        )
        await asyncio.to_thread(
            run_docker,
            [
                "docker",
                "exec",
                container_name,
                "mkdir",
                "-p",
                f"{data_dir}/config",
                f"{data_dir}/auto-harness",
                runtime_harness,
                f"{runtime_parent}/logs",
            ],
            timeout=startup_timeout,
        )
        copy_operations = [
            (bridge_path, container_bridge),
            (
                temp_dir / "task86-overlay.yaml",
                f"{runtime_parent}/task86-overlay.yaml",
            ),
            (temp_dir / ".env", f"{data_dir}/config/.env"),
            (
                temp_dir / "harness-packages.json",
                f"{data_dir}/auto-harness/harness-packages.json",
            ),
        ]
        for source, destination in copy_operations:
            await asyncio.to_thread(
                run_docker,
                ["docker", "cp", str(source), f"{container_name}:{destination}"],
                timeout=startup_timeout,
            )
        await asyncio.to_thread(
            run_docker,
            [
                "docker",
                "exec",
                container_name,
                container_python,
                container_bridge,
                "--merge-config-base",
                f"{data_dir}/config/config.yaml",
                "--merge-config-overlay",
                f"{runtime_parent}/task86-overlay.yaml",
            ],
            timeout=startup_timeout,
        )
        await asyncio.to_thread(
            run_docker,
            [
                "docker",
                "cp",
                str(staged_harness.resolve()) + "/.",
                f"{container_name}:{runtime_harness}",
            ],
            timeout=startup_timeout,
        )

    request = {
        "instruction": instruction,
        "system_overlay": "",
        "session_id": session_id,
        "required_artifacts": list(required_artifact_paths),
        "package_id": package_id,
        "harness_config_path": f"{runtime_harness}/harness_config.yaml",
    }
    command = [
        "docker",
        "exec",
        "-i",
        "-w",
        "/workspace",
        "-e",
        f"JIUWENSWARM_HOME={runtime_parent}/home",
        "-e",
        f"JIUWENSWARM_DATA_DIR={data_dir}",
        "-e",
        "PYTHONIOENCODING=utf-8",
        container_name,
        container_python,
        container_bridge,
        "--workspace",
        "/workspace",
        "--request-file",
        "-",
        "--jiuwenswarm-python",
        container_python,
        "--jiuwenswarm-expected-version",
        expected_version,
        "--jiuwenswarm-startup-timeout-sec",
        str(startup_timeout),
        "--jiuwenswarm-runtime-timeout-sec",
        str(runtime_timeout),
        "--jiuwenswarm-runtime-profile",
        profile,
        "--harness-packages-file",
        f"{data_dir}/auto-harness/harness-packages.json",
        "--log-dir",
        f"{runtime_parent}/logs",
    ]
    completed = await asyncio.to_thread(
        _run_docker_with_input,
        command,
        json.dumps(request, ensure_ascii=False),
        runtime_timeout + (2 * startup_timeout) + 60,
    )
    sensitive_values = [
        str(model_profile.get("api_key") or ""),
        str(model_profile.get("api_base") or ""),
    ]
    try:
        payload = _parse_jiuwenswarm_solver_output(completed.stdout)
    except WorkBuddyInfrastructureError as exc:
        raise WorkBuddyInfrastructureError(_redact_sensitive_text(str(exc), sensitive_values)) from exc
    payload = _redact_sensitive_payload(payload, sensitive_values)
    if completed.returncode != 0 or not payload.get("ok"):
        detail = _redact_sensitive_text(
            str(payload.get("error") or completed.stderr or "unknown error"),
            sensitive_values,
        )
        raise WorkBuddyInfrastructureError("JiuwenSwarm WorkBuddy execution failed: " + _bounded_text(detail, 16000))
    trajectory = payload.get("trajectory")
    trajectory = trajectory if isinstance(trajectory, list) else []
    _write_jiuwenswarm_trajectory(
        output_dir=output_dir,
        role_name=role_name,
        trajectory=trajectory,
    )
    metadata = payload.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    _validate_jiuwenswarm_harness_activation(
        metadata=metadata,
        compilation=harness_compilation,
    )
    metadata.update(
        {
            "containerized": True,
            "runtime_profile": profile,
            "jiuwenswarm_version": expected_version,
            "harness_package_id": package_id,
            "harness_config_path": f"{runtime_harness}/harness_config.yaml",
            "harness_activation_requested": True,
            "harness_runtime_compilation": harness_compilation,
            "stderr_tail": _bounded_text(
                _redact_sensitive_text(completed.stderr, sensitive_values),
                4000,
            ),
        }
    )
    return str(payload.get("final_response") or ""), metadata


def _ensure_container_jiuwenswarm(
    *,
    container_name: str,
    python_executable: str,
    bootstrap_python_executable: str = "",
    expected_version: str,
    timeout_sec: int,
) -> None:
    _ensure_container_jiuwenswarm_unlocked(
        container_name=container_name,
        python_executable=python_executable,
        bootstrap_python_executable=bootstrap_python_executable,
        expected_version=expected_version,
        timeout_sec=timeout_sec,
    )


def _ensure_container_jiuwenswarm_unlocked(
    *,
    container_name: str,
    python_executable: str,
    bootstrap_python_executable: str,
    expected_version: str,
    timeout_sec: int,
) -> None:
    del bootstrap_python_executable
    executable_check = run_docker(
        ["docker", "exec", container_name, "test", "-x", python_executable],
        check=False,
        timeout=min(timeout_sec, 60),
    )
    if executable_check.returncode != 0:
        raise WorkBuddyInfrastructureError(
            f"prebuilt JiuwenSwarm runtime is unavailable at {python_executable}; in-run installation is disabled"
        )
    version_script = "from importlib.metadata import version; print(version('jiuwenswarm'))"
    check = run_docker(
        [
            "docker",
            "exec",
            container_name,
            python_executable,
            "-c",
            version_script,
        ],
        check=False,
        timeout=min(timeout_sec, 60),
    )
    installed = (check.stdout or "").strip() if check.returncode == 0 else ""
    if installed != expected_version:
        raise WorkBuddyInfrastructureError(
            "prebuilt JiuwenSwarm runtime version mismatch: "
            f"expected {expected_version}, found {installed or 'unavailable'}; "
            "in-run installation is disabled"
        )


def _jiuwenswarm_runtime_volume_name(version: str) -> str:
    safe_version = "".join(character if character.isalnum() else "-" for character in str(version or "").lower()).strip(
        "-"
    )
    return f"ach-jiuwenswarm-{safe_version or 'runtime'}-py312"


def _jiuwenswarm_bridge_path(config: EvaluatorConfig) -> Path:
    configured = str(config.jiuwenswarm_executable or "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise WorkBuddyInfrastructureError(f"configured JiuwenSwarm bridge not found: {path}")
        return path
    path = Path(__file__).resolve().parent / "jiuwenswarm_solver.py"
    if not path.is_file():
        raise WorkBuddyInfrastructureError(f"JiuwenSwarm bridge not found: {path}")
    return path


def _load_jiuwenswarm_model_profile(model_config_ref: str) -> dict[str, Any]:
    path = Path(model_config_ref).expanduser().resolve()
    if not path.is_file():
        raise WorkBuddyInfrastructureError(f"evaluation model config not found: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkBuddyInfrastructureError(f"failed to read evaluation model config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkBuddyInfrastructureError("evaluation model config must be a mapping")
    client = payload.get("model_client_config")
    request = payload.get("model_request_config")
    if not isinstance(client, dict) or not isinstance(request, dict):
        raise WorkBuddyInfrastructureError(
            "evaluation model config requires model_client_config and model_request_config"
        )
    values = {
        "api_base": str(client.get("api_base") or "").strip(),
        "api_key": str(client.get("api_key") or "").strip(),
        "client_provider": str(client.get("client_provider") or "OpenAI").strip(),
        "model_name": str(request.get("model") or "").strip(),
        "max_tokens": request.get("max_tokens"),
        "extra_body": request.get("extra_body"),
    }
    for key in ("api_base", "api_key", "model_name"):
        if not values[key]:
            raise WorkBuddyInfrastructureError(f"evaluation model config is missing {key}")
        if "\n" in str(values[key]) or "\r" in str(values[key]):
            raise WorkBuddyInfrastructureError(f"evaluation model config contains an invalid multiline {key}")
    return values


def _task86_jiuwenswarm_config(
    model: dict[str, Any],
    *,
    runtime_timeout_sec: int = 3600,
) -> dict[str, Any]:
    request_config: dict[str, Any] = {"temperature": 0.95}
    return {
        "preferred_language": "en",
        "setup_guide": {"enabled": True},
        "auto_recap": {"enabled": True},
        "auto_memory_enabled": False,
        "logging": {
            "level": "INFO",
            "console_level": "INFO",
            "gateway": "INFO",
            "agent_server": "INFO",
            "full": "INFO",
        },
        "models": {
            "defaults": [
                {
                    "model_client_config": {
                        "api_base": "${API_BASE}",
                        "api_key": "${API_KEY}",
                        "model_name": "${MODEL_NAME}",
                        "client_provider": "${MODEL_PROVIDER:-OpenAI}",
                        "timeout": 360,
                        "stream_first_chunk_timeout": 300,
                        "stream_idle_timeout": 60,
                        "verify_ssl": False,
                        "custom_headers": {},
                    },
                    "model_config_obj": request_config,
                    "is_default": True,
                }
            ]
        },
        "agents": {
            "agent_leader": {
                "workspace": {"stable_base": True},
                "max_iterations": 200,
                "completion_timeout": 6000.0,
            },
            "agent_teammate": {
                "workspace": {"stable_base": True},
                "max_iterations": 200,
                "completion_timeout": 6000.0,
            },
        },
        "sandbox": {"enabled": False},
        "permissions": {"enabled": False, "defaults": {"*": "allow"}},
        "react": {
            "agent_name": "main_agent",
            "enable_task_loop": True,
            "max_iterations": 100,
            "model_name": "${MODEL_NAME}",
            "skill_mode": "all",
            "subagents": {
                "general_agent": {"enabled": True},
                "code_agent": {"enabled": False},
                "research_agent": {"enabled": False},
                "browser_agent": {"enabled": True},
            },
            "context_engine_config": {
                "enable_openrouter_model_context_window_tokens": True,
                "enable_reload": True,
                "enabled": True,
                "message_summary_offloader_config": {
                    "add_message_threshold_ratio": 0.1,
                    "ttl_seconds": 300,
                    "ttl_context_occupancy_ratio": 0.5,
                    "ttl_message_threshold_ratio": 0.05,
                },
                "dialogue_compressor_config": {
                    "trigger_context_ratio": 0.8,
                    "min_target_context_ratio": 0.1,
                },
                "current_round_compressor_config": {
                    "trigger_context_ratio": 0.8,
                    "min_target_context_ratio": 0.1,
                    "keep_recent_messages": 4,
                },
                "round_level_compressor_config": {
                    "trigger_context_ratio": 0.8,
                    "min_target_context_ratio": 0.1,
                    "keep_recent_messages": 4,
                },
                "reasoning_tool_loop_compact_config": {
                    "enabled": True,
                    "consecutive_threshold": 3,
                    "tool_args_consecutive_threshold": 5,
                    "reasoning_min_chars": 4,
                    "reasoning_preview_max_chars": 512,
                    "bailout_threshold": 3,
                    "tool_args_bailout_threshold": 2,
                },
            },
            "evolution": {
                "enabled": True,
                "auto_scan": False,
                "auto_save": False,
                "skill_create": False,
            },
        },
        "tools": ["todo", "skill"],
        "memory": {"mode": "local", "engine": "builtin"},
        "task_memory": {"enabled": True},
        "execution_guard": {
            "llm_retry_rail": {
                "enabled": True,
                "max_retries": 2,
                "repeat_min_pattern_chars": 2,
                "repeat_max_pattern_chars": 64,
                "repeat_min_count": 6,
                "repeat_min_total_chars": 160,
                "repeat_window_chars": 1024,
                "single_char_repeat_count": 90,
            },
            "circuit_breaker": {"enabled": False},
        },
        "team_observability": {"enabled": True},
        "agent_observability": {"enabled": False},
        "telemetry": {"enabled": False},
        "gateway": {
            "session_map_scope": "per_chat_bot",
            "agent_client": {
                "type": "websocket",
                "concurrency": 1,
                "invoke_timeout_s": 60,
                "agent_timeout_s": runtime_timeout_sec,
                "agent_namespace": "default",
            },
        },
    }


def _task86_jiuwenswarm_env(model: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"API_BASE={model['api_base']}",
            f"API_KEY={model['api_key']}",
            f"MODEL_NAME={model['model_name']}",
            f"MODEL_PROVIDER={model['client_provider']}",
            "PYTHONIOENCODING=utf-8",
            "",
        ]
    )


def _jiuwenswarm_package_manifest(
    *,
    package_id: str,
    role_name: str,
    runtime_harness: str,
) -> dict[str, Any]:
    return {
        "packages": [
            {
                "id": package_id,
                "extension_name": role_name,
                "runtime_path": runtime_harness,
                "config_path": f"{runtime_harness}/harness_config.yaml",
                "created_at": datetime.now(UTC).isoformat(),
                "is_active": False,
                "version_label": "rsi-candidate",
                "description": "RSI WorkBuddy candidate Expert Harness",
            }
        ],
        "native_version": {
            "id": "native",
            "extension_name": "Native Agent",
            "is_active": True,
        },
        "active_package_ids": [],
        "last_updated": datetime.now(UTC).isoformat(),
    }


_RSI_PROMPT_RAIL_SOURCE = '''from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.core.single_agent.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class RSICandidatePromptRail(DeepAgentRail):
    """Mount staged RSI prompt sections onto the JiuwenSwarm DeepAgent."""

    def __init__(self) -> None:
        super().__init__()
        self._builder = None
        self._section_names: list[str] = []

    def init(self, agent) -> None:
        self._builder = getattr(agent, "system_prompt_builder", None)
        if self._builder is None:
            return
        payload = json.loads(
            Path(__file__).with_name("rsi_prompt_sections.json").read_text(
                encoding="utf-8"
            )
        )
        for section in payload.get("sections", []):
            name = str(section["name"])
            content = str(section["content"])
            self._builder.add_section(
                PromptSection(
                    name=name,
                    content={"cn": content, "en": content},
                    priority=int(section.get("priority", 30)),
                )
            )
            self._section_names.append(name)

    def uninit(self, agent) -> None:
        del agent
        if self._builder is not None:
            for name in self._section_names:
                self._builder.remove_section(name)
        self._section_names = []
        self._builder = None
'''


def _compile_jiuwenswarm_runtime_harness(
    *,
    source_dir: Path,
    target_dir: Path,
    extension_name: str,
) -> dict[str, Any]:
    """Compile the RSI sidecar layout into OpenJiuwen 0.1.16 resources."""
    shutil.copytree(source_dir, target_dir)
    source_manifest = _read_harness_yaml(target_dir / "harness_config.yaml")
    prompt_sections = _collect_harness_prompt_sections(
        target_dir,
        source_manifest,
    )
    declared_skill_dirs, skill_mode = _collect_harness_skill_dirs(
        target_dir,
        source_manifest,
    )
    runtime_skill_roots = _stage_jiuwenswarm_skill_roots(
        target_dir,
        declared_skill_dirs,
    )
    if declared_skill_dirs:
        prompt_sections.append(
            {
                "name": "rsi_candidate_skill_routing",
                "content": _jiuwenswarm_skill_routing_prompt(
                    target_dir,
                    declared_skill_dirs,
                ),
                "priority": 89,
            }
        )
    package_tools = _collect_package_resources(
        target_dir,
        source_manifest,
        kind="tools",
    )
    package_rails = _collect_package_resources(
        target_dir,
        source_manifest,
        kind="rails",
    )

    expected_prefixes: list[str] = []
    if prompt_sections:
        prompt_path = target_dir / "rsi_prompt_sections.json"
        prompt_path.write_text(
            json.dumps({"sections": prompt_sections}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "rsi_candidate_prompt_rail.py").write_text(
            _RSI_PROMPT_RAIL_SOURCE,
            encoding="utf-8",
        )
        package_rails.insert(
            0,
            {
                "type": "package",
                "module": (f"openjiuwen.extensions.harness.{extension_name}.rsi_candidate_prompt_rail"),
                "class": "RSICandidatePromptRail",
            },
        )
        expected_prefixes.append("rail:RSICandidatePromptRail")
    if runtime_skill_roots:
        expected_prefixes.append("skill_dir:")

    resources: dict[str, Any] = {}
    if package_tools:
        resources["tools"] = package_tools
    if package_rails:
        resources["rails"] = package_rails
    if runtime_skill_roots:
        resources["skills"] = {"dirs": runtime_skill_roots, "mode": skill_mode}

    compiled_manifest = {
        "schema_version": "harness_config.v0.1",
        "id": str(source_manifest.get("id") or extension_name),
        "name": str(source_manifest.get("name") or extension_name),
        "description": str(source_manifest.get("description") or ""),
        "language": "en",
        "resources": resources,
    }
    (target_dir / "harness_config.yaml").write_text(
        yaml.safe_dump(
            compiled_manifest,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return {
        "schema_version": "harness_config.v0.1",
        "prompt_sections": [section["name"] for section in prompt_sections],
        "skill_dirs": declared_skill_dirs,
        "runtime_skill_roots": runtime_skill_roots,
        "package_tool_count": len(package_tools),
        "package_rail_count": len(package_rails),
        "expected_loaded_resource_prefixes": expected_prefixes,
    }


def _read_harness_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkBuddyInfrastructureError(f"failed to read Harness YAML {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkBuddyInfrastructureError(f"Harness YAML must contain a mapping: {path}")
    return dict(payload)


def _collect_harness_prompt_sections(
    harness_dir: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    prompts = manifest.get("prompts")
    raw_sections = prompts.get("sections", []) if isinstance(prompts, dict) else []
    if isinstance(raw_sections, list):
        for index, raw in enumerate(raw_sections):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or f"section_{index}")
            content = _harness_prompt_content(raw.get("content"))
            file_name = str(raw.get("file") or "").strip()
            if file_name:
                content = _read_harness_relative_text(harness_dir, file_name)
            if content.strip():
                sections[name] = {
                    "name": f"rsi_candidate_{_safe_id(name).lower()}",
                    "content": content,
                    "priority": int(raw.get("priority") or 30),
                }

    for name, priority in (("identity", 11), ("soul", 21)):
        path = harness_dir / f"{name}.md"
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                sections[name] = {
                    "name": f"rsi_candidate_{name}",
                    "content": content,
                    "priority": priority,
                }
    return list(sections.values())


def _harness_prompt_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for language in ("en", "cn"):
            content = value.get(language)
            if isinstance(content, str) and content.strip():
                return content
        for content in value.values():
            if isinstance(content, str) and content.strip():
                return content
    return ""


def _read_harness_relative_text(harness_dir: Path, raw_path: str) -> str:
    root = harness_dir.resolve()
    path = (root / raw_path).resolve()
    if path != root and root not in path.parents:
        raise WorkBuddyInfrastructureError(f"Harness prompt path escapes its package: {raw_path}")
    if not path.is_file():
        raise WorkBuddyInfrastructureError(f"Harness prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _collect_harness_skill_dirs(
    harness_dir: Path,
    manifest: dict[str, Any],
) -> tuple[list[str], str]:
    raw_items: list[Any] = []
    mode = "all"
    resources = manifest.get("resources")
    if isinstance(resources, dict):
        skills = resources.get("skills")
        if isinstance(skills, dict):
            raw_items.extend(skills.get("dirs", []))
            mode = str(skills.get("mode") or mode)

    sidecar = harness_dir / "skills" / "skills.yaml"
    if sidecar.is_file():
        payload = _read_harness_yaml(sidecar)
        raw_items.extend(payload.get("skills", []))

    rails_path = harness_dir / "rails" / "rails.yaml"
    if rails_path.is_file():
        rails = _read_harness_yaml(rails_path).get("rails", [])
        if isinstance(rails, list):
            for rail in rails:
                if not isinstance(rail, dict) or rail.get("type") != "core.skill_use":
                    continue
                params = rail.get("params")
                if isinstance(params, dict):
                    mode = str(params.get("skill_mode") or mode)

    normalized: list[str] = []
    for item in raw_items:
        candidates: list[Any]
        if isinstance(item, str):
            candidates = [item]
        elif isinstance(item, dict) and isinstance(item.get("dirs"), list):
            candidates = item["dirs"]
        elif isinstance(item, dict):
            candidates = [item.get("dir")]
        else:
            continue
        for raw in candidates:
            value = str(raw or "").strip().replace("\\", "/")
            if not value or value in normalized:
                continue
            root = harness_dir.resolve()
            path = (root / value).resolve()
            if path != root and root not in path.parents:
                raise WorkBuddyInfrastructureError(f"Harness skill path escapes its package: {value}")
            if not path.is_dir():
                raise WorkBuddyInfrastructureError(f"Harness skill directory not found: {path}")
            normalized.append(value)
    return normalized, mode


def _stage_jiuwenswarm_skill_roots(
    harness_dir: Path,
    declared_skill_dirs: list[str],
) -> list[str]:
    """Stage declared Skills under the parent-root shape SkillUseRail expects.

    OpenJiuwen's SkillUseRail treats every configured directory as a root whose
    immediate child directories are Skills. Passing ``skills/my_skill`` makes
    the rail inspect children *inside* ``my_skill`` and silently discover no
    capability. Keep an allow-listed staging root so only manifest-declared
    Skills become visible to JiuwenSwarm.
    """
    if not declared_skill_dirs:
        return []
    runtime_root_name = "rsi_runtime_skills"
    runtime_root = harness_dir / runtime_root_name
    runtime_root.mkdir(parents=True, exist_ok=True)
    seen_names: set[str] = set()
    for declared in declared_skill_dirs:
        source = (harness_dir / declared).resolve()
        runtime_name = source.name
        if runtime_name in seen_names:
            raise WorkBuddyInfrastructureError(f"Harness declares duplicate Skill runtime name: {runtime_name}")
        seen_names.add(runtime_name)
        shutil.copytree(source, runtime_root / runtime_name)
    return [runtime_root_name]


def _jiuwenswarm_skill_routing_prompt(
    harness_dir: Path,
    declared_skill_dirs: list[str],
) -> str:
    """Describe the natural JiuwenSwarm Skill entrypoint with exact names."""
    lines = [
        "# Runtime Skill Routing",
        "",
        "Runtime extension Skills are loaded with `skill_tool`; do not guess a ",
        "filesystem path or treat their presence as proof that they are relevant.",
        "When one description materially matches the current task, call ",
        "`skill_tool` with its exact runtime name and `relative_file_path` set to ",
        "`SKILL.md` before investigating or editing. Do not call irrelevant Skills.",
        "",
        "Available runtime Skills:",
    ]
    for declared in declared_skill_dirs:
        skill_dir = (harness_dir / declared).resolve()
        description = _harness_skill_description(skill_dir / "SKILL.md")
        lines.append(f"- `{skill_dir.name}`: {description}")
    return "\n".join(lines)


def _harness_skill_description(path: Path) -> str:
    """Read a bounded description from Skill frontmatter for routing only."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkBuddyInfrastructureError(f"failed to read Harness Skill: {path}: {exc}") from exc
    description = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise WorkBuddyInfrastructureError(f"invalid Harness Skill frontmatter: {path}: {exc}") from exc
            if isinstance(frontmatter, dict):
                description = str(frontmatter.get("description") or "").strip()
    return " ".join(description.split()) or "Reusable workflow supplied by the active Harness."


def _collect_package_resources(
    harness_dir: Path,
    manifest: dict[str, Any],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    raw_items: list[Any] = []
    resources = manifest.get("resources")
    if isinstance(resources, dict) and isinstance(resources.get(kind), list):
        raw_items.extend(resources[kind])
    sidecar = harness_dir / kind / f"{kind}.yaml"
    if sidecar.is_file():
        payload = _read_harness_yaml(sidecar)
        if isinstance(payload.get(kind), list):
            raw_items.extend(payload[kind])

    package_items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("type") != "package":
            continue
        if not item.get("module") or not (item.get("class") or item.get("class_name")):
            continue
        normalized = dict(item)
        if "class" not in normalized:
            normalized["class"] = normalized.pop("class_name")
        package_items.append(normalized)
    return package_items


def _validate_jiuwenswarm_harness_activation(
    *,
    metadata: dict[str, Any],
    compilation: dict[str, Any],
) -> None:
    expected = compilation.get("expected_loaded_resource_prefixes")
    if not isinstance(expected, list) or not expected:
        return
    activation = metadata.get("harness_activation")
    if not isinstance(activation, dict):
        raise WorkBuddyInfrastructureError("JiuwenSwarm returned no Harness activation evidence")
    loaded = activation.get("loaded_resources")
    loaded = [str(item) for item in loaded] if isinstance(loaded, list) else []
    missing = [prefix for prefix in expected if not any(item.startswith(str(prefix)) for item in loaded)]
    if missing:
        raise WorkBuddyInfrastructureError(
            "JiuwenSwarm activated the candidate package without loading its "
            f"required resources; missing prefixes={missing}, loaded={loaded}"
        )


def _run_docker_with_input(
    command: list[str],
    input_text: str,
    timeout_sec: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        encoding="utf-8",
        errors="replace",
    )


def _parse_jiuwenswarm_solver_output(stdout: str) -> dict[str, Any]:
    start = "===JIUWENSWARM_SOLVER_OUTPUT_START==="
    end = "===JIUWENSWARM_SOLVER_OUTPUT_END==="
    start_index = stdout.rfind(start)
    end_index = stdout.rfind(end)
    if start_index < 0 or end_index <= start_index:
        raise WorkBuddyInfrastructureError(
            "JiuwenSwarm solver returned no structured output: " + _bounded_text(stdout, 4000)
        )
    raw = stdout[start_index + len(start) : end_index].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkBuddyInfrastructureError(
            "JiuwenSwarm solver returned invalid JSON: " + _bounded_text(raw, 4000)
        ) from exc
    if not isinstance(payload, dict):
        raise WorkBuddyInfrastructureError("JiuwenSwarm solver result must be an object")
    return payload


def _write_jiuwenswarm_trajectory(
    *,
    output_dir: Path,
    role_name: str,
    trajectory: list[Any],
) -> None:
    steps: list[dict[str, Any]] = []
    control_messages: list[dict[str, Any]] = []
    tool_results: dict[str, Any] = {}
    for message in trajectory:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role in {"system", "user"}:
            control_messages.append(dict(message))
        elif role == "tool":
            tool_results[str(message.get("tool_call_id") or "")] = message.get("content", "")
    for message in trajectory:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        steps.append(
            {
                "type": "llm",
                "detail": {
                    "messages": control_messages,
                    "response": {
                        "role": "assistant",
                        "content": str(message.get("content") or ""),
                        **({"tool_calls": message.get("tool_calls")} if message.get("tool_calls") else {}),
                    },
                },
            }
        )
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            function = function if isinstance(function, dict) else {}
            tool_id = str(tool_call.get("id") or "")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            steps.append(
                {
                    "type": "tool",
                    "detail": {
                        "tool_name": str(function.get("name") or "unknown"),
                        "call_args": arguments,
                        "call_result": tool_results.get(tool_id, ""),
                    },
                }
            )
    trajectory_dir = output_dir / "tr"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    path = trajectory_dir / f"{_safe_id(role_name) or 'office_worker'}.jsonl"
    path.write_text(
        json.dumps({"steps": steps}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _redact_sensitive_text(value: Any, sensitive_values: list[str]) -> str:
    text = str(value or "")
    for sensitive_value in sensitive_values:
        if sensitive_value:
            text = text.replace(sensitive_value, "[REDACTED]")
    return text


def _redact_sensitive_payload(value: Any, sensitive_values: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: _redact_sensitive_payload(item, sensitive_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_payload(item, sensitive_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_payload(item, sensitive_values) for item in value)
    if isinstance(value, str):
        return _redact_sensitive_text(value, sensitive_values)
    return value


class WorkBuddyOfficeJudger(EvaluationJudger):
    """Score artifacts with WorkBuddy's official CompositeVerifier."""

    method = "workbuddy_office_official"

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)
        container_name = str(execution_result.metadata.get("workbuddy_office_solver_container", "")).strip()
        if not container_name:
            raise WorkBuddyInfrastructureError("WorkBuddy Office solver container is unavailable")
        try:
            result = run_workbuddy_office_verifier(
                case=case,
                container_name=container_name,
                output_dir=Path(output_dir),
            )
            metadata = dict(result)
            metadata.pop("score", None)
            metadata.pop("passed", None)
            failed_checks = result.get("failed_checks", [])
            return JudgeResult(
                method=self.method,
                score=float(result["score"]),
                passed=bool(result["passed"]),
                reason=(
                    ""
                    if result["passed"]
                    else _failure_reason(
                        failed_checks,
                        status=str(result.get("test_status", "")),
                        score=float(result["score"]),
                    )
                ),
                metadata=metadata,
            )
        except WorkBuddyInfrastructureError:
            raise
        except Exception as exc:
            raise WorkBuddyInfrastructureError(f"WorkBuddy Office verifier infrastructure failed: {exc}") from exc
        finally:
            remove_terminal_bench_container(container_name)


class WorkBuddyOfficeEvaluator(TeamEvaluator):
    """TeamEvaluator facade with WorkBuddy-owned backend and judger."""

    def __init__(self, config: EvaluatorConfig) -> None:
        super().__init__(config)
        self.case_runner = CaseRunner(
            backend=WorkBuddyOfficeBackend(config),
            judger=WorkBuddyOfficeJudger(),
        )
        register_signal_extractor(
            WorkBuddyOfficeJudger.method,
            AtomicChecksSignalExtractor(),
        )


def _failure_reason(failed_checks: Any, *, status: str, score: float) -> str:
    summaries: list[str] = []
    if isinstance(failed_checks, list):
        for check in failed_checks[:12]:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name", "unnamed_check") or "unnamed_check")
            detail = str(check.get("detail", "") or "").strip()
            summaries.append(f"{name}: {detail}" if detail else name)
    prefix = f"WorkBuddy Office verifier {status or 'failed'}; score={score:.4f}"
    return prefix if not summaries else prefix + "; failed checks: " + " | ".join(summaries)


def _prepare_expert_harness_prompt_overlay(
    agent: Any,
    *,
    evaluation_prompt: str,
) -> None:
    """Reserve the identity section for the evolvable Expert Harness."""
    builder = getattr(agent, "system_prompt_builder", None)
    if builder is None:
        raise WorkBuddyInfrastructureError("WorkBuddy agent has no system prompt builder before Harness loading")
    builder.remove_section("identity")
    builder.add_section(
        PromptSection(
            name="evaluation_context",
            content={"cn": evaluation_prompt, "en": evaluation_prompt},
            priority=5,
        )
    )
    agent.apply_prompt_builder_to_react_agent()


def _required_workbuddy_artifact_paths(case: dict[str, Any]) -> tuple[str, ...]:
    """Load required output paths from the benchmark-owned verifier contract."""
    config = case.get("workbuddy_office")
    config = config if isinstance(config, dict) else {}
    task_dir_value = str(config.get("task_dir", "") or "").strip()
    if not task_dir_value:
        return ()
    contract_path = Path(task_dir_value).expanduser().resolve() / "tests" / "judge.yaml"
    if not contract_path.is_file():
        return ()
    try:
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkBuddyInfrastructureError(f"failed to read WorkBuddy artifact contract: {contract_path}") from exc

    required: list[str] = []
    artifacts = contract.get("artifacts", []) if isinstance(contract, dict) else []
    for raw in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(raw, dict) or not bool(raw.get("required", False)):
            continue
        value = str(raw.get("path", "") or "").strip().replace("\\", "/")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise WorkBuddyInfrastructureError(f"invalid required WorkBuddy artifact path: {value!r}")
        normalized = path.as_posix()
        if normalized not in required:
            required.append(normalized)
    return tuple(required)


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value)[:80]


def _workbuddy_workspace_dir(*, output_dir: Path, session_id: str) -> Path:
    if os.name != "nt":
        return output_dir / "workspace"
    workspace_key = f"{output_dir.resolve()}\0{session_id}"
    digest = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()[:16]
    return Path(".local/w").resolve() / digest


__all__ = [
    "WorkBuddyOfficeBackend",
    "WorkBuddyOfficeEvaluator",
    "WorkBuddyOfficeJudger",
]
