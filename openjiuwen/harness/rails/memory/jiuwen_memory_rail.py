# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JiuwenMemoryRail — AgentMemory 外接记忆 rail。

行为基线 = 同目录的 ``ExternalMemoryRail``，与 AgentMemory
（agent-memory 仓库的 ``jiuwen_memory`` 引擎）语义对齐的差异见下；
provider 由宿主注入——典型为 agent-memory 适配包的
``AgentMemoryMemoryProvider``（``jiuwen_memory_adapter/jiuwenswarm/agent_memory_provider.py``，
经 PYTHONPATH 以顶层模块引入），本 rail 只依赖 ``MemoryProvider`` ABC，
不 import 具体实现。

与 ``ExternalMemoryRail`` 的差异（设计文档
``agent-memory:jiuwen_memory_adapter/jiuwenswarm/jiuwen_memory_rail_design.md`` §6.1.2）：

1. **``on_session_end`` 接线**：基线从不调用 ``provider.on_session_end``（ABC 方法成
   死代码）。本 rail 在 ``uninit`` 撤销前（``session_end_extract=True`` 时）触发批量
   EXTRACT，并暴露 ``end_session()`` 供宿主在会话正常关闭时显式调用。缺省
   ``False``：``sync_turn`` 已用 ``infer=true`` 同步抽取，会话末再跑一次批量 EXTRACT
   是重复的 LLM 开销；仅当 sync 改为原文存储（infer=false）时才需要打开。
2. **可用性预检**：``init`` 时调 ``provider.is_available()``，不可用则 warning 一条
   并进入降级空转（不注册工具、不注入提示词、不跑 prefetch）——配置错误（如
   ``base_url`` 拼错）不再表现为每轮请求 400 的日志噪音。
3. **``session_id`` 缺省 ``""``**：AgentMemory 的 scope 语义是 session 留空 = 跨会话
   共享、显式传值才隔离。基线缺省 ``"__default__"`` 让所有会话共享同一个伪 session，
   长期记忆"碰巧能用"；宿主一旦传真实 per-session id，跨会话记忆**静默失效**。
   缺省空串让"跨会话共享"成为显式契约，要隔离时由宿主显式传入。
4. **工具 id 前缀** ``jiuwen_memory_<tool_name>``（基线为
   ``external_memory_<provider.name>_<tool_name>``）。
5. **交互轮次聚合**：按真实会话暂存原始请求和已接受的 ask_user 回答，
   最终回复后提交一次 sync_turn；暂存最多 16,000 字符、128 个会话，
   30 分钟未活动后在后续前台调用中淘汰，不持久化。

