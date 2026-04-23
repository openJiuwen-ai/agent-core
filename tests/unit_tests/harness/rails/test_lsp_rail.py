# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：LspRail — 初始化、工具注册、清理

此测试文件直接测试 lsp_rail 模块的核心功能。
如果环境缺少 a2a 模块，整个文件会被 skip。
"""

from __future__ import annotations

import pytest

# 在导入 openjiuwen 之前先检查依赖
try:
    import a2a  # noqa: F401
except ImportError:
    pytest.skip("Requires 'a2a' module", allow_module_level=True)

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import InitializeOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ability_manager():
    am = MagicMock()
    am.add = MagicMock(return_value=MagicMock(added=True))
    am.remove = MagicMock()
    return am


def _make_deep_config(workspace_root="/workspace", language="cn"):
    cfg = MagicMock()
    cfg.sys_operation = MagicMock()
    cfg.workspace = MagicMock()
    cfg.workspace.root_path = workspace_root
    cfg.language = language
    return cfg


class _FakeDeepAgent:
    """Minimal stand-in accepted by isinstance(agent, DeepAgent)."""
    def __init__(self, workspace_root="/workspace", language="cn"):
        self.deep_config = _make_deep_config(workspace_root, language)
        self.ability_manager = _make_ability_manager()


def _make_agent(workspace_root="/workspace", language="cn"):
    return _FakeDeepAgent(workspace_root=workspace_root, language=language)


@contextmanager
def _patch_init_deps(agent, *, workspace_root="/workspace"):
    """Patch the three external deps touched by LspRail.init()."""
    mock_tool = MagicMock()
    mock_tool.card = MagicMock(id="lsp-tool-id", name="lsp")
    loop = asyncio.new_event_loop()
    try:
        with patch("openjiuwen.harness.tools.LspTool") as MockLspTool, \
             patch("openjiuwen.core.runner.runner.Runner") as MockRunner, \
             patch("openjiuwen.harness.lsp.initialize_lsp", new_callable=AsyncMock), \
             patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent), \
             patch("asyncio.get_running_loop", return_value=loop):
            MockLspTool.return_value = mock_tool
            yield MockLspTool, MockRunner, mock_tool, loop
        # drain tasks created by init() before closing the loop
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()


@contextmanager
def _patch_uninit_deps():
    loop = asyncio.new_event_loop()
    try:
        with patch("openjiuwen.core.runner.runner.Runner") as MockRunner, \
             patch("openjiuwen.harness.lsp.shutdown_lsp", new_callable=AsyncMock), \
             patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent), \
             patch("asyncio.get_running_loop", return_value=loop):
            yield MockRunner, loop
        # drain any tasks created by uninit() before closing the loop
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()


# ===========================================================================
# 1. 构造函数
# ===========================================================================

class TestLspRailInit:
    def test_default_attributes(self):
        rail = LspRail()
        assert rail.options is None
        assert rail._lsp_tool is None
        assert rail._initialized is False

    def test_custom_options_stored(self):
        opts = InitializeOptions(cwd="/my/project")
        rail = LspRail(options=opts)
        assert rail.options is opts

    def test_priority_is_60(self):
        assert LspRail.priority == 60


# ===========================================================================
# 2. init() — 工具注册
# ===========================================================================

class TestLspRailInitMethod:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Reset LSPServerManager singleton before each test."""
        from openjiuwen.harness.lsp.core.manager import LSPServerManager
        LSPServerManager._instance = None
        LSPServerManager._lock = None
        yield
        LSPServerManager._instance = None
        LSPServerManager._lock = None

    def test_registers_tool_instance_in_resource_manager(self):
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent) as (_, MockRunner, mock_tool, _):
            rail.init(agent)
        MockRunner.resource_mgr.add_tool.assert_called_once_with(mock_tool)

    def test_resource_mgr_receives_tool_not_card(self):
        """resource_mgr.add_tool 必须收到 Tool 实例，而非 ToolCard。"""
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent) as (_, MockRunner, mock_tool, _):
            rail.init(agent)
        arg = MockRunner.resource_mgr.add_tool.call_args[0][0]
        assert arg is mock_tool
        assert arg is not mock_tool.card

    def test_registers_tool_card_in_ability_manager(self):
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent) as (_, _, mock_tool, _):
            rail.init(agent)
        agent.ability_manager.add.assert_called_once_with(mock_tool.card)

    def test_initialized_flag_set_after_success(self):
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent):
            rail.init(agent)
        assert rail._initialized is True

    def test_lsp_tool_created_with_config_language(self):
        """LspTool 应使用 agent.deep_config.language 初始化，并传递 operation、workspace 和 agent_id"""
        rail = LspRail()
        agent = _make_agent(language="en")
        with _patch_init_deps(agent) as (MockLspTool, _, _, _):
            rail.init(agent)
        MockLspTool.assert_called_once_with(
            operation=agent.deep_config.sys_operation,
            language="en",
            workspace="/workspace",
            agent_id=None,
        )

    def test_lsp_tool_defaults_to_cn(self):
        """默认语言应为 cn，并正确传递 operation、workspace 和 agent_id"""
        rail = LspRail()
        agent = _make_agent()  # 默认 language="cn"
        with _patch_init_deps(agent) as (MockLspTool, _, _, _):
            rail.init(agent)
        MockLspTool.assert_called_once_with(
            operation=agent.deep_config.sys_operation,
            language="cn",
            workspace="/workspace",
            agent_id=None,
        )

    def test_skips_when_not_deep_agent(self):
        """非 DeepAgent 实例时 init() 应静默跳过。"""
        rail = LspRail()
        plain_agent = MagicMock()
        plain_agent.deep_config = MagicMock()
        # DeepAgent is NOT patched to _FakeDeepAgent here, so isinstance fails
        with patch("openjiuwen.harness.tools.LspTool") as MockLspTool:
            rail.init(plain_agent)
        MockLspTool.assert_not_called()
        assert rail._initialized is False

    def test_skips_when_no_deep_config(self):
        rail = LspRail()
        agent = _make_agent()
        agent.deep_config = None
        with patch("openjiuwen.harness.tools.LspTool") as MockLspTool, \
             patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent):
            rail.init(agent)
        MockLspTool.assert_not_called()
        assert rail._initialized is False


