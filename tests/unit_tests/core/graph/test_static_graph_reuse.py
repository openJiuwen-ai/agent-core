# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""静态图实例复用相关单元测试。

覆盖：
1. execution_session_ctx ContextVar 通道的基本功能与解析链
2. PregelGraph.compile 纯函数化（fresh vertex、每次新建 pregel）
3. BranchRouter / BranchComponent 的 session 解析链
4. 循环组件执行期状态会话化（break 标志、轮次计数的并发隔离）
"""
from unittest.mock import MagicMock

import pytest

# 注意：必须先导入 workflow 包，再导入 graph 模块，
# 否则会触发 graph ↔ vertex ↔ workflow.components.base ↔ workflow 的循环导入。
import openjiuwen.core.workflow  # noqa: F401
from openjiuwen.core.common.constants.constant import FINISH_INDEX
from openjiuwen.core.graph.graph import PregelGraph
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.execution_session_ctx import (
    _component_exec,
    _workflow_session,
    current_component_exec,
    current_workflow_session,
    reset_component_exec,
    reset_workflow_session,
    resolve_routing_session,
    set_component_exec,
    set_workflow_session,
)
from openjiuwen.core.workflow import BranchComponent, BranchRouter
from openjiuwen.core.workflow.components.flow.loop.loop_comp import (
    _LOOP_EXEC_SESSION,
    _resolve_loop_session,
    AdvancedLoopComponent,
    PostLoopBody,
)
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow.components import Session


# ============================================================================
# Fixtures：确保 ContextVar 测试隔离
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_contextvars():
    """每个测试前后重置所有相关 ContextVar，防止跨测试泄漏。"""
    wf_token = _workflow_session.set(None)
    comp_token = _component_exec.set((None, None, None))
    loop_token = _LOOP_EXEC_SESSION.set(None)
    yield
    _workflow_session.reset(wf_token)
    _component_exec.reset(comp_token)
    _LOOP_EXEC_SESSION.reset(loop_token)


# ============================================================================
# 1. execution_session_ctx ContextVar 通道
# ============================================================================

class TestExecutionSessionCtx:
    """execution_session_ctx 模块的 ContextVar 通道功能测试。"""

    def test_workflow_session_set_reset(self):
        """workflow 级 session：set 后可读，reset 后恢复原值。"""
        assert current_workflow_session() is None

        mock_session = MagicMock()
        token = set_workflow_session(mock_session)
        assert current_workflow_session() is mock_session

        reset_workflow_session(token)
        assert current_workflow_session() is None

    def test_workflow_session_nested_reset(self):
        """嵌套 set/reset：子层 reset 后恢复父层值（栈式）。"""
        parent = MagicMock(name="parent")
        child = MagicMock(name="child")

        token_parent = set_workflow_session(parent)
        assert current_workflow_session() is parent

        token_child = set_workflow_session(child)
        assert current_workflow_session() is child

        reset_workflow_session(token_child)
        assert current_workflow_session() is parent

        reset_workflow_session(token_parent)
        assert current_workflow_session() is None

    def test_component_exec_set_reset(self):
        """组件级执行上下文：set 后可读 (session, context, owner)，reset 后恢复。"""
        assert current_component_exec() == (None, None, None)

        mock_session = MagicMock()
        mock_context = MagicMock()
        token = set_component_exec(mock_session, mock_context)

        session, context, owner = current_component_exec()
        assert session is mock_session
        assert context is mock_context
        assert owner is None

        reset_component_exec(token)
        assert current_component_exec() == (None, None, None)

    def test_resolve_routing_session_priority_chain(self):
        """路由 session 解析链：组件级 > workflow 级 > fallback。"""
        # 全部为空 → 返回 fallback
        fallback = MagicMock(name="fallback")
        assert resolve_routing_session(fallback) is fallback
        assert resolve_routing_session(None) is None

        class WrappedSession:
            def __init__(self, inner):
                self._inner = inner

        # 仅 workflow 级有值（无 _inner 的普通 session）→ 原样返回。
        # spec=BaseSession：BaseSession 无 _inner，getattr 走默认分支——
        # 裸 MagicMock 会自动伪造 _inner 属性，违反 session 值契约。
        wf_session = MagicMock(name="wf_session", spec=BaseSession)
        token_wf = set_workflow_session(wf_session)
        try:
            assert resolve_routing_session(fallback) is wf_session
        finally:
            reset_workflow_session(token_wf)

        # workflow 级为只读视图（带 _inner，如 checkpoint 作用域视图）→ 解包还原
        raw_session = MagicMock(name="raw_session")
        token_view = set_workflow_session(WrappedSession(raw_session))
        try:
            assert resolve_routing_session(fallback) is raw_session
        finally:
            reset_workflow_session(token_view)

        # 组件级有值（带 _inner 包装器）→ 解包后返回
        inner_session = MagicMock(name="inner")
        wrapped = WrappedSession(inner_session)
        token_comp = set_component_exec(wrapped, MagicMock())
        try:
            assert resolve_routing_session(fallback) is inner_session
        finally:
            reset_component_exec(token_comp)

        # 组件级有值（无 _inner，如 NodeSession）→ 原样返回
        class PlainSession:
            pass

        plain_session = PlainSession()
        token_comp2 = set_component_exec(plain_session, MagicMock())
        try:
            assert resolve_routing_session(fallback) is plain_session
        finally:
            reset_component_exec(token_comp2)

    def test_resolve_routing_session_component_overrides_workflow(self):
        """组件级优先级高于 workflow 级。"""
        wf_session = MagicMock(name="wf")

        class PlainSession:
            pass

        comp_session = PlainSession()

        token_wf = set_workflow_session(wf_session)
        token_comp = set_component_exec(comp_session, MagicMock())
        try:
            assert resolve_routing_session(None) is comp_session
        finally:
            reset_component_exec(token_comp)
            reset_workflow_session(token_wf)


# ============================================================================
# 2. PregelGraph.compile 纯函数化
# ============================================================================

class _MockExecutable:
    """最小可执行对象，用于构建 PregelGraph 节点。"""

    def __init__(self, name="mock"):
        self.name = name

    def skip_trace(self):
        return True

    def graph_invoker(self):
        return False

    async def on_invoke(self, inputs, session=None, context=None, **kwargs):
        return inputs


def _build_simple_graph() -> PregelGraph:
    """构建一个含两个节点一条边的简单 PregelGraph。"""
    graph = PregelGraph()
    graph.add_node("node_a", _MockExecutable("a"))
    graph.add_node("node_b", _MockExecutable("b"))
    graph.add_edge("node_a", "node_b")
    return graph


def _make_fake_session(session_id="test-session", workflow_id="test-wf"):
    """构造 compile 所需的最小 fake session。"""
    session = MagicMock()
    session.session_id.return_value = session_id
    session.workflow_id.return_value = workflow_id
    session.config.return_value.get_env.return_value = 10

    checkpointer = MagicMock()
    checkpointer.graph_store.return_value = {}
    session.checkpointer.return_value = checkpointer

    state = MagicMock()
    session.state.return_value = state

    session.actor_manager.side_effect = AttributeError("no actor manager")

    return session


class TestPregelGraphCompilePure:
    """PregelGraph.compile 纯函数化测试。"""

    def test_compile_creates_fresh_vertex_each_time(self):
        """每次 compile 为每个节点新建 fresh Vertex，不复用 build 期 Vertex。"""
        graph = _build_simple_graph()
        session = _make_fake_session()

        compiled1 = graph.compile(session)
        compiled2 = graph.compile(session)

        assert compiled1._pregel is not compiled2._pregel

        for node_id, vertex in graph.nodes.items():
            assert vertex._session is None, f"build期 Vertex[{node_id}] 不应被 init"

    def test_compile_fresh_vertex_wraps_shared_executable(self):
        """fresh Vertex 包装的是共享的 executable 实例（不复制组件）。"""
        graph = _build_simple_graph()
        session = _make_fake_session()

        compiled = graph.compile(session)

        for node_id, build_vertex in graph.nodes.items():
            pregel_node = compiled._pregel.nodes[node_id]
            fresh_vertex = pregel_node.func
            assert fresh_vertex is not build_vertex
            assert fresh_vertex._executable is build_vertex._executable
            assert fresh_vertex._session is not None

    def test_compile_always_rebuilds_pregel(self):
        """compile 不再判断 self.pregel is None，每次都全新构建。"""
        graph = _build_simple_graph()
        session = _make_fake_session()

        compiled1 = graph.compile(session)
        compiled2 = graph.compile(session)

        assert compiled1._pregel is not compiled2._pregel

    def test_after_step_captures_session_not_shared_attr(self):
        """after_step 闭包捕获 compile 时传入的 session，不读 self._session。"""
        graph = _build_simple_graph()
        s1 = _make_fake_session("s1")
        s2 = _make_fake_session("s2")

        c1 = graph.compile(s1)
        graph.compile(s2)

        loop_stub = MagicMock()
        loop_stub.step = 1
        loop_stub.config = {"ns": "wf", "session_id": "s1"}
        loop_stub.active_nodes = []

        c1._pregel.after_step(loop_stub)
        s1.state().commit.assert_called()
        s2.state().commit.assert_not_called()

    def test_compile_preserves_is_end_node(self):
        """fresh Vertex 继承 build 期 Vertex 的 is_end_node 属性。"""
        graph = PregelGraph()
        graph.add_node("start", _MockExecutable("start"))
        graph.add_node("end", _MockExecutable("end"))
        graph.end_node("end")
        graph.add_edge("start", "end")

        session = _make_fake_session()
        compiled = graph.compile(session)

        assert compiled._pregel.nodes["end"].func.is_end_node is True
        assert compiled._pregel.nodes["start"].func.is_end_node is False


# ============================================================================
# 3. BranchRouter / BranchComponent session 解析链
# ============================================================================

class TestBranchRouterSessionResolution:
    """BranchRouter 的 session 解析链测试。"""

    @pytest.mark.asyncio
    async def test_branch_component_invoke_sets_component_exec(self):
        """BranchComponent.invoke 入口设置组件级执行上下文。"""
        comp = BranchComponent()
        comp.add_branch(condition="True", target="next")

        # mock Session 需要有 _inner 属性（set_session 会访问）
        mock_session = MagicMock(spec=Session)
        mock_session._inner = MagicMock()
        mock_context = MagicMock(spec=ModelContext)

        assert current_component_exec() == (None, None, None)

        await comp.invoke({}, mock_session, mock_context)

        session, context, owner = current_component_exec()
        assert session is mock_session
        assert context is mock_context
        assert owner is comp._router

    @pytest.mark.asyncio
    async def test_branch_router_resolves_session_via_contextvar(self):
        """BranchRouter.__call__ 通过 ContextVar 解析 session，不依赖 self._session。"""
        router = BranchRouter(report_trace=False)
        router.add_branch(condition="True", target=["next"])

        mock_session = MagicMock()
        mock_session.state.return_value = MagicMock()
        token = set_workflow_session(mock_session)

        try:
            assert router._session is None
            result = await router()
            assert result == ["next"]
        finally:
            reset_workflow_session(token)

    @pytest.mark.asyncio
    async def test_branch_router_fallback_to_self_session(self):
        """ContextVar 均为空时回退到 self._session。"""
        router = BranchRouter(report_trace=False)
        router.add_branch(condition="True", target=["next"])

        # 直接设置 _session 绕过 set_session 的类型检查
        fallback_session = MagicMock()
        fallback_session.state.return_value = MagicMock()
        router._session = fallback_session

        assert current_workflow_session() is None
        assert current_component_exec() == (None, None, None)

        result = await router()
        assert result == ["next"]


# ============================================================================
# 4. 循环组件执行期状态会话化
# ============================================================================

class _FakeNodeSession:
    """模拟 NodeSession，用于循环组件状态测试。"""

    def __init__(self):
        self._state = {}

    def state(self):
        return self

    def get(self, key):
        return self._state.get(key)

    def update(self, data):
        self._state.update(data)

    def commit(self):
        pass


class TestLoopExecutionSessionCtx:
    """循环组件执行期状态会话化测试。"""

    def test_resolve_loop_session_prefers_contextvar(self):
        """_resolve_loop_session 优先返回 ContextVar 中的 session。"""
        component = MagicMock()
        component._node_session = _FakeNodeSession()

        ctx_session = _FakeNodeSession()
        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            assert _resolve_loop_session(component) is ctx_session
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_resolve_loop_session_falls_back_to_instance_field(self):
        """ContextVar 为空时回退到组件实例的 _node_session。"""
        component = MagicMock()
        instance_session = _FakeNodeSession()
        component._node_session = instance_session

        assert _LOOP_EXEC_SESSION.get() is None
        assert _resolve_loop_session(component) is instance_session

    @pytest.mark.asyncio
    async def test_post_loop_body_uses_contextvar_session(self):
        """PostLoopBody.on_invoke 将轮次计数写入 ContextVar 中的 session。"""
        post_body = PostLoopBody()
        ctx_session = _FakeNodeSession()

        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            await post_body.on_invoke({}, MagicMock())
            assert ctx_session.get(FINISH_INDEX) == 0
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_post_loop_body_get_finish_index_from_contextvar(self):
        """PostLoopBody.get_finish_index 从 ContextVar session 读取。"""
        post_body = PostLoopBody()
        ctx_session = _FakeNodeSession()
        ctx_session.update({FINISH_INDEX: 5})

        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            assert post_body.get_finish_index() == 5
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_post_loop_body_set_finish_index_to_contextvar(self):
        """PostLoopBody.set_finish_index 写入 ContextVar session。"""
        post_body = PostLoopBody()
        ctx_session = _FakeNodeSession()

        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            post_body.set_finish_index(3)
            assert ctx_session.get(FINISH_INDEX) == 3
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_post_loop_body_fallback_to_instance_field(self):
        """ContextVar 为空时，PostLoopBody 回退到实例字段 _finish_index。"""
        post_body = PostLoopBody()
        post_body._finish_index = 7

        assert _LOOP_EXEC_SESSION.get() is None
        assert post_body.get_finish_index() == 7

        post_body.set_finish_index(9)
        assert post_body._finish_index == 9

    def test_advanced_loop_is_broken_uses_contextvar(self):
        """AdvancedLoopComponent.is_broken 从 ContextVar session 读取 break 标志。"""
        component = AdvancedLoopComponent(
            body=_MockExecutable("body"),
            condition="True",
        )
        ctx_session = _FakeNodeSession()
        ctx_session.update({"_broken": True})

        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            assert component.is_broken() is True
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_advanced_loop_break_loop_writes_contextvar(self):
        """AdvancedLoopComponent.break_loop 写入 ContextVar session 并 commit。"""
        component = AdvancedLoopComponent(
            body=_MockExecutable("body"),
            condition="True",
        )
        ctx_session = _FakeNodeSession()
        commit_spy = MagicMock(wraps=ctx_session.commit)
        ctx_session.commit = commit_spy

        token = _LOOP_EXEC_SESSION.set(ctx_session)
        try:
            component.break_loop()
            assert ctx_session.get("_broken") is True
            commit_spy.assert_called_once()
        finally:
            _LOOP_EXEC_SESSION.reset(token)

    def test_advanced_loop_is_broken_no_session_returns_false(self):
        """无执行期 session 时 is_broken 返回 False（不抛异常）。"""
        component = AdvancedLoopComponent(
            body=_MockExecutable("body"),
            condition="True",
        )
        component._node_session = None

        assert _LOOP_EXEC_SESSION.get() is None
        assert component.is_broken() is False

    def test_loop_concurrent_break_isolation(self):
        """并发隔离模拟：两个会话的 break 标志互不干扰。"""
        component = AdvancedLoopComponent(
            body=_MockExecutable("body"),
            condition="True",
        )

        session_a = _FakeNodeSession()
        session_b = _FakeNodeSession()

        token_a = _LOOP_EXEC_SESSION.set(session_a)
        try:
            component.break_loop()
            assert component.is_broken() is True
        finally:
            _LOOP_EXEC_SESSION.reset(token_a)

        token_b = _LOOP_EXEC_SESSION.set(session_b)
        try:
            assert component.is_broken() is False
        finally:
            _LOOP_EXEC_SESSION.reset(token_b)

        token_a2 = _LOOP_EXEC_SESSION.set(session_a)
        try:
            assert component.is_broken() is True
        finally:
            _LOOP_EXEC_SESSION.reset(token_a2)
