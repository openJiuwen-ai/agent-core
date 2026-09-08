"""Runtime graph-to-flow submission contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.symphony import CombinationCandidate, EvolutionSubmitResult, SymphonyRuntime
from openjiuwen.symphony.observation import ObservationReceipt


def _planned_graph(*, include_snapshot: bool = True) -> dict:
    value = {
        "graph": {
            "id": "plan-1",
            "type": "planned_graph",
            "directed": True,
            "nodes": {},
            "edges": [],
        }
    }
    if include_snapshot:
        value["graph_snapshot"] = {
            "static_revision": "static-1",
            "observation_revision": "observation-1",
            "merged_revision": "merged-1",
        }
    return value


def _execution_graph(*, outcome: str = "success", include_snapshot: bool = True) -> dict:
    value = {
        "trace_id": "trace-1",
        "query": "summarize",
        "outcome": outcome,
        "graph": {
            "id": "execution-1",
            "type": "execution_graph",
            "directed": True,
            "nodes": {
                "skill:a": {
                    "label": "skill",
                    "metadata": {
                        "capability_type": "skill",
                        "version": "v1",
                        "content_hash": "sha256:a",
                        "input_ports": ["query"],
                        "output_ports": ["text"],
                    },
                },
                "skill:b": {
                    "label": "skill",
                    "metadata": {
                        "capability_type": "skill",
                        "version": "v1",
                        "content_hash": "sha256:b",
                        "input_ports": ["text"],
                        "output_ports": ["summary"],
                    },
                },
            },
            "edges": [
                {
                    "source": "skill:a",
                    "target": "skill:b",
                    "relation": "can_feed",
                    "metadata": {
                        "success": True,
                        "port_mappings": [{"source_output": "text", "target_input": "text"}],
                        "evidence_refs": ["trace-1#span=1", "trace-1#span=2"],
                    },
                }
            ],
        },
    }
    if include_snapshot:
        value["graph_snapshot"] = {
            "static_revision": "static-start",
            "observation_revision": "observation-start",
            "merged_revision": "merged-start",
        }
    return value


def _runtime(graph_engine: object, flow_engine: object | None) -> SymphonyRuntime:
    runtime = object.__new__(SymphonyRuntime)
    runtime.graph_engine = graph_engine
    runtime.flow_engine = flow_engine  # type: ignore[assignment]
    runtime.graph_scope_id = "workspace:test"
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_status", ["accepted", "audit_only", "duplicate"])
async def test_runtime_submits_graph_then_projects_success_edges_to_flow(receipt_status: str) -> None:
    receipt = ObservationReceipt(
        evidence_id="execution-1",
        graph_scope_id="workspace:test",
        sequence=1,
        status=receipt_status,
    )
    captured = []

    def submit_observation(value):
        captured.append(value)
        return receipt

    graph_engine = SimpleNamespace(submit_observation=submit_observation)
    flow_engine = SimpleNamespace(submit=AsyncMock(return_value=(CombinationCandidate("recipe-1", 1),)))
    runtime = _runtime(graph_engine, flow_engine)

    result = await runtime.submit_evolution(
        _planned_graph(),
        _execution_graph(),
        session_id="session-1",
        capture_mode="agent",
    )

    assert result == EvolutionSubmitResult(receipt, (CombinationCandidate("recipe-1", 1),))
    submitted = flow_engine.submit.await_args.args[0]
    assert submitted["graph"]["id"] == "execution-1"
    assert len(submitted["graph"]["edges"]) == 1
    assert captured[0].evidence_id == "execution-1"


@pytest.mark.asyncio
async def test_runtime_builds_canonical_graph_observation() -> None:
    captured = []
    receipt = ObservationReceipt(
        evidence_id="execution-1",
        graph_scope_id="workspace:test",
        sequence=1,
        status="accepted",
    )

    def submit(value):
        captured.append(value)
        return receipt

    runtime = _runtime(SimpleNamespace(submit_observation=submit), None)
    result = await runtime.submit_evolution(
        _planned_graph(),
        _execution_graph(),
        session_id="session-1",
        capture_mode="team",
    )

    assert result.graph_receipt is receipt
    assert result.new_candidates == ()
    value = captured[0]
    assert value.evidence_id == "execution-1"
    assert value.graph_scope_id == "workspace:test"
    assert value.trace.session_id == "session-1"
    assert value.trace.capture_mode == "team"
    assert value.task.task_cluster_id is None
    assert value.graph_snapshot.static_revision == "static-1"
    assert set(value.capabilities) == {"skill:a", "skill:b"}


@pytest.mark.asyncio
async def test_runtime_uses_execution_start_snapshot_when_plan_has_none() -> None:
    captured = []

    def submit(value):
        captured.append(value)
        return ObservationReceipt(
            evidence_id="execution-1",
            graph_scope_id="workspace:test",
            sequence=1,
            status="duplicate",
        )

    graph_engine = SimpleNamespace(submit_observation=submit)
    runtime = _runtime(graph_engine, None)

    await runtime.submit_evolution(
        _planned_graph(include_snapshot=False),
        _execution_graph(),
        session_id="session-1",
        capture_mode="agent",
    )

    assert captured[0].graph_snapshot.static_revision == "static-start"


@pytest.mark.asyncio
async def test_runtime_fails_closed_without_any_invoke_start_snapshot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph_engine = SimpleNamespace(submit_observation=Mock())
    runtime = _runtime(graph_engine, None)

    result = await runtime.submit_evolution(
        None,
        _execution_graph(include_snapshot=False),
        session_id="session-1",
        capture_mode="agent",
    )

    assert result == EvolutionSubmitResult(None, ())
    assert "ValueError" in caplog.text
    graph_engine.submit_observation.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_capture_mode(caplog: pytest.LogCaptureFixture) -> None:
    graph_engine = SimpleNamespace(submit_observation=Mock())
    runtime = _runtime(graph_engine, None)

    result = await runtime.submit_evolution(
        _planned_graph(),
        _execution_graph(),
        session_id="session-1",
        capture_mode="invalid",  # type: ignore[arg-type]
    )

    assert result == EvolutionSubmitResult(None, ())
    assert "ValueError" in caplog.text
    graph_engine.submit_observation.assert_not_called()


@pytest.mark.asyncio
async def test_graph_failure_prevents_flow_submission(caplog: pytest.LogCaptureFixture) -> None:
    flow_engine = SimpleNamespace(submit=AsyncMock())

    def fail(value):
        del value
        raise RuntimeError("boom")

    runtime = _runtime(SimpleNamespace(submit_observation=fail), flow_engine)

    result = await runtime.submit_evolution(
        _planned_graph(),
        _execution_graph(),
        session_id="session-1",
        capture_mode="agent",
    )

    assert result == EvolutionSubmitResult(None, ())
    assert "RuntimeError" in caplog.text
    assert "boom" not in caplog.text
    flow_engine.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_success_task_does_not_enter_flow() -> None:
    receipt = ObservationReceipt(
        evidence_id="execution-1",
        graph_scope_id="workspace:test",
        sequence=1,
        status="accepted",
    )
    flow_engine = SimpleNamespace(submit=AsyncMock())
    runtime = _runtime(
        SimpleNamespace(submit_observation=lambda value: receipt),
        flow_engine,
    )

    result = await runtime.submit_evolution(
        None,
        _execution_graph(outcome="partial"),
        session_id="session-1",
        capture_mode="agent",
    )

    assert result.new_candidates == ()
    flow_engine.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_flow_failure_isolated_after_graph_receipt(caplog: pytest.LogCaptureFixture) -> None:
    receipt = ObservationReceipt(
        evidence_id="execution-1",
        graph_scope_id="workspace:test",
        sequence=1,
        status="accepted",
    )
    flow_engine = SimpleNamespace(submit=AsyncMock(side_effect=RuntimeError("private")))
    runtime = _runtime(SimpleNamespace(submit_observation=lambda value: receipt), flow_engine)

    result = await runtime.submit_evolution(
        _planned_graph(),
        _execution_graph(),
        session_id="session-1",
        capture_mode="agent",
    )

    assert result == EvolutionSubmitResult(receipt, ())
    assert "RuntimeError" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_runtime_aclose_closes_flow_before_graph_and_isolates_flow_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []

    async def close_flow() -> None:
        order.append("flow")
        raise RuntimeError("private")

    graph_engine = SimpleNamespace(close=Mock(side_effect=lambda: order.append("graph")))
    runtime = _runtime(graph_engine, SimpleNamespace(close=close_flow))

    await runtime.aclose()

    assert order == ["flow", "graph"]
    assert "RuntimeError" in caplog.text
    assert "private" not in caplog.text


def test_runtime_sync_close_requires_aclose_when_flow_is_configured() -> None:
    graph_engine = SimpleNamespace(close=Mock())
    runtime = _runtime(graph_engine, SimpleNamespace(close=AsyncMock()))

    with pytest.raises(RuntimeError, match="aclose"):
        runtime.close()

    graph_engine.close.assert_not_called()


def test_runtime_exposes_invoke_start_snapshot_provider() -> None:
    snapshot = SimpleNamespace(
        static_revision="static-start",
        observation_revision="observation-start",
        merged_revision="merged-start",
    )
    runtime = _runtime(SimpleNamespace(get_snapshot=Mock(return_value=snapshot)), None)

    result = runtime.capture_graph_snapshot()

    assert result == {
        "static_revision": "static-start",
        "observation_revision": "observation-start",
        "merged_revision": "merged-start",
    }
