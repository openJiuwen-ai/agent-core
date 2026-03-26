# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import locale
import os
import platform
import re
import shutil
from typing import Optional, Dict, Any, AsyncIterator, Callable, List, Literal, Tuple

import pathlib
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import LogEventType, sys_operation_logger
from openjiuwen.core.sys_operation.local.utils import OperationUtils, StreamEvent, StreamEventType
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result
from openjiuwen.core.sys_operation.shell import BaseShellOperation, ShellType
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult, ExecuteCmdData, ExecuteCmdChunkData,
    ExecuteCmdBackgroundData, ExecuteCmdBackgroundResult
)


_POWERSHELL_TOKENS = (
    "powershell ", "powershell.exe ", "pwsh ", "pwsh.exe ",
    "get-childitem", "set-location", "remove-item", "test-path",
    "join-path", "select-object", "where-object", "foreach-object",
    "invoke-webrequest", "invoke-restmethod", "out-file", "start-process",
    "$env:", "$psversiontable", "$null", "$true", "$false",
)

_PS_VARIABLE_PATTERN = re.compile(r"(^|[\s;(])\$[A-Za-z_][A-Za-z0-9_]*")
_POWERSHELL_CANDIDATES = ("pwsh", "powershell", "powershell.exe")


def _looks_like_powershell(command: str) -> bool:
    lowered = (command or "").strip().lower()
    if not lowered:
        return False
    if any(token in lowered for token in _POWERSHELL_TOKENS):
        return True
    if "@'" in command or '@"' in command:
        return True
    if _PS_VARIABLE_PATTERN.search(command):
        return True
    return False


def _available_powershell() -> str:
    for candidate in _POWERSHELL_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "powershell"


