from openjiuwen.core.runner.resources_manager.agent_group_manager import AgentGroupMgr
from openjiuwen.core.runner.resources_manager.agent_manager import AgentMgr
from openjiuwen.core.runner.resources_manager.model_manager import ModelMgr
from openjiuwen.core.runner.resources_manager.prompt_manager import PromptMgr
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr
from openjiuwen.core.runner.resources_manager.workflow_manager import WorkflowMgr


class ResourceRegistry:
    def __init__(self) -> None:
        self._tool_mgr = ToolMgr()
        self._workflow_mgr = WorkflowMgr()
        self._prompt_mgr = PromptMgr()
        self._model_mgr = ModelMgr()
        self._agent_mgr: AgentMgr = AgentMgr()
        self._agent_group_mgr: AgentGroupMgr = AgentGroupMgr()

    def tool(self) -> ToolMgr:
        return self._tool_mgr

    def prompt(self) -> PromptMgr:
        return self._prompt_mgr

    def model(self) -> ModelMgr:
        return self._model_mgr

    def workflow(self) -> WorkflowMgr:
        return self._workflow_mgr

    def agent(self) -> AgentMgr:
        return self._agent_mgr

    def agent_group(self) -> AgentGroupMgr:
        return self._agent_group_mgr