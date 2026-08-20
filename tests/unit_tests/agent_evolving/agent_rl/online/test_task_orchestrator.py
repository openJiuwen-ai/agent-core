from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_evolving.agent_rl.online.task_orchestrator import (
    OnlineRLTaskOrchestrator,
    RLTaskAttemptSpec,
)
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    AgentRuntimeBinding,
    ModelProtocol,
    Task,
)


def _attempt_spec(output_dir: Path) -> RLTaskAttemptSpec:
    session_id = "collection-session-9"
    return RLTaskAttemptSpec(
        training_key="train-a",
        collection_session_id=session_id,
        tokenizer_revision="tokenizer-r1",
        template_revision="template-r2",
        runtime=AgentRuntimeBinding(
            attempt_id="attempt-9",
            agent_session_id="00000000-0000-4000-8000-000000000009",
            protocol=ModelProtocol.ANTHROPIC_MESSAGES,
            model_base_url="http://gateway:18080",
            api_key="scoped",
            custom_headers={"x-session-id": session_id, "x-user-id": "train-a"},
            requested_model="model-a",
            policy_version="policy-v2",
        ),
        output_dir=output_dir,
    )


@pytest.mark.asyncio
async def test_task_attempt_aborts_collection_and_stops_environment_on_agent_failure(tmp_path: Path) -> None:
    manager = SimpleNamespace(
        create_session=AsyncMock(),
        finalize_session=AsyncMock(),
        abort_session=AsyncMock(),
        submit_task_reward=AsyncMock(),
    )
    environment = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    benchmark = SimpleNamespace(prepare_environment=AsyncMock(), evaluate=AsyncMock())
    agent = SimpleNamespace(
        set_logs_dir=lambda _: None,
        setup=AsyncMock(return_value=True),
        run=AsyncMock(side_effect=RuntimeError("agent failed")),
        name=lambda: "fake_external",
    )

    with pytest.raises(RuntimeError, match="agent failed"):
        await OnlineRLTaskOrchestrator(
            collection_manager=manager,
        ).run_attempt(
            _attempt_spec(tmp_path / "attempt"),
            Task(task_id="task-9", instruction="make it pass"),
            agent=agent,
            benchmark=benchmark,
            environment=environment,
        )

    manager.abort_session.assert_awaited_once_with("collection-session-9")
    manager.finalize_session.assert_not_awaited()
    benchmark.evaluate.assert_not_awaited()
    manager.submit_task_reward.assert_not_awaited()
    environment.stop.assert_awaited_once()
