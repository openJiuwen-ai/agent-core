# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import os
from typing import Optional, Dict, Any, Literal, AsyncIterator, Callable, List
import sys
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.code import BaseCodeOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
    ExecuteCodeData,
)

_SUPPORT_LANGUAGE_CMD_MAP: Dict[str, Callable[[str], List[str]]] = {
    "python": lambda code: [sys.executable, "-c", code],
    "javascript": lambda code: ["node", "-e", code]
}


@operation(name="code", mode=OperationMode.LOCAL, description="local code operation")
class CodeOperation(BaseCodeOperation):
    """Code operation"""

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            time_out: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        """
        Execute arbitrary code asynchronously.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
            time_out: Maximum execution time in seconds. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            ExecuteCodeResult: Execution result.
        """
        if not code or not code.strip():
            return ExecuteCodeResult(code=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code,
                                     message=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                                         execution="execute_code",
                                         error_msg="code can not be empty"),
                                     data=None)

        if language not in _SUPPORT_LANGUAGE_CMD_MAP:
            return ExecuteCodeResult(code=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code,
                                     message=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                                         execution="execute_code",
                                         error_msg=f"{language} is not supported"),
                                     data=ExecuteCodeData(code_content=code, language=language))

        try:
            cmd = _SUPPORT_LANGUAGE_CMD_MAP[language](code)
            # Prepare environment variables, for example interpreter_path
            env = os.environ.copy()
            if environment:
                env.update(environment)
            # Create subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=time_out
                )
                exit_code = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                stderr_content = f"execution timeout after {time_out} seconds"
                return ExecuteCodeResult(
                    code=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code,
                    message=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                        execution="execute_code",
                        error_msg=stderr_content),
                    data=ExecuteCodeData(
                        code_content=code,
                        language=language,
                        exit_code=-1,
                        stdout="",
                        stderr=stderr_content
                    )
                )

            # Decode output
            stdout_text = stdout.decode('utf-8', errors='replace') if stdout else ""
            stderr_text = stderr.decode('utf-8', errors='replace') if stderr else ""

            # Create result
            executed_code = StatusCode.SUCCESS.code if exit_code == 0 else \
                StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            executed_message = "Code executed successfully" if exit_code == 0 else \
                StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                    execution="execute_code",
                    error_msg=f"execution failed with exit code {exit_code}, stderr {stderr_text}")
            return ExecuteCodeResult(
                code=executed_code,
                message=executed_message,
                data=ExecuteCodeData(
                    code_content=code,
                    language=language,
                    exit_code=exit_code,
                    stdout=stdout_text,
                    stderr=stderr_text
                )
            )
        except FileNotFoundError:
            stderr_content = f"{language} file not found error"
            return ExecuteCodeResult(
                code=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code,
                message=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                    execution="execute_code",
                    error_msg=stderr_content),
                data=ExecuteCodeData(
                    code_content=code,
                    language=language,
                    exit_code=-1,
                    stdout="",
                    stderr=stderr_content
                )
            )

        except Exception as e:
            stderr_content = f"unexpected error: {str(e)}"
            return ExecuteCodeResult(
                code=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code,
                message=StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.errmsg.format(
                    execution="execute_code",
                    error_msg=stderr_content),
                data=ExecuteCodeData(
                    code_content=code,
                    language=language,
                    exit_code=-1,
                    stdout="",
                    stderr=stderr_content
                )
            )

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            time_out: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        """
        Execute arbitrary code asynchronously, by streaming.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
                Defaults to "python".
            time_out: Maximum execution time in seconds. Terminates the process if exceeded.
                Must be a positive integer. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCodeStreamResult]: Streaming structured results.
        """
        pass
