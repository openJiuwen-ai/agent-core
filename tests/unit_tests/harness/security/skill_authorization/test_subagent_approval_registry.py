# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SubagentApprovalRegistry 并发语义回归（2026-09-28 审批挂死事故族）。

事故形态：子代理每个工具调用都要审批，用户连点 20 次「允许」——同 scope
并发请求被 CapacityError 硬丢弃 ×5；最后一次 resolve 返回 True 后等待方
死等（卡在无界的 sender 收尾/锁获取），最终被外层 2h 硬超时杀掉。

风格参照 test_interrupt_resume.py：真实 asyncio、Event 控同步、
wait_for 做断言上限。每用例注入全新实例（_session_locks 绑定首个请求者
的 loop，跨用例复用会残留旧 loop 的锁）。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from openjiuwen.harness.security.skill_authorization.subagent_approval_registry import (
    SubagentApprovalCancelled,
    SubagentApprovalCapacityError,
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    SubagentApprovalRequest,
    SubagentApprovalTimeout,
)

_KIND = SubagentApprovalKind.TOOL_PERMISSION


@pytest.fixture(autouse=True)
def fresh_registry():
    SubagentApprovalRegistry.reset_instance_for_tests()
    registry = SubagentApprovalRegistry()
    SubagentApprovalRegistry._instance = registry
    yield registry
    SubagentApprovalRegistry.reset_instance_for_tests()


def _request(approval_id: str, scope: str = "scope-1") -> SubagentApprovalRequest:
    return SubagentApprovalRequest(
        approval_id=approval_id,
        kind=_KIND,
        session_id="session-1",
        agent_scope_id=scope,
        tool_call_id=f"call-{approval_id}",
        payload={},
    )


def test_complete_future_warns_when_future_already_done(caplog):
    """resolve/cancel 落空（future 已终态）必须留下可归因的 warning。"""
    loop = asyncio.new_event_loop()
    try:
        for prepared in ("cancelled", "resolved"):
            future = loop.create_future()
            if prepared == "cancelled":
                future.cancel()
            else:
                future.set_result("early")
            with caplog.at_level(
                logging.WARNING, logger="openjiuwen.harness.security.skill_authorization.subagent_approval_registry"
            ):
                SubagentApprovalRegistry._complete_future(
                    future,
                    "answer",
                    None,
                    request=_request(f"approval-{prepared}"),
                    via="resolve",
                )
            assert future.done()
            dropped = [r for r in caplog.records if "approval_completion_dropped" in r.message]
            assert dropped, f"{prepared}: completion_dropped warning expected"
            message = dropped[-1].getMessage()
            assert f"approval-{prepared}" in message
            assert "scope-1" in message
            assert "tool_permission" in message
            assert "via=resolve" in message
    finally:
        loop.close()


def test_complete_future_still_sets_pending_future():
    """正常路径（future 未终态）行为不变。"""
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        SubagentApprovalRegistry._complete_future(
            future, "answer", None, request=_request("approval-live"), via="resolve"
        )
        assert future.result() == "answer"
    finally:
        loop.close()


