# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：JiuwenMemoryRail。

契约依据（基础装配差异与生命周期行为；交互恢复另见 test_jiuwen_memory_interrupts.py）：
``agent-memory:jiuwen_memory_adapter/jiuwenswarm/jiuwen_memory_rail_design.md`` §6.1。
mock 风格对齐同目录 ``test_external_memory_rail.py``。
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.memory.external.provider import MemoryProvider
from openjiuwen.core.single_agent.rail.base import RunKind
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.rails.memory.jiuwen_memory_rail import JiuwenMemoryRail
from openjiuwen.harness.rails.memory.memory_turn import _MemoryTurn


class MockInputs:
    """测试用 Mock Inputs"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockCallbackContext:
    """测试用 Mock AgentCallbackContext"""

    def __init__(self, inputs):
        self.inputs = inputs
        self.extra = {}
        self.agent = None
        self.session = None


class MockSession:
    def get_session_id(self) -> str:
        return "sess1"


class MockAgent:
    def __init__(self) -> None:
        self.ability_manager = FakeAbilityManager()
        self.system_prompt_builder = MockPromptBuilder()
        self.prompt_attachment_manager = PromptAttachmentManager()


class MockPromptBuilder:
    language = "cn"

    def __init__(self) -> None:
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section):
        self.added_sections.append(section)

    def remove_section(self, section_name):
        self.removed_sections.append(section_name)


class FakeAbilityManager:
    """add_ability / get / remove_ability 最小实现（card 身份可校验）。"""

    def __init__(self) -> None:
        self._abilities: dict[str, tuple] = {}

    def add_ability(self, card, func):
        if card.name in self._abilities:
            return SimpleNamespace(added=False)
        self._abilities[card.name] = (card, func)
        return SimpleNamespace(added=True)

    def get(self, name):
        entry = self._abilities.get(name)
        return entry[0] if entry else None

    def remove_ability(self, name):
        self._abilities.pop(name, None)

    def names(self) -> set[str]:
        return set(self._abilities)

    def func_of(self, name):
        return self._abilities[name][1]


class MockMemoryProvider(MemoryProvider):
    """测试用 Mock Provider：记录调用供断言。"""

    def __init__(self, *, available: bool = True, prefetch_result: str = "- fact") -> None:
        self.available = available
        self.prefetch_result = prefetch_result
        self.initialize_kwargs: dict | None = None
        self.shutdown_called = False
        self.session_end_calls: list[list] = []
        self.prefetch_queries: list[str] = []
        self.sync_calls: list[tuple[str, str]] = []
        self.sync_raises: Exception | None = None

    @property
    def name(self) -> str:
        return "mock_provider"

    def is_available(self) -> bool:
        return self.available

    async def initialize(self, **kwargs) -> None:
        self.initialize_kwargs = kwargs

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_search",
                "description": "Search memory",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "memory_add",
                "description": "Add memory",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return json.dumps({"result": "success", "tool": tool_name})

    async def prefetch(self, query: str, **kwargs) -> str:
        self.prefetch_queries.append(query)
        return f"## Mock Memory\n{self.prefetch_result}" if self.prefetch_result else ""

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        if self.sync_raises is not None:
            raise self.sync_raises
        self.sync_calls.append((user_msg, assistant_msg))

    def system_prompt_block(self) -> str:
        return "Use memory_search tool."

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def on_session_end(self, messages) -> None:
        self.session_end_calls.append(list(messages or []))


def make_ctx(query=None, result=None, run_kind=None) -> MockCallbackContext:
    return MockCallbackContext(MockInputs(query=query, messages=[], result=result, run_kind=run_kind))


# --------------------------------------------------------------------------- #
# init / uninit / wait_closed
# --------------------------------------------------------------------------- #


class TestInitUninit:
    def test_init_registers_tools_and_prompt_section(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        assert agent.ability_manager.names() == {"memory_search", "memory_add"}
        # 差异 4：工具 id 前缀 jiuwen_memory_*
        card = agent.ability_manager.get("memory_search")
        assert card.id == "jiuwen_memory_memory_search"
        assert agent.system_prompt_builder.added_sections, "provider 提示词段应注入"

        rail._turn_buffer.turns["pending"] = _MemoryTurn("未结束的请求")
        rail.uninit(agent)
        assert rail._turn_buffer.turns == {}
        assert agent.ability_manager.names() == set()
        assert agent.system_prompt_builder.removed_sections
        assert provider.shutdown_called

    def test_registered_tool_func_routes_to_provider(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        # add_ability 存的是 LocalFunction 包装；其 _func 即 rail 注入的闭包
        local_func = agent.ability_manager.func_of("memory_search")
        result = asyncio.run(local_func._func(query="x"))
        assert result == {"result": "success", "tool": "memory_search"}

        rail.uninit(agent)

    def test_unavailable_provider_degrades_to_noop(self):
        """差异 2：is_available 为假 → 不注册工具、不注入提示词、不跑 prefetch。"""
        agent = MockAgent()
        provider = MockMemoryProvider(available=False)
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        assert agent.ability_manager.names() == set()
        assert agent.system_prompt_builder.added_sections == []

        ctx = make_ctx(query="hello")
        asyncio.run(rail.before_invoke(ctx))
        assert provider.initialize_kwargs is None
        assert provider.prefetch_queries == []

        rail.uninit(agent)

    def test_uninit_skips_tools_taken_over_by_other_rail(self):
        """card 身份校验：同名工具已被别的 rail 接管时不误删。"""
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        # 模拟接管：另一张 card 顶掉了 memory_search
        agent.ability_manager._abilities["memory_search"] = (SimpleNamespace(id="other"), None)

        rail.uninit(agent)
        # 被接管的保留；自己独有的 memory_add 被移除
        assert "memory_search" in agent.ability_manager.names()
        assert "memory_add" not in agent.ability_manager.names()

    def test_uninit_with_session_end_extract_triggers_on_session_end(self):
        """差异 1：session_end_extract=True 时 uninit 触发批量 EXTRACT。"""
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider, session_end_extract=True)
        rail.init(agent)
        rail._initialized = True

        rail.uninit(agent)
        assert len(provider.session_end_calls) == 1
        assert provider.shutdown_called

    def test_uninit_without_flag_skips_on_session_end(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)
        rail._initialized = True

        rail.uninit(agent)
        assert provider.session_end_calls == []
        assert provider.shutdown_called

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extract", [False, True])
    async def test_uninit_on_running_loop_is_nonblocking_and_closes_once(self, extract):
        agent = MockAgent()
        provider = MockMemoryProvider()
        provider.shutdown = AsyncMock()
        rail = JiuwenMemoryRail(provider, session_end_extract=extract)
        rail.init(agent)
        rail._initialized = True
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)

        started = time.monotonic()
        rail.uninit(agent)
        rail.uninit(agent)
        elapsed = time.monotonic() - started
        await asyncio.wait_for(heartbeat.wait(), timeout=1.0)
        await asyncio.wait_for(rail.wait_closed(), timeout=1.0)
        rail.uninit(agent)

        assert elapsed < 1.0, "uninit must not block the event-loop thread"
        assert provider.shutdown.await_count == 1
        assert provider.session_end_calls == ([[]] if extract else [])
        assert not rail._initialized
        assert agent.ability_manager.names() == set()
        assert agent.system_prompt_builder.removed_sections

    @pytest.mark.asyncio
    async def test_uninit_returns_while_provider_shutdown_is_pending(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        started = asyncio.Event()
        release = asyncio.Event()

        async def shutdown():
            started.set()
            await release.wait()
            provider.shutdown_called = True

        provider.shutdown = shutdown
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)
        rail.uninit(agent)
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert not rail._shutdown_task.done()
            assert not provider.shutdown_called
            cleanup = rail._shutdown_task
            with pytest.raises(RuntimeError, match="wait_closed"):
                rail.init(agent)
            assert rail._shutdown_task is cleanup
            assert rail._shutdown_started
            assert rail.system_prompt_builder is None
            assert agent.ability_manager.names() == set()
        finally:
            release.set()
            await asyncio.wait_for(rail.wait_closed(), timeout=1.0)
        assert provider.shutdown_called

        rail.init(agent)
        await rail.before_invoke(make_ctx(query="hello"))
        assert rail._initialized
        assert provider.initialize_kwargs is not None
        rail.uninit(agent)
        await asyncio.wait_for(rail.wait_closed(), timeout=1.0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [RuntimeError("close failed"), asyncio.TimeoutError()])
    async def test_uninit_background_shutdown_errors_are_handled(self, failure, monkeypatch):
        agent = MockAgent()
        provider = MockMemoryProvider()
        provider.on_session_end = AsyncMock(side_effect=RuntimeError("extract failed"))
        provider.shutdown = AsyncMock(side_effect=failure)
        warnings = []
        monkeypatch.setattr(
            "openjiuwen.harness.rails.memory.jiuwen_memory_rail.logger.warning",
            lambda message: warnings.append(message),
        )
        rail = JiuwenMemoryRail(provider, session_end_extract=True)
        rail.init(agent)
        rail._initialized = True
        rail.uninit(agent)
        await asyncio.wait_for(rail.wait_closed(), timeout=1.0)

        provider.on_session_end.assert_awaited_once_with([])
        provider.shutdown.assert_awaited_once()
        assert any("on_session_end failed" in message for message in warnings)
        assert any("shutdown" in message for message in warnings)

    @pytest.mark.parametrize("extract", [False, True])
    def test_wait_closed_finishes_cleanup_before_loop_exit(self, extract):
        agent = MockAgent()
        provider = MockMemoryProvider()
        completed = []

        async def extract_session(_messages):
            await asyncio.sleep(0)
            completed.append("extract")

        async def shutdown():
            await asyncio.sleep(0)
            completed.append("shutdown")

        provider.on_session_end = extract_session
        provider.shutdown = shutdown
        rail = JiuwenMemoryRail(provider, session_end_extract=extract)

        async def close_before_exit():
            await rail.wait_closed()  # No scheduled cleanup yet.
            rail.init(agent)
            rail._initialized = True
            rail.uninit(agent)
            await asyncio.wait_for(rail.wait_closed(), timeout=1.0)
            await rail.wait_closed()  # Repeated waits do not repeat cleanup.

        asyncio.run(close_before_exit())
        assert completed == (["extract", "shutdown"] if extract else ["shutdown"])
        assert rail._shutdown_task.done()
        assert not rail._shutdown_task.cancelled()

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_cancel_cleanup(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        started = asyncio.Event()
        release = asyncio.Event()

        async def shutdown():
            started.set()
            await release.wait()
            provider.shutdown_called = True

        provider.shutdown = shutdown
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)
        rail.uninit(agent)
        waiter = asyncio.create_task(rail.wait_closed())
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not rail._shutdown_task.done()
        finally:
            release.set()
            await asyncio.wait_for(rail.wait_closed(), timeout=1.0)
        assert provider.shutdown_called

    def test_repeated_uninit_without_loop_closes_once_per_mount(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        provider.shutdown = AsyncMock()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)
        rail.uninit(agent)
        rail.uninit(agent)
        provider.shutdown.assert_awaited_once()
        asyncio.run(rail.wait_closed())

        rail.init(agent)
        rail.uninit(agent)
        assert provider.shutdown.await_count == 2


# --------------------------------------------------------------------------- #
# before_invoke / before_model_call / after_invoke / end_session
# --------------------------------------------------------------------------- #


class TestInvokeLifecycle:
    @pytest.mark.asyncio
    async def test_before_invoke_initializes_with_empty_session_default(self):
        """差异 3：session_id 缺省空串 = 跨会话共享。"""
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider, user_id="u1", scope_id="t1")
        rail.init(agent)

        ctx = make_ctx(query="记住常用语言是 Python")
        ctx.agent = agent
        ctx.session = MockSession()
        await rail.before_invoke(ctx)

        assert provider.initialize_kwargs == {
            "user_id": "u1",
            "scope_id": "t1",
            "agent_id": "",
            "session_id": "",
        }
        assert provider.prefetch_queries == ["记住常用语言是 Python"]

        # prefetch 命中经真实 PromptAttachmentManager 注入为 MEMORY 附件
        items = await agent.prompt_attachment_manager.collect_for_session("sess1")
        assert [item.id for item in items] == ["session.sess1.external_memory_prefetch"]
        assert items[0].kind.value == "memory"
        assert items[0].source == "agent_core.jiuwen_memory_rail"
        assert "Mock Memory" in (items[0].content or "")
        assert (items[0].content or "").startswith("<memory-context>")

    @pytest.mark.asyncio
    async def test_after_invoke_syncs_resolved_query_and_output(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        ctx = make_ctx(query="记住深色主题", result={"output": "已记住"})
        await rail.before_invoke(ctx)
        await rail.after_invoke(ctx)
        assert rail._sync_task is not None
        await rail._sync_task  # 冲刷串行化任务

        assert provider.sync_calls == [("记住深色主题", "已记住")]

    @pytest.mark.asyncio
    async def test_after_invoke_skips_background_runs(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        ctx = make_ctx(query="q", result={"output": "a"}, run_kind=RunKind.HEARTBEAT)
        await rail.before_invoke(ctx)
        await rail.after_invoke(ctx)

        assert provider.sync_calls == []

    @pytest.mark.asyncio
    async def test_after_invoke_breaker_opens_after_consecutive_failures(self):
        agent = MockAgent()
        provider = MockMemoryProvider()
        provider.sync_raises = RuntimeError("server down")
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        ctx = make_ctx(query="q", result={"output": "a"})
        await rail.before_invoke(ctx)
        for _ in range(5):
            await rail.after_invoke(ctx)
            await rail._sync_task

        # 连续 5 次失败 → 熔断开启，第 6 次不再下发 sync（复用旧任务引用）
        assert rail._sync_consecutive_failures >= 5
        await rail.after_invoke(ctx)
        assert rail._sync_task.done()
        assert len(provider.sync_calls) == 0  # 全部失败，无成功调用

    @pytest.mark.asyncio
    async def test_end_session_triggers_provider_session_end(self):
        """差异 1：显式入口，与 session_end_extract 开关无关。"""
        agent = MockAgent()
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(agent)

        ctx = make_ctx(query="q")
        await rail.before_invoke(ctx)
        await rail.end_session([{"role": "user", "content": "q"}])

        assert len(provider.session_end_calls) == 1

    @pytest.mark.asyncio
    async def test_end_session_noop_before_initialize(self):
        provider = MockMemoryProvider()
        rail = JiuwenMemoryRail(provider)
        rail.init(MockAgent())

        await rail.end_session()
        assert provider.session_end_calls == []
