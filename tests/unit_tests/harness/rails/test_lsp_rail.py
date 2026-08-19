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
from contextlib import contextmanager, nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import InitializeOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ability_manager():
    am = MagicMock()
    am.add = MagicMock(return_value=MagicMock(added=True))
    am.remove = MagicMock()
    am.add_ability = MagicMock(return_value=MagicMock(added=True))
    am.remove_ability = MagicMock()
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
def _patch_init_deps(agent, *, workspace_root="/workspace", suppress_initialization=True):
    """Patch the external dependencies touched by ``LspRail.init()``."""
    mock_tool = MagicMock()
    mock_tool.card = MagicMock(id="lsp-tool-id", name="lsp")
    initialization_patch = (
        patch.object(LspRail, "_start_lsp_initialization")
        if suppress_initialization
        else nullcontext()
    )
    with (
        patch("openjiuwen.harness.tools.LspTool") as MockLspTool,
        patch("openjiuwen.core.runner.runner.Runner") as MockRunner,
        patch("openjiuwen.harness.lsp.initialize_lsp", new_callable=AsyncMock),
        patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent),
        initialization_patch,
    ):
        MockLspTool.return_value = mock_tool
        yield MockLspTool, MockRunner, mock_tool


@contextmanager
def _patch_uninit_deps():
    """Patch dependencies touched by ``LspRail.uninit()``."""
    with (
        patch("openjiuwen.core.runner.runner.Runner") as MockRunner,
        patch("openjiuwen.harness.lsp.shutdown_lsp", new_callable=AsyncMock) as mock_shutdown,
        patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent),
    ):
        yield MockRunner, mock_shutdown


# ===========================================================================
# 1. 构造函数
# ===========================================================================


class TestLspRailInit:
    def test_default_attributes(self):
        rail = LspRail()
        assert rail.options is None
        assert rail._lsp_tool is None
        assert rail._initialized is False
        assert rail._initialize_options is None
        assert rail._initialization_lock is None
        assert rail._initialization_task is None
        assert rail._shutdown_task is None

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
        with _patch_init_deps(agent) as (_, _, mock_tool):
            rail.init(agent)
        agent.ability_manager.add_ability.assert_called_once_with(mock_tool.card, mock_tool)

    def test_resource_mgr_receives_tool_not_card(self):
        """resource_mgr.add_tool 必须收到 Tool 实例，而非 ToolCard。"""
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent) as (_, _, mock_tool):
            rail.init(agent)
        card_arg, resource_arg = agent.ability_manager.add_ability.call_args[0]
        assert resource_arg is mock_tool
        assert card_arg is mock_tool.card

    def test_registers_tool_card_in_ability_manager(self):
        rail = LspRail()
        agent = _make_agent()
        with _patch_init_deps(agent) as (_, _, mock_tool):
            rail.init(agent)
        agent.ability_manager.add_ability.assert_called_once_with(mock_tool.card, mock_tool)

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
        with _patch_init_deps(agent) as (MockLspTool, _, _):
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
        with _patch_init_deps(agent) as (MockLspTool, _, _):
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
        with (
            patch("openjiuwen.harness.tools.LspTool") as MockLspTool,
            patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent),
        ):
            rail.init(agent)
        MockLspTool.assert_not_called()
        assert rail._initialized is False

    @pytest.mark.asyncio
    async def test_init_schedules_lsp_initialization_on_running_loop(self):
        rail = LspRail()
        agent = _make_agent()

        with (
            _patch_init_deps(agent, suppress_initialization=False),
            patch.object(rail, "_ensure_lsp_initialized", new_callable=AsyncMock) as mock_initialize,
        ):
            rail.init(agent)
            assert rail._initialization_task is not None
            mock_initialize.assert_not_awaited()
            await rail._initialization_task

        mock_initialize.assert_awaited_once()
        assert rail._initialize_options == InitializeOptions(cwd="/workspace")

    def test_start_lsp_initialization_uses_sync_fallback_without_running_loop(self):
        rail = LspRail()

        with patch.object(rail, "_ensure_lsp_initialized", new_callable=AsyncMock) as mock_initialize:
            rail._start_lsp_initialization()

        mock_initialize.assert_awaited_once()


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
        """Run init() and return its resolved InitializeOptions."""
        with _patch_init_deps(agent):
            rail.init(agent)
        return rail._initialize_options

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