@operation(name="shell", mode=OperationMode.LOCAL, description="local shell operation")
class ShellOperation(BaseShellOperation):
    """Shell operation"""

    _DANGEROUS_PATTERNS: List[Tuple[re.Pattern, str]] = [
        (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "rm -rf"),
        (re.compile(r"\bdel\s+/[a-z]*[fsq][a-z]*\b", re.IGNORECASE), "del /f /s /q"),
        (re.compile(r"\brd\s+/s\s+/q\b", re.IGNORECASE), "rd /s /q"),
        (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "format drive"),
        (re.compile(r"\bshutdown\b", re.IGNORECASE), "shutdown"),
        (re.compile(r"\breboot\b", re.IGNORECASE), "reboot"),
        (re.compile(r"\bdiskpart\b", re.IGNORECASE), "diskpart"),
        (re.compile(r"\bmkfs\b", re.IGNORECASE), "mkfs"),
        (re.compile(r"\breg\s+delete\b", re.IGNORECASE), "reg delete"),
        (re.compile(r"\bremove-item\b[^\n\r]*-recurse[^\n\r]*-force", re.IGNORECASE), "Remove-Item -Recurse -Force"),
    ]

    _BUFFERING_WRAPPERS: Dict[str, Callable[[str], str]] = {
        "windows": lambda cmd: cmd,
        "linux": lambda cmd: f"stdbuf -oL -eL {cmd}",
        "darwin": lambda cmd: f"script -q /dev/null {cmd}",
    }

    @staticmethod
    def _resolve_execution_plan(command: str, shell_type: ShellType) -> tuple[list[str] | str, bool, str]:
        """Resolve command execution plan based on shell_type.

        Returns:
            (args_or_cmd, use_shell, resolved_shell_name):
            - args_or_cmd: arg list for create_subprocess_exec, or str for create_subprocess_shell
            - use_shell: True → create_subprocess_shell, False → create_subprocess_exec
            - resolved_shell_name: resolved shell name (for logging)
        """
        is_windows = os.name == "nt"

        if is_windows:
            if shell_type == ShellType.AUTO:
                if _looks_like_powershell(command):
                    exe = _available_powershell()
                    return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
                return command, True, "cmd"
            if shell_type == ShellType.POWERSHELL:
                exe = _available_powershell()
                return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
            if shell_type == ShellType.CMD:
                return command, True, "cmd"
            if shell_type in {ShellType.BASH, ShellType.SH}:
                exe = shutil.which("bash") if shell_type == ShellType.BASH else shutil.which("sh")
                if not exe:
                    raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                                      execution="_resolve_execution_plan",
                                      error_msg=f"shell '{shell_type.value}' is not available on this system")
                return [exe, "-lc" if shell_type == ShellType.BASH else "-c", command], False, shell_type.value
            raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                              execution="_resolve_execution_plan",
                              error_msg=f"unsupported shell_type for Windows: {shell_type.value}")

        # Non-Windows: auto and sh both use create_subprocess_shell (OS default /bin/sh)
        if shell_type in {ShellType.AUTO, ShellType.SH}:
            return command, True, "sh"
        if shell_type == ShellType.BASH:
            exe = shutil.which("bash") or "/bin/bash"
            return [exe, "-lc", command], False, "bash"
        if shell_type == ShellType.POWERSHELL:
            exe = shutil.which("pwsh") or shutil.which("powershell")
            if not exe:
                raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                                  execution="_resolve_execution_plan",
                                  error_msg="shell 'powershell' is not available on this system")
            return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
        if shell_type == ShellType.CMD:
            raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                              execution="_resolve_execution_plan",
                              error_msg="shell_type 'cmd' is only supported on Windows")
        raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                          execution="_resolve_execution_plan",
                          error_msg=f"unsupported shell_type: {shell_type.value}")

    async def _create_subprocess(
        self,
        command: str,
        cwd: pathlib.Path,
        env: Dict[str, str],
        shell_type: ShellType = ShellType.AUTO,
        background: bool = False,
    ) -> asyncio.subprocess.Process:
        """Create an asyncio subprocess with the appropriate shell.

        Args:
            command: Shell command to execute.
            cwd: Working directory.
            env: Environment variables.
            shell_type: Shell selection (auto/cmd/powershell/bash/sh).
            background: If True, redirect all I/O to DEVNULL (no output capture).

        Returns:
            asyncio.subprocess.Process
        """
        args, use_shell, _ = self._resolve_execution_plan(command, shell_type)

        if background:
            stdout = asyncio.subprocess.DEVNULL
            stderr = asyncio.subprocess.DEVNULL
            stdin = asyncio.subprocess.DEVNULL
        else:
            stdout = asyncio.subprocess.PIPE
            stderr = asyncio.subprocess.PIPE
            stdin = None

        if use_shell:
            cmd = self._wrap_command_with_buffering(args) if not background else args
            return await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd),
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdResult:
        """
        Asynchronously execute a command(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.
            shell_type: Shell to use, one of "auto"/"cmd"/"powershell"/"bash"/"sh" (default: "auto").

        Returns:
            ExecuteCmdResult: Execution result.
        """
        shell_type_enum = ShellType.from_str(shell_type)
        method_name = self.execute_cmd.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute cmd", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_cmd_err(error_msg: str, data: Optional[ExecuteCmdData] = None) -> ExecuteCmdResult:
            """Create standard error result for cmd execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd", "error_msg": error_msg},
                result_cls=ExecuteCmdResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute cmd", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        if not command or not command.strip():
            return _create_exec_cmd_err(error_msg="command can not be empty")

        actual_cwd = None
        try:
            # Resolve CWD
            actual_cwd = self._resolve_cwd(cwd)
            blocked = self._check_command_safety(command)
            if blocked:
                return _create_exec_cmd_err(error_msg=f"command rejected for safety: {blocked}",
                                            data=ExecuteCmdData(command=command, cwd=str(actual_cwd)))
            if not self._check_allowlist(command):
                return _create_exec_cmd_err(error_msg="command not allowed by allowlist",
                                            data=ExecuteCmdData(command=command, cwd=str(actual_cwd)))

            exec_env = OperationUtils.prepare_environment(environment)
            proc = await self._create_subprocess(command, actual_cwd, exec_env, shell_type=shell_type_enum)

            encoding = (options or {}).get("encoding", self._detect_shell_encoding())
            process_handler = OperationUtils.create_handler(process=proc, encoding=encoding, timeout=timeout)
            invoke_data = await process_handler.invoke()
            invoke_exception = getattr(invoke_data, "exception", None)
            if isinstance(invoke_exception, asyncio.TimeoutError):
                return _create_exec_cmd_err(f"execution timeout after {timeout} seconds",
                                            data=ExecuteCmdData(
                                                command=command,
                                                cwd=str(actual_cwd),
                                                exit_code=invoke_data.exit_code,
                                                stdout=invoke_data.stdout,
                                                stderr=invoke_data.stderr
                                            ))
            success_result = ExecuteCmdResult(
                code=StatusCode.SUCCESS.code,
                message="Command executed successfully",
                data=ExecuteCmdData(
                    command=command,
                    cwd=str(actual_cwd),
                    exit_code=invoke_data.exit_code,
                    stdout=invoke_data.stdout,
                    stderr=invoke_data.stderr
                )
            )
            sys_operation_logger.info("End to execute cmd", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
            ))
            return success_result

        except Exception as e:
            return _create_exec_cmd_err(error_msg=f"unexpected error: {str(e)}",
                                        data=ExecuteCmdData(command=command, cwd=str(actual_cwd)))

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        """
        Asynchronously execute a command streaming(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.
            shell_type: Shell to use, one of "auto"/"cmd"/"powershell"/"bash"/"sh" (default: "auto").

        Returns:
            AsyncIterator[ExecuteCmdStreamResult]: Streaming structured results.
        """
        shell_type_enum = ShellType.from_str(shell_type)
        method_name = self.execute_cmd_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute cmd streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_cmd_stream_err(error_msg: str,
                                        data: Optional[ExecuteCmdChunkData] = None) -> ExecuteCmdStreamResult:
            """Create standardized error result for streaming command execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_stream", "error_msg": error_msg},
                result_cls=ExecuteCmdStreamResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute cmd streaming", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        chunk_index = 0

        if not command or not command.strip():
            yield _create_exec_cmd_stream_err(
                error_msg="command can not be empty",
                data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1)
            )
            return

        try:
            actual_cwd = self._resolve_cwd(cwd)
            blocked = self._check_command_safety(command)
            if blocked:
                yield _create_exec_cmd_stream_err(
                    error_msg=f"command rejected for safety: {blocked}",
                    data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1))
                return
            if not self._check_allowlist(command):
                yield _create_exec_cmd_stream_err(
                    error_msg="command not allowed by allowlist",
                    data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1))
                return

            exec_env = OperationUtils.prepare_environment(environment)
            process = await self._create_subprocess(command, actual_cwd, exec_env, shell_type=shell_type_enum)

            chunk_size = (options or {}).get("chunk_size", 1024)
            encoding = (options or {}).get("encoding", self._detect_shell_encoding())
            process_handler = OperationUtils.create_handler(process=process, chunk_size=chunk_size, encoding=encoding,
                                                            timeout=timeout)

            def _stream_event_trans(stream_event_data: StreamEvent, data_idx: int) -> Optional[ExecuteCmdStreamResult]:
                """Transform raw StreamEvent to structured ExecuteCmdStreamResult"""

                def _handle_std_out_err(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle STDOUT/STDERR stream events"""
                    chunk_data = ExecuteCmdChunkData(text=event.data, type=event.type.value, chunk_index=idx)
                    stream_result = ExecuteCmdStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message=f"Get {chunk_data.type} stream successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.debug("Receive execute cmd stream", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_STREAM,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(stream_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
                    ))
                    return stream_result

                def _handle_exec_error(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle execution error events"""
                    chunk_data = ExecuteCmdChunkData(chunk_index=idx, exit_code=-1)
                    error_msg = f"execution receive error: {event.data}"
                    return _create_exec_cmd_stream_err(error_msg, chunk_data)

                def _handle_process_exit(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle process exit event (final chunk)"""
                    exit_code = event.data
                    chunk_data = ExecuteCmdChunkData(chunk_index=idx, exit_code=exit_code)
                    exit_result = ExecuteCmdStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message="Command executed successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.info("End to execute cmd streaming", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_END,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(exit_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
                    ))
                    return exit_result

                event_handler_map: dict[StreamEventType, Any] = {
                    StreamEventType.STDOUT: _handle_std_out_err,
                    StreamEventType.STDERR: _handle_std_out_err,
                    StreamEventType.ERROR: _handle_exec_error,
                    StreamEventType.EXIT: _handle_process_exit
                }
                handler = event_handler_map.get(stream_event_data.type)
                if handler is None:
                    sys_operation_logger.warning("Failed to get event handler", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_ERROR,
                        method_name=method_name,
                        metadata={"stream_type": stream_event_data.type.value}
                    ))
                    return None
                else:
                    return handler(stream_event_data, data_idx)

            async for chunk in process_handler.stream():
                modify_data = _stream_event_trans(chunk, chunk_index)
                if modify_data:
                    yield modify_data
                    chunk_index += 1
                if chunk.type in (StreamEventType.ERROR, StreamEventType.EXIT):
                    return

        except Exception as e:
            yield _create_exec_cmd_stream_err(error_msg=f"unexpected error: {str(e)}",
                                              data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1))
            return

    async def execute_cmd_background(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            environment: Optional[Dict[str, str]] = None,
            grace: float = 3.0,
            shell_type: Literal["auto", "cmd", "powershell", "bash", "sh"] = "auto",
    ) -> ExecuteCmdBackgroundResult:
        """
        Launch a command in the background and return immediately with its PID.

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            environment: Key-value dict of custom environment variables.
            grace: Seconds to wait for early failure detection (default: 3.0).
            shell_type: Shell to use, one of "auto"/"cmd"/"powershell"/"bash"/"sh" (default: "auto").

        Returns:
            ExecuteCmdBackgroundResult: Result containing the process PID.
        """
        shell_type_enum = ShellType.from_str(shell_type)
        method_name = self.execute_cmd_background.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute cmd background", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_cmd_background_err(
                error_msg: str,
                data: Optional[ExecuteCmdBackgroundData] = None
        ) -> ExecuteCmdBackgroundResult:
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_background", "error_msg": error_msg},
                result_cls=ExecuteCmdBackgroundResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute cmd background", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        if not command or not command.strip():
            return _create_exec_cmd_background_err(error_msg="command can not be empty")

        actual_cwd = None
        try:
            actual_cwd = self._resolve_cwd(cwd)
            blocked = self._check_command_safety(command)
            if blocked:
                return _create_exec_cmd_background_err(
                    error_msg=f"command rejected for safety: {blocked}",
                    data=ExecuteCmdBackgroundData(command=command, cwd=str(actual_cwd)))
            if not self._check_allowlist(command):
                return _create_exec_cmd_background_err(
                    error_msg="command not allowed by allowlist",
                    data=ExecuteCmdBackgroundData(command=command, cwd=str(actual_cwd)))

            exec_env = OperationUtils.prepare_environment(environment)
            process = await self._create_subprocess(
                command, actual_cwd, exec_env, shell_type=shell_type_enum, background=True
            )

            process_handler = OperationUtils.create_handler(process=process)
            pid, err = await process_handler.background(grace=grace)
            if err:
                return _create_exec_cmd_background_err(
                    error_msg=f"background command failed: {err}",
                    data=ExecuteCmdBackgroundData(command=command, cwd=str(actual_cwd)))

            success_result = ExecuteCmdBackgroundResult(
                code=StatusCode.SUCCESS.code,
                message="Background command started successfully",
                data=ExecuteCmdBackgroundData(
                    command=command,
                    cwd=str(actual_cwd),
                    pid=pid,
                )
            )
            sys_operation_logger.info("End to execute cmd background", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result

        except Exception as e:
            return _create_exec_cmd_background_err(
                error_msg=f"unexpected error: {str(e)}",
                data=ExecuteCmdBackgroundData(command=command, cwd=str(actual_cwd)) if actual_cwd else None)

    def _check_command_safety(self, command: str) -> Optional[str]:
        """Check command against dangerous patterns. Returns matched label/pattern or None if safe."""
        custom_patterns = getattr(self._run_config, 'dangerous_patterns', None)
        if custom_patterns is not None:
            for raw_pattern in custom_patterns:
                if re.search(raw_pattern, command, re.IGNORECASE):
                    return raw_pattern
            return None
        for pattern, label in self._DANGEROUS_PATTERNS:
            if pattern.search(command):
                return label
        return None

    def _check_allowlist(self, command: str) -> bool:
        """Check if command is in allowlist."""
        if not hasattr(self._run_config, 'shell_allowlist') or not self._run_config.shell_allowlist:
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

    def _wrap_command_with_buffering(self, command: str) -> str:
        """"Wraps a command string with OS-specific buffering wrapper if available."""
        os_name = platform.system().lower()
        wrapper = self._BUFFERING_WRAPPERS.get(os_name)
        return wrapper(command) if wrapper else command

    @staticmethod
    def _detect_shell_encoding() -> str:
        """Detect the shell output encoding for the current system.
        """

        try:
            encoding = locale.getpreferredencoding(False)
            return encoding if encoding else "utf-8"
        except Exception:
            return "utf-8"
