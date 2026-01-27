# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import asyncio
from typing import Any, Optional

from openjiuwen.core.foundation.tool import ToolCard, LocalFunction


class SkillToolKit:
    def __init__(self, sys_operation_id: str):
        self._sys_operation_id = sys_operation_id
        self._runner = None

    @property
    def sys_operation_id(self) -> str:
        return self._sys_operation_id

    @sys_operation_id.setter
    def sys_operation_id(self, sys_operation_id: str):
        self._sys_operation_id = sys_operation_id

    @staticmethod
    async def _await_if_needed(val: Any) -> Any:
        if asyncio.iscoroutine(val):
            return await val
        return val

    def _get_sys_operation(self) -> Any:
        if not self._sys_operation_id:
            return None
        from openjiuwen.core.runner.runner import Runner
        return Runner.resource_mgr.get_sys_operation(self._sys_operation_id)

    def set_runner(self, runner) -> None:
        self._runner = runner

    def create_view_file_tool(self):
        view_file_tool_card = ToolCard(
            id="_internal_view_file",
            name="view_file",
            description=(
                "Given a file_path, reads and returns the file content stored at file_path. "
                "Only reads text files (e.g. .md and .txt files), and does NOT read binary files "
                "(e.g. .pdf, .xlsx, .ppt etc.)"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "file_path": {
                        "description": "The path to the file to view",
                        "type": "string",
                    }
                },
                "required": ["file_path"],
            }
        )

        async def view_file(file_path: str):
            sys_operation = self._get_sys_operation()
            if sys_operation is None:
                return "sys_operation is not available"

            fs = sys_operation.fs()
            res = await self._await_if_needed(fs.read_file(file_path, mode="text"))

            data = getattr(res, "data", None)
            content = getattr(data, "content", None) if data is not None else None
            if content is None:
                content = res

            if isinstance(content, (bytes, bytearray)):
                return f"Binary file detected at {file_path}. Use execute_python_code to read it with pandas/openpyxl."

            return str(content)

        return LocalFunction(card=view_file_tool_card, func=view_file)

    def create_execute_python_code_tool(self):
        execute_python_code_tool_card = ToolCard(
            id="_internal_execute_python_code",
            name="execute_python_code",
            description="Execute Python code",
            input_params={
                "type": "object",
                "properties": {
                    "code_block": {
                        "description": "The Python code to execute",
                        "type": "string",
                    }
                },
                "required": ["code_block"],
            }
        )

        async def execute_python_code(code_block: str):
            sys_operation = self._get_sys_operation()
            if sys_operation is None:
                return "sys_operation is not available"

            code = sys_operation.code()
            res = await self._await_if_needed(code.execute_code(code_block, language="python"))

            data = getattr(res, "data", None)
            if data is not None:
                stdout = getattr(data, "stdout", None) or ""
                stderr = getattr(data, "stderr", None) or ""
                out = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
                if out:
                    return out

            return str(res)

        return LocalFunction(card=execute_python_code_tool_card, func=execute_python_code)

    def create_execute_command_tool(self):
        run_command_tool_card = ToolCard(
            id="_internal_run_command",
            name="run_command",
            description="Execute bash commands in a Linux terminal",
            input_params={
                "type": "object",
                "properties": {
                    "bash_command": {
                        "description": "One or more bash commands to execute",
                        "type": "string",
                    }
                },
                "required": ["bash_command"],
            }
        )

        async def run_command(bash_command: str):
            sys_operation = self._get_sys_operation()
            if sys_operation is None:
                return "sys_operation is not available"

            shell = sys_operation.shell()
            res = await self._await_if_needed(shell.execute_cmd(bash_command))

            data = getattr(res, "data", None)
            if data is not None:
                stdout = getattr(data, "stdout", None) or ""
                stderr = getattr(data, "stderr", None) or ""
                out = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
                if out:
                    return out

            return str(res)

        return LocalFunction(card=run_command_tool_card, func=run_command)

    def add_skill_tools(self, agent):
        execute_python_code_tool = self.create_execute_python_code_tool()
        execute_command_tool = self.create_execute_command_tool()
        view_file_tool = self.create_view_file_tool()

        from openjiuwen.core.runner.runner import Runner
        rm = Runner.resource_mgr
        rm.add_tool([execute_python_code_tool, execute_command_tool, view_file_tool])

        agent.ability_manager.add(execute_python_code_tool.card)
        agent.ability_manager.add(execute_command_tool.card)
        agent.ability_manager.add(view_file_tool.card)
