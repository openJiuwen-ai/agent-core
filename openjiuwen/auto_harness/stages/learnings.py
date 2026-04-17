# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Learnings 阶段 — Session 结束后的反思与经验记录。"""

from __future__ import annotations

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
)

from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_learnings as _parse_learnings_raw,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    Experience,
    ExperienceType,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

logger = logging.getLogger(__name__)


async def run_learnings(
    config: AutoHarnessConfig,
    results: List[CycleResult],
    experience_store: "ExperienceStore",
) -> AsyncIterator[Any]:
    """Session 结束后的反思与经验记录（流式）。

    Args:
        config: Auto Harness 配置。
        results: 本次 session 的执行结果列表。
        experience_store: ExperienceStore 实例。

    Yields:
        OutputSchema chunks from learnings agent.
    """
    if not results:
        return

    from openjiuwen.auto_harness.agent import (
        create_learnings_agent,
    )

    results_text = "\n".join(
        f"- {r.pr_url or r.error or 'completed'} "
        f"(success={r.success}, "
        f"reverted={r.reverted})"
        for r in results
    )

    recent = await experience_store.list_recent(limit=10)
    existing_text = "\n".join(
        f"- [{m.type.value}] {m.topic}: "
        f"{m.summary}"
        for m in recent
    ) or "无"

    agent = create_learnings_agent(
        config,
        session_results=results_text,
        existing_memories=existing_text,
    )

    query = (
        f"本次 session 结果:\n{results_text}\n\n"
        f"已有经验:\n{existing_text}\n"
    )

    try:
        output = ""
        async for chunk in agent.stream(
            {"query": query},
        ):
            yield chunk
            output += extract_text(chunk)

        learnings = _parse_learnings_raw(output)
        for learning in learnings:
            mem_type = learning.get(
                "type", "insight",
            )
            await experience_store.record(Experience(
                type=ExperienceType(mem_type),
                topic=learning.get("topic", ""),
                summary=learning.get(
                    "summary", "",
                ),
                details=learning.get(
                    "details", "",
                ),
            ))
        logger.info(
            "Learnings recorded: %d",
            len(learnings),
        )
    except Exception:
        logger.warning(
            "Learnings phase failed",
            exc_info=True,
        )
