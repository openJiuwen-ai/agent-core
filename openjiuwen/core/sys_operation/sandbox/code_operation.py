# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Dict, Any, Literal, AsyncIterator

from openjiuwen.core.sys_operation.base import BaseOperation, OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result.code_operation_result import ExecuteCodeResult, ExecuteCodeStreamResult


@operation(name="code", mode=OperationMode.SANDBOX, description="sandbox code operation")
class CodeOperation(BaseOperation):
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
        pass

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
