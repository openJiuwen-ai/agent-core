# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import inspect

import warnings
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterator, List, Tuple, Union

from openjiuwen.agent.common.schema import WorkflowSchema, PluginSchema
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.config import ContextEngineConfig
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.agent import StaticAgentRuntime
from openjiuwen.core.runtime.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.resources_manager.workflow_manager import generate_workflow_key
from openjiuwen.core.runtime.wrapper import (
    StaticWrappedRuntime,
    TaskRuntime,
    WrappedRuntime
)
from openjiuwen.core.stream.base import OutputSchema, CustomSchema
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.schema import ToolInfo, Parameters

from openjiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.workflow.workflow_config import WorkflowInputsSchema, WorkflowMetadata

if TYPE_CHECKING:
    from openjiuwen.core.agent.controller.controller import Controller


class AgentRuntime(WrappedRuntime, StaticWrappedRuntime):
    """
    deprecated
    """

    def __init__(self, config: Config = None, resource_mgr: ResourceMgr = None):
        inner = StaticAgentRuntime(config, resource_mgr=resource_mgr)
        super().__init__(inner)
        self._runtime = inner

    async def write_stream(self, data: Union[dict, OutputSchema]):
        return await self.write_custom_stream(data)

    async def pre_run(self, **kwargs) -> Runtime:
        session_id = kwargs.get("session_id")
        if session_id is None:
            session_id = kwargs.get("trace_id")
        inputs = kwargs.get("inputs")
        inner = await self._runtime.create_agent_runtime(session_id, inputs)
        return TaskRuntime(inner=inner)

    def resource_mgr(self):
        return self._inner.resource_manager()

    async def release(self, session_id: str):
        await self._runtime.checkpointer().release(session_id)


