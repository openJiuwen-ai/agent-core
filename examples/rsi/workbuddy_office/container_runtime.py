# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Terminal-Bench container runtime helpers for single-harness execution."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.config import LocalWorkConfig
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdBackgroundData,
    ExecuteCmdBackgroundResult,
    ExecuteCmdChunkData,
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
)
from openjiuwen.core.sys_operation.shell import BaseShellOperation


@dataclass(slots=True)
class TerminalBenchCommandLogEntry:
    """Bounded shell command record for evaluator traces."""

    command: str
    cwd: str
    exit_code: int | None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    timeout_sec: int | None = None
    background: bool = False


@dataclass(slots=True)
class TerminalBenchCommandRecorder:
    """In-memory bounded command recorder for one case run."""

    max_entries: int = 80
    max_stream_chars: int = 2000
    entries: list[TerminalBenchCommandLogEntry] = field(default_factory=list)

    def record(
        self,
        *,
        command: str,
        cwd: str,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
        timeout_sec: int | None = None,
        background: bool = False,
    ) -> None:
        if len(self.entries) >= self.max_entries:
            return
        self.entries.append(
            TerminalBenchCommandLogEntry(
                command=command,
                cwd=cwd,
                exit_code=exit_code,
                stdout_excerpt=_excerpt(stdout, self.max_stream_chars),
                stderr_excerpt=_excerpt(stderr, self.max_stream_chars),
                timeout_sec=timeout_sec,
                background=background,
            )
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {
                "command": entry.command,
                "cwd": entry.cwd,
                "exit_code": entry.exit_code,
                "stdout_excerpt": entry.stdout_excerpt,
                "stderr_excerpt": entry.stderr_excerpt,
                "timeout_sec": entry.timeout_sec,
                "background": entry.background,
            }
            for entry in self.entries
        ]


