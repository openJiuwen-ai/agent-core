# coding: utf-8
"""Tests for the WorkBuddy Bench Office single-harness runtime."""

from __future__ import annotations

import io
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
from typing import Any

import pytest

from examples.rsi.workbuddy_office import adapter as adapter_module
from examples.rsi.workbuddy_office import container_runtime
from examples.rsi.workbuddy_office import runtime
from examples.rsi.workbuddy_office.adapter import WorkBuddyOfficeBackend
from examples.rsi.workbuddy_office.adapter import (
    _prepare_expert_harness_prompt_overlay,
)
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.core.single_agent.prompts import PromptSection, SystemPromptBuilder


def _completed(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout="", stderr="")


def test_solver_container_mounts_additional_bind_and_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_dir = tmp_path / "pip-cache"
    workspace.mkdir()
    cache_dir.mkdir()
    commands: list[list[str]] = []

    def fake_run_docker(
        command: list[str],
        *,
        check: bool = True,
        timeout: int | float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout
        commands.append(command)
        return _completed()

    monkeypatch.setattr(container_runtime, "run_docker", fake_run_docker)

    container_runtime.start_terminal_bench_solver_container(
        docker_image="office:test",
        case_id="case-1",
        workspace_dir=workspace,
        timeout_sec=30,
        container_workspace_dir="/workspace",
        extra_bind_mounts=[(cache_dir, "/root/.cache/pip")],
        extra_volume_mounts=[("ach-jiuwenswarm-test", "/opt/rsi-jiuwenswarm")],
    )

    create_command = commands[1]
    assert f"type=bind,source={cache_dir.resolve()},target=/root/.cache/pip" in create_command
    assert "type=volume,source=ach-jiuwenswarm-test,target=/opt/rsi-jiuwenswarm" in create_command


def test_jiuwenswarm_runtime_volume_name_is_versioned() -> None:
    assert adapter_module._jiuwenswarm_runtime_volume_name("0.2.3") == "ach-jiuwenswarm-0-2-3-py312"


def test_expert_harness_identity_can_replace_the_base_agent_identity() -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.system_prompt_builder = SystemPromptBuilder(language="en")
            self.system_prompt_builder.add_section(
                PromptSection(
                    name="identity",
                    content={"cn": "base", "en": "base"},
                    priority=10,
                )
            )
            self.applied = False

        def apply_prompt_builder_to_react_agent(self) -> None:
            self.applied = True

    agent = FakeAgent()
    _prepare_expert_harness_prompt_overlay(
        agent,
        evaluation_prompt="evaluation contract",
    )

    assert agent.system_prompt_builder.get_section("identity") is None
    section = agent.system_prompt_builder.get_section("evaluation_context")
    assert section is not None
    assert section.render("en") == "evaluation contract"
    assert agent.applied is True


def test_prepare_workspace_extracts_archive_and_reuses_cached_image(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    with tarfile.open(environment_dir / "workspace.tar.gz", "w:gz") as archive:
        data = b"source"
        info = tarfile.TarInfo("input.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    commands: list[list[str]] = []

    def fake_run(command, *, check=True, timeout=None):
        commands.append(command)
        return _completed(0)

    monkeypatch.setattr(runtime, "_run", fake_run)
    workspace_dir = tmp_path / "workspace"
    image = runtime.prepare_workbuddy_office_workspace(
        case={"workbuddy_office": {"task_dir": str(task_dir)}},
        workspace_dir=workspace_dir,
        timeout_sec=60,
    )

    assert (workspace_dir / "input.txt").read_text(encoding="utf-8") == "source"
    assert image.startswith("ach-workbuddy-office-office-case:")
    assert commands == [["docker", "image", "inspect", image]]


def test_image_name_does_not_end_repository_component_with_separator(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "execution-closeout-reconcile-L4-003-successor"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim\n",
        encoding="utf-8",
    )

    image = runtime.workbuddy_office_image_name(task_dir)
    repository, _tag = image.split(":", maxsplit=1)

    assert repository == "ach-workbuddy-office-execution-closeout-reconcile-l4-003"
    assert not repository.endswith("-")


def test_prepare_workspace_retries_transient_docker_build_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim\n",
        encoding="utf-8",
    )
    with tarfile.open(environment_dir / "workspace.tar.gz", "w:gz") as archive:
        data = b"source"
        info = tarfile.TarInfo("input.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    build_attempts = 0

    def fake_run(command, *, check=True, timeout=None):
        nonlocal build_attempts
        if command[:3] == ["docker", "image", "inspect"]:
            return _completed(1)
        build_attempts += 1
        if build_attempts < 3:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="curl: (92) HTTP/2 PROTOCOL_ERROR; Unexpected EOF",
            )
        return _completed(0)

    sleeps: list[int] = []
    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    image = runtime.prepare_workbuddy_office_workspace(
        case={"workbuddy_office": {"task_dir": str(task_dir)}},
        workspace_dir=tmp_path / "workspace",
        timeout_sec=60,
    )

    assert image.startswith("ach-workbuddy-office-office-case:")
    assert build_attempts == 3
    assert sleeps == [1, 2]


def test_apt_bad_gateway_is_a_transient_docker_build_failure() -> None:
    completed = subprocess.CompletedProcess(
        ["docker", "build"],
        100,
        stdout="",
        stderr=(
            "E: Failed to fetch http://deb.debian.org/debian/pkg.deb "
            "502  Bad Gateway [IP: 199.232.114.132 80]\n"
            "E: Unable to fetch some archives"
        ),
    )

    assert runtime._is_transient_docker_build_failure(completed) is True


def test_prepare_workspace_does_not_retry_deterministic_build_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text("RUN exit 2\n", encoding="utf-8")
    with tarfile.open(environment_dir / "workspace.tar.gz", "w:gz") as archive:
        data = b"source"
        info = tarfile.TarInfo("input.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    build_attempts = 0

    def fake_run(command, *, check=True, timeout=None):
        nonlocal build_attempts
        if command[:3] == ["docker", "image", "inspect"]:
            return _completed(1)
        build_attempts += 1
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="Dockerfile parse error",
        )

    monkeypatch.setattr(runtime, "_run", fake_run)

    with pytest.raises(runtime.WorkBuddyInfrastructureError, match="after 1 attempt"):
        runtime.prepare_workbuddy_office_workspace(
            case={"workbuddy_office": {"task_dir": str(task_dir)}},
            workspace_dir=tmp_path / "workspace",
            timeout_sec=60,
        )

    assert build_attempts == 1


def test_prepare_workspace_removes_partial_workspace_after_bad_archive(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim\n",
        encoding="utf-8",
    )
    (environment_dir / "workspace.tar.gz").write_bytes(b"not-a-tar-archive")
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "stale.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(runtime.WorkBuddyInfrastructureError, match="failed to extract"):
        runtime.prepare_workbuddy_office_workspace(
            case={"workbuddy_office": {"task_dir": str(task_dir)}},
            workspace_dir=workspace_dir,
            timeout_sec=60,
        )

    assert not workspace_dir.exists()


def test_windows_external_workspace_uses_short_hashed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "os", SimpleNamespace(name="nt"))

    workspace = adapter_module._workbuddy_workspace_dir(
        output_dir=tmp_path / ("very-long-output-name-" * 8),
        session_id="office_epoch_0001_candidate_execution-closeout-reconcile-L4-003-successor",
    )

    assert workspace.parent == Path(".local/w").resolve()
    assert len(workspace.name) == 16
    assert workspace == adapter_module._workbuddy_workspace_dir(
        output_dir=tmp_path / ("very-long-output-name-" * 8),
        session_id="office_epoch_0001_candidate_execution-closeout-reconcile-L4-003-successor",
    )


def test_required_workbuddy_artifacts_come_from_official_judge_contract(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "judge.yaml").write_text(
        """version: 1
artifacts:
  - id: result
    path: output/result.xlsx
    required: true
    type: xlsx
  - id: optional_notes
    path: output/notes.txt
    required: false
""",
        encoding="utf-8",
    )

    assert adapter_module._required_workbuddy_artifact_paths({"workbuddy_office": {"task_dir": str(task_dir)}}) == (
        "output/result.xlsx",
    )


@pytest.mark.asyncio
async def test_single_harness_propagates_workbuddy_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()

    def fail_prepare(**kwargs):
        raise runtime.WorkBuddyInfrastructureError("workspace extraction failed")

    monkeypatch.setattr(
        adapter_module,
        "prepare_workbuddy_office_workspace",
        fail_prepare,
    )
    backend = WorkBuddyOfficeBackend(config=EvaluatorConfig(model_config_ref="model.yaml"))

    with pytest.raises(
        runtime.WorkBuddyInfrastructureError,
        match="workspace extraction failed",
    ):
        await backend.execute(
            case={
                "case_id": "office-case",
                "input": "Edit the workbook.",
                "workbuddy_office": {"task_dir": str(tmp_path / "task")},
            },
            output_dir=str(tmp_path / "output"),
            session_id="office-case-session",
            harness_refs={"solver": str(harness_dir)},
        )


def _mock_jiuwenswarm_solver_runtime(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    solver_stdout: str,
) -> tuple[EvaluatorConfig, Path, list[list[str]], dict[str, Any]]:
    harness_dir = tmp_path / "office-harness"
    harness_dir.mkdir()
    (harness_dir / "harness_config.yaml").write_text(
        "version: 1\n",
        encoding="utf-8",
    )
    model_config = tmp_path / "evaluation-model.yaml"
    model_config.write_text(
        """model_client_config:
  api_base: https://model.example.invalid/v1
  api_key: sk-test-secret-never-expose
  client_provider: OpenAI
model_request_config:
  model: office-evaluator
  max_tokens: 8192
""",
        encoding="utf-8",
    )

    docker_commands: list[list[str]] = []
    captured_solver: dict[str, Any] = {}

    def fake_run_docker(
        command: list[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout
        docker_commands.append(command)
        if any("from importlib.metadata import version" in part for part in command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="0.2.3\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def fake_run_docker_with_input(
        command: list[str],
        input_text: str,
        timeout_sec: int,
    ) -> subprocess.CompletedProcess[str]:
        captured_solver.update(
            command=command,
            input_text=input_text,
            timeout_sec=timeout_sec,
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=solver_stdout,
            stderr="bridge diagnostic",
        )

    monkeypatch.setattr(adapter_module, "run_docker", fake_run_docker)
    monkeypatch.setattr(
        adapter_module,
        "_run_docker_with_input",
        fake_run_docker_with_input,
    )
    return (
        EvaluatorConfig(
            model_config_ref=str(model_config),
            solver_backend="jiuwenswarm",
        ),
        harness_dir,
        docker_commands,
        captured_solver,
    )


def test_task86_profile_keeps_reference_execution_controls() -> None:
    profile = adapter_module._task86_jiuwenswarm_config(
        {
            "api_base": "https://model.example.invalid/v1",
            "api_key": "unused-in-profile",
            "client_provider": "OpenAI",
            "model_name": "deepseek-v4-flash",
            "max_tokens": 16384,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
        runtime_timeout_sec=3600,
    )

    request = profile["models"]["defaults"][0]["model_config_obj"]
    assert request == {"temperature": 0.95}
    assert profile["setup_guide"]["enabled"] is True
    assert profile["memory"] == {"mode": "local", "engine": "builtin"}
    assert profile["task_memory"]["enabled"] is True
    assert profile["react"]["enable_task_loop"] is True
    assert profile["react"]["max_iterations"] == 100
    assert profile["agents"]["agent_leader"]["max_iterations"] == 200
    assert profile["gateway"]["agent_client"]["agent_timeout_s"] == 3600
    assert profile["react"]["evolution"]["enabled"] is True
    assert profile["react"]["context_engine_config"]["reasoning_tool_loop_compact_config"]["enabled"] is True
    assert profile["sandbox"]["enabled"] is False
    assert profile["permissions"]["enabled"] is False


def test_container_jiuwenswarm_never_installs_during_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run_docker(
        command: list[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout
        commands.append(command)
        if "test" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="0.2.2\n", stderr="")

    monkeypatch.setattr(adapter_module, "run_docker", fake_run_docker)

    with pytest.raises(
        runtime.WorkBuddyInfrastructureError,
        match="in-run installation is disabled",
    ):
        adapter_module._ensure_container_jiuwenswarm(
            container_name="office-case",
            python_executable="/opt/rsi-jiuwenswarm/bin/python",
            expected_version="0.2.3",
            timeout_sec=600,
        )

    assert not any("pip" in command for command in commands)


def test_container_jiuwenswarm_accepts_prebuilt_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_docker(
        command: list[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout
        stdout = "" if "test" in command else "0.2.3\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(adapter_module, "run_docker", fake_run_docker)

    adapter_module._ensure_container_jiuwenswarm(
        container_name="office-case",
        python_executable="/opt/rsi-jiuwenswarm/bin/python",
        expected_version="0.2.3",
        timeout_sec=60,
    )


def test_compile_jiuwenswarm_harness_mounts_prompt_and_skill_resources(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "office_worker"
    (source / "skills" / "office_baseline").mkdir(parents=True)
    (source / "rails").mkdir()
    original_manifest = "schema_version: '1.0'\nid: office_worker\nname: Office worker\n"
    (source / "harness_config.yaml").write_text(
        original_manifest,
        encoding="utf-8",
    )
    (source / "identity.md").write_text("Candidate identity", encoding="utf-8")
    (source / "soul.md").write_text("Candidate soul", encoding="utf-8")
    (source / "skills" / "skills.yaml").write_text(
        "skills:\n  - skills/office_baseline\n",
        encoding="utf-8",
    )
    (source / "skills" / "office_baseline" / "SKILL.md").write_text(
        "---\ndescription: Build and verify office artifacts.\n---\n# Office baseline\n",
        encoding="utf-8",
    )
    hidden = source / "skills" / "unlisted_skill"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text(
        "---\ndescription: Must remain unavailable.\n---\n",
        encoding="utf-8",
    )
    (source / "rails" / "rails.yaml").write_text(
        "rails:\n  - type: core.skill_use\n    params:\n      skill_mode: all\n",
        encoding="utf-8",
    )
    target = tmp_path / "staged" / "office_worker"

    compilation = adapter_module._compile_jiuwenswarm_runtime_harness(
        source_dir=source,
        target_dir=target,
        extension_name="office_worker",
    )

    assert (source / "harness_config.yaml").read_text(encoding="utf-8") == (original_manifest)
    compiled = adapter_module._read_harness_yaml(target / "harness_config.yaml")
    assert compiled["schema_version"] == "harness_config.v0.1"
    assert compiled["resources"]["skills"] == {
        "dirs": ["rsi_runtime_skills"],
        "mode": "all",
    }
    runtime_skill_root = target / "rsi_runtime_skills"
    assert (runtime_skill_root / "office_baseline" / "SKILL.md").is_file()
    assert not (runtime_skill_root / "unlisted_skill").exists()
    rail = compiled["resources"]["rails"][0]
    assert rail == {
        "type": "package",
        "module": ("openjiuwen.extensions.harness.office_worker.rsi_candidate_prompt_rail"),
        "class": "RSICandidatePromptRail",
    }
    assert compilation["expected_loaded_resource_prefixes"] == [
        "rail:RSICandidatePromptRail",
        "skill_dir:",
    ]
    assert compilation["skill_dirs"] == ["skills/office_baseline"]
    assert compilation["runtime_skill_roots"] == ["rsi_runtime_skills"]

    spec = importlib.util.spec_from_file_location(
        "test_rsi_candidate_prompt_rail",
        target / "rsi_candidate_prompt_rail.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = SystemPromptBuilder(language="en")
    agent = SimpleNamespace(system_prompt_builder=builder)
    prompt_rail = module.RSICandidatePromptRail()
    prompt_rail.init(agent)
    assert builder.get_section("rsi_candidate_identity").render("en") == ("Candidate identity")
    assert builder.get_section("rsi_candidate_soul").render("en") == ("Candidate soul")
    routing = builder.get_section("rsi_candidate_skill_routing").render("en")
    assert "`skill_tool`" in routing
    assert "`office_baseline`" in routing
    assert "Build and verify office artifacts." in routing
    assert "unlisted_skill" not in routing
    prompt_rail.uninit(agent)
    assert builder.get_section("rsi_candidate_identity") is None


def test_validate_jiuwenswarm_activation_rejects_empty_loaded_resources() -> None:
    with pytest.raises(
        runtime.WorkBuddyInfrastructureError,
        match="without loading its required resources",
    ):
        adapter_module._validate_jiuwenswarm_harness_activation(
            metadata={"harness_activation": {"loaded_resources": []}},
            compilation={
                "expected_loaded_resource_prefixes": [
                    "rail:RSICandidatePromptRail",
                    "skill_dir:",
                ]
            },
        )


@pytest.mark.asyncio
async def test_jiuwenswarm_solver_runs_pinned_task86_in_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "ok": True,
        "final_response": "Office artifact updated.",
        "metadata": {
            "bridge": "jiuwenswarm",
            "diagnostic": ("https://model.example.invalid/v1 used sk-test-secret-never-expose"),
        },
        "trajectory": [
            {"role": "system", "content": "system policy"},
            {"role": "user", "content": "Update the workbook"},
            {
                "role": "assistant",
                "content": "I will inspect it.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command":"ls"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "report.xlsx"},
            {"role": "assistant", "content": "Done."},
        ],
    }
    solver_stdout = (
        "bridge log\n===JIUWENSWARM_SOLVER_OUTPUT_START===\n"
        + json.dumps(payload)
        + "\n===JIUWENSWARM_SOLVER_OUTPUT_END===\n"
    )
    config, harness_dir, docker_commands, captured_solver = _mock_jiuwenswarm_solver_runtime(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        solver_stdout=solver_stdout,
    )
    output_dir = tmp_path / "case-output"

    response, metadata = await adapter_module._run_jiuwenswarm_solver(
        config=config,
        container_name="workbuddy-case-container",
        output_dir=output_dir,
        harness_path=harness_dir,
        role_name="office_worker",
        instruction="Update the workbook",
        session_id="office-case-001",
    )

    assert response == "Office artifact updated."
    assert metadata["containerized"] is True
    assert metadata["runtime_profile"] == "task86"
    assert metadata["jiuwenswarm_version"] == "0.2.3"
    assert any("from importlib.metadata import version" in part for command in docker_commands for part in command)
    assert not any("pip" in command for command in docker_commands)

    solver_command = captured_solver["command"]
    assert solver_command[:4] == ["docker", "exec", "-i", "-w"]
    assert "workbuddy-case-container" in solver_command
    assert solver_command[solver_command.index("-w") + 1] == "/workspace"
    assert solver_command[solver_command.index("--jiuwenswarm-runtime-profile") + 1] == "task86"
    assert solver_command[solver_command.index("--jiuwenswarm-expected-version") + 1] == "0.2.3"
    assert all(command[0] == "docker" for command in docker_commands)

    request = json.loads(captured_solver["input_text"])
    assert request["instruction"] == "Update the workbook"
    assert request["system_overlay"] == ""
    assert request["required_artifacts"] == []
    assert request["package_id"] == metadata["harness_package_id"]
    assert request["harness_config_path"] == metadata["harness_config_path"]
    assert request["harness_config_path"].endswith("/office_worker/harness_config.yaml")

    exposed_surfaces = json.dumps(
        {
            "docker_commands": docker_commands,
            "solver_command": solver_command,
            "request": request,
            "metadata": metadata,
        }
    )
    assert "sk-test-secret-never-expose" not in exposed_surfaces
    assert "https://model.example.invalid/v1" not in exposed_surfaces
    assert metadata["diagnostic"] == "[REDACTED] used [REDACTED]"

    trajectory = json.loads((output_dir / "tr" / "office_worker.jsonl").read_text(encoding="utf-8"))
    assert [step["type"] for step in trajectory["steps"]] == ["llm", "tool", "llm"]
    assert trajectory["steps"][1]["detail"] == {
        "tool_name": "shell",
        "call_args": {"command": "ls"},
        "call_result": "report.xlsx",
    }


@pytest.mark.asyncio
async def test_jiuwenswarm_solver_rejects_unstructured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, harness_dir, _docker_commands, _captured_solver = _mock_jiuwenswarm_solver_runtime(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        solver_stdout="JiuwenSwarm exited without a structured result",
    )

    with pytest.raises(
        runtime.WorkBuddyInfrastructureError,
        match="returned no structured output",
    ):
        await adapter_module._run_jiuwenswarm_solver(
            config=config,
            container_name="workbuddy-case-container",
            output_dir=tmp_path / "case-output",
            harness_path=harness_dir,
            role_name="office_worker",
            instruction="Update the workbook",
            session_id="office-case-002",
        )


def test_run_verifier_uses_official_bridge_and_preserves_atomic_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "verifier.toml").write_text(
        """[run]
cwd = "/workspace"
command = "python -m pytest /tests/grading --junitxml=/logs/verifier/results.xml"

[env]
WB_BENCH_GOLD_PATH = "/tests/gold/gold_answer.json"
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "case"
    workbuddy_python = tmp_path / "workbuddy-python.exe"
    workbuddy_python.write_text("", encoding="utf-8")

    def fake_run(command, *, check=True, timeout=None):
        if any(str(part).endswith("run_office_verifier.py") for part in command):
            verifier_dir = output_dir / "verifier"
            verifier_dir.mkdir(parents=True, exist_ok=True)
            (verifier_dir / "reward.json").write_text(
                json.dumps(
                    {
                        "reward": 0.6667,
                        "test_pass_rate": 0.6667,
                        "tests_passed": 2,
                        "tests_total": 3,
                    }
                ),
                encoding="utf-8",
            )
            (verifier_dir / "score.json").write_text(
                json.dumps(
                    {
                        "reward": 0.6667,
                        "test_status": "partial_pass",
                        "tests_passed": 2,
                        "tests_total": 3,
                        "diagnostics": {"policy": {"name": "pass_rate"}},
                    }
                ),
                encoding="utf-8",
            )
            (verifier_dir / "results.xml").write_text(
                """<testsuites><testsuite tests="3" failures="1" errors="0" skipped="0">
<testcase classname="grading" name="test_atomic_check[000_valid]" />
<testcase classname="grading" name="test_atomic_check[001_rows]"><failure message="expected 4 rows, got 3" /></testcase>
<testcase classname="grading" name="test_atomic_check[002_output]" />
</testsuite></testsuites>""",
                encoding="utf-8",
            )
        return _completed(0)

    monkeypatch.setattr(runtime, "_run", fake_run)
    result = runtime.run_workbuddy_office_verifier(
        case={
            "workbuddy_office": {
                "task_dir": str(task_dir),
                "success_score": 1.0,
                "python_executable": str(workbuddy_python),
            }
        },
        container_name="office-container",
        output_dir=output_dir,
    )

    assert result["score"] == 0.6667
    assert result["passed"] is False
    assert result["tests_passed"] == 2
    assert result["tests_total"] == 3
    assert result["failed_checks"][0]["name"] == "test_atomic_check[001_rows]"
    assert result["atomic_checks"][1]["detail"] == "expected 4 rows, got 3"
    assert result["diagnostics"]["policy"]["name"] == "pass_rate"


def test_parse_junit_excludes_skipped_checks_from_score(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text(
        """<testsuite tests="2" failures="0" errors="0" skipped="1">
<testcase name="passed" />
<testcase name="not_applicable"><skipped message="not applicable" /></testcase>
</testsuite>""",
        encoding="utf-8",
    )

    checks = runtime._parse_junit_checks(path)

    assert checks == [
        {
            "name": "passed",
            "classname": "",
            "passed": True,
            "status": "passed",
            "detail": "",
        },
        {
            "name": "not_applicable",
            "classname": "",
            "passed": False,
            "status": "skipped",
            "detail": "not applicable",
        },
    ]


def test_official_bridge_failure_is_infrastructure_not_zero_score(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_dir = tmp_path / "tasks" / "office-case"
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "verifier.toml").write_text(
        '[run]\ncommand = "true"\n',
        encoding="utf-8",
    )
    workbuddy_python = tmp_path / "workbuddy-python.exe"
    workbuddy_python.write_text("", encoding="utf-8")

    def fake_run(command, *, check=True, timeout=None):
        if any(str(part).endswith("run_office_verifier.py") for part in command):
            return subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="plugin failed",
            )
        return _completed(0)

    monkeypatch.setattr(runtime, "_run", fake_run)

    with pytest.raises(
        runtime.WorkBuddyInfrastructureError,
        match="plugin failed",
    ):
        runtime.run_workbuddy_office_verifier(
            case={
                "workbuddy_office": {
                    "task_dir": str(task_dir),
                    "python_executable": str(workbuddy_python),
                }
            },
            container_name="office-container",
            output_dir=tmp_path / "case",
        )
