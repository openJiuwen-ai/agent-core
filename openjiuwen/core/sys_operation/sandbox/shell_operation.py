# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Dict, Any, AsyncIterator

from openjiuwen.core.sys_operation.base import BaseOperation, OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result.shell_operation_result import ExecuteCmdResult, ExecuteCmdStreamResult


@operation(name="shell", mode=OperationMode.SANDBOX, description="sandbox shell operation")
class ShellOperation(BaseOperation):
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
        pass

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