class Agent(ABC):
    """
    DEPRECATED: This class is deprecated and will be removed in a future version.
    
    Please use BaseAgent or ControllerAgent instead:
    - BaseAgent: Minimal interface for new agent implementations
    - ControllerAgent: Agent with built-in controller support
    
    Legacy documentation:
    The top-level abstract class and the common base class for all Agents.
    Subclasses must implement:
        - invoke : synchronous one-time call
        - stream : streaming call
    """

    def __init__(self, config: Config) -> None:
        # Emit deprecation warning
        warnings.warn(
            f"{self.__class__.__name__} inherits from deprecated Agent class. "
            "Please migrate to BaseAgent or ControllerAgent. "
            "Agent class will be removed in a future version.",
            DeprecationWarning,
            stacklevel=2
        )

        # All core attributes initialized uniformly in base class
        self._config = config
        self._runtime = AgentRuntime(config=config)
        self._context_engine = self._create_context_engine()
        self._message_handler_class = None

    def config(self) -> Config:
        """Get Agent configuration"""
        return self._config

    @property
    def context_engine(self) -> ContextEngine:
        """Get Context Engine - Unified public interface"""
        return self._context_engine

    def set_message_handler(self, message_handler_class):
        """Set MessageHandler class - Subclasses call this method in constructor to inject concrete implementation
        
        Args:
            message_handler_class: MessageHandler class (not instance)
        """
        self._message_handler_class = message_handler_class

    def get_message_handler(self):
        """Get current MessageHandler class"""
        return self._message_handler_class

    @abstractmethod
    async def invoke(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        pass

    @abstractmethod
    async def stream(self, inputs: Dict, runtime: Runtime = None) -> Iterator[Any]:
        pass

    async def forward(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        """Agent's forward method, invoke the agent with inputs."""
        return await self.invoke(inputs, runtime)

    def _create_context_engine(self) -> ContextEngine:
        """Create ContextEngine - Internal method, called during base class initialization"""
        agent_config = self._config.get_agent_config()
        max_rounds = agent_config.constrain.reserved_max_chat_rounds
        context_config = ContextEngineConfig(
            conversation_history_length=max_rounds * 2
        )
        return ContextEngine(
            agent_id=self._config.get_agent_config().id,
            config=context_config,
            model=None
        )

    def _create_controller(self, runtime: Runtime) -> "Controller":
        """Create Controller instance"""
        from openjiuwen.core.agent.controller.controller import Controller

        if self._message_handler_class is None:
            return None

        # Instantiate MessageHandler
        message_handler = self._message_handler_class(
            self._config.get_agent_config(),
            self._context_engine,
            runtime
        )

        return Controller(
            self._config.get_agent_config(),
            self._context_engine,
            runtime,
            message_handler
        )

    @staticmethod
    def unwrap_result(result):
        """Unwrap OutputSchema result - Public method that subclasses can call directly"""
        if isinstance(result, list):
            if not result:
                return result
            if isinstance(result[0], OutputSchema):
                if len(result) == 1 and result[0].type == "workflow_final":
                    return result[0].payload
                return result
            return result

        if isinstance(result, OutputSchema):
            payload = result.payload
            if isinstance(payload, dict):
                if 'output' in payload and isinstance(payload['output'], str):
                    payload['output'] = payload['output'].strip()
            return payload

        return result

    async def controller_invoke(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        """Controller-based synchronous invocation - Public method that subclasses can call directly"""
        session_id = inputs.pop("conversation_id", "default_session")

        if runtime is None:
            agent_runtime = await self._runtime.pre_run(session_id=session_id)
        else:
            agent_runtime = runtime

        controller = None
        try:
            controller = self._create_controller(agent_runtime)
            await controller.start()
            result = await controller.process_inputs(inputs)

            logger.info("Controller completed, closing stream")

            if runtime is None:
                await agent_runtime.post_run()

            if result is not None:
                return self.unwrap_result(result)

            return {"output": "Message processed by scheduler", "result_type": "answer"}
        except Exception as e:
            if UserConfig.is_sensitive():
                logger.info(f"{self.__class__.__name__} invoke error.")
            else:
                logger.error(f"{self.__class__.__name__} invoke error: {e}")
            raise
        finally:
            if controller:
                await controller.stop()

    async def controller_stream(self, inputs: Dict, runtime: Runtime = None) -> AsyncIterator[Any]:
        """Controller-based streaming invocation - Public method that subclasses can call directly"""
        session_id = inputs.pop("conversation_id", "default_session")

        if runtime is None:
            agent_runtime = await self._runtime.pre_run(session_id=session_id)
        else:
            agent_runtime = runtime

        async def stream_process():
            controller = None
            try:
                controller = self._create_controller(agent_runtime)
                await controller.start()
                await controller.process_inputs(inputs)
                logger.info("Controller completed, workflow done")
            except Exception as e:
                if UserConfig.is_sensitive():
                    logger.info(f"{self.__class__.__name__} stream error.")
                else:
                    logger.error(f"{self.__class__.__name__} stream error: {e}")
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          f"{self.__class__.__name__} stream error.")
            finally:
                if controller:
                    await controller.stop()
                if runtime is None:
                    await agent_runtime.post_run()

        task = asyncio.create_task(stream_process())
        async for result in agent_runtime.stream_iterator():
            yield result

        try:
            await task
        except Exception as e:
            logger.error(f"{self.__class__.__name__} stream error.")
            if UserConfig.is_sensitive():
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          f"{self.__class__.__name__} stream error.")
            else:
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          f"{self.__class__.__name__} stream error.") from e

    async def runner_controller_stream(self, inputs: Dict, runtime: Runtime):
        """
        Interface adapted for runner, will be replaced with controller_stream
        after all agents fully adapt to runner
        """
        controller = None
        try:
            controller = self._create_controller(runtime)
            await controller.start()
            await controller.process_inputs(inputs)
            logger.info("Controller completed, workflow done")
        except Exception as e:
            if UserConfig.is_sensitive():
                logger.info(f"{self.__class__.__name__} stream error.")
            else:
                logger.error(f"{self.__class__.__name__} stream error: {e}")
            raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                      f"{self.__class__.__name__} stream error.")
        finally:
            if controller:
                await controller.stop()

    def bind_workflows(self, workflows: List[Workflow]):
        """Bind workflows to Agent
        
        Args:
            workflows: List of workflow instances
        """
        workflow_items = [
            (generate_workflow_key(
                workflow.config().metadata.id,
                workflow.config().metadata.version
            ), workflow)
            for workflow in workflows
        ]
        self._runtime.add_workflows(workflow_items)

        for workflow in workflows:
            metadata = workflow.config().metadata
            workflow_schema = WorkflowSchema(
                id=metadata.id,
                name=metadata.name,
                version=metadata.version,
                description=metadata.description
            )
            self._config.get_agent_config().workflows.append(workflow_schema)

    def bind_tools(self, tools: List[Tool]):
        """Bind tools to Agent
        
        Args:
            tools: List of tool instances
        """
        # Filter only supported tool types
        tool_items = [
            (tool.name, tool)
            for tool in tools
            if isinstance(tool, (RestfulApi, LocalFunction))
        ]
        self._runtime.add_tools(tool_items)

        for tool in tools:
            self._config.get_agent_config().tools.append(tool.name)

    def get_llm_calls(self) -> Dict:
        raise NotImplementedError("")

    def copy(self) -> "Agent":
        raise NotImplementedError("")


