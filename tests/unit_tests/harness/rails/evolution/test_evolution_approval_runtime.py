# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for shared approval runtime helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime


class _Owner:
    def __init__(self) -> None:
        self.manager = Mock()
        self.pending_approval_snapshots: dict[str, Any] = {}


class TestEvolutionApprovalRuntime(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner = _Owner()
        self.runtime = EvolutionApprovalRuntime(
            manager=self.owner.manager,
            pending_approval_snapshots=self.owner.pending_approval_snapshots,
        )

    async def test_finalize_staged_evolution_request_uses_approval_emitter(self):
        request = object()
        calls: List[str] = []

        async def _emit_approval(staged_request):
            self.assertIs(staged_request, request)
            calls.append("approval")

        async def _auto_approved(staged_request):
            calls.append("auto")

        result = await self.runtime.finalize_staged_evolution_request(
            request,
            requires_approval=True,
            emit_approval_request=_emit_approval,
            on_auto_approved=_auto_approved,
        )

        self.assertIs(result, request)
        self.assertEqual(calls, ["approval"])

    async def test_finalize_staged_evolution_request_uses_auto_approved_callback(self):
        request = object()
        calls: List[str] = []

        async def _emit_approval(staged_request):
            calls.append("approval")

        def _auto_approved(staged_request):
            self.assertIs(staged_request, request)
            calls.append("auto")

        result = await self.runtime.finalize_staged_evolution_request(
            request,
            requires_approval=False,
            emit_approval_request=_emit_approval,
            on_auto_approved=_auto_approved,
        )

        self.assertIs(result, request)
        self.assertEqual(calls, ["auto"])

    async def test_approve_pending_request_delegates_to_manager(self):
        pending = SimpleNamespace(skill_name="skill-a")
        self.owner.pending_approval_snapshots["req-1"] = pending
        self.owner.manager.approve_request = AsyncMock(
            return_value=SimpleNamespace(pending_count=0, applied_count=1)
        )

        resolved_pending, result = await self.runtime.approve_pending_request(
            "req-1",
            rail_name="TestRail",
            action_name="on_approve",
        )

        self.owner.manager.approve_request.assert_awaited_once_with("req-1")
        self.assertIs(resolved_pending, pending)
        self.assertEqual(result.applied_count, 1)

    async def test_approve_unknown_request_does_not_call_manager(self):
        self.owner.manager.approve_request = AsyncMock()

        resolved_pending, result = await self.runtime.approve_pending_request(
            "missing",
            rail_name="TestRail",
            action_name="on_approve",
        )

        self.owner.manager.approve_request.assert_not_awaited()
        self.assertIsNone(resolved_pending)
        self.assertIsNone(result)

    async def test_approve_partial_failure_returns_result_for_caller_retry(self):
        pending = SimpleNamespace(skill_name="skill-a")
        partial = SimpleNamespace(pending_count=2, applied_count=1)
        self.owner.pending_approval_snapshots["req-partial"] = pending
        self.owner.manager.approve_request = AsyncMock(return_value=partial)

        resolved_pending, result = await self.runtime.approve_pending_request(
            "req-partial",
            rail_name="TestRail",
            action_name="on_approve",
        )

        self.owner.manager.approve_request.assert_awaited_once_with("req-partial")
        self.assertIs(resolved_pending, pending)
        self.assertIs(result, partial)

    async def test_reject_pending_request_delegates_to_manager(self):
        pending = SimpleNamespace(skill_name="skill-a")
        self.owner.pending_approval_snapshots["req-2"] = pending
        self.owner.manager.reject_request = AsyncMock(
            return_value=SimpleNamespace(rejected_count=1)
        )

        resolved_pending, result = await self.runtime.reject_pending_request(
            "req-2",
            rail_name="TestRail",
            action_name="on_reject",
        )

        self.owner.manager.reject_request.assert_awaited_once_with("req-2")
        self.assertIs(resolved_pending, pending)
        self.assertEqual(result.rejected_count, 1)
