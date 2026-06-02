# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import codecs
import locale
import os
import platform
import re
import shlex
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
from openjiuwen.core.sys_operation.shell_process_registry import (
    register_shell_process,
    resolve_shell_session_id,
    unregister_shell_process,
)


_POWERSHELL_TOKENS = (
    "powershell ", "powershell.exe ", "pwsh ", "pwsh.exe ",
    "get-childitem", "set-location", "remove-item", "test-path",
    "join-path", "select-object", "where-object", "foreach-object",
    "invoke-webrequest", "invoke-restmethod", "out-file", "start-process",
    "$env:", "$psversiontable", "$null", "$true", "$false",
)

_PS_VARIABLE_PATTERN = re.compile(r"(^|[\s;(])\$[A-Za-z_][A-Za-z0-9_]*")
_POWERSHELL_EXECUTABLE_PATTERN = re.compile(r"^\s*(?:powershell(?:\.exe)?|pwsh(?:\.exe)?)\b", re.IGNORECASE)
_POWERSHELL_COMMAND_ARG_PATTERN = re.compile(r"(?is)(?:^|\s)-(?:command|c)\s+(?P<script>.+)\s*$")
_POWERSHELL_CANDIDATES = ("pwsh", "powershell", "powershell.exe")
_POSIX_COMMANDS = frozenset({
    "ls", "grep", "egrep", "fgrep", "cat", "head", "tail", "find", "rm",
    "cp", "mv", "touch", "chmod", "chown", "sed", "awk", "gawk", "cut",
    "sort", "uniq", "wc", "du", "df", "pwd", "which", "mkdir",
})
_QUOTED_WINDOWS_PATH_PATTERN = re.compile(r"(?P<quote>['\"])(?P<path>[A-Za-z]:\\[^'\"]+)(?P=quote)")
_UNQUOTED_WINDOWS_PATH_PATTERN = re.compile(r"(?<![\w/])(?P<path>[A-Za-z]:\\[^\s|&;]+)")


def _track_shell_process(proc: asyncio.subprocess.Process) -> str | None:
    sid = resolve_shell_session_id()
    if sid:
        register_shell_process(sid, proc)
    return sid


def _untrack_shell_process(session_id: str | None, proc: asyncio.subprocess.Process) -> None:
    if session_id:
        unregister_shell_process(session_id, proc)


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
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        system_powershell = pathlib.Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system_powershell.exists():
            return str(system_powershell)

    for candidate in _POWERSHELL_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "powershell"


def _is_wsl_bash_path(path: str) -> bool:
    normalized = os.path.normcase(os.path.normpath(path))
    system_root = os.path.normcase(os.path.normpath(os.environ.get("SystemRoot") or r"C:\Windows"))
    return normalized == os.path.join(system_root, "system32", "bash.exe") or (
        "\\microsoft\\windowsapps\\bash.exe" in normalized
    )


def _git_bash_candidates() -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    env_path = os.environ.get("GIT_BASH") or os.environ.get("GIT_BASH_PATH")
    if env_path:
        candidates.append(pathlib.Path(env_path))

    for root in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData") and str(pathlib.Path(os.environ["LocalAppData"]) / "Programs"),
    ):
        if root:
            candidates.append(pathlib.Path(root) / "Git" / "bin" / "bash.exe")

    git_path = shutil.which("git")
    if git_path:
        git_exe = pathlib.Path(git_path)
        # Typical layout: C:\Program Files\Git\cmd\git.exe -> ..\bin\bash.exe
        candidates.append(git_exe.parent.parent / "bin" / "bash.exe")

    return candidates


def _available_git_bash() -> str | None:
    if os.name != "nt":
        return None
    for candidate in _git_bash_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def _available_bash(*, allow_wsl: bool = True) -> str | None:
    if os.name == "nt":
        git_bash = _available_git_bash()
        if git_bash:
            return git_bash
    resolved = shutil.which("bash")
    if resolved and (allow_wsl or not _is_wsl_bash_path(resolved)):
        return resolved
    return None


