# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import abstractmethod, ABC
from enum import Enum
from typing import Optional, Dict, Any, AsyncIterator, List, Literal

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.sys_operation.base import BaseOperation
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult, ExecuteCmdBackgroundResult
)


class ShellType(str, Enum):
    AUTO = "auto"
    CMD = "cmd"
    POWERSHELL = "powershell"
    BASH = "bash"
    SH = "sh"

    @classmethod
    def from_str(cls, value: str) -> "ShellType":
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.AUTO


class BaseShellOperation(BaseOperation, ABC):
    """Base shell operation"""

    def list_tools(self) -> List[ToolCard]:
        method_names = [
            "execute_cmd",
            "execute_cmd_stream",
            "execute_cmd_background"
        ]
        return self._generate_tool_cards(method_names)

    @abstractmethod
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
        pass

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
        pass

    @abstractmethod
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
        pass