class TerminalBenchDockerShellOperation(BaseShellOperation):
    """Execute agent shell commands inside a live Terminal-Bench container."""

    def __init__(
        self,
        *,
        container_name: str,
        host_workspace_dir: Path,
        container_workspace_dir: str = "/app",
        recorder: TerminalBenchCommandRecorder | None = None,
        clean_shell: bool = False,
        runtime_environment: dict[str, str] | None = None,
        enforce_in_container_timeout: bool = False,
    ) -> None:
        super().__init__(
            name="terminal_bench_docker_shell",
            mode=OperationMode.LOCAL,
            description="Run shell commands in the Terminal-Bench task container.",
            run_config=LocalWorkConfig(shell_allowlist=None, restrict_to_sandbox=False),
        )
        self._container_name = container_name
        self._host_workspace_dir = host_workspace_dir.resolve()
        self._container_workspace_dir = container_workspace_dir.rstrip("/") or "/"
        self._recorder = recorder
        self._clean_shell = bool(clean_shell)
        self._runtime_environment = dict(runtime_environment or {})
        self._enforce_in_container_timeout = bool(enforce_in_container_timeout)

    async def execute_cmd(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = 300,
        environment: dict[str, str] | None = None,
        options: dict[str, Any] | None = None,
        shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdResult:
        container_cwd = self._container_cwd(cwd)
        docker_command = self._docker_exec_command(
            command,
            cwd=container_cwd,
            environment=environment,
            detached=False,
            timeout=timeout,
        )
        host_timeout = timeout
        if timeout is not None and self._enforce_in_container_timeout:
            # Let the in-container timeout reap the command tree before the
            # host gives up on the docker client.
            host_timeout = timeout + 10
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                docker_command,
                text=True,
                capture_output=True,
                timeout=host_timeout,
                encoding="utf-8",
                errors="replace",
            )
            if self._recorder is not None:
                self._recorder.record(
                    command=command,
                    cwd=container_cwd,
                    exit_code=completed.returncode,
                    stdout=completed.stdout or "",
                    stderr=completed.stderr or "",
                    timeout_sec=timeout,
                )
            return ExecuteCmdResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ExecuteCmdData(
                    command=command,
                    cwd=container_cwd,
                    exit_code=completed.returncode,
                    stdout=completed.stdout or "",
                    stderr=completed.stderr or "",
                ),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _timeout_stream(exc.stdout)
            stderr = _timeout_stream(exc.stderr) + f"\ncommand timed out after {timeout}s"
            if self._recorder is not None:
                self._recorder.record(
                    command=command,
                    cwd=container_cwd,
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                    timeout_sec=timeout,
                )
            return ExecuteCmdResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ExecuteCmdData(
                    command=command,
                    cwd=container_cwd,
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                ),
            )

    async def execute_cmd_stream(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = 300,
        environment: dict[str, str] | None = None,
        options: dict[str, Any] | None = None,
        shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        result = await self.execute_cmd(
            command,
            cwd=cwd,
            timeout=timeout,
            environment=environment,
            options=options,
            shell_type=shell_type,
        )
        data = result.data
        index = 0
        if data and data.stdout:
            yield ExecuteCmdStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ExecuteCmdChunkData(
                    text=data.stdout,
                    type="stdout",
                    chunk_index=index,
                    exit_code=None,
                ),
            )
            index += 1
        if data and data.stderr:
            yield ExecuteCmdStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ExecuteCmdChunkData(
                    text=data.stderr,
                    type="stderr",
                    chunk_index=index,
                    exit_code=None,
                ),
            )
            index += 1
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=ExecuteCmdChunkData(
                text="",
                type=None,
                chunk_index=index,
                exit_code=data.exit_code if data else None,
            ),
        )

    async def execute_cmd_background(
        self,
        command: str,
        *,
        cwd: str | None = None,
        environment: dict[str, str] | None = None,
        grace: float = 3.0,
        shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdBackgroundResult:
        container_cwd = self._container_cwd(cwd)
        docker_command = self._docker_exec_command(
            command,
            cwd=container_cwd,
            environment=environment,
            detached=True,
            timeout=None,
        )
        completed = await asyncio.to_thread(
            subprocess.run,
            docker_command,
            text=True,
            capture_output=True,
            timeout=max(grace, 1.0),
            encoding="utf-8",
            errors="replace",
        )
        if self._recorder is not None:
            self._recorder.record(
                command=command,
                cwd=container_cwd,
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                timeout_sec=int(max(grace, 1.0)),
                background=True,
            )
        if completed.returncode != 0:
            return ExecuteCmdBackgroundResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ExecuteCmdBackgroundData(
                    command=command,
                    cwd=container_cwd,
                    pid=None,
                ),
            )
        return ExecuteCmdBackgroundResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=ExecuteCmdBackgroundData(command=command, cwd=container_cwd, pid=None),
        )

    def _container_cwd(self, cwd: str | None) -> str:
        if not cwd:
            return self._container_workspace_dir
        try:
            cwd_path = Path(cwd).expanduser().resolve()
            relative = cwd_path.relative_to(self._host_workspace_dir)
        except (OSError, ValueError):
            return self._container_workspace_dir
        relative_posix = relative.as_posix()
        if relative_posix == ".":
            return self._container_workspace_dir
        return f"{self._container_workspace_dir}/{relative_posix}"

    def _docker_exec_command(
        self,
        command: str,
        *,
        cwd: str,
        environment: dict[str, str] | None,
        detached: bool,
        timeout: int | None,
    ) -> list[str]:
        docker_command = ["docker", "exec"]
        if detached:
            docker_command.append("-d")
        merged_environment = {**self._runtime_environment, **(environment or {})}
        for key, value in sorted(merged_environment.items()):
            docker_command.extend(["-e", f"{key}={value}"])
        shell_command = ["bash", "-lc", command]
        if self._clean_shell:
            shell_command = ["bash", "--noprofile", "--norc", "-c", command]
        if self._enforce_in_container_timeout and timeout is not None and not detached:
            shell_command = [
                "timeout",
                "--signal=TERM",
                "--kill-after=5s",
                f"{max(int(timeout), 1)}s",
                *shell_command,
            ]
        docker_command.extend(
            [
                "-w",
                cwd,
                self._container_name,
                *shell_command,
            ]
        )
        return docker_command


class TerminalBenchDockerSysOperation(SysOperation):
    """Local file/code operations plus Docker-routed shell operations."""

    def __init__(self, *, sys_operation_id: str, shell_operation: TerminalBenchDockerShellOperation) -> None:
        super().__init__(
            SysOperationCard(
                id=sys_operation_id,
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(
                    shell_allowlist=None,
                    restrict_to_sandbox=False,
                ),
            )
        )
        self._docker_shell_operation = shell_operation

    def shell(self) -> BaseShellOperation:
        return self._docker_shell_operation

    def command_log(self) -> list[dict[str, Any]]:
        recorder = self._docker_shell_operation._recorder
        return recorder.to_list() if recorder is not None else []


