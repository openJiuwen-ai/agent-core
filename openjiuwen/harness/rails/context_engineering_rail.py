# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
from typing import List, Tuple, Union, Optional, Dict

from pydantic import BaseModel

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig
)
from openjiuwen.harness.prompts.sections.workspace import build_workspace_section as _build_workspace
from openjiuwen.harness.prompts.sections.context import build_context_section as _build_context, build_tools_content

logger = logging.getLogger(__name__)


class ContextEngineeringRail(DeepAgentRail):
    """Rail that injects context processor configurations into ReActAgent.

    In ``init``, reads the current ``context_processors`` list from
    ``agent.react_agent._config`` and appends / replaces entries
    by processor key. The processor key is used for deduplication
    (same key = replace, new key = append).

    In ``before_model_call``, injects workspace directory structure
    into the messages for LLM call.

    In ``before_invoke`` and ``on_model_exception``, fix context.
    """

    priority = 85

    def __init__(
            self,
            processors: Union[
                Tuple[str, BaseModel],
                Tuple[str, Dict],
                List[Tuple[str, BaseModel]],
                List[Tuple[str, Dict]],
                None,
            ] = None,
            preset: bool = True,
    ):
        """Initialize ContextEngineeringRail.

        Args:
            processors: One or more (processor_key, config) pairs.
                        processor_key must match the processor's registered name
                        (e.g. "DialogueCompressor", "MessageOffloader").
                        config can be either:
                        - BaseModel: 整对象替换预置配置
                        - dict: 字段级别合并到预置配置（只覆盖指定的字段，其他使用预置默认值）
                        用户配置的增量添加，相同 key 会替换/合并预置或已有的配置。
            preset: 是否启用预置的默认 processor 配置。默认为 True。
        """
        super().__init__()
        self.system_prompt_builder = None
        self._ability_manager = None

        self._preset = preset
        self._user_processors: List[Tuple[str, Union[BaseModel, Dict]]] = []
        if processors is not None:
            if isinstance(processors, tuple):
                self._user_processors = [processors]
            else:
                self._user_processors = list(processors)

    @staticmethod
    def _merge_config_with_overrides(
            base_config: BaseModel,
            overrides: Dict,
    ) -> BaseModel:
        """将 overrides dict 的字段合并到 base_config，保留 base 中未在 overrides 中指定的字段。

        Args:
            base_config: 基础配置对象（Pydantic BaseModel）。
            overrides: 要合并的 dict，只会覆盖指定的字段。

        Returns:
            合并后的配置对象。
        """
        if not overrides:
            return base_config

        base_dict = base_config.model_dump(exclude_none=True)
        merged = {**base_dict, **overrides}

        return type(base_config)(**merged)

    @staticmethod
    def _merge_processors(
            base: List[Tuple[str, BaseModel]],
            overrides: List[Tuple[str, Union[BaseModel, Dict]]],
            model_config=None,
            model_client_config=None,
    ) -> List[Tuple[str, BaseModel]]:
        """合并两个 processor 列表，overrides 中的条目替换/合并 base 中相同 key 的条目。

        支持 BaseModel 对象（整对象替换）和 dict（字段级别合并）两种格式。

        Args:
            base: 基础 processor 列表（预置 processors）。
            overrides: 增量配置列表，dict 格式会与 base 中同名 key 的配置做字段级别合并，
                      BaseModel 格式会整对象替换。
            model_config: 模型配置，用于需要 model 的 processor。
            model_client_config: 模型客户端配置。

        Returns:
            合并后的 processor 列表。
        """
        result: List[Tuple[str, BaseModel]] = []
        override_keys = {key for key, _ in overrides}

        for key, cfg in base:
            if key not in override_keys:
                result.append((key, cfg))

        for key, override_cfg in overrides:
            base_cfg = None
            for k, c in base:
                if k == key:
                    base_cfg = c
                    break

            if base_cfg is not None:
                if isinstance(override_cfg, dict):
                    merged_cfg = ContextEngineeringRail._merge_config_with_overrides(base_cfg, override_cfg)
                else:
                    merged_cfg = override_cfg
            else:
                if isinstance(override_cfg, dict):
                    raise ValueError(
                        f"Processor '{key}' 在预置中不存在，无法从 dict 创建配置。"
                        " 请确保预置中已包含此 processor，或传入完整的 BaseModel 配置对象。"
                    )
                merged_cfg = override_cfg

            if hasattr(merged_cfg, "model") and getattr(merged_cfg, "model", None) is None:
                merged_cfg.model = model_config
            if hasattr(merged_cfg, "model_client") and getattr(merged_cfg, "model_client", None) is None:
                merged_cfg.model_client = model_client_config

            result.append((key, merged_cfg))

        return result


    @staticmethod
    def _build_preset_processors(
            model_config=None,
            model_client_config=None,
    ) -> List[Tuple[str, BaseModel]]:
        """构建预置的默认 processor 配置列表。

        Args:
            model_config: 模型配置，用于需要 model 的 processor。
            model_client_config: 模型客户端配置。

        Returns:
            预置 processor 配置列表。
        """

        if model_config is not None:
            model_cfg = ModelRequestConfig.model_copy(model_config)
        else:
            model_cfg = None

        presets: List[Tuple[str, BaseModel]] = [
            (
                "MessageOffloader",
                MessageOffloaderConfig(
                    messages_threshold=40,
                    tokens_threshold=5000,
                    large_message_threshold=20000,
                    trim_size=5000,
                    offload_message_type=["tool"],
                    keep_last_round=False,
                ),
            ),
            (
                "DialogueCompressor",
                DialogueCompressorConfig(
                    messages_threshold=40,
                    tokens_threshold=100000,
                    keep_last_round=False,
                    model=model_cfg,
                    model_client=model_client_config,
                ),
            ),
        ]
        return presets


    def init(self, agent) -> None:
        """Inject / merge processors into agent.react_agent._config.context_processors."""
        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is None:
            return

        model_config = getattr(config, "model_config_obj", None)
        model_client_config = getattr(config, "model_client_config", None)

        if self._preset:
            all_processors = self._merge_processors(
                self._build_preset_processors(model_config, model_client_config),
                self._user_processors,
                model_config=model_config,
                model_client_config=model_client_config,
            )
        else:
            all_processors = self._merge_processors(
                [],
                self._user_processors,
                model_config=model_config,
                model_client_config=model_client_config,
            )


        config.context_processors = all_processors
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._ability_manager = getattr(agent, "ability_manager", None)

    def uninit(self, agent) -> None:
        """Remove workspace section from system prompt builder."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")


    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self.fix_incomplete_tool_context(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        pass

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace directory structure and context files into messages before model call."""
        if self.system_prompt_builder is None:
            return
        workspace = self.workspace

        if workspace is None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            return

        lang = self.system_prompt_builder.language
        workspace_section = await _build_workspace(
            self.sys_operation,
            workspace,
            lang,
        )
        tools_cn = build_tools_content(self._ability_manager, "cn")
        tools_en = build_tools_content(self._ability_manager, "en")
        context_section = await _build_context(
            self.sys_operation,
            workspace,
            lang,
            tools_content=tools_cn if lang == "cn" else tools_en,
        )

        if workspace_section is not None:
            self.system_prompt_builder.add_section(workspace_section)
        else:
            self.system_prompt_builder.remove_section("workspace")

        if context_section is not None:
            self.system_prompt_builder.add_section(context_section)
        else:
            self.system_prompt_builder.remove_section("context")

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Attempt to fix incomplete tool context when LLM call fails.

        When an LLM call fails (e.g. due to invalid context), this hook
        validates and repairs any incomplete tool_call/ToolMessage pairs
        before requesting a retry.
        """
        await self.fix_incomplete_tool_context(ctx)

    @staticmethod
    async def fix_incomplete_tool_context(ctx: AgentCallbackContext) -> None:
        """Validate and fix incomplete context messages before entering ReAct loop.

        If an assistant message with tool_calls exists without corresponding tool messages,
        add placeholder tool messages to keep context valid for OpenAI API.
        """
        from openjiuwen.core.foundation.llm import ToolMessage, AssistantMessage, UserMessage

        try:
            context = ctx.context
            if context is None:
                return

            messages = context.get_messages()
            if not messages:
                return

            len_messages = len(messages)
            popped = context.pop_messages(size=len_messages)
            if not popped:
                return

            tool_message_cache = {}
            tool_id_cache = []

            async def _enqueue_tool_calls(msg: AssistantMessage) -> None:
                """Enqueue tool_call_ids from an AssistantMessage into the pending cache."""
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    return
                for tc in tool_calls:
                    tool_id_cache.append({
                        "tool_call_id": getattr(tc, "id", ""),
                        "tool_name": getattr(tc, "name", ""),
                    })

            async def _flush_pending_tool_calls() -> None:
                """Flush pending tool calls: first drain tool_message_cache, then emit placeholders."""
                nonlocal tool_id_cache
                if not tool_id_cache:
                    return
                logger.info("Fixed incomplete tool context with placeholder messages")
                for tc in tool_id_cache:
                    tool_call_id = tc["tool_call_id"]
                    tool_name = tc["tool_name"]
                    if tool_call_id in tool_message_cache:
                        await context.add_messages(tool_message_cache.pop(tool_call_id))
                    else:
                        await context.add_messages(ToolMessage(
                            content=f"[工具执行被中断] 工具 {tool_name}执行过程中被用户打断，没有执行结果。",
                            tool_call_id=tool_call_id
                        ))
                tool_id_cache = []

            for msg in popped:
                if isinstance(msg, AssistantMessage):
                    await _flush_pending_tool_calls()
                    await context.add_messages(msg)
                    await _enqueue_tool_calls(msg)
                elif isinstance(msg, ToolMessage):
                    if not tool_id_cache:
                        await context.add_messages(msg)
                    elif msg.tool_call_id == tool_id_cache[0]["tool_call_id"]:
                        await context.add_messages(msg)
                        tool_id_cache.pop(0)
                    else:
                        tool_message_cache[msg.tool_call_id] = msg
                else:
                    await _flush_pending_tool_calls()
                    await context.add_messages(msg)
            await _flush_pending_tool_calls()
        except Exception as e:
            import traceback
            logger.warning("Failed to fix incomplete tool context: %s\n%s", e, traceback.format_exc())
