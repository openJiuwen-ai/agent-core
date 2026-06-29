# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ReActAgent Implementation
ReAct (Reasoning + Acting) paradigm Agent implementation

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import copy
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from pydantic import Field, BaseModel

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.foundation.llm.schema.tool_call_delta import serialize_tool_call_delta
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig
)
from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
    ModelContext
)
from openjiuwen.core.context_engine.observability import (
    resolve_context_trace_ids,
    snapshot_messages,
    write_context_trace,
    context_trace_enabled,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    ToolMessage,
    UserMessage,
    SystemMessage
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler, ResumeContext
from openjiuwen.core.single_agent.interrupt.state import (
    BaseInterruptionState,
    RESUME_START_ITERATION_KEY,
    ToolInterruptionState,
    INTERRUPTION_KEY
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    rail,
)
from openjiuwen.core.single_agent.prompts.builder import (
    PromptSection,
    SystemPromptBuilder,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.operator import StreamingToolExecutor

_IDENTITY_SECTION = "identity"
_STREAMING_TOOL_EXECUTOR_KEY = "_streaming_tool_executor"
_SKILLS_SECTION = "skills"
_IDENTITY_SECTION_PRIORITY = 10
_SKILLS_SECTION_PRIORITY = 90


def _summarize_tool_call(tc: Any) -> str:
    """Format a single tool call for logging."""
    if isinstance(tc, dict):
        fn = tc.get("function", {})
        return f"{fn.get('name', '?')}({str(fn.get('arguments', ''))[:100]})"
    fn = getattr(tc, "function", tc)
    name = getattr(fn, "name", getattr(tc, "name", "?"))
    args = str(getattr(fn, "arguments", getattr(tc, "arguments", "")))[:100]
    return f"{name}({args})"


def log_llm_request(
        log: Any,
        messages: Optional[List[Any]],
        tools: Optional[List[Any]],
) -> None:
    """Log LLM request messages and tools."""
    msgs = messages or []
    tool_count = len(tools) if tools else 0
    log.info(
        f"[LLM] >>> request: msg_count={len(msgs)}, "
        f"tool_count={tool_count}"
    )
    if UserConfig.is_sensitive():
        return
    for idx, msg in enumerate(msgs):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id", "")
        else:
            role = getattr(msg, "role", "")
            content = str(getattr(msg, "content", ""))
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", "")
        parts: List[str] = [f"[LLM]   msg[{idx}] role={role}"]
        if content:
            parts.append(f"content={content[:300]}")
        if tool_calls:
            tc_summary = [_summarize_tool_call(tc) for tc in tool_calls]
            parts.append(f"tool_calls=[{', '.join(tc_summary)}]")
        if tool_call_id:
            parts.append(f"tool_call_id={tool_call_id}")
        log.info(", ".join(parts))


def log_llm_response(log: Any, ai_message: Any) -> None:
    """Log LLM response content and tool calls."""
    usage = getattr(ai_message, "usage_metadata", None)
    usage_str = ""
    if usage:
        usage_str = (
            f", tokens={{input={getattr(usage, 'input_tokens', '?')}, "
            f"output={getattr(usage, 'output_tokens', '?')}}}"
        )
    if UserConfig.is_sensitive():
        tc_count = len(ai_message.tool_calls) if ai_message.tool_calls else 0
        log.info(
            f"[LLM] <<< response: "
            f"content_len={len(ai_message.content or '')}, "
            f"tool_call_count={tc_count}{usage_str}"
        )
    else:
        log.info(
            f"[LLM] <<< response: "
            f"content={ai_message.content or ''}{usage_str}"
        )
        if ai_message.tool_calls:
            for tc in ai_message.tool_calls:
                log.info(
                    f"[LLM]   tool_call: "
                    f"{tc.name}({tc.arguments})"
                )


class ReActAgentConfig(BaseModel):
    """ReActAgent Configuration Class

    Attributes:
        max_iterations: Maximum number of ReAct loop iterations
    """
    mem_scope_id: str = Field(default="", description="Memory scope ID")
    model_name: str = Field(default="", description="Model name")
    model_provider: str = Field(default="openai", description="Model provider")
    api_key: str = Field(default="", description="API key")
    api_base: str = Field(default="", description="API base URL")
    custom_headers: Optional[dict[str, Any]] = Field(default=None, description="Additional headers for LLM requests")
    prompt_template_name: str = Field(
        default="",
        description="Prompt template name"
    )
    prompt_template: List[Dict] = Field(
        default_factory=list,
        description="Prompt template list"
    )

    max_iterations: int = Field(default=5, description="Maximum iterations")

    # LLM configuration objects (for Model initialization)
    model_client_config: Optional[ModelClientConfig] = Field(
        default=None,
        description="Model client configuration"
    )
    model_config_obj: Optional[ModelRequestConfig] = Field(
        default=None,
        description="Model request configuration"
    )

    sys_operation_id: Optional[str] = None

    context_engine_config: ContextEngineConfig = Field(
        default=ContextEngineConfig(
            max_context_message_num=200,
            default_window_round_num=10
        ),
        description="Context engine configuration"
    )

    context_processors: List[Tuple[str, BaseModel]] = Field(
        default=None,
        description="Context processors configuration"
    )

    workspace: Optional[Any] = Field(default=None, description="Workspace instance for filesystem operations")

    def configure_model(self, model_name: str) -> 'ReActAgentConfig':
        """Configure model name

        Args:
            model_name: Model name

        Returns:
            self (supports chaining)
        """
        self.model_name = model_name
        return self

    def configure_model_provider(
            self,
            provider: str,
            api_key: str,
            api_base: str
    ) -> 'ReActAgentConfig':
        """Configure model provider details

        Args:
            provider: Model provider name (e.g., "openai")
            api_key: API key
            api_base: API base URL

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
        return self

    def configure_prompt(self, prompt_name: str) -> 'ReActAgentConfig':
        """Configure prompt template name

        Args:
            prompt_name: Prompt template name

        Returns:
            self (supports chaining)
        """
        self.prompt_template_name = prompt_name
        return self

    def configure_prompt_template(
            self,
            prompt_template: List[Dict]
    ) -> 'ReActAgentConfig':
        """Configure prompt template directly

        Args:
            prompt_template: Prompt template list, format like
                [{"role": "system", "content": "..."}]

        Returns:
            self (supports chaining)
        """
        self.prompt_template = prompt_template
        return self

    def configure_context_engine(
            self,
            max_context_message_num: Optional[int] = 200,
            default_window_round_num: Optional[int] = 10,
            enable_reload: bool = False,
            enable_kv_cache_release: bool = False,
    ) -> 'ReActAgentConfig':
        """
        Configure the context-engine parameters that control how conversation history
        is truncated, offloaded and reloaded.

        Parameters
        ----------
        max_context_message_num : int, optional, default 200
            Hard upper bound on the total number of messages kept in the context
            window.  `None` means no hard limit.
        default_window_round_num : int, optional, default 10
            Number of **most-recent conversation rounds** to retain (a round =
            user message → final assistant reply without tool calls).  When set,
            it takes precedence over `default_window_message_num`.  Must be > 0
            if given.
        enable_reload : bool, default False
            Whether the agent is allowed to **automatically reload** messages that
            were previously off-loaded (via hints such as `[[OFFLOAD:...]]`).
            Enable this if you want the model to retrieve long content on demand;
            disable it to keep hints as plain text.
        enable_kv_cache_release : bool, default False
            Whether to release GPU KV-cache for offloaded messages via the
            inference backend (e.g. InferenceAffinity).  Matches
            ``ContextEngineConfig.enable_kv_cache_release``.
        """
        self.context_engine_config = ContextEngineConfig(
            max_context_message_num=max_context_message_num,
            default_window_round_num=default_window_round_num,
            enable_reload=enable_reload,
            enable_kv_cache_release=enable_kv_cache_release,
        )
        return self

    def configure_mem_scope(self, mem_scope_id: str) -> 'ReActAgentConfig':
        """Configure memory scope ID

        Args:
            mem_scope_id: Memory scope ID

        Returns:
            self (supports chaining)
        """
        self.mem_scope_id = mem_scope_id
        return self

    def configure_max_iterations(
            self,
            max_iterations: int
    ) -> 'ReActAgentConfig':
        """Configure maximum iterations

        Args:
            max_iterations: Maximum number of ReAct loop iterations

        Returns:
            self (supports chaining)
        """
        self.max_iterations = max_iterations
        return self

    def configure_model_client(
            self,
            provider: str,
            api_key: str,
            api_base: str,
            model_name: str,
            verify_ssl: bool = False,
    ) -> 'ReActAgentConfig':
        """Configure model client for LLM initialization

        This method creates ModelClientConfig and ModelRequestConfig
        for the Model class initialization.

        Args:
            provider: Model provider name (e.g., "OpenAI", "SiliconFlow")
            api_key: API key
            api_base: API base URL
            model_name: Model name
            verify_ssl: Whether to verify SSL (default False)

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name

        self.model_client_config = ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            verify_ssl=verify_ssl,
            custom_headers=self.custom_headers,
        )
        if self.model_config_obj is None:
            self.model_config_obj = ModelRequestConfig(model_name=model_name)
        else:
            self.model_config_obj.model_name = model_name
        return self

    def configure_custom_headers(
            self,
            custom_headers: Optional[dict[str, Any]] = None,
    ) -> 'ReActAgentConfig':
        """Configure additional headers sent with each model request.

        Args:
            custom_headers: Additional headers sent with each model request

        Returns:
            self (supports chaining)
        """
        self.custom_headers = custom_headers
        if self.model_client_config is not None:
            self.model_client_config.custom_headers = custom_headers
        return self

    def configure_context_processors(
            self,
            processors: List[Tuple[str, BaseModel]]
    ) -> 'ReActAgentConfig':
        self.context_processors = processors
        return self


@dataclass
class WorkflowInterruptEntry:
    """Per-workflow interruption record."""
    tool_call: Any
    component_ids: List[str]
    workflow_execution_state: Any
    collected_input: Any = None  # None means not yet collected


class InterruptionState(BaseInterruptionState):
    """Workflow interruption state for resume support.

    interrupted_workflows: per-workflow entries keyed by workflow_id
    pending_workflow_id: workflow_id currently waiting for user feedback
    pending_component_id: component_id currently waiting for user feedback
    original_query: the original user query from the first invoke (before any resume)
    """
    interrupted_workflows: Dict[str, WorkflowInterruptEntry]
    pending_workflow_id: str
    pending_component_id: str


class ReActAgent(BaseAgent):
    """ReAct paradigm Agent implementation
    ReAct loop: Reasoning -> Acting -> Observation -> Repeat

    Input format (compatible with legacy):
        {"query": "user question", "conversation_id": "session_123"}

    Output format (compatible with legacy):
        invoke: {"output": "response content", "result_type": "answer|error"}
        stream: yields OutputSchema objects

    Note:
        This agent currently does not support Runner.run_agent().
        Use agent.invoke() directly with a session parameter.
    """

    def __init__(
            self,
            card: AgentCard,
    ):
        """Initialize ReActAgent

        Args:
            card: Agent card (required)
        """
        self._config = self._create_default_config()
        # Get sys_operation if configured
        sys_operation = None
        if self._config.sys_operation_id:
            from openjiuwen.core.runner import Runner
            sys_operation = Runner.resource_mgr.get_sys_operation(self._config.sys_operation_id)
        self.context_engine = ContextEngine(
            self._config.context_engine_config,
            workspace=self._config.workspace,
            sys_operation=sys_operation,
        )
        self._llm = None
        self.prompt_builder: SystemPromptBuilder = SystemPromptBuilder()
        self.system_prompt_builder: SystemPromptBuilder = self.prompt_builder
        super().__init__(card)
        self._hitl_handler = ToolInterruptHandler(self)
        self._ability_manager.set_context_engine(self.context_engine)
        self._kv_release_warning_logged: bool = False

    def _create_default_config(self) -> ReActAgentConfig:
        """Create default configuration"""
        return ReActAgentConfig()

    def configure(self, config: ReActAgentConfig) -> 'BaseAgent':
        """Set configuration

        Args:
            config: ReActAgentConfig configuration object

        Returns:
            self (supports chaining)

        Note:
            After config update, context_engine and memory_scope
            will be updated accordingly
        """
        old_config = self._config
        self._config = config

        # Reset LLM if model config changed
        if (old_config.model_provider != config.model_provider or
                old_config.api_key != config.api_key or
                old_config.api_base != config.api_base):
            self._llm = None
            self._kv_release_warning_logged = False

        # Get sys_operation from Runner.resource_mgr if sys_operation_id is configured
        sys_operation = None
        if config.sys_operation_id:
            from openjiuwen.core.runner import Runner
            sys_operation = Runner.resource_mgr.get_sys_operation(config.sys_operation_id)

        if (
            old_config.context_engine_config != config.context_engine_config
            or old_config.sys_operation_id != config.sys_operation_id
            or old_config.workspace != config.workspace
        ):
            self.context_engine = ContextEngine(
                config.context_engine_config,
                workspace=config.workspace,
                sys_operation=sys_operation,
            )
            self._ability_manager.set_context_engine(self.context_engine)
        if old_config.sys_operation_id != config.sys_operation_id:
            self.lazy_init_skill()

        # Always rebuild prompt_builder from prompt_template so it reflects the
        # new config. DeepAgent will replace this with the shared builder after
        # calling configure().
        system_content = "\n\n".join(
            msg["content"]
            for msg in config.prompt_template
            if msg.get("role") == "system" and msg.get("content")
        )
        self.prompt_builder = SystemPromptBuilder()
        self.system_prompt_builder = self.prompt_builder
        self.add_prompt_builder_section(
            _IDENTITY_SECTION,
            system_content,
            priority=_IDENTITY_SECTION_PRIORITY,
        )

        return self

    def set_llm(self, llm: Model) -> None:
        """Set LLM model instance directly.

        Args:
            llm: Pre-built Model instance to use.
        """
        self._llm = llm

    def _get_llm(self) -> Model:
        """Get LLM instance (lazy initialization)

        Returns:
            Model instance

        Raises:
            ValueError: If model_client_config is not configured
        """
        if self._llm is None:
            if self._config.model_client_config is None:
                raise ValueError(
                    "model_client_config is required. "
                    "Use configure_model_client() to set it."
                )
            self._llm = Model(
                model_client_config=self._config.model_client_config,
                model_config=self._config.model_config_obj
            )
        return self._llm

    def add_prompt_builder_section(
            self,
            name: str,
            content: Optional[str],
            *,
            priority: int,
    ) -> None:
        """Add/update one text section, or remove it when content is empty."""
        text = (content or "").strip()
        if not text:
            self.prompt_builder.remove_section(name)
            return

        self.prompt_builder.add_section(PromptSection(
            name=name,
            content={"cn": text, "en": text},
            priority=priority,
        ))

    def _build_rendered_system_prompt(
            self,
            inputs: Any,
            extra_render_fields: Optional[Dict[str, str]] = None,
    ) -> str:
        """Render system prompt_template messages and join them into one string."""
        system_messages = [
            SystemMessage(role=msg["role"], content=msg["content"])
            for msg in self._config.prompt_template
            if msg.get("role") == "system" and isinstance(msg.get("content"), str)
        ]
        self._render_system_messages(
            system_messages,
            inputs,
            extra_render_fields=extra_render_fields,
        )
        return "\n\n".join(
            msg.content for msg in system_messages
            if isinstance(msg.content, str) and msg.content
        )

    async def _update_skill_prompt_builder_section(
            self,
            rendered_system_prompt: str,
    ) -> None:
        """Update skills section on prompt_builder in the invoke-stage flow."""
        if not rendered_system_prompt or self._skill_util is None or not self._skill_util.has_skill():
            self.prompt_builder.remove_section(_SKILLS_SECTION)
            return

        await self._warn_missing_skill_read_file_tool()
        self.add_prompt_builder_section(
            _SKILLS_SECTION,
            self._skill_util.get_skill_prompt(),
            priority=_SKILLS_SECTION_PRIORITY,
        )

    def _build_preview_messages(self, context: ModelContext) -> List[Any]:
        """Build a read-only preview of the current model input messages.

        ``get_messages()`` returns a shallow slice of the buffer, so list-level
        mutations by BEFORE_MODEL_CALL rails won't pollute the context buffer.
        The preview is read-only by contract: mutations here do NOT reach the
        LLM — ``_railed_model_call`` rebuilds the final window from
        ``ctx.context`` via ``get_context_window`` and overwrites
        ``ctx.inputs.messages``. To alter history, operate on
        ``model_context``; to alter the system prompt, mutate
        ``self.prompt_builder``.
        """
        preview_messages = context.get_messages()
        preview_system_prompt = self.prompt_builder.build()
        if preview_system_prompt:
            preview_messages.insert(0, SystemMessage(content=preview_system_prompt))
        return preview_messages

    async def _call_model(
            self,
            ctx: AgentCallbackContext,
            context: ModelContext,
            tools: Optional[List[ToolInfo]],
    ) -> AssistantMessage:
        """Fire before_model_call rails then invoke the LLM.

        get_context_window is deferred to _railed_model_call so that
        ContextProcessor sees the final system message after all
        BEFORE_MODEL_CALL rails have updated self.prompt_builder.

        Args:
            ctx: Shared AgentCallbackContext for this invoke
            context: Current ModelContext
            tools: Tool definitions

        Returns:
            AssistantMessage from LLM
        """
        ctx.inputs = ModelCallInputs(
            messages=self._build_preview_messages(context),
            tools=list(tools) if tools else None,
            model_context=context,
        )

        ai_message = await self._railed_model_call(ctx)

        if ai_message is None:
            return None

        log_llm_response(logger, ai_message)

        return ai_message

    @rail(
        before=AgentCallbackEvent.BEFORE_MODEL_CALL,
        after=AgentCallbackEvent.AFTER_MODEL_CALL,
        on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION,
    )
    async def _railed_model_call(self, ctx: AgentCallbackContext) -> AssistantMessage:
        """Execute LLM call with @rail before/after/on_exception hooks.

        All BEFORE_MODEL_CALL rails have run at this point and may have
        added/removed sections on self.prompt_builder. build() is called
        once here so ContextProcessor receives the accurate final token
        budget.

        ctx.inputs.messages and ctx.inputs.tools are updated after
        get_context_window so after_model_call hooks can inspect what was
        actually sent to the LLM.

        Uses llm.stream() when ctx.extra["_streaming"] is True,
        falls back to llm.invoke() otherwise.
        """
        # --- Finalize system message and context window (post-rails) ---
        # Build the base system prompt from registered sections, then fold in
        # environment-context injected by rails via ctx.extra. E is placed at
        # the END of the system string so that when its content changes (e.g.
        # a date rollover) the stable base-prompt prefix stays cache-hit; only
        # the trailing env block and onward recompute. pop() clears ctx.extra
        # after each model call to prevent multi-turn accumulation. Entries
        # with empty content are skipped so no hollow <environment_context>
        # block is folded into the system prompt. Access via .get() for
        # uniform, robust entry field reads.
        system_content = self.prompt_builder.build()
        environment_context = ctx.extra.pop("environment_context", None)
        context_parts = [
            r.get("content")
            for r in (environment_context or [])
            if r.get("content")
        ]
        if context_parts:
            env_block = (
                "<environment_context>\n"
                + "\n\n".join(context_parts)
                + "\n</environment_context>"
            )
            system_content = system_content + "\n\n" + env_block
            logger.debug(
                "[ReActAgent] environment_context folded into system "
                "sources=%s",
                [r.get("source") for r in (environment_context or [])],
            )
        final_system = [SystemMessage(content=system_content)]

        # KV cache release:
        # When ContextEngineConfig.enable_kv_cache_release=True and the current
        # model supports release (InferenceAffinity), pass `model=llm` into
        # get_context_window() so KVCacheManager can decide whether/when
        # to call release().
        llm = self._get_llm()

        ce_config = self._config.context_engine_config or ContextEngineConfig()
        enable_kv_release = getattr(ce_config, "enable_kv_cache_release", False)
        supports_kv_release = False
        supports_fn = getattr(llm, "supports_kv_cache_release", None)
        if callable(supports_fn):
            supports_kv_release = bool(supports_fn())

        # When KV cache release is enabled but the LLM does not support it,
        # log a one-time warning so users understand the setting is ineffective.
        if (
                enable_kv_release
                and not supports_kv_release
                and not self._kv_release_warning_logged
        ):
            logger.warning(
                "ContextEngineConfig.enable_kv_cache_release is True, "
                "but the current LLM does not support KV cache release; "
                "KV cache release will not take effect."
            )
            self._kv_release_warning_logged = True

        context_window_kwargs = {
            "system_messages": final_system,
            "tools": ctx.inputs.tools if ctx.inputs.tools else None,
        }
        if enable_kv_release and supports_kv_release:
            context_window_kwargs["model"] = llm

        _session_id = ""
        if ctx.session is not None:
            _sid_getter = getattr(ctx.session, "get_session_id", None)
            if callable(_sid_getter):
                try:
                    _session_id = str(_sid_getter() or "")
                except Exception:
                    _session_id = ""
        _gw_start = time.perf_counter()
        logger.debug(
            "[ReActAgent] get_context_window start session_id=%s",
            _session_id,
        )
        context_window = await ctx.context.get_context_window(
            **context_window_kwargs
        )
        _out_messages = context_window.get_messages()
        _out_tools = context_window.get_tools() or []
        logger.debug(
            "[ReActAgent] get_context_window done session_id=%s elapsed_ms=%.1f "
            "messages_out=%s tools_out=%s",
            _session_id,
            (time.perf_counter() - _gw_start) * 1000,
            len(_out_messages),
            len(_out_tools),
        )
        # Update ctx.inputs: after_model_call hooks inspect these to see
        # what was actually sent. (LLM call uses them too, but could
        # equally pass context_window.get_*() directly.)
        ctx.inputs.messages = context_window.get_messages()
        ctx.inputs.tools = context_window.get_tools()

        try:
            trace_ids = resolve_context_trace_ids(ctx.session, ctx.context)
            msgs_for_log = ctx.inputs.messages or []
            total_chars = 0
            active_skill_pin_count = 0
            for _m in msgs_for_log:
                if isinstance(_m, dict):
                    _c = _m.get("content", "")
                    _meta = _m.get("metadata")
                else:
                    _c = getattr(_m, "content", "") or ""
                    _meta = getattr(_m, "metadata", None)
                total_chars += len(_c) if isinstance(_c, str) else len(str(_c))
                if isinstance(_meta, dict) and _meta.get("active_skill_pin"):
                    active_skill_pin_count += 1
            if context_trace_enabled():
                write_context_trace(
                    "llm.context.messages_for_model",
                    {
                        **trace_ids,
                        "message_count": len(msgs_for_log),
                        "messages": snapshot_messages(ctx.inputs.messages),
                        "tool_count": len(ctx.inputs.tools or []),
                    },
                )
            logger.info(
                "[LLM] context_for_model session_id=%s context_id=%s message_count=%s "
                "tool_count=%s total_content_chars=%s active_skill_pin_messages=%s",
                trace_ids.get("session_id"),
                trace_ids.get("context_id"),
                len(msgs_for_log),
                len(ctx.inputs.tools or []),
                total_chars,
                active_skill_pin_count,
            )
        except Exception as exc:
            logger.warning("[ContextTrace] llm.context.messages_for_model failed: %s", exc)

        log_llm_request(logger, ctx.inputs.messages, ctx.inputs.tools)
        # --- End context window finalization ---

        session = ctx.session

        # Build extra kwargs for LLM calls when KV cache release is enabled.
        extra_kwargs: dict = {}
        build_kwargs_fn = getattr(llm, "build_kv_cache_invoke_kwargs", None)
        if callable(build_kwargs_fn):
            extra_kwargs.update(build_kwargs_fn(
                session=session,
                enable_kv_cache_release=enable_kv_release,
            ))

        _max_tokens_override = ctx.extra.get("_max_tokens_override")
        if _max_tokens_override is not None:
            extra_kwargs["max_tokens"] = _max_tokens_override

        if not ctx.extra.get("_streaming"):
            logger.debug(
                "[ReActAgent] llm.invoke start session_id=%s message_count=%s tool_count=%s",
                _session_id,
                len(ctx.inputs.messages or []),
                len(ctx.inputs.tools or []),
            )
            ai_message = await llm.invoke(
                model=self._config.model_name,
                messages=ctx.inputs.messages,
                tools=ctx.inputs.tools or None,
                **extra_kwargs,
            )
            ctx.inputs.response = ai_message
            return ai_message

        # Streaming path: accumulate chunks via __add__, write to session in real-time

        # Clean up any leftover executor from a previous iteration before
        # creating a new one (defensive — _execute_tool_call normally pops it).
        await self._cleanup_streaming_executor(ctx)

        agent_tag = getattr(getattr(self, "card", None), "id", "") or ""
        executor = StreamingToolExecutor(
            executor_fn=lambda tc: self.ability_manager.execute_single(
                parent_ctx=ctx,
                tool_call=tc,
                session=session,
                tag=agent_tag,
            ),
            tag=agent_tag,
        )
        ctx.extra[_STREAMING_TOOL_EXECUTOR_KEY] = executor

        accumulated_chunk = None
        chunk_index = 0

        logger.debug(
            "[ReActAgent] llm.stream start session_id=%s message_count=%s tool_count=%s",
            _session_id,
            len(ctx.inputs.messages or []),
            len(ctx.inputs.tools or []),
        )
        async for chunk in llm.stream(
                model=self._config.model_name,
                messages=ctx.inputs.messages,
                tools=ctx.inputs.tools or None,
                **extra_kwargs,
        ):
            if accumulated_chunk is None:
                accumulated_chunk = chunk
            else:
                accumulated_chunk = accumulated_chunk + chunk

            if chunk.reasoning_content:
                await session.write_stream(OutputSchema(
                    type="llm_reasoning",
                    index=chunk_index,
                    payload={"content": chunk.reasoning_content, "result_type": "answer"},
                ))
                chunk_index += 1
            if chunk.content:
                await session.write_stream(OutputSchema(
                    type="llm_output",
                    index=chunk_index,
                    payload={"content": chunk.content, "result_type": "answer"},
                ))
                chunk_index += 1
            if chunk.tool_calls:
                await session.write_stream(OutputSchema(
                    type="tool_calls.delta",
                    index=chunk_index,
                    payload={
                        "tool_calls": [
                            serialize_tool_call_delta(tc)
                            for tc in chunk.tool_calls
                        ],
                        "result_type": "answer",
                    },
                ))
                chunk_index += 1
            # 每个chunk结束时，判断是否构成完整tool_call
            self._stream_schedule_ready_tool_calls(
                executor, accumulated_chunk, finalize=False,
            )
        # 所有chuck结束时，做一次最终检查和去重（finalize=True）
        self._stream_schedule_ready_tool_calls(
            executor, accumulated_chunk, finalize=True,
        )

        if accumulated_chunk is None:
            ai_message = AssistantMessage(content="", tool_calls=[], finish_reason="null")
        else:
            ai_message = AssistantMessage(
                content=accumulated_chunk.content or "",
                tool_calls=accumulated_chunk.tool_calls or [],
                usage_metadata=accumulated_chunk.usage_metadata,
                reasoning_content=accumulated_chunk.reasoning_content,
                finish_reason=accumulated_chunk.finish_reason or "null",
            )
        ctx.inputs.response = ai_message
        if ai_message.usage_metadata:
            await session.write_stream(OutputSchema(
                type="llm_usage",
                index=0,
                payload={"usage_metadata": ai_message.usage_metadata.model_dump(), "result_type": "answer"},
            ))
        return ai_message

    @staticmethod
    def _render_system_messages(
            system_messages: List,
            inputs: Any,
            *,
            extra_render_fields: Optional[Dict[str, str]] = None,
    ) -> None:
        """Render inputs fields into system message placeholders in-place."""
        from openjiuwen.core.session import InteractiveInput

        render_fields: Dict[str, str] = {}
        if isinstance(inputs, dict):
            render_fields.update({k: v for k, v in inputs.items() if isinstance(v, str)})
        elif not isinstance(inputs, InteractiveInput):
            render_fields["query"] = str(inputs)
        if extra_render_fields:
            render_fields.update({
                key: value for key, value in extra_render_fields.items()
                if isinstance(value, str)
            })
        if not render_fields:
            return
        for msg in system_messages:
            if not isinstance(msg.content, str):
                continue
            try:
                msg.content = PromptTemplate(content=msg.content).format(render_fields).content
            except BaseError as e:
                logger.warning("Failed to render system message placeholder: %s", e)

    @staticmethod
    def _stream_schedule_ready_tool_calls(
            executor: StreamingToolExecutor,
            accumulated_chunk: Any,
            *,
            finalize: bool,
    ) -> None:
        """Schedule completed streaming tool_calls into the executor.

        A tool_call in ``accumulated_chunk.tool_calls`` is treated as fully
        formed once a later tool_call appears after it (the LLM has moved on);
        on ``finalize=True`` (called once the stream ends) the last one is
        also considered fully formed.

        ``arguments`` is JSON-validated as a sanity check before scheduling.
        Tools with malformed JSON mid-stream are skipped and will be picked up
        by the post-stream fallback in ``_execute_tool_call``.
        """
        if accumulated_chunk is None:
            return
        tcs = accumulated_chunk.tool_calls or []
        if not tcs:
            return
        boundary = len(tcs) if finalize else len(tcs) - 1
        for tc in tcs[:boundary]:
            if executor.is_added(tc):
                continue
            if tc.arguments:
                try:
                    json.loads(tc.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.debug(
                        "Streaming tool_call %s args not yet valid JSON; "
                        "deferring to post-stream execution.",
                        tc.name,
                    )
                    continue
            executor.add(tc)

    async def _cleanup_streaming_executor(
            self,
            ctx: AgentCallbackContext,
    ) -> None:
        """Cancel and reap any leftover streaming executor on ``ctx``.

        Idempotent: safe to call multiple times. Used both as a defensive
        pre-step before creating a new executor in the next iteration, and in
        ``_inner_invoke``'s ``finally`` to guarantee no task leaks.
        """
        executor: Optional[StreamingToolExecutor] = ctx.extra.pop(
            _STREAMING_TOOL_EXECUTOR_KEY, None,
        )
        if executor is None:
            return
        executor.cancel_all()
        try:
            await executor.wait_all()
        except BaseException as exc:
            logger.debug("Streaming executor cleanup swallowed: %s", exc)

    async def _execute_tool_call(
            self,
            ctx: AgentCallbackContext,
            tool_calls: List,
            session: Optional[Session],
            context: ModelContext,
    ) -> list:
        """Execute tool calls and commit tool messages into context.

        When the streaming path pre-launched some tool calls via
        ``StreamingToolExecutor``, results are reused (preserving order) and
        any remaining tool_calls are scheduled before waiting. Otherwise
        falls back to the original parallel ``ability_manager.execute``.

        Returns:
            List of (tool_result, tool_message) tuples
        """
        if not tool_calls:
            return []

        for tool_call in tool_calls:
            logger.info(f"Executing tool: {tool_call.name} with args: {tool_call.arguments}")

        executor: Optional[StreamingToolExecutor] = ctx.extra.pop(
            _STREAMING_TOOL_EXECUTOR_KEY, None,
        )

        if executor is None:
            # 如果stream执行器为空，降级到使用原先的并行执行器
            logger.warning("No streaming_tool_executor, use origin executor instead")
            results = await self.ability_manager.execute(
                ctx=ctx, tool_call=tool_calls, session=session,
            )
        else:
            for tc in tool_calls:
                if not executor.is_added(tc):
                    executor.add(tc)
            ordered = await executor.wait_all()
            results = self._consume_streaming_executor_results(
                ctx=ctx, tool_calls=tool_calls, ordered=ordered,
            )

        add_kwargs = {
            "system_messages": ctx.extra.get("_active_system_messages") or [],
            "tools": ctx.extra.get("_active_tools") or [],
        }
        for tool_result, tool_message in results:
            if tool_message is not None:
                await context.add_messages(tool_message, **add_kwargs)

        multimodal_message = self._build_multimodal_tool_results_message(
            tool_result for tool_result, _ in results
        )
        if multimodal_message is not None:
            await context.add_messages(multimodal_message, **add_kwargs)

        return results

    @staticmethod
    def _consume_streaming_executor_results(
            ctx: AgentCallbackContext,
            tool_calls: List,
            ordered: List[Tuple[Any, Any]],
    ) -> List[Tuple[Any, Optional[ToolMessage]]]:
        """Convert StreamingToolExecutor output to ability_manager.execute format.

        ``ordered`` is what ``StreamingToolExecutor.wait_all()`` returned:
        ``[(tool_call, value), ...]`` where ``value`` is either the
        ``execute_single`` 3-tuple ``(result, tool_msg, tool_ctx)`` or a
        ``BaseException`` instance.

        Also propagates ``force_finish`` from any tool_ctx back to the
        parent ``ctx``, mirroring ``AbilityManager.execute``'s behavior.
        """
        final: List[Tuple[Any, Optional[ToolMessage]]] = []
        propagated = False
        ordered_by_index = {
            (tc.id, tc.index): value for tc, value in ordered
        }

        for i, tc in enumerate(tool_calls):
            value = ordered_by_index.get((tc.id, tc.index))

            if isinstance(value, ToolInterruptException):
                final.append((value, None))
                continue

            if isinstance(value, BaseException) or value is None:
                err_msg = (
                    f"Ability execution error: {value}" if value is not None
                    else "Tool was not scheduled or cancelled"
                )
                logger.error(err_msg)
                final.append((
                    None,
                    ToolMessage(
                        content=err_msg,
                        tool_call_id=tc.id,
                        metadata={},
                    ),
                ))
                continue

            result, tool_msg, tool_ctx = value
            if tool_ctx is not None and not propagated:
                ff = tool_ctx.consume_force_finish()
                if ff is not None:
                    ctx.request_force_finish(ff.result)
                    propagated = True
            final.append((result, tool_msg))

        return final

    @staticmethod
    def _build_multimodal_tool_result_messages(tool_result: Any) -> List[UserMessage]:
        message = ReActAgent._build_multimodal_tool_results_message([tool_result])
        return [message] if message is not None else []

    @staticmethod
    def _build_multimodal_tool_results_message(tool_results: Any) -> Optional[UserMessage]:
        content: List[dict[str, Any]] = []
        loaded_paths: List[str] = []

        for tool_result in tool_results:
            for item in ReActAgent._iter_multimodal_image_items(tool_result):
                source_path = str(item.get("source_path") or "unknown image")
                data_url = item["data_url"]
                loaded_paths.append(source_path)
                content.append(
                    {
                        "type": "text",
                        "text": f"Image loaded from read_file: {source_path}",
                    }
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    }
                )

        if not content:
            return None

        if len(loaded_paths) > 1:
            summary_lines = [
                "Images loaded by tool results:",
                *[f"{index}. {path}" for index, path in enumerate(loaded_paths, start=1)],
            ]
            content.insert(0, {"type": "text", "text": "\n".join(summary_lines)})

        return UserMessage(content=content)

    @staticmethod
    def _iter_multimodal_image_items(tool_result: Any) -> List[dict[str, Any]]:
        data = getattr(tool_result, "data", None)
        if not isinstance(data, dict):
            return []

        multimodal_items = data.get("multimodal")
        if not isinstance(multimodal_items, list):
            return []

        image_items: List[dict[str, Any]] = []
        for item in multimodal_items:
            if not isinstance(item, dict) or item.get("type") != "image":
                continue

            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
                continue

            image_items.append(item)

        return image_items

    def _is_interrupted(self, tool_result: Any) -> bool:
        """Detect whether a tool result signals workflow interruption."""
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState
        if isinstance(tool_result, WorkflowOutput):
            return tool_result.state == WorkflowExecutionState.INPUT_REQUIRED
        if isinstance(tool_result, list):
            return any(
                hasattr(item, "type") and item.type == "__interaction__"
                for item in tool_result
            )
        return False

    def _extract_component_ids(self, tool_result: Any) -> List[str]:
        """Extract component IDs from an interrupted workflow result, sorted for stability."""
        from openjiuwen.core.workflow import WorkflowOutput
        if isinstance(tool_result, WorkflowOutput) and isinstance(tool_result.result, list):
            ids = []
            for item in tool_result.result:
                if (hasattr(item, "type") and item.type == "__interaction__" and
                        hasattr(item, "payload") and hasattr(item.payload, "id")):
                    ids.append(item.payload.id)
            return sorted(ids)
        if isinstance(tool_result, list):
            ids = []
            for item in tool_result:
                if (hasattr(item, "type") and item.type == "__interaction__" and
                        hasattr(item, "payload") and isinstance(item.payload, dict)):
                    ids.append(item.payload.get("component_id", ""))
            return sorted(ids)
        return []

    def _extract_workflow_id(self, tool_call: Any) -> str:
        """Resolve workflow_id from tool_call.name via ability_manager."""
        for ability in self._ability_manager.list():
            if ability.name == tool_call.name:
                return ability.id
        return tool_call.name  # fallback

    def _after_execute_tool_call(
            self,
            results: list,
            tool_calls: list,
            ai_message: AssistantMessage,
            iteration: int,
            original_query: str = "",
    ) -> Optional[InterruptionState]:
        """Check tool results for workflow interruption and build InterruptionState if found.

        Collects ALL interrupted workflows into interrupted_workflows dict.
        Sets pending_workflow_id/pending_component_id to the first interrupted one.

        Returns:
            InterruptionState if any workflow result is interrupted, else None
        """
        interrupted: Dict[str, WorkflowInterruptEntry] = {}
        first_workflow_id = None
        first_component_id = None

        for i, (tool_result, _) in enumerate(results):
            if self._is_interrupted(tool_result):
                workflow_id = self._extract_workflow_id(tool_calls[i])
                component_ids = self._extract_component_ids(tool_result)
                interrupted[workflow_id] = WorkflowInterruptEntry(
                    tool_call=tool_calls[i],
                    component_ids=component_ids,
                    workflow_execution_state=tool_result,
                )
                if first_workflow_id is None:
                    first_workflow_id = workflow_id
                    first_component_id = component_ids[0] if component_ids else ""

        if not interrupted:
            return None

        return InterruptionState(
            ai_message=ai_message,
            iteration=iteration,
            interrupted_workflows=interrupted,
            pending_workflow_id=first_workflow_id,
            pending_component_id=first_component_id,
            original_query=original_query,
        )

    def _after_execute_tool_call_for_hitl(
            self,
            results: list,
            tool_calls: list,
            ai_message: AssistantMessage,
            iteration: int,
            original_query: str = "",
    ) -> tuple[Optional['ToolInterruptionState'], list]:
        return self._hitl_handler.build_interrupt_state(
            results, tool_calls, ai_message, iteration, original_query=original_query
        )

    def _save_interruption_state(self, state: InterruptionState, session) -> None:
        if session:
            session.update_state({INTERRUPTION_KEY: state})

    def _load_interruption_state(self, session) -> Optional[InterruptionState]:
        if session:
            return session.get_state(INTERRUPTION_KEY)
        return None

    def _clear_interruption_state(self, session) -> None:
        if session:
            session.update_state({INTERRUPTION_KEY: None})

    def _build_interrupt_result(self, state: InterruptionState) -> Dict[str, Any]:
        """Build the interrupt result dict returned to the caller."""
        pending_entry = state.interrupted_workflows[state.pending_workflow_id]
        return {
            "result_type": "interrupt",
            "workflow_execution_state": pending_entry.workflow_execution_state,
            "component_ids": [state.pending_component_id],
        }

    async def _commit_interrupt(
            self,
            interrupt: Union[InterruptionState, 'ToolInterruptionState'],
            context: ModelContext,
            session: Optional[Session],
            invoke_inputs: InvokeInputs,
            sub_agent_outputs: list = None,
    ) -> Dict[str, Any]:
        """Persist interruption state and return the interrupt result dict.

        Writes a single placeholder ToolMessage for the pending workflow's tool_call.
        """
        if isinstance(interrupt, ToolInterruptionState):
            return await self._hitl_handler.commit_interrupt(
                interrupt, context, session, invoke_inputs, sub_agent_outputs
            )

        pending_entry = interrupt.interrupted_workflows[interrupt.pending_workflow_id]
        await context.add_messages(ToolMessage(
            tool_call_id=pending_entry.tool_call.id,
            content="[INTERRUPTED - Waiting for user input]",
        ))
        await self.context_engine.save_contexts(session)
        self._save_interruption_state(interrupt, session)
        result = self._build_interrupt_result(interrupt)
        invoke_inputs.result = result
        return result

    async def _handle_resume(
            self,
            interruption_state: Union[InterruptionState, 'ToolInterruptionState'],
            user_input: Any,
            ctx: AgentCallbackContext,
            context: ModelContext,
            session: Optional[Session],
            *,
            invoke_inputs: InvokeInputs,
    ) -> Optional[Dict[str, Any]]:
        """Process one resume step.

        Collects user feedback for the pending workflow/component.
        Triggers concurrent resume only when ALL interrupted workflows have feedback.

        Returns interrupt result dict if still waiting, or None to continue ReAct loop.
        """
        if isinstance(interruption_state, ToolInterruptionState):
            resume_ctx = ResumeContext(
                state=interruption_state,
                user_input=user_input,
                ctx=ctx,
                context=context,
                session=session,
                invoke_inputs=invoke_inputs,
                execute_tool_call=self._execute_tool_call,
            )
            return await self._hitl_handler.handle_resume(resume_ctx)

        resume_iteration = interruption_state.iteration
        logger.info(f"Resuming ReAct from iteration {resume_iteration + 1}")

        # Step 1: record feedback for the current pending workflow
        pending_wf_id = interruption_state.pending_workflow_id
        pending_comp_id = interruption_state.pending_component_id
        pending_entry = interruption_state.interrupted_workflows[pending_wf_id]

        interactive_input = self._build_interactive_input(user_input, [pending_comp_id])
        pending_entry.collected_input = interactive_input

        # Step 2: check if all interrupted workflows have collected feedback
        all_collected = all(
            entry.collected_input is not None
            for entry in interruption_state.interrupted_workflows.values()
        )

        if not all_collected:
            # Find next workflow without feedback and commit interrupt for it
            for wf_id, entry in interruption_state.interrupted_workflows.items():
                if entry.collected_input is None:
                    next_comp_id = entry.component_ids[0] if entry.component_ids else ""
                    interruption_state.pending_workflow_id = wf_id
                    interruption_state.pending_component_id = next_comp_id
                    return await self._commit_interrupt(interruption_state, context, session, invoke_inputs)

        # Step 3: all feedbacks collected — write ai_message and concurrently resume all workflows
        resume_ai_message = copy.deepcopy(interruption_state.ai_message)
        await context.add_messages(
            resume_ai_message,
            system_messages=ctx.extra.get("_active_system_messages") or [],
            tools=ctx.extra.get("_active_tools") or [],
        )

        all_tool_calls = []
        for entry in interruption_state.interrupted_workflows.values():
            tc_copy = copy.deepcopy(entry.tool_call)
            tc_copy.arguments = entry.collected_input
            all_tool_calls.append(tc_copy)

        async with AbilityManager.tool_batch_scope(session):
            results = await self._execute_tool_call(
                ctx, all_tool_calls, session, context,
            )
        workflow_interrupt = self._after_execute_tool_call(
            results, all_tool_calls, resume_ai_message, resume_iteration,
            original_query=interruption_state.original_query,
        )
        if workflow_interrupt:
            return await self._commit_interrupt(workflow_interrupt, context, session, invoke_inputs)

        # All workflows completed — continue ReAct loop from next iteration
        ctx.extra[RESUME_START_ITERATION_KEY] = resume_iteration + 1
        return None

    def _extract_user_text(self, user_input: Any) -> str:
        """Extract plain text from user_input (supports InteractiveInput or str)."""
        from openjiuwen.core.session import InteractiveInput
        if isinstance(user_input, InteractiveInput):
            if user_input.user_inputs:
                return str(next(iter(user_input.user_inputs.values())))
            if user_input.raw_inputs is not None:
                return str(user_input.raw_inputs)
            return ""
        return str(user_input)

    def _build_interactive_input(self, user_query: Any, component_ids: List[str]) -> Any:
        """Build an InteractiveInput from user feedback and component IDs."""
        from openjiuwen.core.session import InteractiveInput
        if isinstance(user_query, InteractiveInput):
            provided_ids = set(user_query.user_inputs.keys())
            fallback = next(iter(user_query.user_inputs.values()), "") if provided_ids else ""
            for comp_id in component_ids:
                if comp_id not in provided_ids:
                    user_query.update(comp_id, fallback)
            return user_query
        if component_ids:
            interactive_input = InteractiveInput()
            for comp_id in component_ids:
                interactive_input.update(comp_id, str(user_query))
            return interactive_input
        return InteractiveInput(raw_inputs=str(user_query))

    async def _warn_missing_skill_read_file_tool(self) -> None:
        """
        Log a warning when skill prompt is enabled but the required read_file tool
        is missing from ability_manager.
        """
        tool_infos = await self.ability_manager.list_tool_info()

        has_read_file = False
        existing_tool_names: List[str] = []

        for t in tool_infos or []:
            name = getattr(t, "name", None)
            if isinstance(name, str) and name:
                existing_tool_names.append(name)
                if name == "read_file":
                    has_read_file = True

        if has_read_file:
            return

        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import build_error

        err = build_error(
            StatusCode.AGENT_TOOL_NOT_FOUND,
            error_msg=(
                "skill prompt requires tool 'read_file' but it is not found in ability_manager. "
                f"existing_tools={sorted(set(existing_tool_names))}"
            )
        )
        logger.warning(str(err))

    async def _init_context(
            self,
            session: Optional[Session]
    ) -> ModelContext:
        # Always create token_counter for token statistics, regardless of context_processors
        from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
        if self._config.context_processors:
            context = await self.context_engine.create_context(
                session=session,
                processors=self._config.context_processors,
                token_counter=TiktokenCounter()
            )
        else:
            context = await self.context_engine.create_context(
                session=session,
                token_counter=TiktokenCounter()
            )
        context_reloader = context.reloader_tool()
        if self._config.context_engine_config.enable_reload:
            self.ability_manager.add(context_reloader.card)
            from openjiuwen.core.runner import Runner
            if not Runner.resource_mgr.get_tool(context_reloader.card.id, tag=self.card.id):
                Runner.resource_mgr.add_tool(context_reloader, tag=self.card.id)
        else:
            self.ability_manager.remove(context_reloader.card.name)
        return context

    async def invoke(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            **kwargs,
    ) -> Dict[str, Any]:
        """Execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict: {"query": "...", "conversation_id": "..."}
                - str: Used directly as query
            session: Session object (required for tool execution)
            **kwargs: Internal flags. _streaming=True selects
                the llm.stream() path inside _railed_model_call.

        Returns:
            Dict with output and result_type
        """
        if not isinstance(inputs, (dict, str)):
            raise ValueError("Input must be dict with 'query' or str")

        if isinstance(inputs, dict):
            query = inputs.get("query", "")
            conversation_id = inputs.get("conversation_id")
        else:
            query = inputs
            conversation_id = None

        # Auto-create session when not provided
        need_cleanup = False
        if session is None:
            session_id = conversation_id or "default_session"
            session = create_agent_session(
                session_id=session_id, card=self.card
            )
            await session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            need_cleanup = True
        return await self._inner_invoke(session=session, inputs=inputs, query=query, conversation_id=conversation_id,
                                        need_cleanup=need_cleanup, **kwargs)

    @with_session()
    async def _inner_invoke(self, session, inputs, query, need_cleanup, conversation_id, **kwargs):
        invoke_inputs = InvokeInputs(query=query, conversation_id=conversation_id)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)
        ctx.extra["_streaming"] = kwargs.get("_streaming", False)
        if isinstance(inputs, dict):
            ctx.extra["user_id"] = inputs.get("user_id", "")
            ctx.extra["run_kind"] = inputs.get("run_kind", "")
            ctx.extra["run_context"] = inputs.get("run_context", "")
            _sq = inputs.get("_steering_queue")
            if _sq is not None:
                ctx.bind_steering_queue(_sq)

        try:
            async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
                user_input = ctx.inputs.query
                if not user_input:
                    raise ValueError("Input must contain 'query'")

                hitl_state = self._hitl_handler.load(session)
                interruption_state = hitl_state or self._load_interruption_state(session)
                if interruption_state is not None:
                    if hitl_state is not None:
                        self._hitl_handler.clear(session)
                    else:
                        self._clear_interruption_state(session)
                    # Restore original query so MemoryRail.after_invoke writes the right UserMessage
                    ctx.extra["_original_query"] = interruption_state.original_query

                context = await self._init_context(session)
                ctx.context = context

                rendered_system_prompt = self._build_rendered_system_prompt(
                    inputs,
                    extra_render_fields=ctx.extra.get("memory_variables"),
                )
                self.add_prompt_builder_section(
                    _IDENTITY_SECTION,
                    rendered_system_prompt,
                    priority=_IDENTITY_SECTION_PRIORITY,
                )
                await self._update_skill_prompt_builder_section(rendered_system_prompt)
                tools = await self.ability_manager.list_tool_info()

                ctx.extra["_active_system_messages"] = [
                    SystemMessage(content=rendered_system_prompt)
                ]
                ctx.extra["_active_tools"] = tools

                # Get background messages from session state
                background_messages = session.get_state("background_messages")
                if background_messages:
                    await context.add_messages(
                        background_messages,
                        system_messages=ctx.extra.get("_active_system_messages") or [],
                        tools=ctx.extra.get("_active_tools") or [],
                    )

                start_iteration = 0
                if interruption_state is not None:
                    is_tool_interruption = isinstance(interruption_state, ToolInterruptionState)
                    
                    if is_tool_interruption:
                        # Tool Interrupt: not write UserMessage, recovery input is passed to Rail via ctx.extra
                        await self._handle_resume(
                            interruption_state, user_input, ctx, context, session, invoke_inputs=invoke_inputs
                        )
                        start_iteration = ctx.extra.pop(RESUME_START_ITERATION_KEY, 0)
                    else:
                        # Workflow Interrupt
                        await context.add_messages(
                            UserMessage(content=self._extract_user_text(user_input)),
                            system_messages=ctx.extra.get("_active_system_messages") or [],
                            tools=ctx.extra.get("_active_tools") or [],
                        )
                        resume_result = await self._handle_resume(
                            interruption_state, user_input, ctx, context, session, invoke_inputs=invoke_inputs
                        )
                        if resume_result is not None:
                            pass  # invoke_inputs.result already set by _handle_resume/_commit_interrupt
                        else:
                            start_iteration = ctx.extra.pop(RESUME_START_ITERATION_KEY, 0)
                else:
                    await context.add_messages(
                        UserMessage(content=self._extract_user_text(user_input)),
                        system_messages=ctx.extra.get("_active_system_messages") or [],
                        tools=ctx.extra.get("_active_tools") or [],
                    )

                if invoke_inputs.result is None:
                    _truncation_retry_count = 0
                    for iteration in range(start_iteration, self._config.max_iterations):
                        _session_id = ""
                        if session is not None:
                            _sid_getter = getattr(session, "get_session_id", None)
                            if callable(_sid_getter):
                                try:
                                    _session_id = str(_sid_getter() or "")
                                except Exception:
                                    _session_id = ""
                        logger.info(
                            f"ReAct iteration {iteration + 1}/{self._config.max_iterations}"
                        )
                        logger.debug(
                            "[ReActAgent] iteration session_id=%s",
                            _session_id,
                        )

                        # Inject pending steering messages
                        # before the next model call.
                        steering = ctx.drain_steering()
                        if steering:
                            combined = "\n".join(steering)
                            await context.add_messages(
                                UserMessage(
                                    content=(
                                        f"[STEERING] "
                                        f"{combined}"
                                    )
                                ),
                                system_messages=ctx.extra.get("_active_system_messages") or [],
                                tools=ctx.extra.get("_active_tools") or [],
                            )

                        _model_start = time.perf_counter()
                        logger.debug(
                            "[ReActAgent] model_call start session_id=%s iteration=%s",
                            _session_id,
                            iteration + 1,
                        )
                        async with AbilityManager.tool_batch_scope(session):
                            ai_message = await self._call_model(
                                ctx,
                                context,
                                tools,
                            )
                            logger.debug(
                                "[ReActAgent] model_call done session_id=%s iteration=%s "
                                "elapsed_ms=%.1f has_tool_calls=%s finish_reason=%s",
                                _session_id,
                                iteration + 1,
                                (time.perf_counter() - _model_start) * 1000,
                                bool(ai_message and getattr(ai_message, "tool_calls", None)),
                                getattr(ai_message, "finish_reason", "null"),
                            )

                            finish = ctx.consume_force_finish()
                            if finish:
                                await self.context_engine.save_contexts(session)
                                invoke_inputs.result = finish.result
                                break

                            _truncation_detected = (
                                ai_message is not None
                                and getattr(ai_message, "finish_reason", "null") == "length"
                            )

                            if _truncation_detected and _truncation_retry_count < 1:
                                _truncation_retry_count += 1
                                logger.info(
                                    "[ReActAgent] truncation detected session_id=%s iteration=%s, "
                                    "retrying with increased max_tokens",
                                    _session_id,
                                    iteration + 1,
                                )
                                await session.write_stream(OutputSchema(
                                    type="truncation_retry",
                                    index=0,
                                    payload={
                                        "finish_reason": "length",
                                        "truncated_content": (ai_message.content or "")[:200],
                                        "phase": "retry_attempt",
                                    },
                                ))
                                _truncated_output_tokens = (
                                    getattr(ai_message.usage_metadata, "output_tokens", 16384)
                                    or 16384
                                )
                                ctx.extra["_max_tokens_override"] = _truncated_output_tokens * 2
                                ai_message = await self._call_model(
                                    ctx,
                                    context,
                                    tools,
                                )
                                ctx.extra.pop("_max_tokens_override", None)
                                logger.debug(
                                    "[ReActAgent] truncation_retry done session_id=%s iteration=%s "
                                    "finish_reason=%s",
                                    _session_id,
                                    iteration + 1,
                                    getattr(ai_message, "finish_reason", "null"),
                                )
                                finish = ctx.consume_force_finish()
                                if finish:
                                    await self.context_engine.save_contexts(session)
                                    invoke_inputs.result = finish.result
                                    break
                                _truncation_detected = (
                                    ai_message is not None
                                    and getattr(ai_message, "finish_reason", "null") == "length"
                                )

                            if _truncation_detected:
                                logger.info(
                                    "[ReActAgent] truncation persists session_id=%s iteration=%s, "
                                    "continuing to next iteration with truncation notice",
                                    _session_id,
                                    iteration + 1,
                                )
                                await context.add_messages(
                                    AssistantMessage(
                                        content=ai_message.content or "",
                                        tool_calls=ai_message.tool_calls,
                                        reasoning_content=getattr(ai_message, "reasoning_content", None),
                                        usage_metadata=ai_message.usage_metadata,
                                        finish_reason=ai_message.finish_reason,
                                    ),
                                    system_messages=ctx.extra.get("_active_system_messages") or [],
                                    tools=ctx.extra.get("_active_tools") or [],
                                )
                                await context.add_messages(
                                    UserMessage(
                                        content=(
                                            "[TRUNCATION_NOTICE] Your previous response was "
                                            "truncated due to output length limits. Please "
                                            "continue from where you left off, or provide a "
                                            "more concise response."
                                        ),
                                    ),
                                    system_messages=ctx.extra.get("_active_system_messages") or [],
                                    tools=ctx.extra.get("_active_tools") or [],
                                )
                                await session.write_stream(OutputSchema(
                                    type="truncation_retry",
                                    index=0,
                                    payload={
                                        "finish_reason": "length",
                                        "truncated_content": (ai_message.content or "")[:200],
                                        "phase": "persist",
                                    },
                                ))
                                continue

                            await context.add_messages(
                                AssistantMessage(
                                    content=ai_message.content,
                                    tool_calls=ai_message.tool_calls,
                                    reasoning_content=getattr(ai_message, "reasoning_content", None),
                                    usage_metadata=ai_message.usage_metadata,
                                ),
                                system_messages=ctx.extra.get("_active_system_messages") or [],
                                tools=ctx.extra.get("_active_tools") or [],
                            )

                            if not ai_message.tool_calls:
                                # If steering arrived while the
                                # model was generating, continue
                                # the loop so the next iteration
                                # drains and injects it.
                                if ctx.has_pending_steering():
                                    continue
                                await self.context_engine.save_contexts(session)
                                result = {
                                    "output": ai_message.content,
                                    "result_type": "answer",
                                    "finish_reason": getattr(ai_message, "finish_reason", "null"),
                                }
                                invoke_inputs.result = result
                                break

                            _tool_names = [
                                getattr(tc, "name", "") for tc in ai_message.tool_calls
                            ]
                            _tools_start = time.perf_counter()
                            logger.debug(
                                "[ReActAgent] tool_call start session_id=%s iteration=%s tools=%s",
                                _session_id,
                                iteration + 1,
                                _tool_names,
                            )
                            results = await self._execute_tool_call(
                                ctx, ai_message.tool_calls, session, context,
                            )
                            logger.debug(
                                "[ReActAgent] tool_call done session_id=%s iteration=%s tools=%s "
                                "elapsed_ms=%.1f continuing_loop",
                                _session_id,
                                iteration + 1,
                                _tool_names,
                                (time.perf_counter() - _tools_start) * 1000,
                            )

                            finish = ctx.consume_force_finish()
                            if finish:
                                await self.context_engine.save_contexts(session)
                                invoke_inputs.result = finish.result
                                break

                            hitl_interrupt, sub_agent_outputs = self._after_execute_tool_call_for_hitl(
                                results, ai_message.tool_calls, ai_message, iteration,
                                original_query=ctx.extra.get("_original_query", ""),
                            )
                            if hitl_interrupt:
                                await self._commit_interrupt(
                                    hitl_interrupt, context, session, invoke_inputs,
                                    sub_agent_outputs,
                                )
                                break

                            workflow_interrupt = self._after_execute_tool_call(
                                results, ai_message.tool_calls, ai_message, iteration,
                                original_query=ctx.extra.get("_original_query", ""),
                            )
                            if workflow_interrupt:
                                await self._commit_interrupt(
                                    workflow_interrupt, context, session, invoke_inputs,
                                )
                                break
                    else:
                        await self.context_engine.save_contexts(session)
                        result = {"output": "Max iterations reached without completion", "result_type": "error"}
                        invoke_inputs.result = result

            # after_invoke rails have fired; return result (possibly adapted by rails via ctx.extra)
            return ctx.extra.get("invoke_result", invoke_inputs.result)
        finally:
            await self._cleanup_streaming_executor(ctx)
            if need_cleanup:
                await self.context_engine.save_contexts(session)
                await session.post_run()

    async def write_invoke_result_to_stream(
            self,
            result: Dict[str, Any],
            session: Session,
    ) -> None:
        """Public wrapper — delegates to the internal implementation."""
        await self._write_invoke_result_to_stream(result, session)

    async def _write_invoke_result_to_stream(
            self,
            result: Dict[str, Any],
            session: Session,
    ) -> None:
        """Write the final invoke result to the session stream.

        For interrupt results, only emit the single OutputSchema whose payload.id
        matches the pending_component_id, so callers see exactly one interaction chunk.
        """
        result_type = result.get("result_type", "")
        if result_type == "interrupt":
            if "interrupt_ids" in result:
                await self._hitl_handler.write_interrupt_to_stream(result, session)
            else:
                workflow_state = result.get("workflow_execution_state")
                component_ids = result.get("component_ids", [])
                pending_id = component_ids[0] if component_ids else None
                schemas = (
                    workflow_state.result
                    if workflow_state is not None
                    and isinstance(getattr(workflow_state, "result", None), list)
                    else []
                )
                for schema in schemas:
                    if (pending_id is None
                            or (hasattr(schema, "payload")
                                and hasattr(schema.payload, "id")
                                and schema.payload.id == pending_id)):
                        await session.write_stream(schema)
        else:
            await session.write_stream(OutputSchema(
                type="answer",
                index=0,
                payload={
                    "output": result.get("output", ""),
                    "result_type": result_type,
                    "finish_reason": result.get("finish_reason"),
                },
            ))

    async def stream(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        """Stream execute ReAct process

        Args:
            inputs: User input
            session: Session object (if None, auto create)
            stream_modes: Stream output modes (optional)

        Yields:
            OutputSchema objects from stream_iterator
        """
        # Auto-create session when not provided
        need_cleanup = False
        if session is None:
            if isinstance(inputs, dict):
                conversation_id = inputs.get("conversation_id")
            else:
                conversation_id = None
            session_id = conversation_id or "default_session"
            session = create_agent_session(
                session_id=session_id, card=self.card
            )
            need_cleanup = True

        # Only call pre_run/post_run for agent sessions, not workflow sessions
        self.is_agent_session = hasattr(session, "pre_run") and hasattr(session, "post_run")
        # self.is_agent_session = isinstance(session, AgentSession)
        if self.is_agent_session:
            await session.pre_run(
                inputs=inputs if isinstance(inputs, dict) else None
            )

        async for chunk in self._inner_stream(session=session, inputs=inputs, need_cleanup=need_cleanup):
            yield chunk

    @with_session()
    async def _inner_stream(self, session, inputs, need_cleanup):
        async def stream_process():
            try:
                final_result = await self.invoke(inputs, session, _streaming=True)
                if isinstance(final_result, list):
                    for schema in final_result:
                        await session.write_stream(schema)
                else:
                    await self._write_invoke_result_to_stream(
                        final_result, session
                    )
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
            finally:
                if need_cleanup:
                    await self.context_engine.save_contexts(session)
                if self.is_agent_session:
                    await session.post_run()

        if self.is_agent_session:
            # Agent sessions use stream_iterator for consuming output
            task = asyncio.create_task(stream_process())

            async for result in session.stream_iterator():
                yield result

            await task
        else:
            # Workflow sessions: just run stream_process, output goes to session.write_stream()
            # The workflow graph consumes from session.write_stream() via StreamWriterManager
            await stream_process()

    async def clear_session(self, session_id: str = "default_session"):
        """Release session resources and clear context cache."""
        from openjiuwen.core.runner import Runner
        await Runner.release(session_id=session_id)
        await self.context_engine.clear_context(session_id=session_id)


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