async def _wait_for_pending(registry, count: int = 1, timeout: float = 2.0) -> None:
    """轮询直到 registry 里有足够 pending（模拟卡片下发后外部可 resolve）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while len(registry.pending_requests()) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"expected {count} pending approvals, got {registry.pending_requests()}")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_sender_drain_timeout_does_not_block_settlement(fresh_registry, monkeypatch, caplog):
    """sender 吞掉 CancelledError 死循环时，收尾超时即放弃，请求仍能结算。

    2026-09-28 事故机制 A：resolve 后等待方卡死在无界的 sender 收尾 gather
    （持 session 交互锁）。收尾有界后，请求必须在短时间内返回答案。
    """
    from openjiuwen.harness.security.skill_authorization import subagent_approval_registry as mod

    monkeypatch.setattr(mod, "_SENDER_DRAIN_TIMEOUT_SECONDS", 0.2)
    sender_started = asyncio.Event()
    # 可停止的吞取消轮询：测试收尾时置 stop 让 sender 退出，否则它会连
    # 事件循环关闭时的最终取消也吞掉，把整个 pytest 进程挂死。
    stop = asyncio.Event()
    sender_tasks: list[asyncio.Task] = []

    async def swallow_cancel_sender(_request):
        sender_tasks.append(asyncio.current_task())
        sender_started.set()
        while not stop.is_set():
            try:
                await asyncio.wait_for(asyncio.Event().wait(), 0.05)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                continue

    request_task = asyncio.create_task(
        fresh_registry.request(
            kind=_KIND,
            session_id="session-1",
            agent_scope_id="scope-1",
            tool_call_id="call-1",
            payload={},
            sender=swallow_cancel_sender,
            timeout=5.0,
        )
    )
    await asyncio.wait_for(sender_started.wait(), timeout=2)
    await _wait_for_pending(fresh_registry)
    approval_id = fresh_registry.pending_requests()[0].approval_id

    assert fresh_registry.resolve(
        session_id="session-1",
        approval_id=approval_id,
        kind=_KIND,
        answer={"approved": True},
    )

    try:
        answer = await asyncio.wait_for(request_task, timeout=2)
        assert answer == {"approved": True}
        abandoned = [r for r in caplog.records if "approval_sender_drain_abandoned" in r.message]
        assert abandoned, "expected sender drain abandonment warning"
    finally:
        stop.set()
        for task in sender_tasks:
            task.cancel()
        await asyncio.gather(*sender_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_sender_drain_completes_quickly_when_cooperative(fresh_registry):
    """正常协作的 sender（被取消即退出）路径不受影响——收尾立即完成。"""
    sender_started = asyncio.Event()

    async def cooperative_sender(_request):
        sender_started.set()
        await asyncio.Event().wait()

    request_task = asyncio.create_task(
        fresh_registry.request(
            kind=_KIND,
            session_id="session-1",
            agent_scope_id="scope-1",
            tool_call_id="call-1",
            payload={},
            sender=cooperative_sender,
            timeout=5.0,
        )
    )
    await asyncio.wait_for(sender_started.wait(), timeout=2)
    await _wait_for_pending(fresh_registry)
    approval_id = fresh_registry.pending_requests()[0].approval_id

    assert fresh_registry.resolve(session_id="session-1", approval_id=approval_id, kind=_KIND, answer="yes")
    assert await asyncio.wait_for(request_task, timeout=1) == "yes"


def _make_request_task(registry, call_id, *, sender, timeout=5.0, scope="scope-1"):
    return asyncio.create_task(
        registry.request(
            kind=_KIND,
            session_id="session-1",
            agent_scope_id=scope,
            tool_call_id=call_id,
            payload={},
            sender=sender,
            timeout=timeout,
        )
    )


@pytest.mark.asyncio
async def test_turn_wait_timeout_raises_approval_timeout(fresh_registry):
    """前驱持锁超过 turn 上限时，排队者抛 SubagentApprovalTimeout 且不留痕。"""
    card_shown = asyncio.Event()

    async def hanging_sender(_request):
        card_shown.set()
        await asyncio.Event().wait()  # 卡片"已展示"，sender 立即让位

    # A：长 TTL 持锁等待应答。
    task_a = _make_request_task(fresh_registry, "call-a", sender=hanging_sender, timeout=5.0)
    await asyncio.wait_for(card_shown.wait(), timeout=2)

    # B：同 session 不同 scope（当前实现同 scope 并发仍硬拒，后续提交改排队），
    # 排队等同一把 session 锁，turn 上限 0.3s。
    fresh_registry._turn_wait_timeout = 0.3
    task_b = _make_request_task(fresh_registry, "call-b", sender=hanging_sender, timeout=5.0, scope="scope-2")

    with pytest.raises(SubagentApprovalTimeout):
        await asyncio.wait_for(task_b, timeout=2)
    assert "turn wait timed out" in str(task_b.exception())
    # B 已摘除：迟到 resolve 返回 False。
    b_ids = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-b"]
    assert b_ids == [], "timed-out queued request must be discarded"

    # A 不受影响，正常 resolve。
    a_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-a"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=a_id, kind=_KIND, answer="yes")
    assert await asyncio.wait_for(task_a, timeout=2) == "yes"


@pytest.mark.asyncio
async def test_ttl_discards_before_late_click_returns_false(fresh_registry):
    """TTL 超时先摘除：迟到点击 resolve 返回 False（既有语义防回归）。"""

    async def quick_sender(_request):
        return None

    task = _make_request_task(fresh_registry, "call-a", sender=quick_sender, timeout=0.1)
    await _wait_for_pending(fresh_registry)
    approval_id = fresh_registry.pending_requests()[0].approval_id

    with pytest.raises(SubagentApprovalTimeout):
        await asyncio.wait_for(task, timeout=2)
    assert fresh_registry.resolve(session_id="session-1", approval_id=approval_id, kind=_KIND, answer="late") is False


def test_resolve_returns_false_for_unknown_or_mismatched(fresh_registry):
    """未知 id / kind 不匹配 / scope 不匹配均返回 False（既有语义防回归）。"""
    assert (
        fresh_registry.resolve(
            session_id="session-1",
            approval_id="subagent_tool_permission_nope",
            kind=_KIND,
            answer="x",
        )
        is False
    )


@pytest.mark.asyncio
async def test_queued_request_resolved_before_turn_returns_answer(fresh_registry):
    """排队期间被直接 resolve：立即返回答案，不等锁；锁无泄漏。"""
    card_shown = asyncio.Event()

    async def hanging_sender(_request):
        card_shown.set()
        await asyncio.Event().wait()

    task_a = _make_request_task(fresh_registry, "call-a", sender=hanging_sender, timeout=5.0)
    await asyncio.wait_for(card_shown.wait(), timeout=2)

    task_b = _make_request_task(fresh_registry, "call-b", sender=hanging_sender, timeout=5.0)
    await _wait_for_pending(fresh_registry, count=2)
    b_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-b"][0]

    assert fresh_registry.resolve(session_id="session-1", approval_id=b_id, kind=_KIND, answer="b-answer")
    # B 立即返回（A 仍持锁），不等 turn。
    assert await asyncio.wait_for(task_b, timeout=1) == "b-answer"

    # A settle 后锁正常释放：第三个请求能正常进入并完成。
    a_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-a"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=a_id, kind=_KIND, answer="a-answer")
    assert await asyncio.wait_for(task_a, timeout=1) == "a-answer"

    task_c = _make_request_task(fresh_registry, "call-c", sender=hanging_sender, timeout=5.0)
    await asyncio.wait_for(card_shown.wait(), timeout=2)
    c_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-c"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=c_id, kind=_KIND, answer="c-answer")
    assert await asyncio.wait_for(task_c, timeout=1) == "c-answer"


@pytest.mark.asyncio
async def test_same_scope_requests_queue_and_deliver_in_order(fresh_registry):
    """同 scope 并发请求排队（FIFO），逐卡顺序投递，互不丢弃。"""
    send_order: list[str] = []
    gate = asyncio.Event()

    async def gated_sender(request):
        send_order.append(request.tool_call_id)
        await gate.wait()

    task_a = _make_request_task(fresh_registry, "call-a", sender=gated_sender, timeout=5.0)
    await _wait_for_pending(fresh_registry)
    task_b = _make_request_task(fresh_registry, "call-b", sender=gated_sender, timeout=5.0)
    await _wait_for_pending(fresh_registry, count=2)
    await asyncio.sleep(0.1)
    assert send_order == ["call-a"], "B must not display a card while A holds the session lock"

    # A 的 sender 完成（放行 gate）→ A 进入 TTL 等待 → resolve A → B 接卡。
    gate.set()
    a_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-a"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=a_id, kind=_KIND, answer="a-yes")
    assert await asyncio.wait_for(task_a, timeout=2) == "a-yes"

    await asyncio.wait_for(_wait_for_sender(send_order, "call-b"), timeout=2)
    b_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-b"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=b_id, kind=_KIND, answer="b-yes")
    assert await asyncio.wait_for(task_b, timeout=2) == "b-yes"
    assert send_order == ["call-a", "call-b"]


async def _wait_for_sender(send_order, call_id, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while call_id not in send_order:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"sender for {call_id} never ran; order={send_order}")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_scope_queue_is_bounded(fresh_registry):
    """同 scope 注册数超过 1 + max_queued_per_scope 时才拒绝。"""

    async def hanging_sender(_request):
        await asyncio.Event().wait()

    fresh_registry._max_queued_per_scope = 1
    task_a = _make_request_task(fresh_registry, "call-a", sender=hanging_sender, timeout=30.0)
    await _wait_for_pending(fresh_registry)
    task_b = _make_request_task(fresh_registry, "call-b", sender=hanging_sender, timeout=30.0)
    await _wait_for_pending(fresh_registry, count=2)

    with pytest.raises(SubagentApprovalCapacityError):
        await _make_request_task(fresh_registry, "call-c", sender=hanging_sender, timeout=30.0)

    # 已注册的两个不受影响：cancel 冲刷。
    assert fresh_registry.cancel_scope("session-1", "scope-1") == 2
    for task in (task_a, task_b):
        with pytest.raises(SubagentApprovalCancelled):
            await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_cancel_scope_flushes_active_and_queued(fresh_registry):
    """cancel_scope 冲刷持卡者与排队者；排队者的 sender 从未被调用。"""
    send_order: list[str] = []
    gate = asyncio.Event()

    async def gated_sender(request):
        send_order.append(request.tool_call_id)
        await gate.wait()

    task_a = _make_request_task(fresh_registry, "call-a", sender=gated_sender, timeout=30.0)
    await _wait_for_pending(fresh_registry)
    task_b = _make_request_task(fresh_registry, "call-b", sender=gated_sender, timeout=30.0)
    await _wait_for_pending(fresh_registry, count=2)

    assert fresh_registry.cancel_scope("session-1", "scope-1") == 2
    for task in (task_a, task_b):
        with pytest.raises(SubagentApprovalCancelled):
            await asyncio.wait_for(task, timeout=2)
    assert send_order == ["call-a"], "queued request's sender must never run"
    assert fresh_registry.pending_requests() == ()


@pytest.mark.asyncio
async def test_incident_shape_six_concurrent_approvals_all_register(fresh_registry):
    """09-28 形态：单 session 单 scope 6 并发审批，默认上限下全部注册（不再硬拒）。

    检视意见 2026-10-12 ①：旧默认 max_pending_per_session=5 会把单 session
    有效并发钳到 5，6 并发仍丢 1 张；默认提到 20 后事故形态不再触发。
    """
    async def hanging_sender(_request):
        await asyncio.Event().wait()

    tasks = [
        _make_request_task(fresh_registry, f"call-{i}", sender=hanging_sender, timeout=30.0) for i in range(6)
    ]
    try:
        await _wait_for_pending(fresh_registry, count=6)
        assert len(fresh_registry.pending_requests()) == 6
    finally:
        assert fresh_registry.cancel_scope("session-1", "scope-1") == 6
        for task in tasks:
            with pytest.raises(SubagentApprovalCancelled):
                await asyncio.wait_for(task, timeout=2)


def test_session_cap_below_scope_depth_warns(caplog):
    """session 上限 ≤ scope 队列深度时告警（单 scope 队列不可达）。"""
    with caplog.at_level(
        logging.WARNING,
        logger="openjiuwen.harness.security.skill_authorization.subagent_approval_registry",
    ):
        SubagentApprovalRegistry(max_pending_per_session=5, max_queued_per_scope=8)
    assert any("unreachable" in record.getMessage() for record in caplog.records)


def test_resolve_turn_timeout_scales_with_queue_depth(fresh_registry):
    """turn 等待上限：自动模式与排队深度挂钩；显式配置优先。"""
    from openjiuwen.harness.security.skill_authorization import subagent_approval_registry as mod

    slack = mod._TURN_WAIT_SLACK_SECONDS
    assert fresh_registry._resolve_turn_timeout(120.0, 0) == 120.0 + slack
    assert fresh_registry._resolve_turn_timeout(120.0, 3) == 120.0 * 4 + slack
    fresh_registry._turn_wait_timeout = 7.0
    assert fresh_registry._resolve_turn_timeout(120.0, 3) == 7.0


@pytest.mark.asyncio
async def test_deep_queue_gets_card_within_scaled_turn_wait(fresh_registry, monkeypatch):
    """排队深度 3：靠后请求按深度自适应等待并拿到卡（固定 2×TTL 会先撞 turn 超时）。

    检视意见 2026-10-12 ③：前驱各自吃满 TTL 才释放锁，固定倍数（2×TTL）在
    深度 ≥3 时让靠后排队者在拿到卡前就被 turn 超时拒绝。
    """
    from openjiuwen.harness.security.skill_authorization import subagent_approval_registry as mod

    monkeypatch.setattr(mod, "_TURN_WAIT_SLACK_SECONDS", 0.5)
    send_order: list[str] = []

    async def quick_sender(request):
        send_order.append(request.tool_call_id)

    ttl = 0.4
    task_a = _make_request_task(fresh_registry, "call-a", sender=quick_sender, timeout=ttl)
    await _wait_for_pending(fresh_registry)
    task_b = _make_request_task(fresh_registry, "call-b", sender=quick_sender, timeout=ttl)
    task_c = _make_request_task(fresh_registry, "call-c", sender=quick_sender, timeout=ttl)
    await _wait_for_pending(fresh_registry, count=3)

    # A 吃满 TTL(~0.4s) 超时 → B 接卡吃满 TTL(~0.8s) → C 接卡；
    # C 的 turn 上限 = 0.4×3 + 0.5 = 1.7s（固定 2×TTL=0.8s 会在接卡瞬间超时）。
    await asyncio.wait_for(_wait_for_sender(send_order, "call-c"), timeout=3)
    c_id = [r.approval_id for r in fresh_registry.pending_requests() if r.tool_call_id == "call-c"][0]
    assert fresh_registry.resolve(session_id="session-1", approval_id=c_id, kind=_KIND, answer="c-yes")
    assert await asyncio.wait_for(task_c, timeout=2) == "c-yes"

    for task in (task_a, task_b):
        with pytest.raises(SubagentApprovalTimeout):
            await asyncio.wait_for(task, timeout=2)
