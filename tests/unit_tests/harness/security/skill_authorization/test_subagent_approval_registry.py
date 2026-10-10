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
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    SubagentApprovalRequest,
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