def _available_sh() -> str | None:
    if os.name == "nt":
        for bash_path in (_available_git_bash(),):
            if bash_path:
                sh_path = pathlib.Path(bash_path).parent.parent / "usr" / "bin" / "sh.exe"
                if sh_path.exists():
                    return str(sh_path)
    return shutil.which("sh")


def _strip_matching_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _unwrap_powershell_command(command: str) -> str | None:
    """Extract the script from an explicit powershell -Command wrapper.

    Agents often emit commands such as ``powershell -Command "Get-Item ..."``.
    When the local shell runner has already selected PowerShell, executing that
    wrapper would start a nested PowerShell process and load the user's profile.
    On Windows machines with broken Conda profile hooks this fails before the
    intended script runs, so we execute the inner script directly.
    """
    if not _POWERSHELL_EXECUTABLE_PATTERN.match(command or ""):
        return None
    remainder = _POWERSHELL_EXECUTABLE_PATTERN.sub("", command, count=1).strip()
    match = _POWERSHELL_COMMAND_ARG_PATTERN.search(remainder)
    if not match:
        return None
    script = _strip_matching_quotes(match.group("script"))
    return script or None


def _split_shell_segments(command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if char in {'"', "'"}:
            quote = None if quote == char else char if quote is None else quote
        if quote is None and command.startswith(("&&", "||"), index):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2
            continue
        if quote is None and char in {"|", ";", "\n", "\r"}:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _segment_base_command(segment: str) -> str:
    try:
        tokens = shlex.split(segment, posix=False)
    except ValueError:
        return ""
    if not tokens:
        return ""
    base = _strip_matching_quotes(tokens[0]).rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _looks_like_posix(command: str) -> bool:
    return any(_segment_base_command(segment) in _POSIX_COMMANDS for segment in _split_shell_segments(command or ""))


def _normalize_windows_paths_for_bash(command: str) -> str:
    def replace_path(match: re.Match[str]) -> str:
        value = match.group("path").replace("\\", "/")
        quote = match.groupdict().get("quote")
        return f"{quote}{value}{quote}" if quote else value

    normalized = _QUOTED_WINDOWS_PATH_PATTERN.sub(replace_path, command)
    return _UNQUOTED_WINDOWS_PATH_PATTERN.sub(replace_path, normalized)


@operation(name="shell", mode=OperationMode.LOCAL, description="local shell operation")
class ShellOperation(BaseShellOperation):
    """Shell operation"""

    # 已知在无 TTY 环境下会挂死的命令模式。
    # 每个条目: (pattern, description, auto_env_overrides | None)
    # auto_env_overrides 会在检测到时自动注入环境变量，降低挂死概率。
    _TUI_COMMAND_PATTERNS: List[Tuple[re.Pattern, str, Optional[Dict[str, str]]]] = [
        (re.compile(r"\b(npx\s+)?playwright\s+test\b", re.IGNORECASE),
         "Playwright test runner may require TTY", {"CI": "true"}),
        (re.compile(r"\b(npm|npx|yarn|pnpm)\s+(run\s+)?test\b", re.IGNORECASE),
         "Test runner (npm/pnpm/yarn) may require TTY", {"CI": "true"}),
        (re.compile(r"\bvitest\b.*(--watch|--ui)", re.IGNORECASE),
         "Vitest watch/UI mode requires TTY", {"CI": "true"}),
        (re.compile(r"\b(top|htop|vim|vi|nano|less|more)\b", re.IGNORECASE),
         "Interactive TUI program will hang without TTY", None),
    ]

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
        (
            re.compile(r"\bpkill\b[^\n\r;|&]*jiuwenswarm(?!-tui)", re.IGNORECASE),
            "pkill targeting jiuwenswarm backend",
        ),
        (
            re.compile(r"\bkillall\b[^\n\r;|&]*jiuwenswarm(?!-tui)", re.IGNORECASE),
            "killall targeting jiuwenswarm backend",
        ),
        (
            re.compile(r"\bpkill\b[^\n\r;|&]*jiuwenclaw", re.IGNORECASE),
            "pkill targeting jiuwenclaw backend",
        ),
        (
            re.compile(r"\bkillall\b[^\n\r;|&]*jiuwenclaw", re.IGNORECASE),
            "killall targeting jiuwenclaw backend",
        ),
    ]

    _BUFFERING_WRAPPERS: Dict[str, Callable[[str], str]] = {
        "windows": lambda cmd: cmd,
        "linux": lambda cmd: f"stdbuf -oL -eL /bin/sh -c {shlex.quote(cmd)}",
        "darwin": lambda cmd: f"script -q /dev/null /bin/sh -c {shlex.quote(cmd)}",
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
                powershell_command = _unwrap_powershell_command(command)
                if powershell_command is not None:
                    exe = _available_powershell()
                    return [exe, "-NoProfile", "-NonInteractive", "-Command", powershell_command], False, "powershell"
                if _looks_like_powershell(command):
                    exe = _available_powershell()
                    return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
                if _looks_like_posix(command):
                    exe = _available_bash(allow_wsl=False)
                    if exe:
                        return [exe, "-lc", _normalize_windows_paths_for_bash(command)], False, "bash"
                return command, True, "cmd"
            if shell_type == ShellType.POWERSHELL:
                exe = _available_powershell()
                command = _unwrap_powershell_command(command) or command
                return [exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"
            if shell_type == ShellType.CMD:
                return command, True, "cmd"
            if shell_type in {ShellType.BASH, ShellType.SH}:
                exe = _available_bash() if shell_type == ShellType.BASH else _available_sh()
                if not exe:
                    raise build_error(StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                                      execution="_resolve_execution_plan",
                                      error_msg=f"shell '{shell_type.value}' is not available on this system")
                return [exe, "-lc" if shell_type == ShellType.BASH else "-c",
                        _normalize_windows_paths_for_bash(command)], False, shell_type.value
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
        *,
        shell_type: ShellType = ShellType.AUTO,
        background: bool = False,
        stream: bool = False,
    ) -> asyncio.subprocess.Process:
        """Create an asyncio subprocess with the appropriate shell.

        Args:
            command: Shell command to execute.
            cwd: Working directory.
            env: Environment variables.
            shell_type: Shell selection (auto/cmd/powershell/bash/sh).
            background: If True, redirect all I/O to DEVNULL (no output capture).
            stream: If True, apply OS-specific buffering wrapper for
                real-time line output (e.g. ``script`` on macOS).  Only
                meaningful for streaming execution; one-shot
                ``execute_cmd`` should pass False to avoid PTY side
                effects such as git launching a pager.

        Returns:
            asyncio.subprocess.Process
        """
        args, use_shell, _ = self._resolve_execution_plan(command, shell_type)

        # 进程组隔离：创建新 session，后续可用 os.killpg() 清理整棵进程树。
        # 仅 POSIX 生效；Windows 通过 CREATE_NEW_PROCESS_GROUP 但语义不同，
        # 此处保持 _kill_process_tree 的 fallback 路径。
        subprocess_kw: dict[str, Any] = {}
        if os.name != "nt":
            _jw_start_new_session = os.getenv("JW_START_NEW_SESSION", "true").strip().lower()
            if _jw_start_new_session not in ("0", "false", "no", "off"):
                subprocess_kw["start_new_session"] = True

        if background:
            stdout = asyncio.subprocess.DEVNULL
            stderr = asyncio.subprocess.DEVNULL
            stdin = asyncio.subprocess.DEVNULL
        else:
            stdout = asyncio.subprocess.PIPE
            stderr = asyncio.subprocess.PIPE
            stdin = asyncio.subprocess.DEVNULL

        if use_shell:
            cmd = self._wrap_command_with_buffering(args) if (stream and not background) else args
            return await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd),
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                **subprocess_kw,
            )
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            **subprocess_kw,
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

            # 框架层超时上限，防止 LLM 设置过长 timeout 导致 agent 长时间无响应
            _max_exec_cmd_timeout = int(os.getenv("JW_EXECUTE_CMD_MAX_TIMEOUT", "600"))
            timeout = min(timeout or 300, _max_exec_cmd_timeout)

            exec_env = OperationUtils.prepare_environment(environment)
            is_tui, tui_warning = self._detect_and_mitigate_tui(command, exec_env)
            if is_tui and tui_warning:
                sys_operation_logger.warning(
                    tui_warning,
                    event_type=LogEventType.SYS_OP_ERROR,
                    metadata={"command": command[:200]},
                )
            if os.name == "nt":
                system_encoding = self._detect_shell_encoding()
                if system_encoding and system_encoding.lower() not in ("utf-8", "utf8"):
                    lang_encoding = self._get_lang_encoding(system_encoding)
                    exec_env["LANG"] = f"C.{lang_encoding}"
            proc = await self._create_subprocess(command, actual_cwd, exec_env, shell_type=shell_type_enum)
            track_sid = _track_shell_process(proc)

            encoding = (options or {}).get("encoding", self._detect_shell_encoding())
            process_handler = OperationUtils.create_handler(process=proc, encoding=encoding, timeout=timeout)
            try:
                invoke_data = await process_handler.invoke()
            finally:
                _untrack_shell_process(track_sid, proc)
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

            # 框架层超时上限，防止 LLM 设置过长 timeout 导致 agent 长时间无响应
            _max_exec_cmd_timeout = int(os.getenv("JW_EXECUTE_CMD_MAX_TIMEOUT", "600"))
            timeout = min(timeout or 300, _max_exec_cmd_timeout)

            exec_env = OperationUtils.prepare_environment(environment)
            is_tui, tui_warning = self._detect_and_mitigate_tui(command, exec_env)
            if is_tui and tui_warning:
                sys_operation_logger.warning(
                    tui_warning,
                    event_type=LogEventType.SYS_OP_ERROR,
                    metadata={"command": command[:200]},
                )
            if os.name == "nt":
                system_encoding = self._detect_shell_encoding()
                if system_encoding and system_encoding.lower() not in ("utf-8", "utf8"):
                    lang_encoding = self._get_lang_encoding(system_encoding)
                    exec_env["LANG"] = f"C.{lang_encoding}"
            process = await self._create_subprocess(
                command, actual_cwd, exec_env, shell_type=shell_type_enum, stream=True,
            )
            track_sid = _track_shell_process(process)

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

            try:
                async for chunk in process_handler.stream():
                    modify_data = _stream_event_trans(chunk, chunk_index)
                    if modify_data:
                        yield modify_data
                        chunk_index += 1
                    if chunk.type in (StreamEventType.ERROR, StreamEventType.EXIT):
                        return
            finally:
                _untrack_shell_process(track_sid, process)

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
            track_sid = _track_shell_process(process)

            process_handler = OperationUtils.create_handler(process=process)
            pid, err = await process_handler.background(grace=grace)
            if err:
                _untrack_shell_process(track_sid, process)
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
        """Resolve CWD: explicit param -> ContextVar -> os.getcwd()."""
        from openjiuwen.core.sys_operation.cwd import get_cwd

        if not cwd:
            return pathlib.Path(get_cwd())

        target = pathlib.Path(cwd).expanduser()
        if not target.is_absolute():
            target = pathlib.Path(get_cwd()) / target
        return target.resolve()

    def _detect_and_mitigate_tui(self, command: str, exec_env: Dict[str, str]) -> Tuple[bool, Optional[str]]:
        """检测 TUI/PTY 依赖命令并注入缓解环境变量。

        Returns:
            (is_tui_detected, warning_message_or_None)
        """
        if os.getenv("JW_TUI_DETECTION_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
            return False, None
        for pattern, description, auto_env in self._TUI_COMMAND_PATTERNS:
            if pattern.search(command):
                if auto_env:
                    for key, value in auto_env.items():
                        if key not in exec_env:
                            exec_env[key] = value
                return True, f"TUI command detected: {description} (auto-mitigated)"
        return False, None

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

    @staticmethod
    def _get_lang_encoding(encoding: str) -> str:
        """Convert encoding name to LANG-style encoding name using Python's codec registry.

        Args:
            encoding: Python encoding name (e.g., 'cp936', 'gbk', 'utf-8')

        Returns:
            LANG-style encoding name (e.g., 'GBK', 'UTF-8')
        """
        try:
            info = codecs.lookup(encoding)
            return info.name.upper()
        except LookupError:
            return encoding.upper()
