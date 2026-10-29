# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the cancel-path bug fixes.

Covers:
- CompiledGraph._invoke reports a cancellation with exception=None so the
  checkpointer clears the session and the same session can run again
  (reporting it as an exception would keep the snapshot and make the next
  non-interactive invoke fail at pre_workflow_execute).
- Vertex.stream_call, when cancelled, sets _stream_done to an error (not True) and
  Vertex.call() raises instead of marking the node "success".
"""
import asyncio
import pytest

# Break the vertex -> workflow -> graph -> vertex circular import at collection time.
import openjiuwen.core.multi_agent.team_runtime.message_bus  # noqa: F401

from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.graph.executable import Executable
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.session.checkpointer.inmemory import (
    InMemoryCheckpointer,
    WorkflowStorage,
)
from openjiuwen.core.graph.graph import CompiledGraph


class FakeState:
    def get_state(self, copied=False):
        return {"snapshot": "keep-me"}

    def get_updates(self):
        return {}

    def set_updates(self, u):
        pass

    def commit(self):
        pass


def make_session(sid, wid):
    class S:
        def session_id(self):
            return sid

        def workflow_id(self):
            return wid

        def parent(self):
            return None

        def config(self):
            return None

        def state(self):
            return FakeState()

    return S


@pytest.mark.asyncio
async def test_checkpointer_clear_and_keep_branches():
    """锁定 checkpointer 两条分支的语义，作为取消路径决策的依据。

    - exception=None（正常结束，取消即走这里）→ 清理会话，可再次执行；
    - exception 非空 → 保存快照且不清理，但会让同 session 的后续
      非 InteractiveInput 调用在 pre 阶段被拒。
    正因两者的差异，取消路径选择上报 None 而不是 CancelledError。
    """
    cp = InMemoryCheckpointer()
    sid, wid = "sid-1", "wid-1"

    async def post(result, exc):
        cp._workflow_stores[sid] = WorkflowStorage()
        cp._session_to_workflow_ids[sid] = set()
        await cp.post_workflow_execute(make_session(sid, wid)(), result, exc)

    # 正常结束 / 取消：清理
    await post({}, None)
    assert sid not in cp._workflow_stores

    # 异常终止：保留快照（但不清理，导致该会话无法直接重跑）
    cp._workflow_stores[sid] = WorkflowStorage()
    cp._session_to_workflow_ids[sid] = set()
    raised = None
    try:
        await cp.post_workflow_execute(make_session(sid, wid)(), {}, asyncio.CancelledError())
    except asyncio.CancelledError as e:
        raised = e
    assert raised is not None
    assert sid in cp._workflow_stores


class FakeExecutable(Executable):
    def skip_trace(self):
        return False

    def graph_invoker(self):
        return False

    def component_type(self):
        return ""


class Recorder:
    def __init__(self):
        self.calls = []

    def mark_producer_done(self, node_id):
        self.calls.append(node_id)


class FakeSession:
    def __init__(self, recorder):
        self._recorder = recorder

    def tracer(self):
        return None

    def actor_manager(self):
        return self._recorder

    def session_id(self):
        return "sid-1"

    def workflow_id(self):
        return "wid-1"

    def parent(self):
        return None


@pytest.mark.asyncio
async def test_vertex_cancel_not_marked_success():
    v = Vertex("n1")
    recorder = Recorder()
    v._executable = FakeExecutable()
    v._session = FakeSession(recorder)
    v._has_stream_call = True
    v.is_end_node = False
    v._component_ability = [ComponentAbility.COLLECT]
    v._has_call = len(v._component_ability) > len(v._stream_abilities())
    v._node_config = None
    v._call_count = 0

    async def fake_run(self, ability, is_subgraph=False, config=None, event=None):
        await asyncio.sleep(10)

    v._run_executable_with_retry = fake_run.__get__(v, Vertex)

    event = asyncio.Event()
    error_calls = []
    task = asyncio.create_task(v.stream_call(event, error_calls.append))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = v._stream_done.result()
    assert res is not True

    try:
        await v.call()
        raise AssertionError("call() should have raised")
    except Exception:
        assert recorder.calls == [], "mark_producer_done called despite cancellation"


class FakePregel:
    async def run(self, config=None):
        raise asyncio.CancelledError()


class FakeCp:
    def __init__(self):
        self.calls = []

    async def pre_workflow_execute(self, session, inputs):
        pass

    async def post_workflow_execute(self, session, result, exception):
        self.calls.append((result, exception))


class S2:
    def session_id(self):
        return "sid-1"

    def workflow_id(self):
        return "wid-1"

    def state(self):
        class St:
            def commit_user_inputs(self, i):
                pass

        return St()


@pytest.mark.asyncio
async def test_graph_cancel_clears_state_for_rerun():
    """取消时按“正常结束”上报，使会话可再次执行。

    反例（曾经的实现）：把 CancelledError 作为 exception 传给
    post_workflow_execute 以保留快照。checkpointer 的异常分支只保存不清理，
    随后同 (session_id, workflow_id) 的非 InteractiveInput 调用会在
    pre_workflow_execute 因“状态已存在且未开强制清理”报错 111121，
    用户点停止后就再也无法重跑该会话。
    """
    cp = FakeCp()
    g = CompiledGraph(FakePregel(), cp)
    try:
        await g._invoke({}, S2(), None)
    except asyncio.CancelledError:
        pass
    assert cp.calls, "post_workflow_execute was never called"
    result, exception = cp.calls[-1]
    assert exception is None, "取消应以正常结束上报（exception=None），否则会话无法重跑"
    assert result == {}
