# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from typing import Any

from team_hybrid import TaskExecutionTeam
from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.runner import Runner


def _payload(chunk: Any) -> dict[str, Any]:
    payload = getattr(chunk, "payload", chunk)
    if not isinstance(payload, dict):
        raise AssertionError(f"unexpected chunk payload: {payload!r}")
    return payload


def _has_event(payloads: list[dict[str, Any]], event: str, source_agent_id: str | None = None) -> bool:
    return any(
        payload.get("event") == event
        and (source_agent_id is None or payload.get("source_agent_id") == source_agent_id)
        for payload in payloads
    )


async def main() -> None:
    await Runner.start()
    team_card = TeamCard(id="task_execution_team_stream", name="task_execution_team_stream", description="任务执行团队")
    team = TaskExecutionTeam(card=team_card, config=TeamConfig(max_agents=10))
    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        payloads: list[dict[str, Any]] = []
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=team.card.id,
            inputs={"task": "开发新功能模块"},
        ):
            payloads.append(_payload(chunk))

        if len(payloads) <= 1:
            raise AssertionError(f"expected multiple stream chunks, got {len(payloads)}")

        if not _has_event(payloads, "team_started"):
            raise AssertionError("missing team_started event")
        if not _has_event(payloads, "orchestrator_received", "orchestrator"):
            raise AssertionError("missing orchestrator stream event")
        if not _has_event(payloads, "executor_started", "executor1"):
            raise AssertionError("missing executor1 stream event")
        if not _has_event(payloads, "aggregator_progress", "aggregator"):
            raise AssertionError("missing aggregator progress event")
        if not _has_event(payloads, "reporter_completed", "reporter"):
            raise AssertionError("missing reporter stream event")

        final_payload = next((payload for payload in payloads if payload.get("event") == "team_completed"), None)
        if final_payload is None:
            raise AssertionError("missing team_completed event")

        result = final_payload.get("result")
        if not isinstance(result, dict):
            raise AssertionError(f"unexpected final result payload: {result!r}")
        if result.get("orchestration", {}).get("status") != "broadcast_done":
            raise AssertionError(f"unexpected orchestration result: {result.get('orchestration')!r}")
        if result.get("report", {}).get("status") != "report_generated":
            raise AssertionError(f"unexpected report result: {result.get('report')!r}")
        if result.get("report", {}).get("total") != team.expected_result_count():
            raise AssertionError(f"unexpected report total: {result.get('report')!r}")

        multi_agent_logger.info(f"stream check passed with {len(payloads)} chunks")
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
        await team.runtime.stop()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
