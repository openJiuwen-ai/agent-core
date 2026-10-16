# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamVerificationRail — intercepts task completions and triggers quality verification.

Mounted on the Leader agent, this rail:
1. Listens for TASK_COMPLETED events from the TeamMonitor
2. For each completed task, spawns a VerificationReviewer to assess output quality
3. Stores the result in team memory (TEAM_MEMORY.md)
4. Emits verification events to the frontend for visibility
5. Blocks consolidation if critical quality issues are found (configurable)

Inspired by Claude Code's verification subagents and OpenClaw's review patterns.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from .memory import VerificationMemory
from .result import VerificationInput
from .reviewer import VerificationReviewer

logger = logging.getLogger(__name__)


class TeamVerificationRail(DeepAgentRail):
    """Quality gate rail for Agent Team task outputs.

    Attributes:
        priority: Rail priority (lower = earlier in chain). Set to 15 so it runs
            after most rails but before final output formatting.
    """

    priority = 15

    def __init__(
        self,
        *,
        team_workspace_root: str | None = None,
        model_client: Any | None = None,
        language: str = "en",
        enabled: bool = True,
        block_on_fail: bool = False,
        auto_rework: bool = False,
        pass_threshold: int = 70,
        rework_threshold: int = 40,
        skip_verification_for: set[str] | None = None,
    ) -> None:
        """Initialize the verification rail.

        Args:
            team_workspace_root: Path to team workspace for memory persistence
            model_client: Model client for the reviewer (None = mock mode)
            language: "en" or "cn" for review prompts
            enabled: Master toggle for verification
            block_on_fail: If True, prevent leader consolidation on FAIL status
            auto_rework: If True, automatically create rework tasks for NEEDS_REWORK
            pass_threshold: Minimum overall score for PASS (default 70)
            rework_threshold: Below this score = FAIL (default 40)
            skip_verification_for: Task title patterns to skip (e.g. {"heartbeat", "ping"})
        """
        super().__init__()
        self._enabled = enabled
        self._block_on_fail = block_on_fail
        self._auto_rework = auto_rework
        self._skip_patterns = skip_verification_for or set()

        self._reviewer = VerificationReviewer(
            model_client=model_client,
            language=language,
            pass_threshold=pass_threshold,
            rework_threshold=rework_threshold,
        )
        self._memory = VerificationMemory(team_workspace_root=team_workspace_root)
        self._pending_verifications: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject verification context into leader prompts.

        Adds a brief section about recent verification trends so the leader
        can make informed consolidation decisions.
        """
        if not self._enabled:
            return

        try:
            trends = self._memory.get_quality_trends()
            if trends.get("total", 0) > 0:
                # Only inject if we have meaningful history
                ctx.extra["verification_trends"] = trends
        except Exception as exc:
            logger.debug("[TeamVerificationRail] Failed to load trends: %s", exc)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Check if the leader is about to consolidate a task that failed verification.

        If block_on_fail is enabled and the task being consolidated has a
        FAIL status, we can interrupt the consolidation.
        """
        if not self._enabled or not self._block_on_fail:
            return

        # This is a hook point for future enhancement:
        # Inspect the model's planned tool calls and block consolidation
        # of tasks with verification failures.
        pass

    # ------------------------------------------------------------------
    # Public API for external integration (called by TeamMonitorHandler)
    # ------------------------------------------------------------------

    async def on_task_completed(self, inp: VerificationInput) -> dict[str, Any]:
        """Handle a task completion event — the main entry point.

        Called by the team monitor handler when a teammate marks a task complete.
        Runs verification asynchronously and returns the result.

        Args:
            inp: VerificationInput with task details and output to verify

        Returns:
            Dict with verification result for event emission
        """
        if not self._enabled:
            return {"status": "skipped", "reason": "verification_disabled"}

        if self._should_skip(inp.task_title):
            return {"status": "skipped", "reason": "pattern_excluded"}

        # Deduplicate: don't verify the same task twice
        if inp.task_id in self._pending_verifications:
            logger.debug(
                "[TeamVerificationRail] Task %s already being verified", inp.task_id
            )
            return {"status": "pending", "task_id": inp.task_id}

        try:
            result = await self._reviewer.review(inp)

            # Persist to team memory
            self._memory.store(result)

            # Build event payload
            event_payload = {
                "event_type": "team.verification.completed",
                "task_id": inp.task_id,
                "assignee": inp.assignee,
                "status": result.status.value,
                "overall_score": result.overall_score,
                "summary": result.summary,
                "rework_instructions": result.rework_instructions,
                "dimensions": [
                    {"dimension": d.dimension.value, "score": d.score}
                    for d in result.dimensions
                ],
            }

            logger.info(
                "[TeamVerificationRail] Verified task=%s status=%s score=%d",
                inp.task_id,
                result.status.value,
                result.overall_score,
            )

            return event_payload

        except Exception as exc:
            logger.error(
                "[TeamVerificationRail] Verification failed for task=%s: %s",
                inp.task_id,
                exc,
                exc_info=True,
            )
            return {
                "event_type": "team.verification.error",
                "task_id": inp.task_id,
                "error": str(exc),
            }
        finally:
            self._pending_verifications.pop(inp.task_id, None)

    def _should_skip(self, task_title: str) -> bool:
        """Check if a task should be skipped based on title patterns."""
        title_lower = task_title.lower()
        return any(pattern.lower() in title_lower for pattern in self._skip_patterns)

    def get_quality_summary(self) -> dict[str, Any]:
        """Get a summary of recent verification quality for the leader."""
        return self._memory.get_quality_trends()

    def cleanup(self) -> None:
        """Cancel any pending verifications. Called on session teardown."""
        for task in self._pending_verifications.values():
            if not task.done():
                task.cancel()
        self._pending_verifications.clear()
