# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""WorkflowAgentRefactor - Composition adapter for WorkflowAgent.

No inheritance from legacy.BaseAgent or legacy.ControllerAgent.
Internally holds a base.ControllerAgent (new architecture) and
aligns the full public interface of legacy WorkflowAgent via
delegation.

Phase 4 deliverable of the workflow agent refactor roadmap.
"""

import copy
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.hash_util import generate_key
from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
)
from openjiuwen.core.controller import JsonDataFrame
from openjiuwen.core.controller.base import Controller
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutput,
    ControllerOutputChunk,
    ControllerOutputPayload,
)
from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.foundation.llm import ModelConfig, ModelClientConfig, Model, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.session import Config
from openjiuwen.core.session.agent import (
    Session,
    create_agent_session,
)
from openjiuwen.core.session.stream import OutputSchema, StreamSchemas
from openjiuwen.core.single_agent.base import (
    ControllerAgent,
)
from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
    WorkflowSchema,
    PluginSchema,
)
from openjiuwen.core.single_agent.legacy.agent import (
    WorkflowFactory,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.workflow import (
    Workflow,
    WorkflowCard,
    generate_workflow_key, WorkflowExecutionState, WorkflowOutput,
)
from openjiuwen.core.application.workflow_agent.workflow_event_handler import (
    WorkflowEventHandler,
)
from openjiuwen.core.application.workflow_agent.workflow_task_executor import (
    WorkflowTaskExecutor,
)


class MockWorkflowAgent:
    """WorkflowAgent-compatible adapter backed by base.ControllerAgent.

    No inheritance. Aligns with legacy.BaseAgent +
    legacy.ControllerAgent public interface via composition.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self, agent_config: WorkflowAgentConfig
    ) -> None:
        # 1. Preserve original config (zero changes to WorkflowAgentConfig)
        self.agent_config = agent_config
        self._config_wrapper = Config()
        self._config_wrapper.set_agent_config(agent_config)
        self._config = self._config_wrapper

        # 3. Hold tools/workflows lists
        self._tools: List[Tool] = []
        self._workflows: List[Workflow] = []

        # 4. Build AgentCard
        card = AgentCard(
            id=agent_config.id,
            name=agent_config.id,
            description=agent_config.description,
        )

        # 5. Build Controller
        controller = Controller()

        # 6. Convert to ControllerConfig
        controller_config = self._build_controller_config(
            agent_config
        )

        # 7. Assemble base.ControllerAgent
        self._inner = ControllerAgent(
            card=card,
            controller=controller,
            config=controller_config,
        )

        # 8. Inject EventHandler/TaskExecutor
        event_handler = WorkflowEventHandler()
        controller.set_event_handler(event_handler)
        controller.add_task_executor(
            "workflow",
            lambda deps: WorkflowTaskExecutor(deps),
        )

    # ------------------------------------------------------------------
    # Config / properties
    # ------------------------------------------------------------------

    def config(self) -> Config:
        """Return legacy Config wrapper."""
        return self._config_wrapper

    @property
    def tools(self) -> List[Tool]:
        return self._tools

    @property
    def workflows(self) -> List[Workflow]:
        return self._workflows

    @property
    def context_engine(self) -> ContextEngine:
        return self._inner.context_engine

    @property
    def controller(self):
        """Delegate to _inner.controller."""
        return self._inner.controller

    @property
    def ability_manager(self):
        """Delegate to _inner.ability_manager."""
        return self._inner.ability_manager

    @property
    def agent_callback_manager(self):
        """Delegate to _inner.agent_callback_manager."""
        return self._inner.agent_callback_manager

    # ------------------------------------------------------------------
    # Skill registration (delegate)
    # ------------------------------------------------------------------

    async def register_skill(self, skill_path):
        """Delegate to _inner."""
        await self._inner.register_skill(skill_path)

    async def register_remote_skills(
        self, skills_dir, github_tree, token=""
    ):
        """Delegate to _inner."""
        await self._inner.register_remote_skills(
            skills_dir, github_tree, token=token
        )

    # ------------------------------------------------------------------
    # Dynamic registration
    # ------------------------------------------------------------------

    def add_tools(self, tools: List[Tool]) -> None:
        """Three-way sync: agent_config + resource_mgr + _inner."""
        from openjiuwen.core.runner import Runner

        for tool in tools:
            if tool.card.name not in self.agent_config.tools:
                self.agent_config.tools.append(
                    tool.card.name
                )
            if hasattr(self.agent_config, "plugins"):
                existing = {
                    p.name
                    for p in self.agent_config.plugins
                }
                if tool.card.name not in existing:
                    self.agent_config.plugins.append(
                        self._tool_to_plugin_schema(tool)
                    )
            existing_names = {
                t.card.name for t in self._tools
            }
            if tool.card.name not in existing_names:
                self._tools.append(tool)
            Runner.resource_mgr.add_tool(
                tool=[tool], tag=self.agent_config.id
            )
            self._inner.ability_manager.add(tool.card)

    def add_workflows(
        self,
        workflows: List[
            Union[Workflow, Callable[[], Workflow]]
        ],
    ) -> None:
        """Three-way sync: agent_config + resource_mgr + _inner."""
        logger.info(
            "WorkflowAgentRefactor.add_workflows: %d",
            len(workflows),
        )

        def make_provider(wf):
            def provider():
                return wf
            return provider

        for item in workflows:
            workflow_card = None
            provider = None

            if isinstance(item, WorkflowFactory):
                provider = item
                workflow_card = provider.card()
            elif callable(item) and hasattr(
                item, "id"
            ) and hasattr(item, "version"):
                provider = item
                workflow_card = WorkflowCard(
                    id=getattr(item, "id"),
                    name=getattr(item, "name", None),
                    description=getattr(
                        item, "description", None
                    ),
                    version=getattr(item, "version"),
                    input_params=(
                        getattr(item, "input_params", None)
                        or getattr(item, "inputs", None)
                    ),
                )
            elif callable(item):
                raise ValueError(
                    "Callable workflow provider must have "
                    "'id' and 'version' attributes."
                )
            else:
                provider = make_provider(item)
                workflow_card = item.card

            workflow_key = generate_workflow_key(
                workflow_card.id, workflow_card.version
            )
            existing_keys = {
                generate_workflow_key(w.id, w.version)
                for w in self.agent_config.workflows
            }

            if workflow_key not in existing_keys:
                self.agent_config.workflows.append(
                    WorkflowSchema(
                        id=workflow_card.id,
                        name=workflow_card.name,
                        version=workflow_card.version,
                        description=(
                            workflow_card.description or ""
                        ),
                        input_params=(
                            workflow_card.input_params
                        ),
                    )
                )

            card_copy = copy.deepcopy(workflow_card)
            card_copy.id = workflow_key
            self._inner.ability_manager.add(card_copy)

            try:
                from openjiuwen.core.runner import Runner

                Runner.resource_mgr.add_workflow(
                    card=card_copy,
                    workflow=provider,
                    tag=self.agent_config.id,
                )
            except Exception as e:
                logger.error(
                    "Failed to add workflow to "
                    "resource_mgr: %s",
                    e,
                )

    def remove_workflows(
        self, workflows: List[Tuple[str, str]]
    ) -> None:
        """Three-way sync removal."""
        logger.info(
            "WorkflowAgentRefactor.remove_workflows: %d",
            len(workflows),
        )
        from openjiuwen.core.runner import Runner

        for wf_id, wf_version in workflows:
            wf_key = generate_workflow_key(
                wf_id, wf_version
            )
            remaining, wf_name = [], None
            for w in self.agent_config.workflows:
                if (
                    w.id == wf_id
                    and w.version == wf_version
                ):
                    wf_name = w.name
                else:
                    remaining.append(w)
            self.agent_config.workflows = remaining

            try:
                Runner.resource_mgr.remove_workflow(
                    wf_key
                )
            except Exception as e:
                logger.error(
                    "Failed to remove workflow from "
                    "resource_mgr: %s",
                    e,
                )
            if wf_name is not None:
                self._inner.ability_manager.remove(
                    wf_name
                )

    def bind_workflows(
        self, workflows: List[Workflow]
    ) -> None:
        """Alias for add_workflows."""
        self.add_workflows(workflows)

    def add_prompt(
        self, prompt_template: List[Dict]
    ) -> None:
        """Append to agent_config.prompt_template."""
        if hasattr(
            self.agent_config, "prompt_template"
        ):
            current = copy.deepcopy(
                self.agent_config.prompt_template
            )
            current.extend(
                copy.deepcopy(prompt_template)
            )
            self.agent_config.prompt_template = current
        else:
            logger.warning(
                "%s has no prompt_template, ignored",
                self.agent_config.__class__.__name__,
            )

    def add_plugins(self, plugins: List) -> None:
        """Append plugins to agent_config."""
        if hasattr(self.agent_config, "plugins"):
            existing = {
                p.name
                for p in self.agent_config.plugins
            }
            for plugin in plugins:
                if plugin.name not in existing:
                    self.agent_config.plugins.append(
                        plugin
                    )
                    existing.add(plugin.name)
        else:
            logger.warning(
                "%s has no plugins field, ignored",
                self.agent_config.__class__.__name__,
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def invoke(
        self,
        inputs: Dict,
        session: Session = None,
    ) -> Dict:
        """Invoke with session management + output adaptation.

        Args:
            inputs: Legacy dict with query, conversation_id, etc.
            session: Optional legacy Session.

        Returns:
            Legacy dict format.
        """
        agent_session, need_cleanup = (
            await self._prepare_session(inputs, session)
        )

        await self._register_model(agent_session)

        try:
            result = await self._inner.invoke(
                inputs, session=agent_session
            )
            return self._adapt_invoke_output(result)
        finally:
            if need_cleanup:
                await self._cleanup_session(
                    agent_session
                )

    async def stream(
        self,
        inputs: Dict,
        session: Session = None,
    ) -> AsyncIterator[Any]:
        """Stream with session management + output adaptation.

        Args:
            inputs: Legacy dict with query, conversation_id, etc.
            session: Optional legacy Session.

        Yields:
            Legacy OutputSchema chunks.
        """
        agent_session, need_cleanup = (
            await self._prepare_session(inputs, session)
        )

        await self._register_model(agent_session)

        try:
            async for chunk in self._inner.stream(
                inputs, session=agent_session
            ):
                if isinstance(chunk, StreamSchemas):
                    yield chunk
        finally:
            if need_cleanup:
                await self._cleanup_session(
                    agent_session
                )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def clear_session(
        self, session_id: str = "default_session"
    ) -> None:
        """Release session + clear context."""
        from openjiuwen.core.runner import Runner

        await Runner.release(session_id=session_id)
        self.context_engine.clear_context(
            session_id=session_id
        )

    async def release_session(
        self, session_id: str
    ) -> None:
        """Delegate to _inner."""
        await self._inner.release_session(session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_context_engine(self) -> ContextEngine:
        """Create ContextEngine from agent_config (reuse legacy logic)."""
        if hasattr(
            self.agent_config, "constrain"
        ) and hasattr(
            self.agent_config.constrain,
            "reserved_max_chat_rounds",
        ):
            max_rounds = (
                self.agent_config.constrain
                .reserved_max_chat_rounds
            )
        else:
            max_rounds = 10
        return ContextEngine(
            config=ContextEngineConfig(
                max_context_message_num=max_rounds * 2
            )
        )

    @staticmethod
    def _build_controller_config(
        agent_config: WorkflowAgentConfig,
    ) -> ControllerConfig:
        """WorkflowAgentConfig -> ControllerConfig mapping."""
        model_config = agent_config.model
        model_id = ""
        if model_config is not None:
            model_id = generate_key(
                model_config.model_info.api_key,
                model_config.model_info.api_base,
                model_config.model_provider
            )
        return ControllerConfig(
            # Workflow agent is serial; one task at a time
            max_concurrent_tasks=1,
            enable_intent_recognition=True,
            intent_llm_id=model_id,
            default_response=agent_config.default_response
        )

    async def _prepare_session(
        self,
        inputs: Dict,
        session: Optional[Session],
    ) -> Tuple[Session, bool]:
        """Create or unwrap session for _inner.

        Returns:
            (agent_session, need_cleanup) tuple.
        """
        session_id = inputs.get(
            "conversation_id", "default_session"
        )

        if session is None:
            agent_session = create_agent_session(
                session_id=session_id,
                card=AgentCard(id=self.agent_config.id),
            )
            await agent_session.pre_run(inputs=inputs)
            await self._inner.context_engine.create_context(
                session=agent_session
            )
            return agent_session, True

        agent_session = session
        await self._inner.context_engine.create_context(
            session=agent_session
        )
        return agent_session, False

    async def _cleanup_session(
        self, agent_session: Session
    ) -> None:
        """Post-run cleanup for self-created sessions."""
        await self._inner.context_engine.save_contexts(
            agent_session
        )
        await agent_session.post_run()

    @staticmethod
    def _adapt_invoke_output(
        result: ControllerOutput,
    ) -> Dict | List:
        """ControllerOutput -> legacy Dict format.

        TaskExecutor already packs WorkflowOutput into
        ControllerOutput.payload.data, so we extract it.
        """
        if result is None:
            return {
                "result_type": "answer",
                "output": {},
            }

        workflow_final_result = {}
        if result.data:
            last_data = result.data[-1]
            if isinstance(last_data, ControllerOutputChunk):
                final_result = last_data.payload.data
                if final_result:
                    data_frame = final_result[0]
                    if isinstance(data_frame, JsonDataFrame):
                        workflow_final_result = data_frame.data.get("result")
                        workflow_exec_state = workflow_final_result.state
                        if workflow_exec_state == WorkflowExecutionState.COMPLETED:
                            return {
                                "result_type": "answer",
                                "output": workflow_final_result,
                            }
                        elif workflow_exec_state == WorkflowExecutionState.INPUT_REQUIRED:
                            return [workflow_final_result.result]
            elif isinstance(last_data, OutputSchema):
                schema_type = last_data.type
                payload = last_data.payload
                if schema_type == "workflow_final" and payload.get("status", "") == "default_response":
                    result = {
                        "output": {"answer": payload.get("response", "")},
                        "result_type": "answer",
                        "status": payload.get("status", "")
                    }
                    return result
        return {
            "result_type": "answer",
            "output": workflow_final_result,
        }

    @staticmethod
    def _tool_to_plugin_schema(
        tool: Tool,
    ) -> PluginSchema:
        """Convert Tool to PluginSchema."""
        inputs = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        if hasattr(tool, "params") and tool.params:
            for param in tool.params:
                inputs["properties"][param.name] = {
                    "type": param.type,
                    "description": param.description,
                }
                if param.required:
                    inputs["required"].append(
                        param.name
                    )
        return PluginSchema(
            id=tool.card.id,
            name=tool.card.name,
            description=getattr(
                tool, "description", ""
            ),
            inputs=inputs,
        )

    async def _register_model(self, session):
        model_config = self.agent_config.model
        if model_config is None:
            return

        model_id = generate_key(
            model_config.model_info.api_key,
            model_config.model_info.api_base,
            model_config.model_provider
        )

        from openjiuwen.core.runner import Runner
        model = await Runner.resource_mgr.get_model(model_id=model_id, session=session)

        if model is None:
            model_client_config = ModelClientConfig(
                client_id=model_id,
                client_provider=model_config.model_provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
                ssl_cert=None,
            )
            model_request_config = ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=model_config.model_info.temperature,
                top_p=model_config.model_info.top_p,
                **(model_config.model_info.model_extra or {})
            )

            def model_provider():
                return Model(model_client_config=model_client_config, model_config=model_request_config)

            Runner.resource_mgr.add_model(model_id=model_id, model=model_provider)


__all__ = [
    "MockWorkflowAgent",
]