def build_terminal_bench_sys_operation(
    *,
    sys_operation_id: str,
    container_name: str,
    workspace_dir: Path,
    recorder: TerminalBenchCommandRecorder | None = None,
    container_workspace_dir: str = "/app",
    clean_shell: bool = False,
    runtime_environment: dict[str, str] | None = None,
    enforce_in_container_timeout: bool = False,
) -> TerminalBenchDockerSysOperation:
    return TerminalBenchDockerSysOperation(
        sys_operation_id=sys_operation_id,
        shell_operation=TerminalBenchDockerShellOperation(
            container_name=container_name,
            host_workspace_dir=workspace_dir,
            container_workspace_dir=container_workspace_dir,
            recorder=recorder,
            clean_shell=clean_shell,
            runtime_environment=runtime_environment,
            enforce_in_container_timeout=enforce_in_container_timeout,
        ),
    )


def start_terminal_bench_solver_container(
    *,
    docker_image: str,
    case_id: str,
    workspace_dir: Path,
    timeout_sec: int,
    container_workspace_dir: str = "/app",
    mount_workspace: bool = True,
    extra_bind_mounts: list[tuple[Path, str]] | None = None,
    extra_volume_mounts: list[tuple[str, str]] | None = None,
) -> str:
    container_name = terminal_bench_solver_container_name(case_id)
    run_docker(["docker", "rm", "-f", container_name], check=False)
    command = [
        "docker",
        "create",
        "--name",
        container_name,
        "--workdir",
        container_workspace_dir,
    ]
    if mount_workspace:
        command.extend(
            [
                "--mount",
                f"type=bind,source={workspace_dir.resolve()},target={container_workspace_dir}",
            ]
        )
    for source, target in extra_bind_mounts or []:
        source_path = source.expanduser().resolve()
        if not source_path.is_dir():
            raise ValueError(f"bind mount source is not a directory: {source_path}")
        if not target.startswith("/"):
            raise ValueError(f"bind mount target must be absolute: {target}")
        command.extend(
            [
                "--mount",
                f"type=bind,source={source_path},target={target}",
            ]
        )
    for source, target in extra_volume_mounts or []:
        if not source.strip():
            raise ValueError("volume mount source must not be empty")
        if not target.startswith("/"):
            raise ValueError(f"volume mount target must be absolute: {target}")
        command.extend(
            [
                "--mount",
                f"type=volume,source={source},target={target}",
            ]
        )
    command.extend([docker_image, "sleep", "infinity"])
    run_docker(command, timeout=timeout_sec)
    run_docker(["docker", "start", container_name], timeout=timeout_sec)
    return container_name


def sync_container_git_patch_to_workspace(
    *,
    container_name: str,
    workspace_dir: Path,
    container_workspace_dir: str,
) -> str:
    """Apply the container's tracked git patch to its pristine host checkout."""
    diff = (
        run_docker(
            [
                "docker",
                "exec",
                "-w",
                container_workspace_dir,
                container_name,
                "git",
                "diff",
                "--binary",
                "HEAD",
            ],
            timeout=120,
        ).stdout
        or ""
    )
    if not diff:
        return ""
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(workspace_dir.resolve()),
            "apply",
            "--binary",
            "--whitespace=nowarn",
            "-",
        ],
        input=diff.encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "failed to sync solver patch from container: "
            f"stdout={_timeout_stream(completed.stdout)}\n"
            f"stderr={_timeout_stream(completed.stderr)}"
        )
    return diff


def remove_terminal_bench_container(container_name: str) -> None:
    run_docker(["docker", "rm", "-f", container_name], check=False)


def terminal_bench_solver_container_name(case_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in case_id.lower()).strip("-")
    safe = safe[:48] or "case"
    return f"ach-tb-solver-{safe}-{uuid.uuid4().hex[:8]}"


def run_docker(
    command: list[str],
    *,
    check: bool = True,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "command failed: " + " ".join(command) + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _excerpt(text: str, max_chars: int) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
