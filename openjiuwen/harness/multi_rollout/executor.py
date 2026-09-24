# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Multi-rollout executor — spawn N isolated attempts, run in parallel,
select best result.

This is the core engine for the task-layer multi-rollout feature.
It wraps a parent DeepAgent, creates N subagents with isolated workspaces,
applies strategy variants, runs them concurrently, and returns the best
result via a pluggable selector.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.multi_rollout.config import (
    MultiRolloutConfig,
)
from openjiuwen.harness.multi_rollout.selector import (
    RolloutResult,
    get_selector,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


class MultiRolloutExecutor:
    """Execute a task via N parallel rollouts and return the best result.

    Usage::

        executor = MultiRolloutExecutor(parent_agent, config)
        result = await executor.invoke({"query": "fix bug #123"})

    The executor is transparent: when multi-rollout is disabled it
    delegates directly to the parent agent.
    """

    def __init__(
        self,
        parent_agent: "DeepAgent",
        config: MultiRolloutConfig,
    ) -> None:
        self._parent = parent_agent
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._config.enabled and self._config.n_rollouts > 1

    async def invoke(
        self,
        inputs: Any,
        session: Any | None = None,
    ) -> dict[str, Any]:
        """Run the task with multi-rollout and return the best result.

        Args:
            inputs: Standard agent inputs (dict with "query", etc.).
            session: Optional parent session.

        Returns:
            The selected best result dict (same shape as parent agent output).
        """
        if not self.is_enabled():
            return await self._parent.invoke(inputs, session)

        start = time.monotonic()
        n = self._config.n_rollouts
        logger.info(
            "[MultiRollout] Starting %d parallel rollouts",
            n,
        )

        # 1. Create isolated subagents
        subagents = self._create_subagents(n)

        # 2. Build per-attempt inputs with strategy prefix
        attempt_inputs = self._build_attempt_inputs(inputs, n)

        # 3. Execute in parallel with timeout
        results = await self._execute_parallel(subagents, attempt_inputs)

        # 4. Select best
        selector = get_selector(self._config.selector_kind)
        best = selector.select(results)

        elapsed = time.monotonic() - start
        if best.is_success:
            logger.info(
                "[MultiRollout] Selected attempt %d / %d in %.2fs",
                best.attempt_index,
                n,
                elapsed,
            )
        else:
            logger.warning(
                "[MultiRollout] All %d attempts failed in %.2fs",
                n,
                elapsed,
            )

        # 5. Return best result (unwrap from RolloutResult)
        if best.exception is not None:
            raise best.exception
        return best.result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_subagents(self, n: int) -> list["DeepAgent"]:
        """Create N isolated subagents via the parent's factory."""
        agents: list[DeepAgent] = []
        for i in range(n):
            sub_id = f"rollout-{i:03d}"
            try:
                sub = self._parent.create_subagent(
                    "general-purpose",
                    subsession_id=sub_id,
                )
                agents.append(sub)
                logger.debug(
                    "[MultiRollout] Created subagent %s", sub_id
                )
            except Exception as exc:
                logger.warning(
                    "[MultiRollout] Failed to create subagent %s: %s",
                    sub_id,
                    exc,
                )
                raise
        return agents

    def _build_attempt_inputs(
        self,
        base_inputs: Any,
        n: int,
    ) -> list[Any]:
        """Inject strategy variant into each attempt's query."""
        variants = self._config.strategy_variants
        results: list[Any] = []
        for i in range(n):
            inp = self._copy_inputs(base_inputs)
            strategy = variants[i % len(variants)]
            query = self._extract_query(inp)
            if query:
                new_query = f"{strategy}\n\nTask:\n{query}"
                self._set_query(inp, new_query)
            results.append(inp)
        return results

    @staticmethod
    def _copy_inputs(inputs: Any) -> Any:
        """Shallow-copy inputs dict so each attempt is independent."""
        if isinstance(inputs, dict):
            return dict(inputs)
        return inputs

    @staticmethod
    def _extract_query(inputs: Any) -> str:
        """Best-effort extraction of the user query from inputs."""
        if isinstance(inputs, dict):
            return str(inputs.get("query", inputs.get("content", "")))
        return str(inputs) if inputs is not None else ""

    @staticmethod
    def _set_query(inputs: Any, query: str) -> None:
        """Set the query field back into inputs."""
        if isinstance(inputs, dict):
            if "query" in inputs:
                inputs["query"] = query
            elif "content" in inputs:
                inputs["content"] = query

    async def _execute_parallel(
        self,
        subagents: list["DeepAgent"],
        attempt_inputs: list[Any],
    ) -> list[RolloutResult]:
        """Run all subagents in parallel with individual timeouts."""
        n = len(subagents)
        timeout = self._config.timeout_per_rollout
        max_par = self._config.max_parallel or n

        semaphore = asyncio.Semaphore(max_par)

        async def _run_one(
            idx: int,
            agent: "DeepAgent",
            inp: Any,
        ) -> RolloutResult:
            async with semaphore:
                logger.debug(
                    "[MultiRollout] Attempt %d starting", idx
                )
                try:
                    result = await asyncio.wait_for(
                        agent.invoke(inp),
                        timeout=timeout,
                    )
                    logger.debug(
                        "[MultiRollout] Attempt %d completed",
                        idx,
                    )
                    return RolloutResult(
                        result=result,
                        attempt_index=idx,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[MultiRollout] Attempt %d timed out "
                        "after %.1fs",
                        idx,
                        timeout,
                    )
                    return RolloutResult(
                        result=None,
                        attempt_index=idx,
                        exception=TimeoutError(
                            f"Rollout {idx} exceeded {timeout}s"
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "[MultiRollout] Attempt %d failed: %s",
                        idx,
                        exc,
                        exc_info=True,
                    )
                    return RolloutResult(
                        result=None,
                        attempt_index=idx,
                        exception=exc,
                    )

        tasks = [
            asyncio.create_task(_run_one(i, subagents[i], attempt_inputs[i]))
            for i in range(n)
        ]
        return await asyncio.gather(*tasks)
