# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Union, Any

from openjiuwen.core.multi_agent import BaseGroup
from openjiuwen.core.runner.message_queue_base import LocalMessageQueue
from openjiuwen.core.single_agent import BaseAgentAlias as BaseAgent
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.runner.drunner.dmessage_queue.dsubscription.reply_topic_subscription import ReplyTopicSubscription
from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_factory import MessageQueueFactory
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.runner_config import RunnerConfig, DEFAULT_RUNNER_CONFIG, set_runner_config, \
    get_runner_config

from openjiuwen.core.session import get_default_inmemory_checkpointer
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.session import Session
from openjiuwen.core.workflow import Session as WorkflowSession
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.single_agent import Session as AgentSession, AgentCard
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.session.stream import BaseStreamMode
from openjiuwen.core.workflow import generate_workflow_key
from openjiuwen.core.workflow import Workflow


class Runner:
    """
    Runner
    """
    _DEFAULT_RUNNER_ID = "global"

    _DEFAULT_AGENT_SESSION_ID = "default_session"

    _AGENT_CONVERSATION_ID = "conversation_id"

    def __init__(self, runner_id: str = _DEFAULT_RUNNER_ID, config: RunnerConfig = None):
        """
        Initialize the Runner with configuration.

        Args:
            runner_id: Runner unique id.
            config: Configuration for the runner. If None, defaults will be used.
        """
        self._runner_id = runner_id
        self._resource_manager = ResourceMgr()
        self._message_queue = LocalMessageQueue()
        if config is not None:
            set_runner_config(config)
        else:
            set_runner_config(DEFAULT_RUNNER_CONFIG)
        # Distributed system related components
        self.system_reply_sub: ReplyTopicSubscription | None = None
        self._distribute_message_queue = None

    @property
    def resource_mgr(self) -> ResourceMgr:
        """Get the resource manager for workflow, agent, agent_group, tool, model, prompt..."""
        return self._resource_manager

    @property
    def pubsub(self):
        """Get the local message queue for publish/subscribe communication."""
        return self._message_queue

    @property
    def dist_pubsub(self):
        """Get the distributed message queue for cross-process communication."""
        return self._distribute_message_queue

    def set_config(self, config: RunnerConfig):
        """Set the runner configuration with provided config object.

        Args:
            config: The RunnerConfig object containing configuration settings
        """
        set_runner_config(config)

    def get_config(self):
        """Retrieve the current runner configuration.

        Returns:
            RunnerConfig: The current configuration object
        """
        return get_runner_config()

    async def start(self) -> bool:
        """Start the runner and its associated components, such as message queue."""
        result = True
        logger.info("[Runner] Starting...")
        if get_runner_config().distributed_mode:
            # start dmq
            self._distribute_message_queue = MessageQueueFactory.create(
                get_runner_config().distributed_config.message_queue_config)
            self._distribute_message_queue.start()
            # start reply topic sub
            self.system_reply_sub = ReplyTopicSubscription(self._distribute_message_queue)
            self.system_reply_sub.activate()
            result = await self._message_queue.start()
        if result:
            logger.info("[Runner] Started.")
        else:
            logger.error("[Runner] Start failed.")
        return result

    async def stop(self):
        """Stop the runner and clean up resources."""
        logger.info("[Runner] Stopping...")
        try:
            if get_runner_config().distributed_mode:
                # 1. Stop ReplyTopicSubscription, clean up collector
                if self.system_reply_sub:
                    await self.system_reply_sub.deactivate()
                    self.system_reply_sub = None
                # 2. Stop MQ
                if self._distribute_message_queue:
                    await self._distribute_message_queue.stop()
                    self._distribute_message_queue = None

            result = await self._message_queue.stop()
            return result
        except Exception as e:
            return False
        finally:
            await self._resource_manager.release()
            logger.info("[Runner] Stopped.")

    async def run_workflow(self,
                           workflow: str | Workflow,
                           inputs: Any,
                           *,
                           session: Optional[str | Session] = None,
                           context: ModelContext = None,
                           envs: Optional[dict[str, Any]] = None):
        """
        Execute a workflow with given inputs.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides,
        """
        workflow_instance, workflow_session = await self._prepare_workflow(workflow, session)
        return await workflow_instance.invoke(inputs, session=workflow_session, context=context)

    async def run_workflow_streaming(self,
                                     workflow: str | Workflow,
                                     inputs: Any,
                                     *,
                                     session: Optional[str | Session] = None,
                                     context: ModelContext = None,
                                     stream_modes: list[BaseStreamMode] = None,
                                     envs: Optional[dict[str, Any]] = None):
        """
        Execute a workflow with streaming output support.

        Args:
            workflow: Workflow name or Workflow instance to execute
            inputs: Input data for the workflow
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        workflow_instance, workflow_session = await self._prepare_workflow(workflow, session)
        async for chunk in workflow_instance.stream(inputs, session=workflow_session,
                                                    stream_modes=stream_modes, context=context):
            yield chunk

    async def run_agent(self,
                        agent: str | BaseAgent,
                        inputs: Any,
                        *,
                        session: Optional[str | Session] = None,
                        context: ModelContext = None,
                        envs: Optional[dict[str, Any]] = None,
                        ):
        """
        Execute a single agent with given inputs.

        Args:
            agent: Agent name or BaseAgent instance to execute
            inputs: Input data for the agent
            session: Existing session ID or Session instance for context persistence
            context: model context
            envs: Environment variables or configuration overrides
        """
        agent_instance, agent_session = await self._prepare_agent(agent, inputs)
        if isinstance(agent_instance, RemoteAgent):
            res = await agent_instance.invoke(inputs)
        elif isinstance(agent_instance, BaseAgent):
            # ControllerAgent handles its own session lifecycle
            res = await agent_instance.invoke(inputs, session=None)
        else:
            res = await agent_instance.invoke(inputs, agent_session)
            await getattr(agent_session, "_inner").post_run()
        return res

    async def run_agent_streaming(self,
                                  agent: str | BaseAgent,
                                  inputs: Any,
                                  *,
                                  session: Optional[str | Session] = None,
                                  context: ModelContext = None,
                                  stream_modes: list[BaseStreamMode] = None,
                                  envs: Optional[dict[str, Any]] = None):
        """
           Execute a single agent with streaming output support.

           Args:
               agent: Agent name or BaseAgent instance to execute
               inputs: Input data for the agent
               session: Existing session ID or Session instance for context persistence
               context: model context
               stream_modes: Types of streaming data to output
               envs: Environment variables or configuration override
        """
        agent_instance, agent_session = await self._prepare_agent(agent, inputs, session)
        if isinstance(agent_instance, RemoteAgent):
            async for chunk in agent_instance.stream(inputs):
                yield chunk
        elif isinstance(agent_instance, BaseAgent):
            # ControllerAgent handles its own session lifecycle
            async for chunk in agent_instance.stream(inputs, session=None):
                yield chunk

    async def run_agent_group(self,
                              agent_group: str | BaseGroup,
                              inputs: Any,
                              *,
                              session: Optional[str | Session] = None,
                              context: ModelContext = None,
                              envs: Optional[dict[str, Any]] = None
                              ):
        """
        Execute a group of agents with given inputs.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model contex
            envs: Environment variables or configuration overrides
        """
        agent_group_instance = await self._prepare_agent_group(agent_group)
        return await agent_group_instance.invoke(inputs)

    async def run_agent_group_streaming(self,
                                        agent_group: str | BaseGroup,
                                        inputs: Any,
                                        *,
                                        session: Optional[str | Session] = None,
                                        context: ModelContext = None,
                                        stream_modes: list[BaseStreamMode] = None,
                                        envs: Optional[dict[str, Any]] = None,
                                        ):
        """
        Execute a group of agents with streaming output support.

        Args:
            agent_group: AgentGroup name or instance to execute
            inputs: Input data for the agent group
            session: Existing session ID or Session instance for context persistence
            context: model context
            stream_modes: Types of streaming data to output
            envs: Environment variables or configuration overrides
        """
        agent_group_instance = await self._prepare_agent_group(agent_group)
        async for chunk in agent_group_instance.stream(inputs):
            yield chunk

    async def release(self, session_id: str):
        """
        Release resources associated with a session.

        Args:
            session_id: ID of the session to clean up
        """
        await get_default_inmemory_checkpointer().release(session_id)

    @classmethod
    def _is_called_by_agent(cls, session: Session) -> bool:
        return session and isinstance(session, AgentSession)

    @classmethod
    def _create_workflow_session(cls, session):
        # Convert workflow session
        if not session:
            workflow_session = create_workflow_session()
        elif isinstance(session, str):
            workflow_session = create_workflow_session(session_id=session)
        elif isinstance(session, AgentSession):
            workflow_session = session.create_workflow_session()
        else:
            workflow_session = session
        return workflow_session

    async def _prepare_agent(self, agent: Union[str, BaseAgent], inputs: Any, session: Optional[str | Session] = None):
        session_id = inputs.get(self._AGENT_CONVERSATION_ID,
                                session if isinstance(session, str) else self._DEFAULT_AGENT_SESSION_ID)
        if isinstance(agent, str):
            agent_instance = await self._resource_manager.get_agent(agent_id=agent)
            if agent_instance is None:
                raise JiuWenBaseException(StatusCode.AGENT_NOT_FOUND.code,
                                          StatusCode.AGENT_NOT_FOUND.errmsg.format(agent))
            if isinstance(agent_instance, RemoteAgent):
                # Remote single_agent does not add session, keep sessionId in input
                if self._AGENT_CONVERSATION_ID not in inputs:
                    inputs[self._AGENT_CONVERSATION_ID] = session_id
                return agent_instance, None
            if hasattr(agent_instance, "card"):
                card = agent_instance.card
            else:
                # LegacyBaseAgent does not have card attribute
                card = AgentCard(id=agent_instance.config().get_agent_config().id)
            task_session = create_agent_session(session_id=session_id,
                                                envs=getattr(agent_instance.config(), "_env"), card=card)
            await get_default_inmemory_checkpointer().pre_agent_execute(
                getattr(getattr(task_session, "_inner"), "_inner"), inputs)
            return agent_instance, task_session

        if hasattr(agent, "card"):
            card = agent.card
        else:
            # LegacyBaseAgent does not have card attribute
            card = AgentCard(id=agent.config().get_agent_config().id)

        if callable(getattr(agent, "config", None)):
            config_obj = agent.config()
        else:
            config_obj = agent.config

        task_session = create_agent_session(session_id=session_id,
                                            envs=getattr(config_obj, "_env", None), card=card)
        await get_default_inmemory_checkpointer().pre_agent_execute(getattr(getattr(task_session, "_inner"), "_inner"),
                                                                    inputs)
        return agent, task_session

    async def _prepare_workflow(self, workflow: Union[str, Workflow],
                                session: str | Session | WorkflowSession) -> tuple[Workflow, WorkflowSession]:
        if isinstance(workflow, str):
            workflow_key = workflow
        else:
            workflow_key = generate_workflow_key(workflow.card.id, workflow.card.version)

        workflow_session = self._create_workflow_session(session)
        if isinstance(workflow, str):
            workflow_instance = await self._resource_manager.get_workflow(workflow_id=workflow_key,
                                                                          session=workflow_session)
        else:
            workflow_instance = workflow
        return workflow_instance, workflow_session

    async def _prepare_agent_group(self, agent_group: Union[str, BaseGroup]):
        if isinstance(agent_group, str):
            return await self._resource_manager.get_agent_group(
                group_id=agent_group
            )
        return agent_group


Runner = Runner(config=DEFAULT_RUNNER_CONFIG)
