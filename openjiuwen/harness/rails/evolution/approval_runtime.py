# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared approval runtime for evolution rails."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.evolution.contracts import (
    ApprovalManagerProtocol,
    PendingApprovalSnapshotStore,
)


class EvolutionApprovalRuntime:
    """Shared approval lifecycle helpers bound to one rail instance."""

    def __init__(
        self,
        *,
        manager: ApprovalManagerProtocol,
        pending_approval_snapshots: PendingApprovalSnapshotStore,
    ) -> None:
        self._manager = manager
        self._pending_approval_snapshots = pending_approval_snapshots

    def lookup_pending_approval_snapshot(
        self,
        request_id: str,
        *,
        rail_name: str,
        action_name: str,
    ) -> Any:
        """Resolve a staged approval snapshot owned by a concrete rail."""
        pending = self._pending_approval_snapshots.get(request_id)
        if pending is None:
            logger.warning("[%s] %s: unknown request_id=%s", rail_name, action_name, request_id)
        return pending

    async def approve_pending_request(
        self,
        request_id: str,
        *,
        rail_name: str,
        action_name: str,
    ) -> tuple[Any, Any]:
        """Approve one staged request through the shared manager lifecycle."""
        pending = self.lookup_pending_approval_snapshot(
            request_id,
            rail_name=rail_name,
            action_name=action_name,
        )
        if pending is None:
            return None, None

        result = await self._manager.approve_request(request_id)
        pending_count = getattr(result, "pending_count", 0)
        if pending_count:
            applied_count = getattr(result, "applied_count", 0)
            logger.warning(
                "[%s] %s partial failure: %d/%d record(s) written for '%s' "
                "(request=%s); retry %s to complete",
                rail_name,
                action_name,
                applied_count,
                applied_count + pending_count,
                pending.skill_name,
                request_id,
                action_name,
            )
        return pending, result

    async def reject_pending_request(
        self,
        request_id: str,
        *,
        rail_name: str,
        action_name: str,
    ) -> tuple[Any, Any]:
        """Reject one staged request through the shared manager lifecycle."""
        pending = self.lookup_pending_approval_snapshot(
            request_id,
            rail_name=rail_name,
            action_name=action_name,
        )
        if pending is None:
            return None, None
        result = await self._manager.reject_request(request_id)
        return pending, result

    async def finalize_staged_evolution_request(
        self,
        request: Any,
        *,
        requires_approval: bool,
        emit_approval_request: Callable[[Any], Any],
        on_auto_approved: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        """Route one staged request into approval buffering or auto-approve side effects."""
        if request is None:
            return None

        if requires_approval:
            outcome = emit_approval_request(request)
            if inspect.isawaitable(outcome):
                await outcome
            return request

        if on_auto_approved is not None:
            outcome = on_auto_approved(request)
            if inspect.isawaitable(outcome):
                await outcome
        return request


__all__ = ["EvolutionApprovalRuntime"]