class WorkflowFactory:
    """Workflow factory class that creates a new workflow instance on each call (concurrency-safe).

    Usage:
        # Method 1: Use decorator (recommended, most concise)
        @workflow_provider(workflow_id="my_workflow", workflow_version="1.0")
        def create_workflow():
            return Workflow()  # No need to set metadata

        agent.add_workflows([create_workflow])

        # Method 2: Direct instantiation
        provider = WorkflowFactory("my_workflow", "1.0", lambda: build_workflow())
        agent.add_workflows([provider])

    Features:
        - Callable: provider() returns a new workflow instance each time
        - Provides id/version attributes for workflow key generation
        - Auto-sets workflow metadata on each call
    """

    def __init__(
            self,
            workflow_id: str,
            workflow_version: str,
            factory: Callable[[], Workflow],
            workflow_name: str = '',
            workflow_description: str = '',
            input_schema=None,
    ):
        """
        Args:
            workflow_id: Workflow ID for registration
            workflow_version: Workflow version for registration
            factory: Factory function that returns a new Workflow instance on each call
        """
        self._factory = factory
        self.id = workflow_id
        self.version = workflow_version
        self.name = workflow_name
        self.input_schema = input_schema if input_schema else {}
        self.workflow_description = workflow_description
        self._metadata = WorkflowMetadata(id=workflow_id, version=workflow_version, name=workflow_name)
        if self.name and self.input_schema:
            workflow_input_schema = self.input_schema if isinstance(self.input_schema,
                                                                    WorkflowInputsSchema) else WorkflowInputsSchema.model_validate(
                self.input_schema)
            self._tool_info = self._convert_to_tool_info(workflow_input_schema)
            from openjiuwen.core.runner.runner import resource_mgr
            resource_mgr.workflow()._workflow_tool_infos[
                generate_workflow_key(workflow_id, workflow_version)] = self._convert_to_tool_info(
                workflow_input_schema)
        else:
            self._tool_info = None

    def _register_tool_info(self, runtime: AgentRuntime):
        if self._tool_info and runtime:
            runtime.resource_mgr().workflow()._workflow_tool_infos[
                generate_workflow_key(self.id, self.version)] = deepcopy(self._tool_info)

    def _convert_to_tool_info(self, workflow_input_schema) -> ToolInfo:
        parameters = Parameters(
            type=workflow_input_schema.type,
            properties=workflow_input_schema.properties,
            required=workflow_input_schema.required
        )
        return ToolInfo(
            name=self.name,
            description=self.workflow_description,
            parameters=parameters,
        )

    def __call__(self):
        """Return a new workflow instance on each call, with metadata auto-set.

        Supports both sync and async factory functions:
        - Sync factory: returns Workflow directly
        - Async factory: returns coroutine that resolves to Workflow
        """
        result = self._factory()

        # Handle async factory (returns coroutine)
        if asyncio.iscoroutine(result) or inspect.iscoroutinefunction(self._factory):
            async def async_wrapper():
                workflow = await result if asyncio.iscoroutine(result) else await self._factory()
                return workflow

            return async_wrapper()
        return result