class TestLspRailInitialization:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        from openjiuwen.harness.lsp.core.manager import LSPServerManager

        LSPServerManager._instance = None
        LSPServerManager._lock = None
        yield
        LSPServerManager._instance = None
        LSPServerManager._lock = None

    @pytest.mark.asyncio
    async def test_before_model_call_injects_diagnostics_without_waiting_for_initialization(self):
        from openjiuwen.harness.lsp.core.diagnostic_registry import LspDiagnosticFile, LspDiagnosticItem

        rail = LspRail()
        context = MagicMock()
        context.inputs.messages = []
        diagnostics = [
            LspDiagnosticFile(
                uri="file:///project/example.py",
                local_path="/project/example.py",
                server_name="pyright",
                diagnostics=[
                    LspDiagnosticItem(
                        message="undefined variable 'value'",
                        severity=1,
                        range={"start": {"line": 2, "character": 4}},
                        code="reportUndefinedVariable",
                    )
                ],
            )
        ]

        with (
            patch.object(rail, "_ensure_lsp_initialized", new_callable=AsyncMock) as mock_initialize,
            patch("openjiuwen.harness.lsp.get_pending_lsp_diagnostics", side_effect=[diagnostics, []]),
        ):
            await rail.before_model_call(context)
            await rail.before_model_call(context)

        mock_initialize.assert_not_awaited()
        assert len(context.inputs.messages) == 1
        assert "undefined variable 'value'" in context.inputs.messages[0].content

    @pytest.mark.asyncio
    async def test_concurrent_initialization_runs_once(self):
        from openjiuwen.harness.lsp.core.manager import LSPServerManager

        rail = LspRail()
        rail._initialize_options = InitializeOptions(cwd="/project")
        manager_state = {"instance": None}
        initialized_manager = MagicMock()
        initialization_started = asyncio.Event()
        allow_initialization_to_finish = asyncio.Event()

        async def initialize_lsp(_options):
            initialization_started.set()
            await allow_initialization_to_finish.wait()
            manager_state["instance"] = initialized_manager

        with (
            patch.object(
                LSPServerManager,
                "get_instance",
                side_effect=lambda: manager_state["instance"],
            ),
            patch.object(rail, "_async_init_lsp", side_effect=initialize_lsp) as mock_init,
        ):
            first = asyncio.create_task(rail._ensure_lsp_initialized())
            await initialization_started.wait()
            second = asyncio.create_task(rail._ensure_lsp_initialized())
            await asyncio.sleep(0)
            allow_initialization_to_finish.set()
            await asyncio.gather(first, second)

        mock_init.assert_awaited_once_with(InitializeOptions(cwd="/project"))


# ===========================================================================
# 4. uninit() — 清理
# ===========================================================================


class TestLspRailUninit:
    def _init_rail(self, rail, agent):
        with _patch_init_deps(agent) as (_, _, mock_tool):
            rail.init(agent)
        return mock_tool

    def test_removes_tool_from_ability_manager(self):
        rail = LspRail()
        agent = _make_agent()
        mock_tool = self._init_rail(rail, agent)
        with _patch_uninit_deps():
            rail.uninit(agent)
        agent.ability_manager.remove_ability.assert_called_once_with(mock_tool.card.name)

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
        with (
            patch("openjiuwen.harness.deep_agent.DeepAgent", _FakeDeepAgent),
            patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        ):
            rail.uninit(agent)  # should not raise

    @pytest.mark.asyncio
    async def test_uninit_schedules_shutdown_on_running_event_loop(self):
        rail = LspRail()
        agent = _make_agent()
        mock_tool = self._init_rail(rail, agent)

        with _patch_uninit_deps() as (_, mock_shutdown):
            rail.uninit(agent)
            assert rail._shutdown_task is not None
            await rail._shutdown_task

        mock_shutdown.assert_awaited_once()
        agent.ability_manager.remove_ability.assert_called_once_with(mock_tool.card.name)


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
    def test_before_model_call_registered(self):
        """before_model_call 用于注入诊断，应注册"""
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

        callbacks = LspRail().get_callbacks()
        assert AgentCallbackEvent.BEFORE_MODEL_CALL in callbacks

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
        assert AgentCallbackEvent.ON_MODEL_EXCEPTION not in callbacks

    def test_after_tool_call_registered(self):
        """after_tool_call 钩子应注册（LspRail 自动诊断依赖此钩子）"""
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

        callbacks = LspRail().get_callbacks()
        assert AgentCallbackEvent.AFTER_TOOL_CALL in callbacks
