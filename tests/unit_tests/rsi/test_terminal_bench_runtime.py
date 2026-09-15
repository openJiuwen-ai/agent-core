# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for deterministic commands in live benchmark containers."""

from pathlib import Path
import subprocess
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.terminal_bench_runtime import (
    TerminalBenchCommandRecorder,
    TerminalBenchDockerShellOperation,
    start_terminal_bench_solver_container,
    sync_container_git_patch_to_workspace,
)


def _swe_operation(tmp_path: Path) -> TerminalBenchDockerShellOperation:
    return TerminalBenchDockerShellOperation(
        container_name="solver-case",
        host_workspace_dir=tmp_path,
        container_workspace_dir="/testbed",
        recorder=TerminalBenchCommandRecorder(),
        clean_shell=True,
        runtime_environment={
            "CONDA_PREFIX": "/opt/miniconda3/envs/testbed",
            "PATH": "/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin",
        },
        enforce_in_container_timeout=True,
    )


def test_swe_command_skips_login_profile_and_wraps_full_command_timeout(
    tmp_path: Path,
) -> None:
    operation = _swe_operation(tmp_path)

    command = operation._docker_exec_command(
        "python -m pytest -q",
        cwd="/testbed",
        environment={"EXTRA": "1"},
        detached=False,
        timeout=30,
    )

    assert command == [
        "docker",
        "exec",
        "-e",
        "CONDA_PREFIX=/opt/miniconda3/envs/testbed",
        "-e",
        "EXTRA=1",
        "-e",
        "PATH=/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin",
        "-w",
        "/testbed",
        "solver-case",
        "timeout",
        "--signal=TERM",
        "--kill-after=5s",
        "30s",
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        "python -m pytest -q",
    ]
    assert "-l" not in command
    assert "-lc" not in command


@pytest.mark.asyncio
async def test_swe_command_allows_container_timeout_to_reap_before_host_timeout(
    tmp_path: Path,
) -> None:
    operation = _swe_operation(tmp_path)
    completed = CompletedProcess(
        args=["docker", "exec"],
        returncode=124,
        stdout="partial output",
        stderr="",
    )

    with patch(
        "openjiuwen.rsi.harness_rsi.evaluator.terminal_bench_runtime.subprocess.run",
        return_value=completed,
    ) as run:
        result = await operation.execute_cmd("python -m pytest", timeout=20)

    assert run.call_args.kwargs["timeout"] == 30
    assert result.data is not None
    assert result.data.exit_code == 124
    assert result.data.stdout == "partial output"
    assert operation._recorder is not None
    assert operation._recorder.entries[-1].exit_code == 124


def test_background_command_uses_clean_shell_without_timeout_wrapper(
    tmp_path: Path,
) -> None:
    operation = _swe_operation(tmp_path)

    command = operation._docker_exec_command(
        "python worker.py",
        cwd="/testbed",
        environment=None,
        detached=True,
        timeout=None,
    )

    assert command[:3] == ["docker", "exec", "-d"]
    assert "timeout" not in command
    assert command[-5:] == [
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        "python worker.py",
    ]


def test_native_solver_container_does_not_bind_mount_host_workspace(
    tmp_path: Path,
) -> None:
    with patch("openjiuwen.rsi.harness_rsi.evaluator.terminal_bench_runtime.run_docker") as run_docker:
        name = start_terminal_bench_solver_container(
            docker_image="swebench/image:latest",
            case_id="repo__case-1",
            workspace_dir=tmp_path,
            timeout_sec=120,
            container_workspace_dir="/testbed",
            mount_workspace=False,
        )

    create_command = run_docker.call_args_list[1].args[0]
    assert name.startswith("ach-tb-solver-repo--case-1-")
    assert "--mount" not in create_command
    assert create_command[-3:] == ["swebench/image:latest", "sleep", "infinity"]


def test_sync_container_git_patch_applies_to_host_checkout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "module.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    diff = """diff --git a/module.py b/module.py
index b742a67..c59d793 100644
--- a/module.py
+++ b/module.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

    with patch(
        "openjiuwen.rsi.harness_rsi.evaluator.terminal_bench_runtime.run_docker",
        return_value=CompletedProcess(
            args=["docker", "exec"],
            returncode=0,
            stdout=diff,
            stderr="",
        ),
    ) as run_docker:
        applied = sync_container_git_patch_to_workspace(
            container_name="solver-case",
            workspace_dir=tmp_path,
            container_workspace_dir="/testbed",
        )

    assert applied == diff
    assert source.read_text(encoding="utf-8") == "value = 2\n"
    command = run_docker.call_args.args[0]
    assert command[-4:] == ["git", "diff", "--binary", "HEAD"]