# ===========================================================================
# 3. init() — cwd 推断
# ===========================================================================

class TestLspRailCwdResolution:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Reset LSPServerManager singleton before each test."""
        from openjiuwen.harness.lsp.core.manager import LSPServerManager
        LSPServerManager._instance = None
        LSPServerManager._lock = None
        yield
        LSPServerManager._instance = None
        LSPServerManager._lock = None

    def _captured_opts(self, rail, agent):
        """Run init() and return the InitializeOptions passed to initialize_lsp."""
        captured = {}
        loop = asyncio.new_event_loop()

        async def _fake_init(opts):
            captured["opts"] = opts

        try:
            with patch("openjiuwen.harness.tools.LspTool") as MockLspTool, \
                 patch("openjiuwen.core.runner.runner.Runner"), \
                 patch("openjiuwen.harness.lsp.initialize_lsp", side_effect=_fake_init), \
                 patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent), \
                 patch("asyncio.get_running_loop", return_value=loop):
                mock_tool = MagicMock()
                mock_tool.card = MagicMock(id="lsp-tool-id", name="lsp")
                MockLspTool.return_value = mock_tool
                rail.init(agent)
                # drain the task that was created
                loop.run_until_complete(asyncio.sleep(0))
        finally:
            loop.close()
        return captured.get("opts")

    def test_uses_workspace_root_as_cwd(self):
        rail = LspRail()
        agent = _make_agent(workspace_root="/my/project")
        opts = self._captured_opts(rail, agent)
        assert opts is not None
        assert opts.cwd == "/my/project"

    def test_explicit_options_cwd_takes_precedence(self):
        rail = LspRail(options=InitializeOptions(cwd="/explicit/cwd"))
        agent = _make_agent(workspace_root="/workspace/root")
        opts = self._captured_opts(rail, agent)
        assert opts.cwd == "/explicit/cwd"

    def test_options_without_cwd_gets_workspace_cwd(self):
        rail = LspRail(options=InitializeOptions(cwd=None))
        agent = _make_agent(workspace_root="/ws")
        opts = self._captured_opts(rail, agent)
        assert opts.cwd == "/ws"


# ===========================================================================
# 4. uninit() — 清理
# ===========================================================================

class TestLspRailUninit:
    def _init_rail(self, rail, agent):
        with _patch_init_deps(agent) as (_, _, mock_tool, _):
            rail.init(agent)
        return mock_tool

    def test_removes_tool_from_ability_manager(self):
        rail = LspRail()
        agent = _make_agent()
        mock_tool = self._init_rail(rail, agent)
        with _patch_uninit_deps():
            rail.uninit(agent)
        agent.ability_manager.remove.assert_called_once_with(mock_tool.card.name)

    def test_removes_tool_from_resource_manager(self):
        rail = LspRail()
        agent = _make_agent()
        mock_tool = self._init_rail(rail, agent)
        with _patch_uninit_deps() as (MockRunner, _):
            rail.uninit(agent)
        MockRunner.resource_mgr.remove_tool.assert_called_once_with(mock_tool.card.id)

    def test_clears_lsp_tool_reference(self):
        rail = LspRail()
        agent = _make_agent()
        self._init_rail(rail, agent)
        with _patch_uninit_deps():
            rail.uninit(agent)
        assert rail._lsp_tool is None
        assert rail._initialized is False

    def test_uninit_without_prior_init_does_not_raise(self):
        rail = LspRail()
        agent = _make_agent()
        with patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent), \
             patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            rail.uninit(agent)  # should not raise


# ===========================================================================
# 5. _async_init_lsp() — 异步初始化
# ===========================================================================

class TestAsyncInitLsp:
    @pytest.mark.asyncio
    async def test_calls_initialize_lsp_with_options(self):
        rail = LspRail()
        opts = InitializeOptions(cwd="/project")
        with patch("openjiuwen.harness.lsp.initialize_lsp", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = MagicMock(success=True, servers_loaded=1)
            await rail._async_init_lsp(opts)
        mock_init.assert_awaited_once_with(opts)

    @pytest.mark.asyncio
    async def test_handles_initialize_lsp_exception_gracefully(self):
        rail = LspRail()
        opts = InitializeOptions(cwd="/project")
        with patch("openjiuwen.harness.lsp.initialize_lsp", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = RuntimeError("server failed to start")
            await rail._async_init_lsp(opts)  # must not raise


# ===========================================================================
# 6. _async_shutdown_lsp() — 异步关闭
# ===========================================================================

class TestAsyncShutdownLsp:
    @pytest.mark.asyncio
    async def test_calls_shutdown_lsp(self):
        rail = LspRail()
        with patch("openjiuwen.harness.lsp.shutdown_lsp", new_callable=AsyncMock) as mock_shutdown:
            await rail._async_shutdown_lsp()
        mock_shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_shutdown_exception_gracefully(self):
        rail = LspRail()
        with patch("openjiuwen.harness.lsp.shutdown_lsp", new_callable=AsyncMock) as mock_shutdown:
            mock_shutdown.side_effect = RuntimeError("shutdown error")
            await rail._async_shutdown_lsp()  # must not raise


# ===========================================================================
# 7. get_callbacks() — 不注册已删除的钩子
# ===========================================================================

class TestGetCallbacks:
    def test_before_model_call_not_registered(self):
        """before_model_call 已删除，不应注册"""
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
        callbacks = LspRail().get_callbacks()
        assert AgentCallbackEvent.BEFORE_MODEL_CALL not in callbacks

    def test_after_invoke_not_registered(self):
        """after_invoke 已删除，不应注册"""
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
        callbacks = LspRail().get_callbacks()
        assert AgentCallbackEvent.AFTER_INVOKE not in callbacks

    def test_unused_hooks_not_registered(self):
        """未实现的钩子不应注册"""
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
        callbacks = LspRail().get_callbacks()
        assert AgentCallbackEvent.BEFORE_TOOL_CALL not in callbacks
        assert AgentCallbackEvent.AFTER_TOOL_CALL not in callbacks
        assert AgentCallbackEvent.ON_MODEL_EXCEPTION not in callbacks
