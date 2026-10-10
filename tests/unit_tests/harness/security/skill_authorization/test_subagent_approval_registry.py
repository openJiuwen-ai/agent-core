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
