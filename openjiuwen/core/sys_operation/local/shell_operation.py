# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
from typing import Optional, Dict, Any, AsyncIterator

from _pytest import pathlib

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.shell import BaseShellOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult, ExecuteCmdData
)


@operation(name="shell", mode=OperationMode.LOCAL, description="local shell operation")
class ShellOperation(BaseShellOperation):
    """Shell operation"""

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        """
        Asynchronously execute a command(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            ExecuteCmdResult: Execution result.
        """
        try:
            if not self._check_allowlist(command):
                return ExecuteCmdResult(
                    code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
                    message=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.errmsg.format(
                        execution="execute_cmd",
                        error_msg="Command not allowed by allowlist")
                )

            exec_env = self._prepare_environment(environment)
            encoding = (options or {}).get("encoding", "utf-8")

            # Resolve CWD
            actual_cwd = self._resolve_cwd(cwd)

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(actual_cwd),
                env=exec_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_chunks = []
            stderr_chunks = []

            async def read_stream(stream, chunks):
                try:
                    while True:
                        chunk = await stream.read(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except Exception as e:
                    return ExecuteCmdResult(
                        code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
                        message=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.errmsg.format(
                            execution="execute_cmd",
                            error_msg=f"Read stream error {e}")
                    )

            stdout_task = asyncio.create_task(read_stream(proc.stdout, stdout_chunks))
            stderr_task = asyncio.create_task(read_stream(proc.stderr, stderr_chunks))

            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout or 300)
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception as e:
                    return ExecuteCmdResult(
                        code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
                        message=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.errmsg.format(
                            execution="execute_cmd",
                            error_msg=f"Stop process error {e}")
                    )

            # Wait for readers to finish capturing remaining output
            await asyncio.wait([stdout_task, stderr_task], timeout=5)

            stdout_str = b"".join(stdout_chunks).decode(encoding, errors='replace')
            stderr_str = b"".join(stderr_chunks).decode(encoding, errors='replace')

            res_data = ExecuteCmdData(
                command=command,
                cwd=str(actual_cwd),
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout_str,
                stderr=stderr_str
            )

            if timed_out:
                return ExecuteCmdResult(
                    code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
                    message=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.errmsg.format(
                        execution="execute_cmd",
                        error_msg=f"Command timed out after {timeout} seconds"),
                    data=res_data
                )

            return ExecuteCmdResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=res_data
            )
        except Exception as e:
            return ExecuteCmdResult(
                code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
                message=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.errmsg.format(
                    execution="execute_cmd",
                    error_msg=str(e))
            )

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        """
        Asynchronously execute a command streaming(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCmdStreamResult]: Streaming structured results.
        """
        pass

    def _prepare_environment(self, custom_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Prepare environment variables."""
        env = os.environ.copy()
        if custom_env:
            env.update(custom_env)
        return env

    def _check_allowlist(self, command: str) -> bool:
        """Check if command is in allowlist."""
        if not hasattr(self._run_config, 'shell_allowlist') or self._run_config.shell_allowlist is None:
            return True

        cmd_prefix = command.split()[0] if command.strip() else ""
        return any(cmd_prefix == allowed or cmd_prefix.endswith(os.sep + allowed)
                   for allowed in self._run_config.shell_allowlist)

    def _resolve_cwd(self, cwd: Optional[str]) -> pathlib.Path:
        """Resolve CWD against work_dir (if configured)."""
        work_dir_val = getattr(self._run_config, 'work_dir', None)

        if work_dir_val is None:
            if not cwd:
                return pathlib.Path.cwd()
            return pathlib.Path(cwd).expanduser().resolve()

        work_dir = pathlib.Path(work_dir_val).resolve()
        if not cwd:
            return work_dir

        target = pathlib.Path(cwd).expanduser()
        if not target.is_absolute():
            target = work_dir / target

        return target.resolve()