def workflow_provider(workflow_id: str, workflow_version: str, workflow_name: str = '', workflow_description: str = '',
                      inputs: Union[dict, WorkflowInputsSchema] = None):
    """Decorator to create a WorkflowFactory from a factory function.

    Usage:
        @workflow_provider(workflow_id="weather_workflow", workflow_version="1.0")
        def create_weather_workflow():
            flow = Workflow()
            # ... build workflow ...
            return flow

        agent.add_workflows([create_weather_workflow])

    Args:
        workflow_id: Workflow ID for registration
        workflow_version: Workflow version for registration

    Returns:
        Decorator that wraps a factory function as WorkflowFactory
    """

    def decorator(func: Callable[[], Workflow]) -> WorkflowFactory:
        return WorkflowFactory(workflow_id, workflow_version, func, workflow_name, workflow_description, inputs)

    return decorator


class BaseAgent(ABC):
    """Base Agent - Minimal interface definition (new architecture)
    """

    def __init__(self, agent_config):
        """Initialize Agent
        
        Args:
            agent_config: Agent configuration
        """
        # 1. Create Config wrapper (backward compatible)
        self._config_wrapper = Config()
        self._config_wrapper.set_agent_config(agent_config)
        self.agent_config = agent_config
        self._config = self._config_wrapper  # Unified interface

        # 2. Create Runtime
        self._runtime = AgentRuntime(config=self._config)

        # 3. Create ContextEngine
        self._context_engine = self._create_context_engine()

        # 4. Uniformly hold tools and workflows (eliminate subclass duplication)
        self._tools: List[Tool] = []
        self._workflows: List[Workflow] = []

    def config(self) -> Config:
        """Get Config wrapper - Backward compatible method interface
        
        Returns:
            Config instance (contains get_agent_config() method)
        """
        return self._config_wrapper

    @property
    def tools(self) -> List[Tool]:
        """Get tools list - Read-only access for subclasses"""
        return self._tools

    @property
    def workflows(self) -> List[Workflow]:
        """Get workflows list - Read-only access for subclasses"""
        return self._workflows

    @property
    def context_engine(self) -> ContextEngine:
        """Get Context Engine - Unified public interface"""
        return self._context_engine

    def _create_context_engine(self) -> ContextEngine:
        """Create ContextEngine - Internal method, called during base class initialization"""
        # Get max conversation rounds configuration
        if (hasattr(self.agent_config, 'constrain') and
                hasattr(self.agent_config.constrain, 'reserved_max_chat_rounds')):
            max_rounds = self.agent_config.constrain.reserved_max_chat_rounds
        else:
            max_rounds = 10  # Default value

        context_config = ContextEngineConfig(
            conversation_history_length=max(max_rounds, 100) * 2
        )
        return ContextEngine(
            agent_id=self.agent_config.id,
            config=context_config,
            model=None
        )

    @abstractmethod
    async def invoke(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        """Synchronous invocation entry point - Abstract method
        
        Subclasses must implement this method
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement invoke() method"
        )

    @abstractmethod
    async def stream(self, inputs: Dict, runtime: Runtime = None) -> AsyncIterator[Any]:
        """Streaming invocation entry point - Abstract method
        
        Subclasses must implement this method
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement stream() method"
        )

    # ===== Dynamic configuration interface (Plan A: Backward compatible) =====

    def add_prompt(self, prompt_template: List[Dict]) -> None:
        """Add Prompt template
        
        Args:
            prompt_template: Prompt template list, format like
                [{"role": "system", "content": "..."}]
        
        Note:
        - This method only updates configuration, does not affect already created runtime
        - Subclasses should override this method if they need to sync runtime
        """
        # Check if configuration has prompt_template field
        if hasattr(self.agent_config, 'prompt_template'):
            # Append mode: Keep original prompt, add new prompt
            self.agent_config.prompt_template.extend(prompt_template)
        else:
            config_class_name = self.agent_config.__class__.__name__
            logger.warning(
                f"{config_class_name} has no prompt_template field, "
                "add_prompt operation ignored"
            )

    def add_tools(self, tools: List[Tool]) -> None:
        """Add tools (update config, runtime, and self._tools simultaneously)
        
        Args:
            tools: List of tool instances
        """

        for tool in tools:
            # 1. Add tool name to config.tools
            if tool.name not in self.agent_config.tools:
                self.agent_config.tools.append(tool.name)

            # 2. Generate PluginSchema (if configuration supports)
            if hasattr(self.agent_config, 'plugins'):
                # Check if already exists
                existing_plugin_names = {
                    p.name for p in self.agent_config.plugins
                }
                if tool.name not in existing_plugin_names:
                    plugin_schema = self._tool_to_plugin_schema(tool)
                    self.agent_config.plugins.append(plugin_schema)

            # 3. Add to self._tools (avoid duplication)
            existing_tool_names = {t.name for t in self._tools}
            if tool.name not in existing_tool_names:
                self._tools.append(tool)

            # 4. Sync to runtime (auto register)
            self._runtime.add_tools([(tool.name, tool)])

    def add_workflows(
            self,
            workflows: List[Union[Workflow, Callable[[], Workflow]]]
    ) -> None:
        """Add workflows (update config and runtime simultaneously).
        
        Supports three registration methods:
        1. Workflow instance - registered directly (note: does not support concurrent calls)
        2. WorkflowFactory object - registered directly (concurrency-safe, recommended)
        3. Callable with id/version attributes - async/sync provider (concurrency-safe)
        
        Args:
            workflows: List of workflow instances or WorkflowFactory/provider objects
        
        Concurrency Notes:
            - Instance: multiple conversations share the same instance, not concurrency-safe
            - WorkflowFactory or provider with id/version: new instance on each get_workflow()
            
        Recommended Usage (concurrent scenarios):
            # Method 1: Use @workflow_provider decorator (most concise)
            @workflow_provider(workflow_id="my_wf", workflow_version="1.0")
            def create_workflow():
                return Workflow()
            agent.add_workflows([create_workflow])
            
            # Method 2: Use WorkflowFactory directly
            provider = WorkflowFactory("my_wf", "1.0", lambda: build_workflow())
            agent.add_workflows([provider])
            
            # Method 3: Async provider with id/version attributes
            async def _create_provider(wf, mgr):
                async def provider():
                    return await wf.compile(mgr)
                provider.id = wf.id
                provider.version = wf.version
                return provider
            providers = [await _create_provider(wf, mgr) for wf in workflows]
            agent.add_workflows(providers)
        """
        logger.info(f"BaseAgent.add_workflows called with {len(workflows)} workflows")

        for item in workflows:
            # Extract workflow_id, workflow_version, and provider/workflow
            workflow_id = None
            workflow_version = None
            workflow_name = None
            workflow_description = None

            if isinstance(item, WorkflowFactory):
                # WorkflowFactory object: use id/version attributes
                provider = item
                workflow_id = provider.id
                workflow_version = provider.version
                item._register_tool_info(self._runtime)
                is_provider = True
            elif callable(item) and hasattr(item, 'id') and hasattr(item, 'version'):
                # Callable with id/version attributes (preferred way for async providers)
                provider = item
                workflow_id = getattr(item, 'id')
                workflow_version = getattr(item, 'version')
                # Optional: get name and description if available
                workflow_name = getattr(item, 'name', None)
                workflow_description = getattr(item, 'description', None)
                is_provider = True
            elif callable(item):
                # Bare callable without id/version: error
                raise ValueError(
                    f"Callable workflow provider must have 'id' and 'version' attributes. "
                    f"Use @workflow_provider decorator or WorkflowFactory class."
                )
            else:
                # Workflow instance: use directly
                workflow = item
                workflow_config = workflow.config()
                workflow_id = workflow_config.metadata.id
                workflow_version = workflow_config.metadata.version
                workflow_name = workflow_config.metadata.name
                workflow_description = workflow_config.metadata.description
                provider = None
                is_provider = False

            workflow_key = generate_workflow_key(workflow_id, workflow_version)

            # Check if already exists
            existing_keys = {
                generate_workflow_key(w.id, w.version)
                for w in self.agent_config.workflows
            }
            logger.info(
                f"Workflow {workflow_key}: existing_keys={existing_keys}, "
                f"exists={workflow_key in existing_keys}, is_provider={is_provider}"
            )

            # Even if schema exists, still need to add workflow
            if workflow_key not in existing_keys:
                # 1. Update config.workflows
                workflow_schema = WorkflowSchema(
                    id=workflow_id,
                    name=workflow_name or workflow_id,
                    version=workflow_version,
                    description=workflow_description or "",
                    inputs={}
                )
                self.agent_config.workflows.append(workflow_schema)

            # 2. Sync to runtime (provider or instance)
            to_register = provider if is_provider else workflow
            self._runtime.add_workflows([(workflow_key, to_register)])

            # 3. Also add to global resource_mgr (for cross-runtime access)
            try:
                from openjiuwen.core.runner.runner import resource_mgr
                logger.info(f"Adding workflow {'provider' if is_provider else 'instance'} "
                            f"{workflow_key} to global resource_mgr")
                resource_mgr.workflow().add_workflow(workflow_key, to_register)
                logger.info(f"Successfully added workflow {'provider' if is_provider else 'instance'} {workflow_key}")
            except Exception as e:
                logger.error(f"Failed to add workflow to global resource_mgr: {e}")

    def remove_workflows(
            self,
            workflows: List[Tuple[str, str]]
    ) -> None:
        """Remove workflows from agent (update config and runtime simultaneously).
        
        Removes workflows from three locations:
        1. agent_config.workflows (WorkflowSchema list)
        2. runtime workflow manager
        3. global resource_mgr (if available)
        
        Args:
            workflows: List of (workflow_id, workflow_version) tuples to remove
            
        Example:
            agent.remove_workflows([
                ("my_workflow", "1.0"),
                ("another_workflow", "2.0")
            ])
        """
        logger.info(f"BaseAgent.remove_workflows called with {len(workflows)} workflows")

        for workflow_id, workflow_version in workflows:
            workflow_key = generate_workflow_key(workflow_id, workflow_version)
            logger.info(f"Removing workflow: {workflow_key}")

            # 1. Remove from agent_config.workflows
            original_count = len(self.agent_config.workflows)
            self.agent_config.workflows = [
                w for w in self.agent_config.workflows
                if not (w.id == workflow_id and w.version == workflow_version)
            ]
            removed_from_config = original_count - len(self.agent_config.workflows)
            logger.info(f"Removed {removed_from_config} workflow schema(s) from config")

            # 2. Remove from runtime
            self._runtime.remove_workflow(workflow_key)
            logger.info(f"Removed workflow {workflow_key} from runtime")

            # 3. Remove from global resource_mgr
            try:
                from openjiuwen.core.runner.runner import resource_mgr
                resource_mgr.workflow().remove_workflow(workflow_key)
                logger.info(f"Successfully removed workflow {workflow_key} from global resource_mgr")
            except Exception as e:
                logger.error(f"Failed to remove workflow from global resource_mgr: {e}")

    def bind_workflows(self, workflows: List[Workflow]) -> None:
        """Bind workflows - Backward compatible alias method
        
        Args:
            workflows: List of workflow instances
        """
        self.add_workflows(workflows)

    def add_plugins(self, plugins: List) -> None:
        """Add plugin Schema
        
        Args:
            plugins: PluginSchema list
        
        Note:
        - This method only updates plugins field in configuration
        - Subclasses should override this method if they need to sync runtime
        """
        if hasattr(self.agent_config, 'plugins'):
            # Check duplication
            existing_names = {p.name for p in self.agent_config.plugins}
            for plugin in plugins:
                if plugin.name not in existing_names:
                    self.agent_config.plugins.append(plugin)
                    existing_names.add(plugin.name)
        else:
            config_class_name = self.agent_config.__class__.__name__
            logger.warning(
                f"{config_class_name} has no plugins field, "
                "add_plugins operation ignored"
            )

    def _tool_to_plugin_schema(self, tool: Tool):
        """Convert Tool instance to PluginSchema
        
        This is an internal method for automatically generating plugin schema
        
        Args:
            tool: Tool instance
            
        Returns:
            PluginSchema: Plugin schema object
        """
        # Generate inputs from tool.params
        inputs = {
            "type": "object",
            "properties": {},
            "required": []
        }

        if hasattr(tool, 'params') and tool.params:
            for param in tool.params:
                prop = {
                    "type": param.type,
                    "description": param.description
                }
                inputs["properties"][param.name] = prop
                if param.required:
                    inputs["required"].append(param.name)

        tool_description = ""
        if hasattr(tool, 'description'):
            tool_description = tool.description

        return PluginSchema(
            id=tool.name,
            name=tool.name,
            description=tool_description,
            inputs=inputs
        )

    async def clear_session(self, session_id: str = "default_session"):
        await self._runtime.release(session_id)


