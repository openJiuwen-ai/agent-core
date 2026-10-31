# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import os
import platform
import shutil
import subprocess
from typing import Optional, Dict, Any, Literal, AsyncIterator, Callable, Tuple
import sys

from openjiuwen.core.common.logging import sys_operation_logger, LogEventType
from openjiuwen.core.sys_operation.local.utils import OperationUtils, StreamEvent, StreamEventType
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result
from openjiuwen.core.sys_operation.result.code_operation_result import ExecuteCodeChunkData

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.code import BaseCodeOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
    ExecuteCodeData,
)


@operation(name="code", mode=OperationMode.LOCAL, description="local code operation")
class CodeOperation(BaseCodeOperation):
    """Code operation"""
    _WINDOWS_CMD_LIMIT: int = 8000
    _UNIX_CMD_LIMIT: int = 100000
    # Successfully resolved interpreter, cached to skip re-probing.
    _python_exe_cache: Optional[str] = None
    # exec_cli/exec_file lambdas resolve the python interpreter internally
    # (cache hit after the pre-warm in _build_subprocess_cmd).
    _SUPPORT_LANGUAGE_CONFIG_DICT: Dict[str, Any] = {
        "python": {
            "exec_cli": lambda code: [CodeOperation._resolve_python_executable(), "-u", "-c", code],
            "exec_file": lambda path: [CodeOperation._resolve_python_executable(), "-u", path],
            "file_suffix": ".py",
        },
        "javascript": {
            "exec_cli": lambda code: ["node", "-e", code],
            "exec_file": lambda path: ["node", path],
            "file_suffix": ".js",
        }
    }

    @staticmethod
    def _is_frozen() -> bool:
        """Whether the current process is a frozen executable (PyInstaller etc.)."""
        return bool(getattr(sys, "frozen", False))

    @classmethod
    def _verify_python_candidate(cls, candidate: str) -> bool:
        """Verifies a candidate path is a real python interpreter.

        Runs ``<candidate> --version`` and checks the output, so that frozen
        GUI binaries or OS stub aliases (e.g. Windows Store python alias) are
        rejected instead of silently launched.

        Args:
            candidate: Absolute path to the candidate executable.

        Returns:
            True if the candidate is a working python interpreter.
        """
        if not candidate or not os.path.isfile(candidate):
            return False
        if cls._is_frozen():
            if os.path.normcase(os.path.abspath(candidate)) == \
                    os.path.normcase(os.path.abspath(sys.executable)):
                return False
        run_kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "timeout": 10,
        }
        if platform.system() == "Windows":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run([candidate, "--version"], **run_kwargs)
        except Exception:
            return False
        return proc.returncode == 0 and b"Python" in (proc.stdout or b"")

    @classmethod
    def _resolve_python_executable(cls) -> Optional[str]:
        """Resolves the python interpreter used to run code.

        Resolution rules:
            - Non-frozen environments always use ``sys.executable`` as-is
              (zero fallback), so behavior stays unchanged.
            - Frozen builds never use ``sys.executable`` because it points
              to the GUI executable itself; instead ``python3``/``python``
              is discovered via PATH and every candidate is verified by
              running ``--version``.

        A successful result is cached; failures are re-probed on the next
        call.

        Returns:
            Absolute path of a usable interpreter, or None if no usable
            interpreter was found.
        """
        if cls._python_exe_cache:
            return cls._python_exe_cache

        if not cls._is_frozen():
            resolved = sys.executable or None
        else:
            resolved = None
            if platform.system() == "Windows":
                candidates = ["python", "python3"]
            else:
                candidates = ["python3", "python"]
            seen: set = set()
            for cand in candidates:
                target = shutil.which(cand)
                if not target:
                    continue
                key = os.path.normcase(target)
                if key in seen:
                    continue
                seen.add(key)
                if cls._verify_python_candidate(target):
                    resolved = target
                    break

        if resolved is None:
            sys_operation_logger.warning(
                f"Failed to resolve python interpreter for code operation "
                f"(frozen={cls._is_frozen()}, hint: install python and "
                f"add it to your system environment variable PATH)")
        else:
            cls._python_exe_cache = resolved
            if cls._is_frozen():
                sys_operation_logger.info(
                    f"Resolved external python interpreter in frozen environment: {resolved}")
        return resolved

    @classmethod
    def _get_default_cmd_limit(cls):
        """Gets the default command length limit based on the operating system.

        Returns:
            int: The default command length limit integer value.
        """
        return cls._WINDOWS_CMD_LIMIT if platform.system() == "Windows" else cls._UNIX_CMD_LIMIT

    @classmethod
    async def _build_subprocess_cmd(cls, code: str, language: Literal["python", "javascript"],
                                    force_file: bool = False) -> Tuple:
        """Builds subprocess command for executing code in specified language.

        Args:
            code: The source code string to be executed.
            language: The programming language of the code (supports "python" or "javascript").
            force_file: Whether to force the use of file mode to execute code, ignoring the code length limit.
                        Defaults to False, which means using CLI mode for short code and file mode for long code.

        Returns:
            A tuple containing:
                - The subprocess command list (None on failure).
                - Path to the temporary file (None if code is short enough for CLI execution or on failure).
                - Failure reason string (None when the command is built successfully),
                  e.g. interpreter not resolved or temp file creation failed.
        """
        lang_config = cls._SUPPORT_LANGUAGE_CONFIG_DICT.get(language)
        if lang_config is None:
            return None, None, None

        if language == "python":
            # Pre-warm the interpreter resolution (the lambdas resolve it
            # again from cache); failure fails fast.
            interpreter = cls._resolve_python_executable()
            if interpreter is None:
                return None, None, ("python interpreter not found, please install "
                                    "Python and add it to your system environment "
                                    "variable PATH.")

        if not force_file and len(code) <= cls._get_default_cmd_limit():
            return lang_config["exec_cli"](code), None, None

        temp_path = await OperationUtils.create_tmp_file(code, lang_config["file_suffix"])
        if temp_path is None:
            return None, None, f"failed to create temp file for {language} code"
        return lang_config["exec_file"](temp_path), temp_path, None

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal["python", "javascript"] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        """
        Execute arbitrary code asynchronously.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
            timeout: Maximum execution time in seconds. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            cwd: Working directory for the subprocess.
            options: Additional execution configuration options.

        Returns:
            ExecuteCodeResult: Execution result.
        """

        method_name = self.execute_code.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute code", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_code_err(error_msg: str, data: Optional[ExecuteCodeData] = None) -> ExecuteCodeResult:
            """Create standard error result for code execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code", "error_msg": error_msg},
                result_cls=ExecuteCodeResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute code", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        if not code or not code.strip():
            return _create_exec_code_err(error_msg="code can not be empty")

        if language not in self._SUPPORT_LANGUAGE_CONFIG_DICT:
            return _create_exec_code_err(error_msg=f"{language} is not supported",
                                         data=ExecuteCodeData(code_content=code, language=language))

        cmd, code_file_path = None, None
        try:
            force_file = (options or {}).get("force_file", False)
            cmd, code_file_path, build_error = await self._build_subprocess_cmd(code, language, force_file)
            if cmd is None:
                return _create_exec_code_err(error_msg=build_error or "subprocess cmd can not be none",
                                             data=ExecuteCodeData(code_content=code, language=language))

            env = OperationUtils.prepare_environment(environment)
            if language == "javascript":
                env["NODE_DISABLE_COLORS"] = "1"
            elif language == "python":
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            encoding = (options or {}).get("encoding", "utf-8")
            process_handler = OperationUtils.create_handler(process=process, encoding=encoding, timeout=timeout)
            invoke_data = await process_handler.invoke()
            invoke_exception = getattr(invoke_data, "exception", None)
            if isinstance(invoke_exception, asyncio.TimeoutError):
                if code_file_path:
                    await OperationUtils.delete_tmp_file(code_file_path)
                return _create_exec_code_err(error_msg=f"execution timeout after {timeout} seconds",
                                             data=ExecuteCodeData(
                                                 code_content=code,
                                                 language=language,
                                                 exit_code=invoke_data.exit_code,
                                                 stdout=invoke_data.stdout,
                                                 stderr=invoke_data.stderr
                                             ))

            success_result = ExecuteCodeResult(
                code=StatusCode.SUCCESS.code,
                message="Code executed successfully",
                data=ExecuteCodeData(
                    code_content=code,
                    language=language,
                    exit_code=invoke_data.exit_code,
                    stdout=invoke_data.stdout,
                    stderr=invoke_data.stderr
                )
            )
            sys_operation_logger.info("End to execute code", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result

        except FileNotFoundError:
            return _create_exec_code_err(error_msg=f"{language} file not found error, please install "
                                                   f"and add it to your system environment variable PATH.",
                                         data=ExecuteCodeData(code_content=code, language=language))

        except Exception as e:
            return _create_exec_code_err(error_msg=f"unexpected error: {str(e)}",
                                         data=ExecuteCodeData(code_content=code, language=language))

        finally:
            if code_file_path:
                await OperationUtils.delete_tmp_file(code_file_path)

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal["python", "javascript"] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        """
        Execute arbitrary code asynchronously, by streaming.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
                Defaults to "python".
            timeout: Maximum execution time in seconds. Terminates the process if exceeded.
                Must be a positive integer. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            cwd: Working directory for the subprocess.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCodeStreamResult]: Streaming structured results.
        """

        method_name = self.execute_code_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute code streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_code_stream_err(error_msg: str,
                                         data: Optional[ExecuteCodeChunkData] = None) -> ExecuteCodeStreamResult:
            """Create standard error result for code stream execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1

            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_code_stream", "error_msg": error_msg},
                result_cls=ExecuteCodeStreamResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute code streaming", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        chunk_index = 0
        if not code or not code.strip():
            yield _create_exec_code_stream_err(error_msg="code can not be empty",
                                               data=ExecuteCodeChunkData(chunk_index=chunk_index, exit_code=-1))
            return

        if language not in self._SUPPORT_LANGUAGE_CONFIG_DICT:
            yield _create_exec_code_stream_err(error_msg=f"{language} is not supported",
                                               data=ExecuteCodeChunkData(chunk_index=chunk_index, exit_code=-1))
            return

        force_file = (options or {}).get("force_file", False)
        cmd, code_file_path, build_error = await self._build_subprocess_cmd(code, language, force_file)
        if cmd is None:
            yield _create_exec_code_stream_err(error_msg=build_error or "subprocess cmd can not be none",
                                               data=ExecuteCodeChunkData(chunk_index=chunk_index, exit_code=-1))
            if code_file_path:
                await OperationUtils.delete_tmp_file(code_file_path)
            return

        try:
            env = OperationUtils.prepare_environment(environment)
            if language == "javascript":
                env["NODE_DISABLE_COLORS"] = "1"
            elif language == "python":
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
            chunk_size = (options or {}).get("chunk_size", 1024)
            encoding = (options or {}).get("encoding", "utf-8")
            process_handler = OperationUtils.create_handler(process=process, chunk_size=chunk_size, encoding=encoding,
                                                            timeout=timeout)

            def _stream_event_trans(stream_event_data: StreamEvent, data_idx: int) -> Optional[ExecuteCodeStreamResult]:
                """Dispatch stream events to corresponding handlers and generate execute code stream results"""

                def _handle_std_out_err(event: StreamEvent, idx: int) -> ExecuteCodeStreamResult:
                    """Handle stdout and stderr events, package data and return success result"""
                    chunk_data = ExecuteCodeChunkData(text=event.data, type=event.type.value, chunk_index=idx)
                    stream_result = ExecuteCodeStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message=f"Get {chunk_data.type} stream successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.debug("Receive execute code stream", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_STREAM,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(stream_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
                    ))
                    return stream_result

                def _handle_exec_error(event: StreamEvent, idx: int) -> ExecuteCodeStreamResult:
                    """Handle execution error events and return standardized error result"""
                    chunk_data = ExecuteCodeChunkData(chunk_index=idx, exit_code=-1)
                    error_msg = f"execution receive error: {event.data}"
                    return _create_exec_code_stream_err(error_msg, chunk_data)

                def _handle_process_exit(event: StreamEvent, idx: int) -> ExecuteCodeStreamResult:
                    """Handle process exit events, return result by exit code judgment"""
                    exit_code = event.data
                    chunk_data = ExecuteCodeChunkData(chunk_index=idx, exit_code=exit_code)
                    exit_result = ExecuteCodeStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message="Code executed successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.info("End to execute code streaming", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_END,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(exit_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
                    ))
                    return exit_result

                event_handler_map: dict[StreamEventType, Callable[[StreamEvent, int], ExecuteCodeStreamResult]] = {
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

        except FileNotFoundError:
            yield _create_exec_code_stream_err(error_msg=f"{language} file not found error, please install "
                                                         f"and add it to your system environment variable PATH.",
                                               data=ExecuteCodeChunkData(chunk_index=chunk_index, exit_code=-1))
            return

        except Exception as e:
            yield _create_exec_code_stream_err(error_msg=f"unexpected error: {str(e)}",
                                               data=ExecuteCodeChunkData(chunk_index=chunk_index, exit_code=-1))
            return

        finally:
            if code_file_path:
                await OperationUtils.delete_tmp_file(code_file_path)
