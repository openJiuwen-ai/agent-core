# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Union, Tuple

from pydantic import BaseModel

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.foundation.tool import Tool, ToolInfo, ToolCard, McpServerConfig
from openjiuwen.core.multi_agent import BaseGroup, GroupCard
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.resources_manager.base import (
    AgentGroupProvider,
    Error,
    Ok,
    Tag,
    GLOBAL,
    Result,
    TagUpdateStrategy,
    TagMatchStrategy,
    AgentProvider,
    WorkflowProvider,
    ModelProvider)
from openjiuwen.core.runner.resources_manager.resource_registry import ResourceRegistry
from openjiuwen.core.session import Session
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.workflow import Workflow


class ResourceMgr:
    """
    Resource Manager for Model, Workflow, Prompt, Tool
    """

    def __init__(self, ) -> None:
        self._resource_registry = ResourceRegistry()

    async def add_agent_group(self,
                              card: GroupCard,
                              agent_group: AgentGroupProvider,
                              *,
                              tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                              tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                              ) -> Result[GroupCard, Exception]:
        """
        Add a single agent group to the resource manager.

        Args:
            card: The agent group's metadata card containing configuration and identification.
            agent_group: Callable provider that creates or returns the agent group instance.
            tag: Optional tag(s) for categorizing and filtering the agent group.
                 If None, no tags will be added or updated.
            tag_update_strategy: Strategy for updating tags when resource already exists.
                MERGE - Add new tags while keeping existing ones (default).
                REPLACE - Replace all existing tags with new tags.

        Returns:
            Result[GroupCard, Exception]: Result object containing the added group card or an exception.
        """
        try:
            await self._resource_registry.agent_group().add_agent_group(card.id, agent_group)
            return Ok(card)
        except Exception as e:
            return Error(e)

    async def remove_agent_group(self,
                                 *,
                                 agent_group_id: Optional[Union[str, list[str]]] = None,
                                 tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 skip_if_not_exists: bool = False,
                                 ) -> Result[Optional[GroupCard], Exception] | list[
        Result[Optional[GroupCard], Exception]]:
        """
        Remove agent group(s) by ID or tag.

        Args:
            agent_group_id: Single ID or list of IDs of agent groups to remove.
                Cannot be used together with tag parameter.
            tag: Single tag or list of tags; removes all agent groups with matching tags.
                Cannot be used together with id parameter.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
                ALL - Resource must have all specified tags.
                ANY - Resource must have at least one of the specified tags.
            skip_if_not_exists: If True, silently skip non-existent resources.
                If False, raise ResourceNotFoundError for non-existent resources.

        Returns:
            Result[Optional[GroupCard], Exception] or list[Result[Optional[GroupCard], Exception]]:
                Result object(s) containing the removed group card(s) or exception.
        """
        try:
            await self._resource_registry.agent_group().remove_agent_group(agent_group_id=agent_group_id)
            return Ok(GroupCard(id=agent_group_id))
        except Exception as e:
            return Error(e)

    async def get_agent_group(self,
                              *,
                              agent_group_id: str = None,
                              tag: Optional[Tag] = None,
                              tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                              session: Optional[Session] = None
                              ) -> Optional[BaseGroup]:
        """
        Get an agent group instance by ID or tag.

        Args:
            agent_group_id: Unique identifier of the agent group. Either id or tag must be provided.
            tag: Optional tag for filtering when id is provided,
                 or main lookup criteria when id is not provided.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the agent group.
                If provided, the agent group will be initialized with this session.

        Returns:
            BaseGroup instance if found, None otherwise.

        Raises:
            ValueError: When neither id nor tag is provided.
        """
        return self._resource_registry.agent_group().get_agent_group(agent_group_id=agent_group_id, session=session)

    def add_agent(self,
                  card: AgentCard,
                  agent: AgentProvider | RemoteAgent,
                  *,
                  tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                  tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                  ) -> Result[AgentCard, Exception]:
        """
        Add a single agent to the resource manager.

        Args:
            card: The agent's metadata card containing configuration and identification.
            agent: Callable provider that creates or returns the agent instance.
            tag: Optional tag(s) for categorizing and filtering the agent.
            tag_update_strategy: Strategy for updating tags when agent already exists.

        Returns:
            Result[AgentCard, Exception]: Result object containing the added agent card or an exception.

        """
        try:
            self._resource_registry.agent().add_agent(agent_id=card.id, agent=agent)
            return Ok(card)
        except Exception as e:
            return Error(e)

    def add_agents(self,
                   agents: list[Tuple[AgentCard, AgentProvider]],
                   *,
                   tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                   tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                   ) -> Result[AgentCard, Exception] | list[Result[AgentCard, Exception]]:
        """
        Add multiple agents in bulk.

        Args:
            agents: List of tuples, each containing (AgentCard, AgentProvider).
            tag: Optional tag(s) to apply to all agents being added.
                Applied in addition to any tags on individual AgentCards.
            tag_update_strategy: Strategy for updating tags when agents already exist.

        Returns:
            Result[AgentCard, Exception] or list[Result[AgentCard, Exception]]:
                Result object(s) containing the added agent card(s) or exception(s).

        """
        results = []
        for card, agent in agents:
            results.append(self.add_agent(card, agent, tag=tag, tag_update_strategy=tag_update_strategy))
        return results

    def remove_agent(self,
                     *,
                     agent_id: Union[str, list[str]] = None,
                     tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_not_exists: bool = False,
                     ) -> Result[Optional[AgentCard], Exception] | list[Result[Optional[AgentCard], Exception]]:
        """
        Remove agent(s) by ID or tag.

        Args:
            agent_id: Single ID or list of IDs of agents to remove.
            tag: Single tag or list of tags; removes all agents with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent resources.

        Returns:
            Result[Optional[AgentCard], Exception] or list[Result[Optional[AgentCard], Exception]]:
                Result object(s) containing the removed agent card(s) or exception(s).

        """
        if isinstance(agent_id, str):
            try:
                self._resource_registry.agent().remove_agent(agent_id)
                return Ok(AgentCard(id=agent_id))
            except Exception as e:
                return Error(e)
        else:
            result = []
            for agent_id in agent_id:
                try:
                    self._resource_registry.agent().remove_agent(agent_id)
                    result.append(Ok(AgentCard(id=agent_id)))
                except Exception as e:
                    return Error(e)
            return result

    async def get_agent(self,
                        *,
                        agent_id: Union[str, list[str]] = None,
                        tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        session: Optional[Session] = None
                        ) -> Optional[BaseAgent] | list[Optional[BaseAgent]]:
        """
        Get agent instance(s) by ID or tag.

        Args:
            agent_id: Single ID or list of IDs of agents to retrieve.
            tag: Single tag or list of tags; returns all agents with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the agents.

        Returns:
            BaseAgent or list[BaseAgent]: Agent instance(s) if found, None otherwise.

        """
        if isinstance(agent_id, str):
            return self._resource_registry.agent().get_agent(agent_id=agent_id)
        results = []
        for agent_id in agent_id:
            results.append(self._resource_registry.agent().get_agent(agent_id=agent_id))
        return results

    def add_workflow(self,
                     card: WorkflowCard,
                     workflow: WorkflowProvider,
                     *,
                     tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                     tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                     ) -> Result[WorkflowCard, Exception]:
        """
        Add a single workflow to the resource manager.

        Args:
            card: The workflow's metadata card containing configuration and identification.
            workflow: Callable provider that creates or returns the workflow instance.
            tag: Optional tag(s) for categorizing and filtering the workflow.
            tag_update_strategy: Strategy for updating tags when workflow already exists.

        Returns:
            Result[WorkflowCard, Exception]: Result object containing the added workflow card or an exception.

        """
        try:
            self._resource_registry.workflow().add_workflow(workflow_id=card.id, workflow=workflow)
            return Ok(card)
        except Exception as e:
            return Error(e)

    def add_workflows(self,
                      workflows: list[Tuple[WorkflowCard, WorkflowProvider]],
                      *,
                      tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                      tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                      ) -> Result[WorkflowCard, Exception] | list[Result[WorkflowCard, Exception]]:
        """
        Add multiple workflows in bulk.

        Args:
            workflows: List of tuples, each containing (WorkflowCard, WorkflowProvider).
            tag: Optional tag(s) to apply to all workflows being added.
            tag_update_strategy: Strategy for updating tags when workflows already exist.

        Returns:
            Result[WorkflowCard, Exception] or list[Result[WorkflowCard, Exception]]:
                Result object(s) containing the added workflow card(s) or exception(s).

        """
        results = []
        for card, workflow in workflows:
            results.append(self.add_workflow(card, workflow, tag=tag, tag_update_strategy=tag_update_strategy))
        return results

    def remove_workflow(self,
                        *,
                        workflow_id: Union[str, list[str]] = None,
                        tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        skip_if_not_exists: bool = False,
                        ) -> Result[Optional[WorkflowCard], Exception] | list[
        Result[Optional[WorkflowCard], Exception]]:
        """
        Remove workflow(s) by ID or tag.

        Args:
            workflow_id: Single ID or list of IDs of workflows to remove.
            tag: Single tag or list of tags; removes all workflows with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent workflows.

        Returns:
            Result[Optional[WorkflowCard], Exception] or list[Result[Optional[WorkflowCard], Exception]]:
                Result object(s) containing the removed workflow card(s) or exception(s).

        """
        if isinstance(workflow_id, str):
            try:
                self._resource_registry.workflow().remove_workflow(workflow_id=workflow_id)
                return Ok(WorkflowCard(id=workflow_id))
            except Exception as e:
                return Error(e)
        else:
            results = []
            for workflow_id in workflow_id:
                try:
                    self._resource_registry.workflow().remove_workflow(workflow_id=workflow_id)
                    results.append(Ok(WorkflowCard(id=workflow_id)))
                except Exception as e:
                    results.append(Error(e))
            return results

    async def get_workflow(self,
                           *,
                           workflow_id: Union[str, list[str]] = None,
                           tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                           tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                           session: Optional[Session] = None
                           ) -> Optional[Workflow] | list[Optional[Workflow]]:
        """
        Get workflow instance(s) by ID or tag.

        Args:
            workflow_id: Single ID or list of IDs of workflows to retrieve.
            tag: Single tag or list of tags; returns all workflows with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the workflows.

        Returns:
            Workflow or list[Workflow]: Workflow instance(s) if found, None otherwise.
        """
        if isinstance(workflow_id, str):
            return await self._resource_registry.workflow().get_workflow(workflow_id=workflow_id, session=session)
        else:
            results = []
            for workflow_id in workflow_id:
                results.append(
                    await self._resource_registry.workflow().get_workflow(workflow_id=workflow_id, session=session))
            return results

    def add_tool(self,
                 tool: Union[Tool, list[Tool]],
                 *,
                 tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                 tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                 ) -> Result[ToolCard, Exception] | list[Result[ToolCard, Exception]]:
        """
        Add tool(s) to the resource manager.

        Args:
            tool: Single Tool instance or list of Tool instances to add.
            tag: Optional tag(s) for categorizing and filtering the tool(s).
            tag_update_strategy: Strategy for updating tags when tools already exist.

        Returns:
            Result[ToolCard, Exception] or list[Result[ToolCard, Exception]]:
                Result object(s) containing the added tool card(s) or exception(s).
        """
        if isinstance(tool, Tool):
            try:
                self._resource_registry.tool().add_tool(tool.card.id, tool)
                return Ok(tool.card)
            except Exception as e:
                return Error(e)
        else:
            results = []
            for item in tool:
                try:
                    self._resource_registry.tool().add_tool(item.card.id, item)
                    results.append(Ok(item.card))
                except Exception as e:
                    results.append(Error(e))
            return results

    def get_tool(self,
                 *,
                 tool_id: Union[str, list[str]] = None,
                 tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                 session: Optional[Session] = None
                 ) -> Optional[Tool] | list[Optional[Tool]]:
        """
        Get tool(s) by ID or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to retrieve.
            tag: Single tag or list of tags; returns all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the tools.

        Returns:
            Tool or list[Tool]: Tool instance(s) if found, None otherwise.
        """
        if isinstance(tool_id, str):
            return self._resource_registry.tool().get_tool(tool_id=tool_id, session=session)
        else:
            results = []
            for tool_id in tool_id:
                results.append(self._resource_registry.tool().get_tool(tool_id=tool_id, session=session))
            return results

    def remove_tool(self,
                    *,
                    tool_id: Union[str, list[str]] = None,
                    tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    skip_if_not_exists: bool = False,
                    ) -> Result[Optional[ToolCard], Exception] | list[Result[Optional[ToolCard], Exception]]:
        """
        Remove tool(s) by ID or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to remove.
            tag: Single tag or list of tags; removes all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent tools.

        Returns:
            Result[Optional[ToolCard], Exception] or list[Result[Optional[ToolCard], Exception]]:
                Result object(s) containing the removed tool card(s) or exception(s).
        """
        if isinstance(tool_id, str):
            try:
                self._resource_registry.tool().remove_tool(tool_id=tool_id)
                return Ok(ToolCard(id=tool_id))
            except Exception as e:
                return Error(e)
        else:
            results = []
            for tool_id in tool_id:
                try:
                    self._resource_registry.tool().remove_tool(tool_id=tool_id)
                    results.append(Ok(ToolCard(id=tool_id)))
                except Exception as e:
                    results.append(Error(e))
            return results

    def add_model(self,
                  model_id: str,
                  model: ModelProvider,
                  *,
                  tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                  tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                  ) -> Result[str, Exception]:
        """
        Add a model to the resource manager.

        Args:
            model_id: Unique identifier for the model.
            model: Callable provider that creates or returns the model instance.
            tag: Optional tag(s) for categorizing and filtering the model.
            tag_update_strategy: Strategy for updating tags when model already exists.

        Returns:
            Result[str, Exception]: Result object containing the model ID or an exception.

        """
        try:
            self._resource_registry.model().add_model(model_id=model_id, model=model)
            return Ok(model_id)
        except Exception as e:
            return Error(e)

    def add_models(self,
                   models: list[Tuple[str, ModelProvider]],
                   *,
                   tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                   tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                   ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple models in bulk.

        Args:
            models: List of tuples, each containing (model_id, ModelProvider).
            tag: Optional tag(s) to apply to all models being added.
            tag_update_strategy: Strategy for updating tags when models already exist.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        results = []
        for id, model in models:
            results.append(self.add_model(model_id=id, model=model, tag=tag, tag_update_strategy=tag_update_strategy))
        return results

    def remove_model(self,
                     *,
                     model_id: Union[str, list[str]] = None,
                     tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_not_exists: bool = False,
                     ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove model(s) by ID or tag.

        Args:
            model_id: Single ID or list of IDs of models to remove.
            tag: Single tag or list of tags; removes all models with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent models.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        if isinstance(model_id, str):
            try:
                self._resource_registry.model().remove_model(model_id=model_id)
                return Ok(model_id)
            except Exception as e:
                return Error(e)
        else:
            results = []
            for model_id in model_id:
                try:
                    self._resource_registry.model().remove_model(model_id=model_id)
                    results.append(Ok(model_id))
                except Exception as e:
                    results.append(Error(e))
            return results

    async def get_model(self,
                        *,
                        model_id: Union[str, list[str]] = None,
                        tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        session: Optional[Session] = None) \
            -> Optional[BaseModel] | list[Optional[BaseModel]]:
        """
        Get model instance(s) by ID or tag.

        Args:
            model_id: Single ID or list of IDs of models to retrieve.
            tag: Single tag or list of tags; returns all models with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the models.

        Returns:
            Model or list[Model]: Model instance(s) if found, None otherwise.

        """
        if isinstance(model_id, str):
            return self._resource_registry.model().get_model(model_id=model_id, session=session)
        else:
            results = []
            for model_id in model_id:
                results.append(self._resource_registry.model().get_model(model_id=model_id, session=session))
            return results

    def add_prompt(self,
                   prompt_id: str,
                   template: "PromptTemplate",
                   *,
                   tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                   tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                   ) -> Result[str, Exception]:
        """
        Add a prompt template to the resource manager.

        Args:
            prompt_id: Unique identifier for the prompt template.
            template: PromptTemplate instance containing the prompt content and configuration.
            tag: Optional tag(s) for categorizing and filtering the prompt.
            tag_update_strategy: Strategy for updating tags when prompt already exists.

        Returns:
            Result[str, Exception]: Result object containing the prompt ID or an exception.
        """
        try:
            self._resource_registry.prompt().add_prompt(template_id=prompt_id, template=template)
            return Ok(prompt_id)
        except Exception as e:
            return Error(e)

    def add_prompts(self,
                    prompts: list[Tuple[str, "PromptTemplate"]],
                    *,
                    tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                    tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE
                    ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple prompt templates in bulk.

        Args:
            prompts: List of tuples, each containing (prompt_id, PromptTemplate).
            tag: Optional tag(s) to apply to all prompts being added.
            tag_update_strategy: Strategy for updating tags when prompts already exist.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).

        """
        result = []
        for prompt_id, prompt in prompts:
            result.append(self.add_prompt(prompt_id, prompt, tag=tag, tag_update_strategy=tag_update_strategy))
        return result

    def remove_prompt(self,
                      *,
                      prompt_id: Union[str, list[str]] = None,
                      tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                      tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                      skip_if_not_exists: bool = False,
                      ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to remove.
            tag: Single tag or list of tags; removes all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent prompts.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).
        """
        if isinstance(prompt_id, str):
            try:
                self._resource_registry.prompt().remove_prompt(template_id=prompt_id)
                return Ok(prompt_id)
            except Exception as e:
                return Error(e)
        else:
            results = []
            for template_id in prompt_id:
                try:
                    self._resource_registry.prompt().remove_prompt(template_id=template_id)
                    results.append(Ok(prompt_id))
                except Exception as e:
                    results.append(Error(e))
            return results

    def get_prompt(self,
                   *,
                   prompt_id: Union[str, list[str]] = None,
                   tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                   tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                   ) -> Optional["PromptTemplate"] | list[Optional["PromptTemplate"]]:
        """
        Get prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to retrieve.
            tag: Single tag or list of tags; returns all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.

        Returns:
            PromptTemplate or list[PromptTemplate]: Prompt template instance(s) if found, None otherwise.
        """
        if isinstance(prompt_id, str):
            return self._resource_registry.prompt().get_prompt(template_id=prompt_id)
        else:
            results = []
            for template_id in prompt_id:
                results.append(self._resource_registry.prompt().get_prompt(template_id=template_id))
            return results

    async def get_tool_infos(self,
                             *,
                             tool_id: Union[str, list[str]] = None,
                             tool_type: Union[str, list[str]] = None,
                             tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             ignore_exception: bool = False,
                             ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get tool information/metadata by ID, type, or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to get info for.
            tool_type: Single type or list of types to filter tools by.
                Common types: ["function", "mcp", "workflow"].
            tag: Single tag or list of tags; returns info for all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions and return None for failing items.

        Returns:
            ToolInfo or list[ToolInfo]: Tool information instance(s) if found, None otherwise.
        """
        if isinstance(tool_id, str):
            tool_info = self._resource_registry.tool().get_tool_infos(tool_ids=tool_id)
            if not tool_info:
                tool_info = self._resource_registry.workflow().get_tool_infos(workflow_ids=tool_id)
            return tool_info
        elif isinstance(tool_id, list):
            results = []
            for tool_id in tool_id:
                tool_info = self._resource_registry.tool().get_tool_infos(tool_ids=tool_id)
                if not tool_info:
                    tool_info = self._resource_registry.workflow().get_tool_infos(workflow_ids=tool_id)
                results.append(tool_info)
            return results
        else:
            tool_infos = self._resource_registry.tool().get_tool_infos()
            tool_infos.append(self._resource_registry.workflow().get_tool_infos())
            return tool_infos

    async def add_mcp_server(self,
                             server_config: Union[McpServerConfig, list[McpServerConfig]],
                             *,
                             tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                             tag_update_strategy: TagUpdateStrategy = TagUpdateStrategy.MERGE,
                             expiry_time: Optional[float] = None,
                             ignore_exception: bool = False
                             ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add MCP (Model Context Protocol) server configuration(s).

        Args:
            server_config: Single or list of McpServerConfig instances.
            tag: Optional tag(s) for categorizing the server(s).
            tag_update_strategy: Strategy for updating tags when servers already exist.
            expiry_time: Optional Unix timestamp when the server configuration expires.
                If None, the configuration does not expire.
            ignore_exception: If True, continue adding other servers if one fails.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        results = await self._resource_registry.tool().add_tool_servers(server_config)
        for config, result in zip(server_config, results):
            if result:
                return Ok(config.server_name)
            else:
                return Error(JiuWenBaseException(-1, f"add mcp server {config.server_name} failed"))

    async def refresh_mcp_server(self,
                                 server_name: Union[str, list[str]],
                                 *,
                                 tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 ignore_exception: bool = False,
                                 skip_if_not_exists: bool = False,
                                 ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Refresh MCP server toolinfos(s) by name.

        Args:
            server_name: Single or list of MCP server names to refresh.
            tag: Optional tag to filter servers to refresh.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, continue refreshing other servers if one fails.
            skip_if_not_exists: If True, skip non-existent servers.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        pass

    async def remove_mcp_server(self,
                                *,
                                server_name: Optional[Union[str, list[str]]] = None,
                                tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                skip_if_not_exists: bool = False,
                                ignore_exception: bool = False,
                                ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove MCP server(s) by name or tag.

        Args:
            server_name: Single or list of MCP server names to remove.
            tag: Single tag or list of tags; removes all servers with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_not_exists: If True, skip non-existent servers.
            ignore_exception: If True, continue removing other servers if one fails.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        results = await self._resource_registry.tool().remove_tool_server(server_name)
        for name, result in zip(server_name, results):
            if result:
                return Ok(name)
            else:
                return Error(JiuWenBaseException(-1, f"add mcp server {name} failed"))

    async def get_mcp_tool(self,
                           *,
                           name: Union[str, list[str]],
                           server_name: Union[str, list[str]],
                           tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                           tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                           session: Optional[Session] = None
                           ) -> Optional[Tool] | list[Optional[Tool]]:
        """
        Get MCP tool(s) by name and server.

        Args:
            name: Single or list of MCP tool names to retrieve.
            server_name: Single or list of MCP server names containing the tools.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            session: Optional session context for the tools.

        Returns:
            Tool or list[Tool]: MCP tool instance(s) if found, None otherwise.
        """
        pass

    async def get_mcp_tool_infos(self,
                                 *,
                                 name: Union[str, list[str]] = None,
                                 server_name: Union[str, list[str]] = None,
                                 tag: Optional[Union[Tag, list[Tag]]] = GLOBAL,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 ignore_exception: bool = False,
                                 ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get MCP tool information/metadata by name and server.

        Args:
            name: Single or list of MCP tool names to get info for.
                If None, returns info for all tools in specified servers.
            server_name: Single or list of MCP server names containing the tools.
                Must be provided if name is None.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions for individual items.

        Returns:
            ToolInfo or list[ToolInfo]: MCP tool information instance(s) if found, None otherwise.
        """
        if not server_name:
            return None
        tool_mgr = self._resource_registry.tool()
        single = isinstance(server_name, str)
        names = [server_name] if single else server_name
        results = [tool_mgr.get_tool_infos(tool_server_name=n) for n in names]
        return results[0] if single else results

    def get_resource_by_tag(self,
                            tag: Tag) -> Optional[list["BaseCard"]]:
        """
        Retrieve all resources associated with a specific tag.

        Args:
            tag: Tag to search for.

        Returns:
            List of BaseCard instances representing resources with the specified tag,
            or None if no resources found.
        """
        pass

    def list_tags(self) -> list[Tag]:
        """
        List all tags currently in use across all resources.

        Returns:
            List of unique tag strings.
        """
        pass

    def has_tag(self, tag: str) -> bool:
        """
            Check if the specified tag exists in the resource_mgr.
        """
        pass

    async def remove_tag(self,
                         tag: Union[Tag, list[Tag]] = None,
                         ignore_exception: bool = False,
                         *,
                         ignore_if_not_exists: bool = False,
                         ) -> Result[Tag, Exception] | list[Result[Tag, Exception]]:
        """
        Remove tag(s) from all resources.

        Args:
            tag: Single tag or list of tags to remove from all resources.
            ignore_exception: If True, ignore exceptions during removal.
            ignore_if_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[Tag, Exception] or list[Result[Tag, Exception]]:
                Result object(s) containing the tag(s) or exception(s).
        """
        pass

    def update_resource_tag(self,
                            resource_id: str,
                            tag: Union[Tag, list[Tag]]
                            ) -> Result[list[Tag], Exception]:
        """
        Replace all tags on a resource with new tag(s).

        Args:
            resource_id: Resource identifier.
            tag: New tag(s) to set on the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing the new tag list or an exception.

        """
        pass

    def add_resource_tag(self,
                         resource_id: str,
                         tag: Union[Tag, list[Tag]]
                         ) -> Result[list[Tag], Exception]:
        """
        Add tag(s) to a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to add to the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing all tags now associated with the resource.
        """
        pass

    def remove_resource_tag(self,
                            resource_id: str,
                            tag: Union[Tag, list[Tag]],
                            *,
                            ignore_if_not_exists: bool = False
                            ) -> Result[list[Tag], Exception]:
        """
        Remove specific tag(s) from a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to remove from the resource.
            ignore_if_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[list[Tag], Exception]: Result object containing remaining tags on the resource.
        """
        pass

    def get_resource_tag(self, resource_id: str) -> Optional[list[Tag]]:
        """
        Get all tags associated with a resource.

        Args:
            resource_id: Resource identifier.

        Returns:
            List of tags associated with the resource, or None if resource not found.
        """
        pass

    def resource_has_tag(self, resource_id: str, tag: str) -> bool:
        """
        Check if a specific resource is associated with the given tag.

        Args:
            resource_id: The unique identifier of the resource to check.
            tag: The tag to verify association with the resource.
        Returns:
            True if the resource has the specified tag, False otherwise.
        """
        pass

    async def release(self):
        await self._resource_registry.tool().release()
