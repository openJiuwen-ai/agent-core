# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import TYPE_CHECKING

from openjiuwen.core.foundation.tool import ToolCard, LocalFunction

if TYPE_CHECKING:
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.agent import BaseAgent


class SkillToolKit:
    """Toolkit for creating and managing skill-related tools.
    
    This class provides methods to create various tools that can be used by agents
    to interact with skills, such as viewing files, executing Python code, and
    running shell commands.
    """
    
    def __init__(self, sys_operation_id):
        """Initialize the skill tool kit.
        
        Args:
            sys_operation_id: The system operation ID used for file and code operations.
        """
        self._sys_operation_id = sys_operation_id

    @property
    def sys_operation_id(self):
        """Get the system operation ID.
        
        Returns:
            str: The system operation ID.
        """
        return self._sys_operation_id

    @sys_operation_id.setter
    def sys_operation_id(self, sys_operation_id):
        """Set the system operation ID.
        
        Args:
            sys_operation_id: The new system operation ID.
        """
        self._sys_operation_id = sys_operation_id

    def create_view_file_tool(self):
        """Create a tool for viewing file contents.
        
        Returns:
            LocalFunction: A tool function that can read and return file contents.
        """
        view_file_tool_card = ToolCard(
            id="_internal_view_file",
            name="view_file",
            description="View the contents of a file at the specified path",
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

        def view_file(file_path):
            # 延迟导入以避免循环依赖
            from openjiuwen.core.runner import Runner
            sys_operation = Runner().resource_mgr.get_sys_operation(self._sys_operation_id)
            res = sys_operation.code().read_file(file_path)
            return str(res)

        return LocalFunction(
            card=view_file_tool_card,
            func=view_file
        )

    def create_execute_python_code_tool(self):
        """Create a tool for executing Python code.
        
        Returns:
            LocalFunction: A tool function that can execute Python code blocks.
        """
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

        def execute_python_code(code_block):
            # 延迟导入以避免循环依赖
            from openjiuwen.core.runner import Runner
            sys_operation = Runner().resource_mgr.get_sys_operation(self._sys_operation_id)
            res = sys_operation.code().execute_code(code_block)
            return str(res)

        return LocalFunction(
            card=execute_python_code_tool_card,
            func=execute_python_code
        )

    def create_execute_command_tool(self):
        """Create a tool for executing bash commands in a Linux terminal.
        
        Returns:
            LocalFunction: A tool function that can execute bash commands.
        """
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

        def run_command(code_block):
            # 延迟导入以避免循环依赖
            from openjiuwen.core.runner import Runner
            sys_operation = Runner().resource_mgr.get_sys_operation(self._sys_operation_id)
            res = sys_operation.code().execute_code(code_block)
            return str(res)

        return LocalFunction(
            card=run_command_tool_card,
            func=run_command
        )

    def add_skill_tools(self, agent: "BaseAgent"):
        """Add skill-related tools to an agent.
        
        This method creates and registers three tools with the agent:
        - execute_python_code: For executing Python code
        - run_command: For executing bash commands
        - view_file: For viewing file contents
        
        Args:
            agent: The agent to add the tools to.
        """
        # 延迟导入以避免循环依赖
        from openjiuwen.core.runner import Runner
        execute_python_code_tool = self.create_execute_python_code_tool()
        execute_command_tool = self.create_execute_command_tool()
        view_file_tool = self.create_view_file_tool()
        Runner().resource_mgr.add_tool(execute_python_code_tool)
        Runner().resource_mgr.add_tool(execute_command_tool)
        Runner().resource_mgr.add_tool(view_file_tool)
        agent.ability_kit.add(execute_python_code_tool.card)
        agent.ability_kit.add(execute_command_tool.card)
        agent.ability_kit.add(view_file_tool.card)
