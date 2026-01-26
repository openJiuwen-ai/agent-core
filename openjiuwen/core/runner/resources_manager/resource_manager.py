# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Callable, Optional, Tuple, List, Union
from pydantic import BaseModel

from openjiuwen.core.common import BaseCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.tool import Tool, ToolInfo, ToolCard
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

from openjiuwen.core.runner.resources_manager.tag_manager import TagMgr
from openjiuwen.core.session import Session
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard, SysOperation
from openjiuwen.core.workflow.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard


class ResourceMgr:
    """
    Resource Manager for Model, Workflow, Prompt, Tool
    """

    def __init__(self, ) -> None:
        self._resource_registry = ResourceRegistry()
        self._tag_mgr = TagMgr()
        self._id_to_card: dict[str, BaseCard] = {}

    async def add_agent_group(self,
                              card: GroupCard,
                              agent_group: AgentGroupProvider,
                              *,
                              tag: Optional[Tag | list[Tag]] = None,
                              ) -> Result[GroupCard, Exception]:
        """
        Add a single agent group to the resource manager.

        Args:
            card: The agent group's metadata card containing configuration and identification.
            agent_group: Callable provider that creates or returns the agent group instance.
            tag: Optional tag(s) for categorizing and filtering the agent group.
                 If None, no tags will be added or updated.

        Returns:
            Result[GroupCard, Exception]: Result object containing the added group card or an exception.
        """
        self._inner_validate_resource_card(card)
        self._inner_validate_provider(card, agent_group)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id,
                                        resource=agent_group,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="group")

    async def remove_agent_group(self,
                                 *,
                                 group_id: Optional[str | list[str]] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 skip_if_tag_not_exists: bool = False,
                                 ) -> Result[Optional[GroupCard], Exception] | list[
        Result[Optional[GroupCard], Exception]]:
        """
        Remove agent group(s) by ID or tag.

        Args:
            group_id: Single ID or list of IDs of agent groups to remove.
                Cannot be used together with tag parameter.
            tag: Single tag or list of tags; removes all agent groups with matching tags.
                Cannot be used together with id parameter.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
                ALL - Resource must have all specified tags.
                ANY - Resource must have at least one of the specified tags.
            skip_if_tag_not_exists: If True, silently skip non-existent resources.
        Returns:
            Result[Optional[GroupCard], Exception] or list[Result[Optional[GroupCard], Exception]]:
                Result object(s) containing the removed group card(s) or exception.
        """
        return self._inner_remove_resources(resource_id=group_id,
                                            tag=tag,
                                            tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists,
                                            resource_type="group")

    async def get_agent_group(self,
                              group_id: str = None,
                              *,
                              tag: Optional[Tag | list[Tag]] = None,
                              tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                              session: Optional[Session] = None
                              ) -> Optional[BaseGroup] | list[Optional[BaseGroup]]:
        """
        Get an agent group instance by ID or tag.

        Args:
            group_id: Unique identifier of the agent group. Either id or tag must be provided.
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
        return await self._inner_get_resources_by_provider(resource_id=group_id,
                                                           tag=tag,
                                                           tag_match_strategy=tag_match_strategy,
                                                           session=session,
                                                           resource_type="group")

    def add_agent(self,
                  card: AgentCard,
                  agent: AgentProvider | RemoteAgent,
                  *,
                  tag: Optional[Tag | list[Tag]] = None
                  ) -> Result[AgentCard, Exception]:
        """
        Add a single agent to the resource manager.

        Args:
            card: The agent's metadata card containing configuration and identification.
            agent: Callable provider that creates or returns the agent instance.
            tag: Optional tag(s) for categorizing and filtering the agent.

        Returns:
            Result[AgentCard, Exception]: Result object containing the added agent card or an exception.
        """
        self._inner_validate_resource_card(card)
        if not isinstance(agent, RemoteAgent):
            self._inner_validate_provider(card, agent)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id if card else None,
                                        resource=agent,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="agent")

    def add_agents(self,
                   agents: list[Tuple[AgentCard, AgentProvider]],
                   *,
                   tag: Optional[Tag | list[Tag]] = None
                   ) -> Result[AgentCard, Exception] | list[Result[AgentCard, Exception]]:
        """
        Add multiple agents in bulk.

        Args:
            agents: List of tuples, each containing (AgentCard, AgentProvider).
            tag: Optional tag(s) to apply to all agents being added.
                Applied in addition to any tags on individual AgentCards.

        Returns:
            Result[AgentCard, Exception] or list[Result[AgentCard, Exception]]:
                Result object(s) containing the added agent card(s) or exception(s).
        """
        if not agents:
            raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID, reason="resource list is None or empty")
        for card, agent in agents:
            self._inner_validate_resource_card(card)
            if not isinstance(agent, RemoteAgent):
                self._inner_validate_provider(card, agent)
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for card, agent in agents:
            results.append(self._inner_add_resource(resource_id=card.id if card else None,
                                                    resource=agent,
                                                    resource_card=card,
                                                    tag=tag,
                                                    resource_type="agent"))
        return results

    def remove_agent(self,
                     agent_id: str | list[str] = None,
                     *,
                     tag: Optional[Tag | list[Tag]] = GLOBAL,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_tag_not_exists: bool = False,
                     ) -> Result[Optional[AgentCard], Exception] | list[Result[Optional[AgentCard], Exception]]:
        """
        Remove agent(s) by ID or tag.

        Args:
            agent_id: Single ID or list of IDs of agents to remove.
            tag: Single tag or list of tags; removes all agents with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent resources.

        Returns:
            Result[Optional[AgentCard], Exception] or list[Result[Optional[AgentCard], Exception]]:
                Result object(s) containing the removed agent card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=agent_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="agent")

    async def get_agent(self,
                        agent_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
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
        return await self._inner_get_resources_by_provider(resource_id=agent_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="agent")

    def add_workflow(self,
                     card: WorkflowCard,
                     workflow: WorkflowProvider,
                     *,
                     tag: Optional[Tag | list[Tag]] = None
                     ) -> Result[WorkflowCard, Exception]:
        """
        Add a single workflow to the resource manager.

        Args:
            card: The workflow's metadata card containing configuration and identification.
            workflow: Callable provider that creates or returns the workflow instance.
            tag: Optional tag(s) for categorizing and filtering the workflow.

        Returns:
            Result[WorkflowCard, Exception]: Result object containing the added workflow card or an exception.
        """
        self._inner_validate_resource_card(card)
        self._inner_validate_provider(card, workflow)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id if card else None,
                                        resource=workflow,
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="workflow")

    def add_workflows(self,
                      workflows: list[Tuple[WorkflowCard, WorkflowProvider]],
                      *,
                      tag: Optional[Tag | list[Tag]] = None
                      ) -> Result[WorkflowCard, Exception] | list[Result[WorkflowCard, Exception]]:
        """
        Add multiple workflows in bulk.

        Args:
            workflows: List of tuples, each containing (WorkflowCard, WorkflowProvider).
            tag: Optional tag(s) to apply to all workflows being added.

        Returns:
            Result[WorkflowCard, Exception] or list[Result[WorkflowCard, Exception]]:
                Result object(s) containing the added workflow card(s) or exception(s).
        """
        if not workflows:
            raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID, reason="resource list is None or empty")
        for card, workflow in workflows:
            self._inner_validate_resource_card(card)
            self._inner_validate_provider(card, workflow)
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for card, workflow in workflows:
            results.append(self._inner_add_resource(resource_id=card.id if card else None,
                                                    resource=workflow,
                                                    resource_card=card,
                                                    tag=tag,
                                                    resource_type="workflow"))
        return results

    def remove_workflow(self,
                        workflow_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
                        tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                        skip_if_tag_not_exists: bool = False,
                        ) -> Result[Optional[WorkflowCard], Exception] | list[
        Result[Optional[WorkflowCard], Exception]]:
        """
        Remove workflow(s) by ID or tag.

        Args:
            workflow_id: Single ID or list of IDs of workflows to remove.
            tag: Single tag or list of tags; removes all workflows with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent workflows.

        Returns:
            Result[Optional[WorkflowCard], Exception] or list[Result[Optional[WorkflowCard], Exception]]:
                Result object(s) containing the removed workflow card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=workflow_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="workflow")

    async def get_workflow(self,
                           workflow_id: str | list[str] = None,
                           *,
                           tag: Optional[Tag | list[Tag]] = None,
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
        return await self._inner_get_resources_by_provider(resource_id=workflow_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="workflow")

    def add_tool(self,
                 tool: Tool | list[Tool],
                 *,
                 tag: Optional[Tag | list[Tag]] = None
                 ) -> Result[ToolCard, Exception] | list[Result[ToolCard, Exception]]:
        """
        Add tool(s) to the resource manager.

        Args:
            tool: Single Tool instance or list of Tool instances to add.
            tag: Optional tag(s) for categorizing and filtering the tool(s).

        Returns:
            Result[ToolCard, Exception] or list[Result[ToolCard, Exception]]:
                Result object(s) containing the added tool card(s) or exception(s).
        """
        if not tool:
            raise build_error(StatusCode.RESOURCE_VALUE_INVALID, reason="resource(s) is None or empty")
        if isinstance(tool, list):
            for item in tool:
                if not item:
                    raise build_error(StatusCode.RESOURCE_VALUE_INVALID, reason="tool item is None or empty")
        if tag is not None:
            self._inner_validate_tag(tag)
        if isinstance(tool, Tool):
            return self._inner_add_resource(resource_id=tool.card.id if tool.card else None,
                                            resource=tool,
                                            resource_card=tool.card,
                                            tag=tag,
                                            resource_type="tool")
        results = []
        for item in tool:
            results.append(self._inner_add_resource(resource_id=item.card.id,
                                                    resource=item,
                                                    resource_card=item.card,
                                                    tag=tag,
                                                    resource_type="tool"))
        return results

    def get_tool(self,
                 tool_id: str | list[str] = None,
                 *,
                 tag: Optional[Tag | list[Tag]] = None,
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
        return self._inner_get_resources(resource_id=tool_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                         session=session, resource_type="tool")

    def remove_tool(self,
                    tool_id: str | list[str] = None,
                    *,
                    tag: Optional[Tag | list[Tag]] = None,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    skip_if_tag_not_exists: bool = False,
                    ) -> Result[Optional[ToolCard], Exception] | list[Result[Optional[ToolCard], Exception]]:
        """
        Remove tool(s) by ID or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to remove.
            tag: Single tag or list of tags; removes all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent tag.

        Returns:
            Result[Optional[ToolCard], Exception] or list[Result[Optional[ToolCard], Exception]]:
                Result object(s) containing the removed tool card(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=tool_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="tool")

    def add_model(self,
                  model_id: str,
                  model: ModelProvider,
                  *,
                  tag: Optional[Tag | list[Tag]] = None
                  ) -> Result[str, Exception]:
        """
        Add a model to the resource manager.

        Args:
            model_id: Unique identifier for the model.
            model: Callable provider that creates or returns the model instance.
            tag: Optional tag(s) for categorizing and filtering the model.

        Returns:
            Result[str, Exception]: Result object containing the model ID or an exception.
        """
        self._inner_validate_resource_id(model_id)
        self._inner_validate_provider(model_id, model)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=model_id,
                                        resource=model,
                                        tag=tag,
                                        resource_type="model")

    def add_models(self,
                   models: list[Tuple[str, ModelProvider]],
                   *,
                   tag: Optional[Tag | list[Tag]] = None
                   ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple models in bulk.

        Args:
            models: List of tuples, each containing (model_id, ModelProvider).
            tag: Optional tag(s) to apply to all models being added.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        if not models:
            raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID, reason="resource(s) is None or empty")

        for model_id, model in models:
            self._inner_validate_resource_id(model_id)
            self._inner_validate_provider(model_id, model)
        if tag is not None:
            self._inner_validate_tag(tag)
        results = []
        for model_id, model in models:
            results.append(self._inner_add_resource(resource_id=model_id,
                                                    resource=model,
                                                    tag=tag,
                                                    resource_type="model"))
        return results

    def remove_model(self,
                     *,
                     model_id: str | list[str] = None,
                     tag: Optional[Tag | list[Tag]] = None,
                     tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                     skip_if_tag_not_exists: bool = False,
                     ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove model(s) by ID or tag.

        Args:
            model_id: Single ID or list of IDs of models to remove.
            tag: Single tag or list of tags; removes all models with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent models.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the model ID(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=model_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists, resource_type="model")

    async def get_model(self,
                        model_id: str | list[str] = None,
                        *,
                        tag: Optional[Tag | list[Tag]] = None,
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
        return await self._inner_get_resources_by_provider(resource_id=model_id, tag=tag,
                                                           tag_match_strategy=tag_match_strategy, session=session,
                                                           resource_type="model")

    def add_prompt(self,
                   prompt_id: str,
                   template: PromptTemplate,
                   *,
                   tag: Optional[Tag | list[Tag]] = None,
                   ) -> Result[str, Exception]:
        """
        Add a prompt template to the resource manager.

        Args:
            prompt_id: Unique identifier for the prompt template.
            template: PromptTemplate instance containing the prompt content and configuration.
            tag: Optional tag(s) for categorizing and filtering the prompt.

        Returns:
            Result[str, Exception]: Result object containing the prompt ID or an exception.
        """
        self._inner_validate_resource_id(prompt_id)
        if not template:
            raise build_error(StatusCode.RESOURCE_VALUE_INVALID, card=prompt_id, reason="resource is None")
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=prompt_id, resource=template, tag=tag, resource_type="prompt")

    def add_prompts(self,
                    prompts: list[Tuple[str, PromptTemplate]],
                    *,
                    tag: Optional[Tag | list[Tag]] = None
                    ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add multiple prompt templates in bulk.

        Args:
            prompts: List of tuples, each containing (prompt_id, PromptTemplate).
            tag: Optional tag(s) to apply to all prompts being added.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).
        """
        if not prompts:
            raise build_error(StatusCode.RESOURCE_VALUE_INVALID, reason="resource(s) is None or empty")
        for prompt_id, prompt in prompts:
            self._inner_validate_resource_id(prompt_id)
            if not prompt:
                raise build_error(StatusCode.RESOURCE_VALUE_INVALID, reason="resource is None")
        if tag is not None:
            self._inner_validate_tag(tag)
        result = []
        for prompt_id, prompt in prompts:
            result.append(
                self._inner_add_resource(resource_id=prompt_id, resource=prompt, tag=tag, resource_type="prompt"))
        return result

    def remove_prompt(self,
                      prompt_id: str | list[str] = None,
                      *,
                      tag: Optional[Tag | list[Tag]] = None,
                      tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                      skip_if_tag_not_exists: bool = False,
                      ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to remove.
            tag: Single tag or list of tags; removes all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent prompts.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the prompt ID(s) or exception(s).
        """
        return self._inner_remove_resources(resource_id=prompt_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists,
                                            resource_type="prompt")

    def get_prompt(self,
                   prompt_id: str | list[str] = None,
                   *,
                   tag: Optional[Tag | list[Tag]] = None,
                   tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                   ) -> Optional[PromptTemplate] | list[Optional[PromptTemplate]]:
        """
        Get prompt template(s) by ID or tag.

        Args:
            prompt_id: Single ID or list of IDs of prompts to retrieve.
            tag: Single tag or list of tags; returns all prompts with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.

        Returns:
            PromptTemplate or list[PromptTemplate]: Prompt template instance(s) if found, None otherwise.
        """
        return self._inner_get_resources(resource_id=prompt_id, tag=tag, tag_match_strategy=tag_match_strategy,
                                         resource_type="prompt")

    def add_sys_operation(self,
                          card: SysOperationCard,
                          *,
                          tag: Optional[Tag | List[Tag]] = None
                          ) -> Result[SysOperationCard, Exception]:
        """Add sys operation via SysOperationCard (with optional tags).

        Args:
            card: SysOperationCard with valid `id` (required)
            tag: Optional single/tag list for classification

        Returns:
            Result[SysOperationCard, Exception]: Success card or error
        """
        self._inner_validate_resource_card(card)
        if tag is not None:
            self._inner_validate_tag(tag)
        return self._inner_add_resource(resource_id=card.id,
                                        resource=SysOperation(card),
                                        resource_card=card,
                                        tag=tag,
                                        resource_type="sys_operation")

    def remove_sys_operation(self,
                             *,
                             sys_operation_id: Optional[str | List[str]] = None,
                             tag: Optional[Tag | List[Tag]] = None,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             skip_if_tag_not_exists: bool = False,
                             ) -> Union[Result[Optional[SysOperationCard], Exception],
    List[Result[Optional[SysOperationCard], Exception]]]:
        """Remove sys operation(s) by ID/tag (supports batch).

        Args:
            sys_operation_id: Optional single/ID list to remove
            tag: Optional single/tag list filter (if no ID)
            tag_match_strategy: ALL/ANY for tag matching (default: ALL)
            skip_if_tag_not_exists: Ignore missing tags (default: False)

        Returns:
            Result/Result list: Removed card(s) or error
        """
        return self._inner_remove_resources(resource_id=sys_operation_id,
                                            tag=tag,
                                            tag_match_strategy=tag_match_strategy,
                                            skip_if_tag_not_exists=skip_if_tag_not_exists,
                                            resource_type="sys_operation")

    def get_sys_operation(self,
                          sys_operation_id: Optional[str] = None,
                          *,
                          tag: Optional[Tag | List[Tag]] = None,
                          tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                          session: Optional[Session] = None
                          ) -> Union[Optional[SysOperation], List[Optional[SysOperation]]]:
        """Get sys operation(s) by ID/tag.

        Args:
            sys_operation_id: Optional specific operation ID
            tag: Optional single/tag list filter (if no ID)
            tag_match_strategy: ALL/ANY for tag matching (default: ALL)
            session: Optional context session

        Returns:
            SysOperation/List[SysOperation]: Matching operation(s) or None
        """
        return self._inner_get_resources(resource_id=sys_operation_id,
                                         tag=tag,
                                         tag_match_strategy=tag_match_strategy,
                                         resource_type="sys_operation")

    async def get_tool_infos(self,
                             tool_id: str | list[str] = None,
                             *,
                             tool_type: str | list[str] = None,
                             tag: Optional[Tag | list[Tag]] = None,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             ignore_exception: bool = False,
                             ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get tool information/metadata by ID, type, or tag.

        Args:
            tool_id: Single ID or list of IDs of tools to get info for.
            type: Single type or list of types to filter tools by.
                Common types: ["function", "mcp", "workflow", "agent", "group"].
            tag: Single tag or list of tags; returns info for all tools with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions and return None for failing items.

        Returns:
            ToolInfo or list[ToolInfo]: Tool information instance(s) if found, None otherwise.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=tool_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results = []
        if not ids_to_get:
            return results
        types = []
        if tool_type is not None:
            types = [tool_type] if isinstance(tool_type, str) else tool_type
        for resource_id in ids_to_get:
            card = self._id_to_card.get(resource_id)
            if types and (self._get_card_type(card) not in types):
                continue
            if card and hasattr(card, "tool_info"):
                results.append(card.tool_info())
                continue
            if exact_match:
                results.append(None)
        return results

    async def add_mcp_server(self,
                             server_config: McpServerConfig | list[McpServerConfig],
                             *,
                             tag: Optional[Tag | list[Tag]] = None,
                             expiry_time: Optional[float] = None
                             ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Add MCP (Model Context Protocol) server configuration(s).

        Args:
            server_config: Single or list of McpServerConfig instances.
            tag: Optional tag(s) for categorizing the server(s).
            expiry_time: Optional Unix timestamp when the server configuration expires.
                If None, the configuration does not expire.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        self._inner_validate_server_config(server_config)
        if tag is not None:
            self._inner_validate_tag(tag)
        if expiry_time is not None and expiry_time <= 0:
            raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, param='expire_time',
                              reason='expire time <= 0')
        add_results = []
        server_configs = [server_config] if isinstance(server_config, McpServerConfig) else server_config
        for config in server_configs:
            try:
                # atomic process
                cards = await self._resource_registry.tool().add_tool_server(config, expiry_time=expiry_time)
                for card in cards:
                    self._id_to_card[card.id] = card
                    self._tag_mgr.tag_resource(card.id, tag if tag else GLOBAL)
                self._tag_mgr.tag_resource(config.server_id, tag if tag else GLOBAL)
                add_results.append(Ok(server_config.server_id))
                tool_names = [card.name for card in cards]
                logger.info(
                    f"add mcp server succeed, id={config.server_id}, server_name={config.server_name},"
                    f" tools={tool_names}")
            except Exception as e:
                add_results.append(Error(e))
                logger.info(
                    f"add mcp server failed, id={config.server_id}, server_name={config.server_name}, reason={str(e)}")

        return add_results if isinstance(server_config, list) else add_results[0]

    async def refresh_mcp_server(self,
                                 server_id: Optional[str | list[str]] = None,
                                 *,
                                 server_name: Optional[str | list[str]] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 ignore_exception: bool = False,
                                 skip_if_tag_not_exists: bool = False,
                                 ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Refresh MCP server tool card(s) by name.

        Args:
            server_id: Single or list of MCP server ids to refresh
            server_name: Single or list of MCP server names to refresh.
            tag: Optional tag to filter servers to refresh.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, continue refreshing other servers if one fails.
            skip_if_tag_not_exists: If True, skip non-existent servers.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        return []

    async def remove_mcp_server(self,
                                server_id: Optional[str | list[str]] = None,
                                *,
                                server_name: Optional[str | list[str]] = None,
                                tag: Optional[Tag | list[Tag]] = None,
                                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                skip_if_tag_not_exists: bool = False,
                                ignore_exception: bool = False,
                                ) -> Result[str, Exception] | list[Result[str, Exception]]:
        """
        Remove MCP server(s) by name or tag.

        Args:
            server_name: Single or list of MCP server names to remove.
            tag: Single tag or list of tags; removes all servers with matching tags.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            skip_if_tag_not_exists: If True, skip non-existent servers.
            ignore_exception: If True, continue removing other servers if one fails.

        Returns:
            Result[str, Exception] or list[Result[str, Exception]]:
                Result object(s) containing the server name(s) or exception(s).
        """
        server_ids_to_remove, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                       skip_if_tag_not_exists,
                                                                       StatusCode.RESOURCE_MCP_SERVER_REMOVE_ERROR)
        results = []
        for mcp_server_id in server_ids_to_remove:
            try:
                self._tag_mgr.remove_resource(server_id)
                tool_ids = await self._resource_registry.tool().remove_tool_server(server_id)
                if tool_ids:
                    self.remove_tool(tool_id=tool_ids)
                logger.info(f"remove mcp server succeed, id={mcp_server_id}")
                results.append(Ok(mcp_server_id))
            except Exception as e:
                if not ignore_exception:
                    raise e
                logger.info(f"remove mcp server failed, id={mcp_server_id}, reason={str(e)}")
                results.append(Error(e))
        return results

    async def get_mcp_tool(self,
                           name: str | list[str] = None,
                           server_id: str | list[str] = None,
                           *,
                           server_name: str | list[str] = None,
                           tag: Optional[Tag | list[Tag]] = None,
                           tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                           skip_if_tag_not_exists: bool = False,
                           ignore_exception: bool = False,
                           session: Optional[Session] = None
                           ) -> Optional[Tool] | list[Optional[Tool]]:
        """
        Get MCP tool(s) by name and server.

        Args:
            name: Single or list of MCP tool names to retrieve.
            server_name: Single or list of MCP server names containing the tools.
            server_id: Single or list of MCP server IDs containing the tools.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions for refresh mcp server if needed.
            session: Optional session context for the tools.

        Returns:
            Tool or list[Tool]: MCP tool instance(s) if found, None otherwise.
        """
        server_ids_to_get, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                    skip_if_tag_not_exists,
                                                                    StatusCode.RESOURCE_MCP_TOOL_GET_ERROR)
        results = []
        tool_names = [name] if isinstance(name, str) else name
        for mcp_server_id in server_ids_to_get:
            try:
                await self._resource_registry.tool().refresh_tool_server(mcp_server_id, skip_not_exist=True)
            except Exception as e:
                if not ignore_exception:
                    raise e
            if tool_names is None:
                results.extend(self._resource_registry.tool().get_mcp_tools(mcp_server_id, session))
                continue
            for tool_name in tool_names:
                tool = self._resource_registry.tool().get_mcp_tool(tool_name, mcp_server_id, session)
                if exact_match:
                    results.append(tool)
                elif tool:
                    results.append(tool)
        return results

    async def get_mcp_tool_infos(self,
                                 name: str | list[str] = None,
                                 server_id: str | list[str] = None,
                                 *,
                                 server_name: str | list[str] = None,
                                 tag: Optional[Tag | list[Tag]] = None,
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                 skip_if_tag_not_exists: bool = False,
                                 ignore_exception: bool = False,
                                 ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]:
        """
        Get MCP tool information/metadata by name and server.

        Args:
            name: Single or list of MCP tool names to get info for.
                If None, returns info for all tools in specified servers.
            server_name: Single or list of MCP server names containing the tools.
                Must be provided if name is None.
            server_id: Single or list of MCP server IDs containing the tools.
            tag: Optional tag to filter servers/tools.
            tag_match_strategy: Strategy for matching tags when using tag parameter.
            ignore_exception: If True, ignore exceptions for refresh mcp server if needed.

        Returns:
            ToolInfo or list[ToolInfo]: MCP tool information instance(s) if found, None otherwise.
        """
        server_ids_to_get, exact_match = self._inner_get_server_ids(server_id, server_name, tag, tag_match_strategy,
                                                                    skip_if_tag_not_exists,
                                                                    StatusCode.RESOURCE_MCP_TOOL_GET_ERROR)
        tool_names = [name] if isinstance(name, str) else name
        results = []
        for mcp_server_id in server_ids_to_get:
            try:
                await self._resource_registry.tool().refresh_tool_server(mcp_server_id, skip_not_exist=True)
            except Exception as e:
                if not ignore_exception:
                    raise e
            tool_ids = []
            if tool_names is None:
                tool_ids = self._resource_registry.tool().get_mcp_tool_id(mcp_server_id)
            else:
                for tool_name in tool_names:
                    tool_ids.append(self._resource_registry.tool().get_mcp_tool_id(mcp_server_id, tool_name))
            for tool_id in tool_ids:
                tool_card = self._id_to_card.get(tool_id) if tool_id else None
                if exact_match:
                    results.append(tool_card.tool_info() if tool_card else None)
                elif tool_card:
                    results.append(tool_card.tool_info())
        return results

    def get_resource_by_tag(self,
                            tag: Tag) -> Optional[list[BaseCard]]:
        """
        Retrieve all resources associated with a specific tag.

        Args:
            tag: Tag to search for.

        Returns:
            List of BaseCard instances representing resources with the specified tag,
            or None if no resources found.
        """
        self._inner_validate_tag(tag)
        resource_ids = self._tag_mgr.get_tag_resources(tag)
        if not resource_ids:
            return [] if resource_ids is not None else None
        return [self._id_to_card.get(resource_id) for resource_id in resource_ids]

    def list_tags(self) -> list[Tag]:
        """
        List all tags currently in use across all resources.

        Returns:
            List of unique tag strings.
        """
        return self._tag_mgr.list_tags()

    def has_tag(self, tag: str) -> bool:
        """
        Check if the specified tag exists in the resource_mgr.

        Args:
            tag: Tag to check for existence.

        Returns:
            True if tag exists, False otherwise.
        """
        self._inner_validate_tag(tag)
        return self._tag_mgr.has_tag(tag)

    async def remove_tag(self,
                         tag: Tag | list[Tag],
                         *,
                         skip_if_tag_not_exists: bool = False,
                         ) -> Result[Tag, Exception] | list[Result[Tag, Exception]]:
        """
        Remove tag(s) from all resources.

        Args:
            tag: Single tag or list of tags to remove from all resources.
            skip_if_tag_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[Tag, Exception] or list[Result[Tag, Exception]]:
                Result object(s) containing the tag(s) or exception(s).
        """
        self._inner_validate_tag(tag)
        tags_to_remove = tag if isinstance(tag, list) else [tag]
        results = []
        for single_tag in tags_to_remove:
            resource_to_removal = self._tag_mgr.remove_tag(tag=single_tag,
                                                           skip_if_not_exists=skip_if_tag_not_exists)
            for resource_id in resource_to_removal:
                self._resource_registry.remove_by_id(resource_id)
            logger.info(f"remove tag succeed, tag={tag}, release resource={resource_to_removal}")
            results.append(Ok(single_tag))
        return results[0] if results and isinstance(tag, Tag) else results

    def update_resource_tag(self,
                            resource_id: str,
                            tag: Tag | list[Tag]
                            ) -> Result[list[Tag], Exception]:
        """
        Replace all tags on a resource with new tag(s).

        Args:
            resource_id: Resource identifier.
            tag: New tag(s) to set on the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing the new tag list or an exception.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            results = self._tag_mgr.update_resource_tags(resource_id, tags=tag,
                                                         tag_update_strategy=TagUpdateStrategy.REPLACE)
            return Ok(results)
        except Exception as e:
            return Error(e)

    def add_resource_tag(self,
                         resource_id: str,
                         tag: Tag | list[Tag]
                         ) -> Result[list[Tag], Exception]:
        """
        Add tag(s) to a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to add to the resource.

        Returns:
            Result[list[Tag], Exception]: Result object containing all tags now associated with the resource.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            now_tags = self._tag_mgr.tag_resource(resource_id, tag)
            return Ok(now_tags)
        except Exception as e:
            return Error(e)

    def remove_resource_tag(self,
                            resource_id: str,
                            tag: Tag | list[Tag],
                            *,
                            skip_if_tag_not_exists: bool = False
                            ) -> Result[list[Tag], Exception]:
        """
        Remove specific tag(s) from a resource.

        Args:
            resource_id: Resource identifier.
            tag: Tag(s) to remove from the resource.
            skip_if_tag_not_exists: If True, ignore non-existent tags.

        Returns:
            Result[list[Tag], Exception]: Result object containing remaining tags on the resource.
        """
        self._inner_validate_resource_id(resource_id)
        self._inner_validate_tag(tag)
        try:
            remain_tags = self._tag_mgr.remove_resource_tags(resource_id, tags=tag,
                                                             skip_if_not_exists=skip_if_tag_not_exists)
            return Ok(remain_tags)
        except Exception as e:
            return Error(e)

    def get_resource_tag(self, resource_id: str) -> Optional[list[Tag]]:
        """
        Get all tags associated with a resource.

        Args:
            resource_id: Resource identifier.

        Returns:
            List of tags associated with the resource, or None if resource not found.
        """
        resource_tag = self._tag_mgr.get_resources_tags(resource_id)
        return resource_tag if resource_tag else None

    def resource_has_tag(self, resource_id: str, tag: Tag) -> bool:
        """
        Check if a specific resource is associated with the given tag.

        Args:
            resource_id: The unique identifier of the resource to check.
            tag: The tag to verify association with the resource.

        Returns:
            True if the resource has the specified tag, False otherwise.
        """
        self._inner_validate_tag(tag)
        self._inner_validate_resource_id(resource_id)
        return self._tag_mgr.has_resource_tag(resource_id, tag)

    async def release(self):
        """
        Release all resources and clean up.

        This method should be called when the ResourceMgr is no longer needed.
        """
        await self._resource_registry.tool().release()

    def _inner_add_resource(self, *, resource_id: str, resource_type: str, resource: Any,
                            resource_card: Optional[BaseCard] = None, tag: Optional[Tag | list[Tag]] = None):
        """
        Internal method to add a resource.

        Args:
            resource_id: Resource identifier.
            resource_type: Type of resource ("agent", "group", "workflow", "tool", "prompt", "model").
            resource: Resource instance or provider.
            resource_card: Optional card associated with the resource.
            tag: Optional tag(s) for the resource.

        Returns:
            Result object indicating success or failure.
        """
        try:
            if self._tag_mgr.has_resource(resource_id):
                raise build_error(StatusCode.RESOURCE_ADD_ERROR, card=resource_card if resource_card else resource_id,
                                  reason=f'resource already exist')
            # add resource
            if resource_type == "workflow":
                self._resource_registry.workflow().add_workflow(resource_id, resource)
            elif resource_type == "agent":
                self._resource_registry.agent().add_agent(resource_id, resource)
            elif resource_type == "group":
                self._resource_registry.agent_group().add_agent_group(resource_id, resource)
            elif resource_type == "tool":
                self._resource_registry.tool().add_tool(resource_id, resource)
            elif resource_type == "prompt":
                self._resource_registry.prompt().add_prompt(resource_id, resource)
            elif resource_type == "model":
                self._resource_registry.model().add_model(resource_id, resource)
            elif resource_type == "sys_operation":
                self._resource_registry.sys_operation().add_sys_operation(resource_id, resource)
            else:
                ...
            if resource_card:
                self._id_to_card[resource_id] = resource_card
            self._tag_mgr.tag_resource(resource_id, tag if tag else GLOBAL)
            if resource_card:
                logger.info(f"add resource succeed, id={resource_id}, type={resource_type}, card={resource_card}")
            else:
                logger.info(f"add resource succeed, id={resource_id}, type={resource_type}")
            return Ok(resource_card if resource_card else resource_id)
        except Exception as e:
            if resource_card:
                logger.error(
                    f"add resource failed, id={resource_id}, type={resource_type}, card={resource_card},"
                    f" reason={str(e)}")
            else:
                logger.info(f"add resource failed, id={resource_id}, type={resource_type}, reason={str(e)}")
            return Error(e)

    def _inner_remove_resources(self, *, resource_id: Optional[str | list[str]], resource_type: str,
                                tag: Tag | list[Tag],
                                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                skip_if_tag_not_exists: bool = False):
        """
        Internal method to remove resources.

        Args:
            resource_id: Resource identifier(s) to remove.
            resource_type: Type of resource.
            tag: Tag(s) to filter resources.
            tag_match_strategy: Strategy for matching tags.
            skip_if_tag_not_exists: Whether to skip non-existent tags.

        Returns:
            Result object(s) indicating success or failure.
        """
        ids_to_remove = []
        if resource_id is not None:
            self._inner_validate_resource_id(resource_id)
            ids_to_remove = resource_id if isinstance(resource_id, list) else [resource_id]
        remove_by_tag = False
        results: list[Result] = []
        if not ids_to_remove:
            self._inner_validate_tag(tag)
            ids_to_remove = self._tag_mgr.find_resources_by_tags(tag, tag_match_strategy=tag_match_strategy,
                                                                 skip_if_not_exists=skip_if_tag_not_exists)
            remove_by_tag = True
            if not ids_to_remove:
                return results
        for remove_id in ids_to_remove:
            error = None
            try:
                self._tag_mgr.remove_resource(resource_id)
                # add resource
                if resource_type == "workflow":
                    self._resource_registry.workflow().remove_workflow(remove_id)
                elif resource_type == "agent":
                    self._resource_registry.agent().remove_agent(remove_id)
                elif resource_type == "group":
                    self._resource_registry.agent_group().remove_agent_group(remove_id)
                elif resource_type == "model":
                    self._resource_registry.model().remove_model(remove_id)
                elif resource_type == "tool":
                    self._resource_registry.tool().remove_tool(remove_id)
                elif resource_type == "prompt":
                    self._resource_registry.prompt().remove_prompt(remove_id)
                elif resource_type == "sys_operation":
                    self._resource_registry.sys_operation().remove_sys_operation(remove_id)
                else:
                    ...
            except Exception as e:
                if not remove_by_tag:
                    error = e
            removed_card = self._id_to_card.pop(remove_id, None)
            if error:
                logger.error(
                    f"remove resource error, id={remove_id}, type={resource_type}, card={removed_card},"
                    f" reason={str(error)}")
                results.append(Error(error))
            elif resource_type in ["tool", "prompt"]:
                results.append(Ok(remove_id))
            else:
                if removed_card or not remove_by_tag:
                    results.append(Ok(removed_card))
            if not error:
                if removed_card:
                    logger.info(f"remove resource succeed, id={resource_id}, type={resource_type},"
                                f" card={removed_card}")
                else:
                    logger.info(f"remove resource succeed, id={resource_id}, type={resource_type}")
        return results if not isinstance(resource_id, str) else results[0]

    def _inner_find_resource_ids(self, *, resource_id: Optional[str | list[str]], tag: Tag | list[Tag],
                                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL):
        """
        Internal method to find resource IDs by ID or tag.

        Args:
            resource_id: Resource identifier(s).
            tag: Tag(s) to search.
            tag_match_strategy: Strategy for matching tags.

        Returns:
            Tuple of (list of resource IDs, whether it was an exact match by ID).
        """
        ids_to_get = None
        exact_match = False
        if resource_id is not None:
            self._inner_validate_resource_id(resource_id)
            ids_to_get = resource_id if isinstance(resource_id, list) else [resource_id]
            exact_match = True
        if not ids_to_get:
            ids_to_get = self._tag_mgr.find_resources_by_tags(tag if tag else GLOBAL,
                                                              tag_match_strategy=tag_match_strategy)
            exact_match = False
        return ids_to_get, exact_match

    def _inner_get_resources(self, *, resource_id: Optional[str | list[str]], resource_type: str, tag: Tag | list[Tag],
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL, session=None):
        """
        Internal method to get resources by ID or tag.

        Args:
            resource_id: Resource identifier(s).
            resource_type: Type of resource.
            tag: Tag(s) to filter.
            tag_match_strategy: Strategy for matching tags.
            session: Optional session context.

        Returns:
            Resource instance(s) or None.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=resource_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results = []
        for get_id in ids_to_get:
            resource = None
            try:
                if self._tag_mgr.has_resource(resource_id):
                    if resource_type == "tool":
                        resource = self._resource_registry.tool().get_tool(get_id, session=session)
                    elif resource_type == "prompt":
                        resource = self._resource_registry.prompt().get_prompt(get_id)
                    elif resource_type == "sys_operation":
                        resource = self._resource_registry.sys_operation().get_sys_operation(get_id)
                    else:
                        ...
            except Exception as e:
                ...
            finally:
                if resource or exact_match:
                    results.append(resource)

        return results[0] if results and isinstance(resource_id, str) else results

    async def _inner_get_resources_by_provider(self, *, resource_id: Optional[str | list[str]], resource_type: str,
                                               tag: Tag | list[Tag],
                                               tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                                               session=None):
        """
        Internal method to get resources by provider.

        Args:
            resource_id: Resource identifier(s).
            resource_type: Type of resource.
            tag: Tag(s) to filter.
            tag_match_strategy: Strategy for matching tags.
            session: Optional session context.

        Returns:
            Resource instance(s) or None.
        """
        ids_to_get, exact_match = self._inner_find_resource_ids(resource_id=resource_id, tag=tag,
                                                                tag_match_strategy=tag_match_strategy)
        results: list = []
        if not ids_to_get:
            return results
        for get_id in ids_to_get:
            resource = None
            try:
                if self._tag_mgr.has_resource(resource_id):
                    # add resource
                    if resource_type == "workflow":
                        resource = await self._resource_registry.workflow().get_workflow(get_id, session=session)
                    elif resource_type == "agent":
                        resource = await self._resource_registry.agent().get_agent(get_id)
                    elif resource_type == "group":
                        resource = await self._resource_registry.agent_group().get_agent_group(get_id)
                    elif resource_type == "model":
                        resource = await self._resource_registry.model().get_model(get_id, session=session)
                    else:
                        ...
            except Exception as e:
                ...
            finally:
                if resource or exact_match:
                    results.append(resource)

        return results[0] if results and isinstance(resource_id, str) else results

    @staticmethod
    def _inner_validate_tag(tag: Tag | list[Tag]):
        """
        Validate tag(s).

        Args:
            tag: Tag or list of tags to validate.

        Raises:
            Error if tag is invalid.
        """
        if not tag:
            raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag, reason="is None or empty value")
        tmp_tags = []
        if isinstance(tag, list):
            if GLOBAL in tag and len(tag) > 1:
                raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag,
                                  reason="The GLOBAL tag already exists and cannot be assigned additional tags.")
            for single_tag in tag:
                if not single_tag:
                    raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag, reason="has None or empty value")
                if single_tag in tmp_tags:
                    raise build_error(StatusCode.RESOURCE_TAG_VALUE_INVALID, tag=tag,
                                      reason=f"has duplicate tag '{single_tag}' item")
                tmp_tags.append(single_tag)

    @staticmethod
    def _inner_validate_resource_card(card: BaseCard):
        """
        Validate resource card.

        Args:
            card: Resource card to validate.

        Raises:
            Error if card is invalid.
        """
        if card is None:
            raise build_error(StatusCode.RESOURCE_CARD_VALUE_INVALID, card=card, reason="card is None")
        if not card.id:
            raise build_error(StatusCode.RESOURCE_CARD_VALUE_INVALID, card=card, reason="card id value is empty value")

    @staticmethod
    def _inner_validate_server_config(server_config):
        """
        Validate MCP server configuration.

        Args:
            server_config: Server configuration to validate.

        Raises:
            Error if configuration is invalid.
        """
        if not server_config:
            raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, server_config=server_config,
                              reason="server_config(s) is None or empty")
        if isinstance(server_config, McpServerConfig) and not server_config.server_id:
            raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, server_config=server_config,
                              reason="server_config's server_id is None or empty")
        ids = []
        if isinstance(server_config, list):
            for config in server_config:
                if not config:
                    raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, server_config=server_config,
                                      reason="server config list has invalid item")
                if not config.server_id:
                    raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, server_config=server_config)
                if config.server_id in ids:
                    raise build_error(StatusCode.RESOURCE_MCP_SERVER_PARAM_INVALID, server_config=server_config,
                                      reason="server config list has duplicate server_id")
                ids.append(config.server_id)

    @staticmethod
    def _inner_validate_resource_id(resource_id: str | list[str]):
        """
        Validate resource ID(s).

        Args:
            resource_id: Resource ID(s) to validate.

        Raises:
            Error if ID(s) are invalid.
        """
        if not resource_id:
            raise build_error(StatusCode.RESOURCE_ID_VALUE_INVALID, resource_id=resource_id, reason="is None or empty")
        if isinstance(resource_id, list):
            tmp_ids = []
            for rid in resource_id:
                if not resource_id:
                    raise build_error(StatusCode.RESOURCE_ID_VALUE_INVALID, resource_id=resource_id,
                                      reason="has None or empty resource_id item")
                if rid in tmp_ids:
                    raise build_error(StatusCode.RESOURCE_ID_VALUE_INVALID, resource_id=resource_id,
                                      reason=f"has duplicate resource_id '{rid}'")

    @staticmethod
    def _inner_validate_provider(card, provider):
        """
        Validate resource provider.

        Args:
            card: Resource card (for error context).
            provider: Provider to validate.

        Raises:
            Error if provider is invalid.
        """
        if not provider:
            raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID, card=card, reason="provider is not None")
        if not isinstance(provider, Callable):
            raise build_error(StatusCode.RESOURCE_PROVIDER_INVALID, card=card, reason="provider is not callable")

    def _inner_get_server_ids(self, server_id, server_name, tag, tag_match_strategy, skip_if_tag_not_exists,
                              error_code):
        """
        Get MCP server IDs by various criteria.

        Args:
            server_id: Server ID(s).
            server_name: Server name(s).
            tag: Tag(s) for filtering.
            tag_match_strategy: Strategy for matching tags.
            skip_if_tag_not_exists: Whether to skip non-existent tags.
            error_code: Error code to use for exceptions.

        Returns:
            Tuple of (list of server IDs, whether it was an exact match).
        """
        server_ids_to_refresh = []
        exact_match = False
        if server_id is not None:
            if not server_id:
                raise build_error(error_code, server_config=server_id,
                                  reason="server_id is empty")
            server_ids_to_refresh.append(server_id)
            exact_match = True
        else:
            if server_name is None:
                server_ids_to_refresh.extend(
                    self._tag_mgr.find_resources_by_tags(tag if tag else GLOBAL, tag_match_strategy,
                                                         skip_if_tag_not_exists))
            else:
                if len(server_name) == 0:
                    raise build_error(error_code, server_id=server_id,
                                      reason="server_name is empty")
                server_names = server_name if isinstance(server_name, list) else [server_name]
                for s_name in server_names:
                    server_ids_to_refresh.extend(self._resource_registry.tool().get_mcp_server_ids(s_name))
        return server_ids_to_refresh, exact_match

    @staticmethod
    def _get_card_type(card):
        """
        Get the type of a card.

        Args:
            card: Card to check.

        Returns:
            Card type as string, or None if unknown.
        """
        if type(card).__name__(card) == "GroupCard":
            return "group"
        elif type(card).__name__(card) == "WorkflowCard":
            return "workflow"
        elif type(card).__name__(card) == "AgentCard":
            return "agent"
        elif type(card).__name__(card) == "RestfulCard":
            return "restfulapi"
        elif type(card).__name__(card) == "McpToolCard":
            return "mcp"
        elif type(card).__name__(card) == "ToolCard":
            return "function"
        else:
            return None