部署：宿主自备 provider（agent-memory 适配包或任意 ``MemoryProvider`` 实现），
``JiuwenMemoryRail(provider, user_id=..., scope_id=..., session_id=...)`` 挂载。
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.memory.external.provider import MemoryProvider
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptKind
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    RESUME_INTERRUPT_ANSWERS_KEY,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, RunKind
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.external_memory import (
    build_external_memory_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail

EXTERNAL_MEMORY_PREFETCH_SECTION = "external_memory_prefetch"

_SYNC_BREAKER_THRESHOLD = 5
_SYNC_BREAKER_COOLDOWN = 120.0
# sync_turn 整体执行总闸（秒）：略大于 provider 单次 llm_timeout（120s），给
# infer=true 抽取里的多轮 LLM 调用留余量；只兜 SDK 无 asyncio 超时的整体路径。
# 超时即 cancel（不 shield），对齐 F02-jiuwenswarm-rail-sync-turn-timeout.md。
_SYNC_TURN_TIMEOUT = 130.0

_TURN_MAX_CHARS = 16_000
_TURN_MAX_SESSIONS = 128
_TURN_TTL = 1800.0
_TURN_TRUNCATION = "\n[交互轮次内容已截断]\n"
_TURN_CONTEXT_KEY = "_jiuwen_memory_turn"


@dataclass
class _MemoryTurn:
    """Bounded user-side material for one logical turn, never a chat history."""

    original: str
    additions: list[str] = field(default_factory=list)
    seen_answers: dict[str, None] = field(default_factory=dict)
    updated: float = field(default_factory=time.monotonic)
    truncated: bool = False

    def __post_init__(self) -> None:
        self.original = self._clip(self.original, _TURN_MAX_CHARS - len(_TURN_TRUNCATION))

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        self.truncated = True
        return text[:limit // 2] + text[-(limit - limit // 2):]

    def append(self, text: str) -> None:
        if not text:
            return
        self.updated = time.monotonic()
        budget = _TURN_MAX_CHARS - len(_TURN_TRUNCATION)
        # Reserve room for recent complete answers while retaining both ends
        # of an unusually large original request.
        if len(self.original) + len(text) + 2 > budget:
            self.original = self._clip(self.original, budget // 2)
        if len(self.original) + len(text) + 2 > budget:
            self.truncated = True
            return
        self.additions.append(text)
        while self.additions and len("\n\n".join([self.original, *self.additions])) > budget:
            self.additions.pop(0)
            self.truncated = True

    def render(self) -> str:
        text = "\n\n".join([self.original, *self.additions])
        return text + _TURN_TRUNCATION if self.truncated else text


class JiuwenMemoryRail(DeepAgentRail):
    """JiuwenMemoryRail — AgentMemory 外接记忆 rail（见模块 docstring 的差异清单）。

    Args:
        provider: 任意 ``MemoryProvider`` 实现。典型为 agent-memory 适配包的
            ``AgentMemoryMemoryProvider``（HTTP / SDK 双模式），由宿主构造注入。
        user_id / scope_id / agent_id / session_id 维度经 ``provider.initialize``
            下发（scope 隔离）。
        agent_id: 当前 deepagent 的 agent 标识，缺省 ``""`` = 不按 agent 隔离。
        session_id: 缺省 ``""`` = 跨会话共享（见差异 3）；显式传值才按会话隔离。
        session_end_extract: ``uninit`` 时是否触发 ``provider.on_session_end``
            批量 EXTRACT（见差异 1，缺省 False）。
    """

    priority = 75
    PREFETCH_TIMEOUT = 5.0

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        user_id: str = "__default__",
        scope_id: str = "__default__",
        agent_id: str = "",
        session_id: str = "",
        session_end_extract: bool = False,
    ):
        super().__init__()
        self._provider = provider
        self._user_id = user_id
        self._scope_id = scope_id
        self._agent_id = agent_id
        self._session_id = session_id
        self._session_end_extract = session_end_extract
        self._initialized = False
        self._available = True
        # Abilities this rail actually registered, mapped from tool name to the
        # exact card that was stored. The name is the ability-manager key, while
        # the card identity tells uninit whether this rail is still the owner or
        # another rail has since taken the name over.
        self._owned_tool_cards: Dict[str, ToolCard] = {}
        self.system_prompt_builder = None
        self.attachment_manager = None
        # Per-invoke prefetch cache
        self._prefetch_cache: Optional[str] = None
        self._prefetch_invoke_id: Optional[int] = None
        # Serialized sync_turn + circuit breaker
        self._sync_task: Optional[asyncio.Task] = None
        self._sync_consecutive_failures: int = 0
        self._sync_breaker_until: float = 0.0
        # Project resolved from the current invoke context; injected into
        # provider tool calls (mem2_add/mem2_search) as routing coords.
        self._current_project: Optional[str] = None
        self._memory_turns: dict[str, _MemoryTurn] = {}

    def init(self, agent) -> None:
        super().init(agent)
        # 保存 agent 引用，供工具调用时现读 project 坐标（见 _tool_func 注入逻辑，
        # 不依赖 before_invoke 缓存的 _current_project）。
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

        # 差异 2：可用性预检——不可用则一条 warning + 降级空转，不逐请求报错。
        self._available = True
        try:
            self._available = bool(self._provider.is_available())
        except Exception as exc:
            logger.warning(f"[JiuwenMemoryRail] provider.is_available() raised: {exc}")
            self._available = False
        if not self._available:
            logger.warning(
                "[JiuwenMemoryRail] provider '%s' not available (check provider config); rail degrades to no-op",
                self._provider.name,
            )
            return

        self._register_provider_tools(agent)
        # Inject provider's static system prompt block
        if self.system_prompt_builder:
            prompt_block = self._provider.system_prompt_block()
            if prompt_block:
                lang = getattr(self.system_prompt_builder, "language", "cn")
                section = build_external_memory_section(prompt_block, language=lang)
                if section:
                    self.system_prompt_builder.add_section(section)

    def uninit(self, agent) -> None:
        self._memory_turns.clear()
        # Unregister tools owned by this rail from the agent
        if hasattr(agent, "ability_manager"):
            for tool_name, tool_card in list(self._owned_tool_cards.items()):
                if agent.ability_manager.get(tool_name) is not tool_card:
                    # Another rail re-registered the name after this rail did
                    # and now owns both the card and the live instance; tearing
                    # it down here would unregister that rail's tool.
                    continue
                try:
                    agent.ability_manager.remove_ability(tool_name)
                except Exception as exc:
                    logger.warning(f"[JiuwenMemoryRail] remove tool '{tool_name}' failed: {exc}")
        self._owned_tool_cards.clear()

        # Remove prompt sections
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.EXTERNAL_MEMORY)
            self.system_prompt_builder.remove_section(EXTERNAL_MEMORY_PREFETCH_SECTION)
            self.system_prompt_builder = None
        self.attachment_manager = None

        # 差异 1：撤销前触发会话末批量 EXTRACT（可选；provider 内部自捕获异常）。
        async def _session_end_if_enabled() -> None:
            if self._session_end_extract and self._initialized:
                try:
                    await self._provider.on_session_end([])
                except Exception as exc:
                    logger.warning(f"[JiuwenMemoryRail] on_session_end failed: {exc}")

        # Async shutdown via LspRail pattern
        try:
            loop = asyncio.get_running_loop()

            async def _shutdown_with_timeout():
                await _session_end_if_enabled()
                try:
                    await asyncio.wait_for(self._provider.shutdown(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("[JiuwenMemoryRail] provider shutdown timed out")
                except Exception as e:
                    logger.warning(f"[JiuwenMemoryRail] provider shutdown error: {e}")

            future = asyncio.run_coroutine_threadsafe(_shutdown_with_timeout(), loop)
            future.result(timeout=15.0)
        except RuntimeError:
            try:
                asyncio.run(_session_end_if_enabled())
                asyncio.run(self._provider.shutdown())
            except Exception as exc:
                logger.warning(f"[JiuwenMemoryRail] shutdown failed: {exc}")
        except Exception as exc:
            logger.warning(f"[JiuwenMemoryRail] shutdown failed: {exc}")

        self._initialized = False

    async def end_session(self, messages=None) -> None:
        """会话正常关闭时由宿主显式调用的批量 EXTRACT 入口（差异 1）。

        与 ``session_end_extract`` 开关无关——开关只管 ``uninit`` 的隐式触发，
        这里是宿主的显式意图。provider 内部自捕获异常，失败不影响后续 shutdown。
        """
        self._memory_turns.clear()
        if not self._initialized:
            return
        try:
            await self._provider.on_session_end(messages or [])
        except Exception as exc:
            logger.warning(f"[JiuwenMemoryRail] end_session failed: {exc}")

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        # Clear prefetch cache
        self._prefetch_cache = None
        self._prefetch_invoke_id = id(ctx)
        self._current_project = self._resolve_project(ctx)

        if not self._available:
            return

        self._begin_memory_turn(ctx)

        if not self._initialized:
            try:
                await self._provider.initialize(
                    user_id=self._user_id,
                    scope_id=self._scope_id,
                    agent_id=self._agent_id,
                    session_id=self._session_id,
                )
                self._initialized = True
                logger.info(f"[JiuwenMemoryRail] Provider '{self._provider.name}' initialized")
            except Exception as e:
                logger.error(f"[JiuwenMemoryRail] Provider initialize failed: {e}")
        await self.before_model_call(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._initialized or not self._available:
            await self._clear_prefetch_attachment(ctx)
            return

        # Remove old cached prefetch section
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(EXTERNAL_MEMORY_PREFETCH_SECTION)

        # Check prefetch cache
        invoke_id = id(ctx)
        if self._prefetch_invoke_id == invoke_id and self._prefetch_cache is not None:
            raw_context = self._prefetch_cache
        else:
            # Use a unified user text resolution method to resolve query for prefetch
            query = self._resolve_user_text_for_memory(ctx)
            if not query:
                await self._clear_prefetch_attachment(ctx)
                return
            try:
                raw_context = await asyncio.wait_for(
                    self._provider.prefetch(
                        query,
                        user_id=self._user_id,
                        scope_id=self._scope_id,
                        agent_id=self._agent_id,
                        session_id=self._session_id,
                        project=self._resolve_project(ctx),
                    ),
                    timeout=self.PREFETCH_TIMEOUT,
                )
                self._prefetch_cache = raw_context
                self._prefetch_invoke_id = invoke_id
            except asyncio.TimeoutError:
                logger.warning("[JiuwenMemoryRail] prefetch timeout")
                await self._clear_prefetch_attachment(ctx)
                return
            except Exception as e:
                logger.error(f"[JiuwenMemoryRail] prefetch failed: {e}")
                await self._clear_prefetch_attachment(ctx)
                return

        if raw_context:
            fenced = self._build_memory_context_block(raw_context)
            if self.attachment_manager is None:
                logger.warning("[JiuwenMemoryRail] prompt attachment manager is unavailable; skip prefetch attachment")
                return
            writer = self.attachment_manager.bind_context(ctx)
            try:
                await writer.add_section(
                    section=EXTERNAL_MEMORY_PREFETCH_SECTION,
                    content=fenced,
                    kind=PromptAttachmentKind.MEMORY,
                    source="agent_core.jiuwen_memory_rail",
                    priority=55,
                    metadata={"provider": self._provider.name},
                    content_kind="text/markdown",
                )
            except ValueError as exc:
                logger.warning("[JiuwenMemoryRail] skip prefetch prompt attachment: %s", exc)
        else:
            await self._clear_prefetch_attachment(ctx)

    async def _clear_prefetch_attachment(self, ctx: AgentCallbackContext) -> None:
        if self.attachment_manager is None:
            return
        try:
            await self.attachment_manager.bind_context(ctx).clear_section(EXTERNAL_MEMORY_PREFETCH_SECTION)
        except ValueError as exc:
            logger.warning("[JiuwenMemoryRail] skip clearing prefetch prompt attachment: %s", exc)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Capture accepted ask_user results, after interrupt ownership routing."""
        if not self._initialized or not self._available or self._is_background_run(ctx):
            return
        if (
            getattr(ctx.inputs, "tool_name", "") != "ask_user"
            or getattr(ctx, "exception", None) is not None
            or getattr(ctx, "is_paused", False)
        ):
            return
        turn = self._memory_turns.get(self._turn_session_id(ctx))
        result = getattr(ctx.inputs, "tool_result", None)
        if turn is None or not isinstance(result, str) or not result:
            return
        call_id = getattr(getattr(ctx.inputs, "tool_call", None), "id", "")
        answers = ctx.extra.get(RESUME_INTERRUPT_ANSWERS_KEY, {}).get(call_id, {})
        for answer in answers.values():
            if answer.kind != InterruptKind.ASK_USER or answer.carried:
                continue
            answer_id = answer.request.interrupt_id
            if answer_id in turn.seen_answers:
                continue
            # Replace a plain-text supplement with the accepted question/answer
            # so values such as an authorization code retain their meaning.
            if answer.unaddressed and isinstance(answer.value, str):
                text = self._unwrap_jiuwenswarm_envelope(answer.value.strip())
                if turn.additions and turn.additions[-1] == text:
                    turn.additions.pop()
            turn.append("用户交互回答：\n" + result)
            turn.seen_answers[answer_id] = None
            if len(turn.seen_answers) > 128:
                turn.seen_answers.pop(next(iter(turn.seen_answers)))

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        if not self._initialized or not self._available:
            return
        if self._is_background_run(ctx):
            return

        session_id = self._turn_session_id(ctx)
        turn = ctx.extra.pop(_TURN_CONTEXT_KEY, None)
        result = getattr(ctx.inputs, "result", None)
        interrupted = isinstance(result, dict) and result.get("result_type") == "interrupt"
        output = "" if interrupted else self._extract_assistant_output(ctx)
        task = asyncio.current_task()
        cancelled = task is not None and task.cancelling() > 0
        has_pending_interrupt = (
            not output
            and self._interruption_state(ctx) is not None
            and getattr(ctx, "exception", None) is None
        )
        # Streaming interrupts carry no terminal answer. An empty output alone
        # is insufficient: errors and cancelled streams must not become turns.
        if not cancelled and (interrupted or has_pending_interrupt):
            if turn is not None:
                turn.updated = time.monotonic()
            return
        if self._memory_turns.get(session_id) is turn:
            self._memory_turns.pop(session_id, None)
        has_invocation_error = getattr(ctx, "exception", None) is not None
        has_error_result = isinstance(result, dict) and result.get("result_type") == "error"
        if cancelled or has_invocation_error or has_error_result:
            return

        query = turn.render() if turn is not None else (
            "" if isinstance(getattr(ctx.inputs, "query", None), InteractiveInput)
            else self._resolve_user_text_for_memory(ctx)
        )
        if not query or not output:
            return
        # Capture routing before awaiting the previous task: the host may
        # refresh its project binding for the next invocation meanwhile.
        project = self._resolve_project(ctx)

        if self._sync_consecutive_failures >= _SYNC_BREAKER_THRESHOLD:
            if time.monotonic() < self._sync_breaker_until:
                return
            self._sync_consecutive_failures = 0

        # Serialize: wait for previous sync_turn to complete
        if self._sync_task and not self._sync_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._sync_task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[JiuwenMemoryRail] Previous sync_turn task timed out after 5s, "
                    "proceeding with new sync (potential race condition)"
                )
            except Exception as exc:
                logger.warning(f"[JiuwenMemoryRail] Error waiting for sync task: {exc}")

        async def _serialized_sync():
            try:
                # 总闸：sync_turn 最多活 _SYNC_TURN_TIMEOUT 秒，超时 cancel 释放协程
                # 栈与引用，避免卡死任务「引用丢失 + 永不结束」随每轮对话累积。
                # 不 shield——超时即取消（区别于串行化等待那个 5s shield）。
                await asyncio.wait_for(
                    self._provider.sync_turn(
                        query,
                        output,
                        user_id=self._user_id,
                        scope_id=self._scope_id,
                        agent_id=self._agent_id,
                        session_id=self._session_id,
                        project=project,
                    ),
                    timeout=_SYNC_TURN_TIMEOUT,
                )
                self._sync_consecutive_failures = 0
            except asyncio.TimeoutError:
                # 必须写在 except Exception 之前：3.11+ TimeoutError 是 Exception
                # 子类，顺序反了会被当普通异常吞掉、失去超时语义。
                self._record_sync_failure(
                    f"sync_turn timed out after {_SYNC_TURN_TIMEOUT}s"
                )
            except Exception as e:
                self._record_sync_failure(str(e))

        self._sync_task = asyncio.create_task(_serialized_sync())

    def _record_sync_failure(self, reason: str) -> None:
        """累加失败计数，连续超阈值时开启熔断（复用原 after_invoke 内联逻辑）。"""
        self._sync_consecutive_failures += 1
        if self._sync_consecutive_failures >= _SYNC_BREAKER_THRESHOLD:
            self._sync_breaker_until = time.monotonic() + _SYNC_BREAKER_COOLDOWN
            logger.warning(
                f"[JiuwenMemoryRail] sync_turn failed "
                f"{self._sync_consecutive_failures} times consecutively, "
                f"circuit breaker open for {_SYNC_BREAKER_COOLDOWN}s: {reason}"
            )
        else:
            logger.warning(f"[JiuwenMemoryRail] sync_turn failed: {reason}")

    # ---- Tool registration ----

    def _register_provider_tools(self, agent) -> None:
        if not hasattr(agent, "ability_manager"):
            return
        try:
            schemas = self._provider.get_tool_schemas()
            for schema in schemas:
                tool_name = schema.get("name", "")
                if not tool_name:
                    continue

                # 差异 4：工具 id 前缀 jiuwen_memory_*
                tool_id = f"jiuwen_memory_{tool_name}"
                tool_card = ToolCard(
                    id=tool_id,
                    name=tool_name,
                    description=schema.get("description", ""),
                    input_params=schema.get("parameters", {}),
                )

                captured_provider = self._provider
                captured_name = tool_name
                captured_rail = self

                async def _tool_func(
                    captured_name=captured_name,
                    captured_provider=captured_provider,
                    captured_rail=captured_rail,
                    **kwargs):
                    # Inject routing context so provider-side coords routing
                    # (project md split) applies to explicit tool calls too.
                    # agent_id / session_id 一并注入，供 provider 的 _resolve_scope
                    # 填 scope 的 agent/session 维（与 sync_turn 路径口径一致）。
                    # pylint: disable=protected-access
                    if "user_id" not in kwargs:
                        kwargs["user_id"] = captured_rail._user_id
                    if "scope_id" not in kwargs:
                        kwargs["scope_id"] = captured_rail._scope_id
                    if "agent_id" not in kwargs and captured_rail._agent_id:
                        kwargs["agent_id"] = captured_rail._agent_id
                    if "session_id" not in kwargs:
                        kwargs["session_id"] = captured_rail._session_id
                    if not kwargs.get("project"):
                        # 现读 agent 的 _jiuwenswarm_project_dir（每轮 refresh），
                        # 不依赖 before_invoke 缓存的 _current_project——before_invoke
                        # 的 ctx.agent 与工具调用时的绑定时机可能不同步，缓存值可能
                        # 为空导致 project 丢失，落在根 MEMORY.md 而非 project 子目录。
                        # 用 `not kwargs.get("project")` 而非 `"project" not in kwargs`：
                        # LocalFunction.invoke 会经 format_with_schema 把 schema 声明的
                        # 可选 project 字段补成 None，键存在但值为空，旧守卫会漏注入。
                        project = captured_rail._resolve_project_from_agent(
                            getattr(captured_rail, "_agent", None)
                        )
                        if project:
                            kwargs["project"] = project
                    result_str = await captured_provider.handle_tool_call(captured_name, kwargs)
                    try:
                        return json.loads(result_str)
                    except json.JSONDecodeError:
                        return {"result": result_str}

                local_func = LocalFunction(card=tool_card, func=_tool_func)

                result = agent.ability_manager.add_ability(tool_card, local_func)
                if result.added:
                    self._owned_tool_cards[tool_name] = tool_card
                    logger.info(f"[JiuwenMemoryRail] Registered tool: {tool_name}")
        except Exception as e:
            logger.error(f"[JiuwenMemoryRail] Failed to register provider tools: {e}")

    # ---- Helper methods ----

    @staticmethod
    def _turn_session_id(ctx: AgentCallbackContext) -> str:
        session = getattr(ctx, "session", None)
        if session is not None:
            session_id = session.get_session_id()
            if isinstance(session_id, str) and session_id:
                return session_id
        conversation_id = getattr(ctx.inputs, "conversation_id", None)
        return conversation_id if isinstance(conversation_id, str) else ""

    @staticmethod
    def _interruption_state(ctx: AgentCallbackContext):
        get_state = getattr(getattr(ctx, "session", None), "get_state", None)
        return get_state(INTERRUPTION_KEY) if callable(get_state) else None

    def _begin_memory_turn(self, ctx: AgentCallbackContext) -> None:
        if self._is_background_run(ctx):
            return
        now = time.monotonic()
        for key, turn in list(self._memory_turns.items()):
            if now - turn.updated >= _TURN_TTL:
                self._memory_turns.pop(key, None)
        session_id = self._turn_session_id(ctx)
        if not session_id:
            return
        query = getattr(ctx.inputs, "query", None)
        resume = isinstance(query, InteractiveInput) or self._interruption_state(ctx) is not None
        turn = self._memory_turns.get(session_id) if resume else None
        if not isinstance(query, InteractiveInput):
            text = self._resolve_user_text_for_memory(ctx)
            if turn is None and text:
                turn = _MemoryTurn(text)
            elif turn is not None:
                turn.append(text)
        if turn is None:
            self._memory_turns.pop(session_id, None)
            return
        self._memory_turns.pop(session_id, None)
        if len(self._memory_turns) >= _TURN_MAX_SESSIONS:
            self._memory_turns.pop(next(iter(self._memory_turns)))
        turn.updated = now
        self._memory_turns[session_id] = turn
        ctx.extra[_TURN_CONTEXT_KEY] = turn

    @staticmethod
    def _is_background_run(ctx: AgentCallbackContext) -> bool:
        inputs = ctx.inputs
        for method_name in ("is_heartbeat", "is_cron"):
            method = getattr(inputs, method_name, None)
            if callable(method) and method():
                return True

        run_kind = getattr(inputs, "run_kind", None) or getattr(ctx, "extra", {}).get("run_kind")
        if run_kind in (RunKind.HEARTBEAT, RunKind.CRON):
            return True
        if isinstance(run_kind, str):
            return run_kind in {RunKind.HEARTBEAT.value, RunKind.CRON.value}
        return False

    @staticmethod
    def _resolve_project_from_agent(agent) -> Optional[str]:
        """从 agent 实例解析 project 坐标（与 ctx 解耦，供工具调用现读）。"""
        if agent is None:
            return None
        if getattr(agent, "_jiuwenswarm_is_projectless", False):
            return None
        project_dir = getattr(agent, "_jiuwenswarm_project_dir", None)
        if isinstance(project_dir, str) and project_dir.strip():
            return os.path.basename(project_dir.rstrip("/\\")) or None
        return None

    @staticmethod
    def _resolve_project(ctx: AgentCallbackContext) -> Optional[str]:
        """Resolve the current project name for memory routing.

        jiuwenswarm binds the active project directory to the agent instance as
        ``_jiuwenswarm_project_dir`` (updated per turn). The memory kernel routes
        by ``coords.project``, which feeds the md path ``memory/{project}/...``,
        so we use the directory basename as the stable project identifier rather
        than the absolute path.

        Projectless mode (``_jiuwenswarm_is_projectless=True``) is set by the
        DeepAgent adapter when jiuwenswarm allocates a per-chat task directory
        (``chat-<n>``) instead of a real project root. In that case we return
        None and let the jiuwen-memory kernel route to its default space —
        otherwise every projectless chat would become its own isolated project
        and history would not carry across chats.

        Returns None when no project directory is bound — the provider then omits
        the coords key and memory lands in the kernel's default directory.
        """
        return JiuwenMemoryRail._resolve_project_from_agent(getattr(ctx, "agent", None))

    @staticmethod
    def _resolve_user_text_for_memory(ctx: AgentCallbackContext) -> str:
        """Resolve user text for memory storage.

        Priority rules:
        1. RunContext.extra["raw_query"]
        2. Prefer non-empty ctx.inputs.query
        3. Fallback to last user message in ctx.inputs.messages
        4. Log warning and return empty string if all are empty

        Note: Should use this method consistently for prefetch and sync_turn.

        Args:
            ctx: AgentCallbackContext

        Returns:
            User text string
        """
        # Highest priority: raw query from RunContext.extra
        #   before_model_call: ctx.extra["run_context"] (bridged to ReActAgent)
        #   after_invoke: ctx.inputs.run_context (on DeepAgent)
        run_context = getattr(ctx.inputs, "run_context", None)
        if run_context is None and isinstance(getattr(ctx, "extra", None), dict):
            run_context = ctx.extra.get("run_context")
        if run_context is not None and hasattr(run_context, "extra"):
            mem_query = run_context.extra.get("raw_query", "")
            if isinstance(mem_query, str) and mem_query.strip():
                return JiuwenMemoryRail._unwrap_jiuwenswarm_envelope(mem_query.strip())

        # Prioritize the query field
        if hasattr(ctx.inputs, "query"):
            q = ctx.inputs.query
            if isinstance(q, str) and q.strip():
                return JiuwenMemoryRail._unwrap_jiuwenswarm_envelope(q.strip())

        # Fallback to messages field
        if hasattr(ctx.inputs, "messages"):
            messages = ctx.inputs.messages
            if messages:
                for msg in reversed(messages):
                    # Handle dict and object with role attribute
                    if isinstance(msg, dict):
                        role = msg.get("role")
                    else:
                        role = getattr(msg, "role", None)

                    if role == "user":
                        if isinstance(msg, dict):
                            content = msg.get("content")
                        else:
                            content = getattr(msg, "content", None)

                        if isinstance(content, str) and content.strip():
                            return JiuwenMemoryRail._unwrap_jiuwenswarm_envelope(content.strip())
                        if isinstance(content, list):
                            texts = [
                                p.get("text", "")
                                for p in content
                                if isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
                            ]
                            if texts:
                                return JiuwenMemoryRail._unwrap_jiuwenswarm_envelope(" ".join(texts).strip())

        # Both query and messages are empty, log warning and return empty string
        session_id = getattr(ctx.inputs, "session_id", "unknown") if hasattr(ctx.inputs, "session_id") else "unknown"
        trace_id = id(ctx) % 10000
        logger.warning(
            f"[JiuwenMemoryRail] Cannot resolve user text for memory. "
            f"session_id={session_id}, trace_id={trace_id}, "
            f"has_query={hasattr(ctx.inputs, 'query')}, "
            f"has_messages={hasattr(ctx.inputs, 'messages')}"
        )
        return ""

    @staticmethod
    def _unwrap_jiuwenswarm_envelope(query: str) -> str:
        """Strip the jiuwenswarm web-channel envelope and return the inner ``content``.

        jiuwenswarm wraps raw user input into a JSON envelope before passing it to the
        memory rail::

            你收到一条消息：\\n{"source": "web", ..., "content": "<真实输入>", "type": "user input", ...}

        The envelope pollutes both keyword tokenization (extra ``{`` ``}`` ``:`` ``"`` tokens
        trigger FTS5 syntax errors) and md-file content (the stored md would carry the
        envelope rather than what the user actually typed). This helper extracts the
        inner ``content`` string when the input matches the envelope shape; otherwise it
        returns the input unchanged so non-envelope queries (SDK, CLI, other channels)
        behave identically.
        """
        if not query or not query.startswith("你收到一条消息："):
            return query
        # locate the JSON object body after the fixed prefix
        json_start = query.find("{")
        if json_start < 0:
            return query
        # find the matching closing brace by simple depth counting; the envelope is a
        # flat dict so depth never exceeds 1, but be defensive against nested values
        depth = 0
        json_end = -1
        for i in range(json_start, len(query)):
            ch = query[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    json_end = i
                    break
        if json_end < 0:
            return query
        import json as _json
        try:
            envelope = _json.loads(query[json_start:json_end + 1])
        except Exception:
            return query
        if not isinstance(envelope, dict):
            return query
        content = envelope.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return query

    @staticmethod
    def _extract_assistant_output(ctx: AgentCallbackContext) -> str:
        """Extract assistant output from after_invoke context.

        Supports multiple result structures:
        - ctx.inputs.result.output (DeepAgent common)
        - ctx.inputs.result.message.content
        - ctx.inputs.result.get("content")

        Args:
            ctx: AgentCallbackContext

        Returns:
            Assistant output string, empty string on parse failure with structured logging
        """
        if not hasattr(ctx.inputs, "result"):
            session_id = (
                getattr(ctx.inputs, "session_id", "unknown") if hasattr(ctx.inputs, "session_id") else "unknown"
            )

            logger.warning(
                f"[JiuwenMemoryRail] Cannot extract assistant output: result missing. session_id={session_id}"
            )
            return ""

        result = ctx.inputs.result
        if not isinstance(result, dict):
            return str(result) if result else ""

        # Try multiple output keys
        output_keys = ["output", "message", "content", "text", "response"]
        for key in output_keys:
            if key in result:
                value = result[key]
                if isinstance(value, str) and value.strip():
                    return value.strip()
                # message may be nested structure
                if isinstance(value, dict) and "content" in value:
                    content = value["content"]
                    if isinstance(content, str) and content.strip():
                        return content.strip()

        # Parse failure, log warning and return empty string
        session_id = getattr(ctx.inputs, "session_id", "unknown") if hasattr(ctx.inputs, "session_id") else "unknown"
        logger.warning(
            f"[JiuwenMemoryRail] Cannot extract assistant output from result keys. "
            f"session_id={session_id}, available_keys={list(result.keys())}"
        )
        return ""

    @staticmethod
    def _build_memory_context_block(raw_context: str) -> str:
        return (
            "<memory-context>\n"
            "[System note: recalled memory context from long-term memory, NOT new user input.]\n\n"
            f"{raw_context}\n"
            "</memory-context>"
        )


__all__ = ["JiuwenMemoryRail"]
