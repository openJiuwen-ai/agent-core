# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared online evolution pipeline coordinator for skill and team-skill rails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.experience.lifecycle import LocalApplyPreview
from openjiuwen.agent_evolving.experience.types import EvolutionContext, ExperienceApprovalRequest
from openjiuwen.agent_evolving.protocols import EXPERIENCES_TARGET, USER_INTENT_SIGNAL
from openjiuwen.agent_evolving.signal import EvolutionSignal, EvolutionTarget, get_signal_source
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.agent_evolving.update_execution import execute_updates
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.operator.skill_call import SkillExperienceOperator


class OnlineEvolutionOrchestrator:
    """Coordinate the shared online evolution pipeline for one skill target.

    The manager remains the owner of lifecycle state; this class only
    sequences context building, update generation, local preview, staging,
    and optional auto-approval for the rail adapters.
    """

    def __init__(
        self,
        *,
        store: EvolutionStore,
        updater: SingleDimUpdater,
        manager: ExperienceManager,
        skill_ops: Dict[str, SkillExperienceOperator],
        request_id_prefix: Optional[str] = None,
        stage_source: str = "experience_updater",
    ) -> None:
        self._store = store
        self._updater = updater
        self._manager = manager
        self._skill_ops = skill_ops
        self._request_id_prefix = request_id_prefix
        self._stage_source = stage_source

    async def evolve(
        self,
        *,
        skill_name: str,
        signals: list[EvolutionSignal],
        messages: Optional[list[dict]] = None,
        user_query: str = "",
        trajectory: Trajectory | None = None,
        requires_approval: bool,
        metadata: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> Optional[ExperienceApprovalRequest]:
        """Run online evolution and return the staged approval request, if any."""
        if not skill_name or not signals:
            return None
        if not self._store.skill_exists(skill_name):
            return None

        operator = self._skill_ops.get(skill_name)
        if operator is None:
            from openjiuwen.core.operator.skill_call import SkillExperienceOperator

            operator = self._skill_ops[skill_name] = SkillExperienceOperator(skill_name)

        online_context = await self._build_context(
            skill_name=skill_name,
            signals=signals,
            messages=messages,
            user_query=user_query,
            trajectory=trajectory,
            metadata=metadata,
        )
        preview = await self._generate_local_apply_preview(operator, online_context)
        if not preview.records:
            logger.info("[OnlineEvolutionOrchestrator] no applied updates for skill=%s", skill_name)
            return None

        request = self._manager.stage_apply_results(
            skill_name,
            preview.apply_results,
            requires_approval=requires_approval,
            source=source or self._stage_source,
            request_id_prefix=self._request_id_prefix,
            user_query=online_context.user_query,
            signal_type=self._get_signal_type(online_context),
            signal_source=self._get_signal_source(online_context),
        )
        if requires_approval:
            return request

        await self._manager.approve_request(request.request_id or "")
        return request

    async def _build_context(
        self,
        *,
        skill_name: str,
        signals: list[EvolutionSignal],
        messages: Optional[list[dict]] = None,
        user_query: str = "",
        trajectory: Trajectory | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EvolutionContext:
        skill_content = await self._store.read_skill_content(skill_name)
        existing_desc_records = await self._store.get_pending_records(
            skill_name,
            EvolutionTarget.DESCRIPTION,
        )
        existing_body_records = await self._store.get_pending_records(
            skill_name,
            EvolutionTarget.BODY,
        )
        existing_script_records = await self._store.get_pending_records(
            skill_name,
            EvolutionTarget.SCRIPT,
        )
        return EvolutionContext(
            skill_name=skill_name,
            signals=list(signals),
            messages=list(messages or []),
            user_query=user_query,
            skill_content=skill_content,
            existing_desc_records=existing_desc_records,
            existing_body_records=existing_body_records,
            existing_script_records=existing_script_records,
            trajectory=trajectory,
            metadata=dict(metadata or {}),
        )

    async def _generate_local_apply_preview(
        self,
        operator: SkillExperienceOperator,
        online_context: EvolutionContext,
    ) -> LocalApplyPreview:
        self._updater.bind(
            operators={operator.operator_id: operator},
            targets=[EXPERIENCES_TARGET],
            online_contexts={online_context.skill_name: online_context},
        )
        trajectories = [online_context.trajectory] if online_context.trajectory is not None else []
        updates = await self._updater.process(
            trajectories,
            online_context.signals,
            {},
        )
        apply_results = execute_updates(
            {operator.operator_id: operator},
            updates,
        )
        return self._manager.build_local_apply_preview(online_context.skill_name, apply_results)

    @staticmethod
    def _get_preferred_signal(online_context: EvolutionContext) -> EvolutionSignal | None:
        preferred_signal = next(
            (signal for signal in online_context.signals if signal.signal_type == USER_INTENT_SIGNAL),
            online_context.signals[0] if online_context.signals else None,
        )
        return preferred_signal

    @staticmethod
    def _get_signal_type(online_context: EvolutionContext) -> str | None:
        preferred_signal = OnlineEvolutionOrchestrator._get_preferred_signal(online_context)
        return preferred_signal.signal_type if preferred_signal is not None else None

    @staticmethod
    def _get_signal_source(online_context: EvolutionContext) -> str | None:
        preferred_signal = OnlineEvolutionOrchestrator._get_preferred_signal(online_context)
        if preferred_signal is None:
            return None
        return get_signal_source(preferred_signal)
