# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Summary Team lifecycle helpers mixed into :class:`OrganizationRuntimeManager`."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import team_logger

from openjiuwen.agent_teams.organization.events import (
    OrgSummaryCompletedEvent,
    OrgSummaryProvisionedEvent,
    OrgSummaryProvisionFailedEvent,
    OrgSummarySourceFailedEvent,
    OrgSummarySourcesReadyEvent,
    OrgSummarySourcesUpdatedEvent,
    OrgSummaryTaskCreatedEvent,
)
from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization import runtime_prompts as prompts
from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_TERMINAL_STATUS_VALUES,
    OrgSummaryExecutionStatus,
)
from openjiuwen.agent_teams.organization.summary import SummaryTeamFactory
from openjiuwen.agent_teams.organization.task_pool import TERMINAL_SUMMARY_EXECUTION_STATUSES
from openjiuwen.agent_teams.tools.database.engine import get_current_time

logger = team_logger


class OrganizationSummaryMixin:
    """Provision, bind, resume, and wake Summary Teams (§4.4 / §8).

    Expects the host class to provide ``_ensure_summary_factory``, ``_bind_team``,
    ``_resolve_leader``, and ``_schedule_leader_turn``.
    """

    async def _resume_summary_executions(
        self,
        *,
        manager: TeamOrganizationManager,
        session_id: str,
    ) -> None:
        """Recover in-flight Summary Teams after a rebind (§8).

        Event delivery is best-effort; the SummaryExecution table is the durable
        source of truth.  For an execution that never bound a team (interrupted
        during provisioning), re-provision it through the factory and converge on
        the same post-launch binding.  For a bound one, re-schedule a ready Summary
        Task to its running dynamic team, and re-evaluate sources for a still-
        WAITING one so a dropped ''sources ready'' notification is rebuilt.
        """
        summary_factory = self._ensure_summary_factory()
        for execution in await manager.task_pool.list_summary_executions():
            if execution.status in TERMINAL_SUMMARY_EXECUTION_STATUSES:
                continue
            summary_task = await manager.task_pool.get_task(execution.summary_task_id)
            if summary_task is None:
                logger.debug(
                    "summary resume: task %s is gone; skipping execution %s",
                    execution.summary_task_id,
                    execution.execution_id,
                )
                continue
            if summary_task.status.value in ORG_TASK_TERMINAL_STATUS_VALUES:
                # Summary finished but release() may have failed earlier, leaving
                # a live execution + bound team. Retry cleanup; do not re-wake.
                if execution.summary_team_id:
                    logger.info(
                        "summary resume: retrying release for terminal task %s (execution=%s team=%s)",
                        execution.summary_task_id,
                        execution.execution_id,
                        execution.summary_team_id,
                    )
                    await self._release_summary_task(
                        manager=manager,
                        summary_task_id=execution.summary_task_id,
                        session_id=session_id,
                    )
                continue
            summary_team_id = execution.summary_team_id
            if not summary_team_id:
                if summary_factory is None:
                    logger.debug(
                        "summary resume: no factory; cannot recover execution %s",
                        execution.execution_id,
                    )
                    continue
                root_task_id = str(summary_task.root_task_id or execution.root_task_id)
                from_team_id = summary_task.created_by.team_id or (await self._owner_team_id(manager))
                logger.info(
                    "summary resume: re-provisioning execution %s (task=%s root=%s owner=%s)",
                    execution.execution_id,
                    execution.summary_task_id,
                    root_task_id,
                    from_team_id,
                )
                try:
                    launched = await summary_factory.recover(
                        execution_id=execution.execution_id,
                        organization_id=manager.organization_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        owner_team_id=from_team_id,
                        session_id=session_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "summary resume: recover failed for execution %s (task=%s): %s",
                        execution.execution_id,
                        execution.summary_task_id,
                        exc,
                        exc_info=True,
                    )
                    await self._abort_summary_launch(
                        manager=manager,
                        execution=execution,
                        from_team_id=from_team_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        failure_reason=str(exc),
                        session_id=session_id,
                        summary_factory=summary_factory,
                    )
                    continue
                summary_team_id = launched.team_id
                try:
                    await self._complete_summary_provision(
                        manager=manager,
                        execution=execution,
                        launched=launched,
                        from_team_id=from_team_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        session_id=session_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "summary resume: complete-provision failed for execution %s: %s",
                        execution.execution_id,
                        exc,
                        exc_info=True,
                    )
                    await self._abort_summary_launch(
                        manager=manager,
                        execution=execution,
                        from_team_id=from_team_id,
                        root_task_id=root_task_id,
                        summary_task_id=execution.summary_task_id,
                        failure_reason=str(exc),
                        session_id=session_id,
                        summary_factory=summary_factory,
                        launched=launched,
                    )
                    continue
            else:
                # Remount org tools after restart; avoid ensure_team_binding to
                # prevent re-entering this resume path via _resume_assignable_tasks.
                try:
                    await self._bind_summary_team(
                        manager=manager,
                        team_id=summary_team_id,
                        session_id=session_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "summary resume: could not rebind org tools for team %s (task=%s): %s",
                        summary_team_id,
                        execution.summary_task_id,
                        exc,
                    )
            evaluation = await manager.task_pool.evaluate_summary_sources(
                summary_task_id=execution.summary_task_id,
            )
            if not evaluation.get("ready"):
                logger.debug(
                    "summary resume: sources not ready for task %s; leaving team %s idle",
                    execution.summary_task_id,
                    summary_team_id,
                )
                continue
            logger.info(
                "summary resume: sources ready; waking team %s for task %s",
                summary_team_id,
                execution.summary_task_id,
            )
            self._schedule_summary_turn(
                manager=manager,
                session_id=session_id,
                summary_team_id=summary_team_id,
                summary_task_id=execution.summary_task_id,
                root_task_id=execution.root_task_id,
            )

    async def _handle_summary_task_created(
        self,
        *,
        manager: TeamOrganizationManager,
        summary_factory: SummaryTeamFactory,
        event: OrgSummaryTaskCreatedEvent,
        session_id: str,
    ) -> None:
        """Provision a dynamic Summary Team and delegate the Summary Task (§4.4.1)."""
        task = await manager.task_pool.get_task(event.summary_task_id)
        if task is None:
            logger.warning(
                "summary task created event ignored: task %s not found",
                event.summary_task_id,
            )
            return
        root_task_id = str(task.root_task_id or event.summary_task_id)
        from_team_id = task.created_by.team_id or (await self._owner_team_id(manager))
        logger.debug(
            "provisioning summary team for task %s (root=%s owner=%s session=%s)",
            event.summary_task_id,
            root_task_id,
            from_team_id,
            session_id,
        )
        execution = await manager.task_pool.create_summary_execution(
            root_task_id=root_task_id,
            summary_task_id=event.summary_task_id,
        )
        try:
            launched = await summary_factory.provision(
                organization_id=manager.organization_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                owner_team_id=from_team_id,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "summary team provision failed for task %s (execution=%s): %s",
                event.summary_task_id,
                execution.execution_id,
                exc,
                exc_info=True,
            )
            await self._abort_summary_launch(
                manager=manager,
                execution=execution,
                from_team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                failure_reason=str(exc),
                session_id=session_id,
                summary_factory=summary_factory,
            )
            return
        logger.info(
            "summary team provisioned: task=%s team=%s leader=%s",
            event.summary_task_id,
            launched.team_id,
            launched.leader_id,
        )
        try:
            await self._complete_summary_provision(
                manager=manager,
                execution=execution,
                launched=launched,
                from_team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "summary team post-provision bind failed for task %s (team=%s): %s",
                event.summary_task_id,
                launched.team_id,
                exc,
                exc_info=True,
            )
            await self._abort_summary_launch(
                manager=manager,
                execution=execution,
                from_team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=event.summary_task_id,
                failure_reason=str(exc),
                session_id=session_id,
                summary_factory=summary_factory,
                launched=launched,
            )

    async def _complete_summary_provision(
        self,
        *,
        manager: TeamOrganizationManager,
        execution: Any,
        launched: Any,
        from_team_id: str,
        root_task_id: str,
        summary_task_id: str,
        session_id: str,
    ) -> None:
        """Bind the launched team into the org runtime and delegate the Summary Task."""
        await self._bind_summary_team(
            manager=manager,
            team_id=launched.team_id,
            session_id=session_id,
        )
        summary_task = await manager.task_pool.get_task(summary_task_id)
        already_delegated = summary_task is not None and summary_task.assignment.team_id == launched.team_id
        if not already_delegated:
            delegated = await manager.task_pool.delegate_task(
                task_id=summary_task_id,
                from_team_id=from_team_id,
                to_team_id=launched.team_id,
            )
            if not delegated.ok:
                raise RuntimeError(
                    f"summary task delegate failed for {summary_task_id}: {delegated.reason}"
                )
        else:
            logger.debug(
                "summary task %s already delegated to %s; skipping delegate",
                summary_task_id,
                launched.team_id,
            )
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.RUNNING,
            summary_team_id=launched.team_id,
        )
        await manager.task_pool.bind_root_summary_team(
            root_task_id=root_task_id,
            summary_team_id=launched.team_id,
        )
        logger.info(
            "summary provision bound: task=%s team=%s execution=%s root=%s",
            summary_task_id,
            launched.team_id,
            execution.execution_id,
            root_task_id,
        )
        await manager.task_pool.publish_event(
            OrgSummaryProvisionedEvent(
                organization_id=manager.organization_id,
                team_id=from_team_id,
                root_task_id=root_task_id,
                summary_task_id=summary_task_id,
                summary_team_id=launched.team_id,
            )
        )

    async def _bind_summary_team(
        self,
        *,
        manager: TeamOrganizationManager,
        team_id: str,
        session_id: str,
    ) -> None:
        """Mount org tools and event subscriptions on a provisioned Summary Team."""
        agent, backend = await self._resolve_leader(team_id, session_id)
        await self._bind_team(
            agent=agent,
            backend=backend,
            manager=manager,
            session_id=session_id,
        )
        logger.debug(
            "summary team %s bound into organization %s (session=%s)",
            team_id,
            manager.organization_id,
            session_id,
        )

    async def _abort_summary_launch(
        self,
        *,
        manager: TeamOrganizationManager,
        execution: Any,
        from_team_id: str,
        root_task_id: str,
        summary_task_id: str,
        failure_reason: str,
        session_id: str,
        summary_factory: SummaryTeamFactory | None = None,
        launched: Any | None = None,
    ) -> None:
        """Fail the execution/task and optionally release a host-activated team."""
        await self._fail_summary_provision(
            manager=manager,
            execution=execution,
            from_team_id=from_team_id,
            root_task_id=root_task_id,
            summary_task_id=summary_task_id,
            failure_reason=failure_reason,
        )
        if launched is not None:
            await self._release_launched_summary_team(
                summary_factory=summary_factory,
                execution=execution,
                summary_team_id=launched.team_id,
                session_id=session_id,
            )

    async def _fail_summary_provision(
        self,
        *,
        manager: TeamOrganizationManager,
        execution: Any,
        from_team_id: str,
        root_task_id: str,
        summary_task_id: str,
        failure_reason: str,
    ) -> None:
        """Mark the execution FAILED, fail the Summary Task, wake the root leader."""
        logger.warning(
            "summary provision failed: task=%s execution=%s reason=%s",
            summary_task_id,
            execution.execution_id,
            failure_reason,
        )
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.FAILED,
        )
        await manager.task_pool.fail_summary_task(
            summary_task_id=summary_task_id,
            failure_reason=failure_reason,
        )
        await manager.task_pool.publish_event(
            OrgSummaryProvisionFailedEvent(
                organization_id=manager.organization_id,
                team_id=from_team_id,
                summary_task_id=summary_task_id,
                root_task_id=root_task_id,
                failure_reason=failure_reason,
            )
        )

    async def _release_launched_summary_team(
        self,
        *,
        summary_factory: Any,
        execution: Any,
        summary_team_id: str,
        session_id: str,
    ) -> None:
        """Stop a host-activated Summary Team that never landed on the execution row."""
        if summary_factory is None or not summary_team_id:
            return
        try:
            await summary_factory.release(
                execution_id=execution.execution_id,
                summary_team_id=summary_team_id,
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to release orphaned summary team %s (execution=%s)",
                summary_team_id,
                execution.execution_id,
                exc_info=True,
            )

    async def _handle_summary_provisioned(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummaryProvisionedEvent,
        session_id: str,
    ) -> None:
        """Wake only when sources are already ready (§4.4.3); else wait for SourcesReady."""
        evaluation = await manager.task_pool.evaluate_summary_sources(
            summary_task_id=event.summary_task_id,
        )
        if evaluation.get("ready"):
            self._schedule_summary_turn(
                manager=manager,
                session_id=session_id,
                summary_team_id=event.summary_team_id,
                summary_task_id=event.summary_task_id,
                root_task_id=event.root_task_id,
            )

    async def _handle_summary_provision_failed(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummaryProvisionFailedEvent,
        session_id: str,
    ) -> None:
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=event.root_task_id,
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_sources_updated(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummarySourcesUpdatedEvent,
        session_id: str,
    ) -> None:
        evaluation = await manager.task_pool.evaluate_summary_sources(summary_task_id=event.summary_task_id)
        if evaluation.get("ready"):
            await manager.task_pool.publish_event(
                OrgSummarySourcesReadyEvent(
                    organization_id=manager.organization_id,
                    team_id=None,
                    summary_task_id=event.summary_task_id,
                )
            )
            return
        source_failed = evaluation.get("source_failed")
        if source_failed is not None:
            await manager.task_pool.publish_event(
                OrgSummarySourceFailedEvent(
                    organization_id=manager.organization_id,
                    team_id=None,
                    summary_task_id=event.summary_task_id,
                    source_task_id=source_failed,
                    failure_reason=str(evaluation.get("reason") or "source task failed"),
                )
            )

    async def _handle_summary_sources_ready(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummarySourcesReadyEvent,
        session_id: str,
    ) -> None:
        summary_task = await manager.task_pool.get_task(event.summary_task_id)
        if summary_task is None:
            return
        root_task_id = str(summary_task.root_task_id or event.summary_task_id)
        execution = await self._running_summary_execution(manager=manager, summary_task_id=event.summary_task_id)
        if execution is None or not execution.summary_team_id:
            return
        self._schedule_summary_turn(
            manager=manager,
            session_id=session_id,
            summary_team_id=execution.summary_team_id,
            summary_task_id=event.summary_task_id,
            root_task_id=root_task_id,
        )

    async def _handle_summary_source_failed(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummarySourceFailedEvent,
        session_id: str,
    ) -> None:
        summary_task = await manager.task_pool.get_task(event.summary_task_id)
        if summary_task is None:
            return
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=str(summary_task.root_task_id or event.summary_task_id),
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_completed(
        self,
        *,
        manager: TeamOrganizationManager,
        event: OrgSummaryCompletedEvent,
        session_id: str,
    ) -> None:
        await self._release_summary_task(
            manager=manager,
            summary_task_id=event.summary_task_id,
            session_id=session_id,
        )
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=event.root_task_id,
            summary_task_id=event.summary_task_id,
        )

    async def _handle_summary_task_failed(
        self,
        *,
        manager: TeamOrganizationManager,
        task_id: str,
        session_id: str,
    ) -> None:
        summary_task = await manager.task_pool.get_task(task_id)
        if summary_task is None:
            return
        await self._release_summary_task(
            manager=manager,
            summary_task_id=task_id,
            session_id=session_id,
        )
        await self._schedule_summary_root_turn(
            manager=manager,
            session_id=session_id,
            root_task_id=str(summary_task.root_task_id or task_id),
            summary_task_id=task_id,
        )

    async def _release_summary_task(
        self,
        *,
        manager: TeamOrganizationManager,
        summary_task_id: str,
        session_id: str,
    ) -> None:
        execution = await self._live_summary_execution(
            manager=manager,
            summary_task_id=summary_task_id,
        )
        if execution is None:
            logger.debug(
                "no live summary execution for task %s; nothing to release",
                summary_task_id,
            )
            return

        summary_factory = self._ensure_summary_factory()
        if summary_factory is None:
            logger.debug(
                "no summary factory; skipping release of execution %s",
                execution.execution_id,
            )
            return
        if execution.summary_team_id:
            logger.debug(
                "releasing summary team %s (execution=%s)",
                execution.summary_team_id,
                execution.execution_id,
            )
            try:
                await summary_factory.release(
                    execution_id=execution.execution_id,
                    summary_team_id=execution.summary_team_id,
                    session_id=session_id,
                )
            except Exception:  # noqa: BLE001
                # Keep the live status so resume can retry; marking RELEASED here
                # would orphan the still-running Summary Team (§4.4.3).
                logger.warning(
                    "Failed to release summary execution %s; leaving status=%s for retry",
                    execution.execution_id,
                    execution.status,
                    exc_info=True,
                )
                return
        await manager.task_pool.update_summary_execution(
            execution_id=execution.execution_id,
            status=OrgSummaryExecutionStatus.RELEASED,
            released_at=get_current_time(),
        )

    async def _live_summary_execution(
        self,
        *,
        manager: TeamOrganizationManager,
        summary_task_id: str,
    ) -> Any | None:
        for execution in await manager.task_pool.list_summary_executions(summary_task_id=summary_task_id):
            if execution.status not in TERMINAL_SUMMARY_EXECUTION_STATUSES:
                return execution
        return None

    async def _running_summary_execution(
        self,
        *,
        manager: TeamOrganizationManager,
        summary_task_id: str,
    ) -> Any | None:
        for execution in await manager.task_pool.list_summary_executions(summary_task_id=summary_task_id):
            if execution.summary_team_id and execution.status is not OrgSummaryExecutionStatus.RELEASED:
                return execution
        return None

    async def _owner_team_id(self, manager: TeamOrganizationManager) -> str:
        organization = await manager.get_organization()
        return organization.owner_team_id if organization is not None else ""

    def _schedule_summary_turn(
        self,
        *,
        manager: TeamOrganizationManager,
        session_id: str,
        summary_team_id: str,
        summary_task_id: str,
        root_task_id: str,
    ) -> None:
        prompt = prompts.summary_aggregate_turn(
            organization_id=manager.organization_id,
            summary_task_id=summary_task_id,
            root_task_id=root_task_id,
        )
        self._schedule_leader_turn(team_id=summary_team_id, session_id=session_id, prompt=prompt)

    async def _schedule_summary_root_turn(
        self,
        *,
        manager: TeamOrganizationManager,
        session_id: str,
        root_task_id: str,
        summary_task_id: str,
    ) -> None:
        team_id = await self._resolve_root_team_id(manager=manager, root_task_id=root_task_id)
        if not team_id:
            return
        prompt = prompts.summary_root_attention_turn(
            organization_id=manager.organization_id,
            summary_task_id=summary_task_id,
            root_task_id=root_task_id,
        )
        self._schedule_leader_turn(team_id=team_id, session_id=session_id, prompt=prompt)

    async def _resolve_root_team_id(self, *, manager: TeamOrganizationManager, root_task_id: str) -> str:
        root = await manager.task_pool.get_task(root_task_id)
        if root is not None:
            if root.assignment.team_id:
                return root.assignment.team_id
            if root.created_by.team_id:
                return root.created_by.team_id
        return await self._owner_team_id(manager)


__all__ = ["OrganizationSummaryMixin"]