class ControllerAgent(BaseAgent):
    """Agent that holds Controller (new architecture)
    """

    def __init__(self, agent_config, controller=None):
        """Initialize ControllerAgent
        
        Args:
            agent_config: Agent configuration
            controller: Optional Controller instance (will be auto-configured)
            
        Note:
            If controller is provided, it will be automatically configured with
            config, context_engine and runtime from this agent via setup_from_agent()
            
        Usage:
            # Simplest way - controller auto-configured:
            controller = WorkflowController()  # No parameters needed
            agent = ControllerAgent(config=config, controller=controller)
            
            # Alternative - set controller after agent creation:
            agent = ControllerAgent(config=config)
            agent.controller = WorkflowController()  # Will be auto-configured
        """
        super().__init__(agent_config)
        self.controller = controller

        # Auto-configure controller if provided
        if self.controller is not None:
            self._setup_controller()

    def _setup_controller(self):
        """Setup controller with agent's config, context_engine and runtime"""
        if hasattr(self.controller, 'setup_from_agent'):
            self.controller.setup_from_agent(self)

    @property
    def controller(self):
        """Get controller"""
        return self._controller

    @controller.setter
    def controller(self, value):
        """Set controller and auto-configure it"""
        self._controller = value
        # Auto-configure when setting controller
        # Only if agent is already initialized (has _context_engine)
        if value is not None and hasattr(self, '_context_engine'):
            self._setup_controller()

    async def invoke(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        """Synchronous invocation - Fully delegate to controller
        
        Args:
            inputs: Input data
            runtime: Runtime instance (if None, auto create)
        
        Returns:
            Execution result
        """
        if not self.controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no controller, "
                "subclass should create controller before invocation"
            )

        # If runtime not provided, create one
        session_id = inputs.get("conversation_id", "default_session")
        if runtime is None:
            agent_runtime = await self._runtime.pre_run(session_id=session_id)
        else:
            agent_runtime = runtime

        try:
            # Fully delegate to controller
            result = await self.controller.invoke(inputs, agent_runtime)
            if runtime is None:
                await agent_runtime.post_run()

            return result
        except Exception as e:
            await agent_runtime.post_run()
            raise

    async def stream(self, inputs: Dict, runtime: Runtime = None) -> AsyncIterator[Any]:
        """Streaming invocation - Fully delegate to controller
        
        Args:
            inputs: Input data
            runtime: Runtime instance (if None, auto create)
        
        Yields:
            Streaming output
        
        Note:
            When external runtime is provided, data is written to it but not read
            from stream_iterator (to avoid nested read deadlock). External caller
            reads stream data from runtime.
        """
        if not self.controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no controller, "
                "subclass should create controller before invocation"
            )

        # If runtime not provided, create one
        session_id = inputs.get("conversation_id", "default_session")
        if runtime is None:
            agent_runtime = await self._runtime.pre_run(session_id=session_id)
            need_cleanup = True
            own_stream = True  # Owns stream lifecycle
        else:
            agent_runtime = runtime
            need_cleanup = False
            own_stream = False  # External owns stream lifecycle

            # Sync agent's tools to external runtime
            # When external runtime is provided, agent's tools need to be registered
            if self._tools:
                tools_to_add = [(tool.name, tool) for tool in self._tools]
                agent_runtime.add_tools(tools_to_add)
            # Sync agent's workflows to external runtime
            # When external runtime is provided, agent's workflows need to be registered
            try:
                agent_workflow_mgr = self._runtime.resource_mgr().workflow()
                # Sync workflow instances and providers
                for workflow_id, workflow in agent_workflow_mgr.get_all_workflows().items():
                    agent_runtime.add_workflow(workflow_id, workflow)
                    logger.debug(f"Synced workflow {workflow_id} to external runtime")
            except Exception as e:
                logger.warning(f"Failed to sync workflows to external runtime: {e}")
        # Store final result for send_to_agent
        final_result_holder = {"result": None}

        # Fully delegate to controller
        async def stream_process():
            try:
                res = await self.controller.invoke(inputs, agent_runtime)
                final_result_holder["result"] = res
                # Interrupt: list contains __interaction__ OutputSchema
                # Only WorkflowController writes to runtime here
                # Other controllers (e.g. HierarchicalMainController) forward
                # lower agent results, which already wrote to shared runtime
                from openjiuwen.agent.workflow_agent.workflow_controller import (
                    WorkflowController
                )
                if isinstance(res, list) and isinstance(self.controller, WorkflowController):
                    for item in res:
                        if isinstance(item, CustomSchema):
                            await agent_runtime.write_custom_stream(item)
                        else:
                            await agent_runtime.write_stream(item)
            finally:
                if need_cleanup:
                    await agent_runtime.post_run()

        task = asyncio.create_task(stream_process())

        if own_stream:
            # Read from stream_iterator only when owning stream
            # External caller reads if external runtime provided
            async for result in agent_runtime.stream_iterator():
                yield result

        await task

        # When own_stream=False, yield final result to send_to_agent
        # so send_to_agent can get agent's actual return value
        if not own_stream and final_result_holder["result"] is not None:
            res = final_result_holder["result"]
            if isinstance(res, list):
                # Interrupt: return list (contains __interaction__)
                for item in res:
                    yield item
            else:
                # Normal completion: yield dict or other result
                yield res

    async def clear_session(self, session_id: str = "default_session"):
        await self._runtime.release(session_id)
        self.context_engine.clear_context(session_id)
        await self.controller.cleanup_conversation(session_id)
