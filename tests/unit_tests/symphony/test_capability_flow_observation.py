from __future__ import annotations

from types import SimpleNamespace

import pytest

import openjiuwen.symphony.flow.observation as observation_module
from openjiuwen.symphony.flow.engine import SymphonyFlowEngine
from openjiuwen.symphony.flow.observation import (
    SymphonyFlowObservationSink,
    build_symphony_flow_observation_sink,
)
from openjiuwen.symphony.orchestration import SymphonyFlowConfig


class FakeFlowEngine:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.distill_calls = 0

    def ingest(self, payload: dict[str, object]) -> bool:
        self.payloads.append(payload)
        return True

    async def distill(self) -> None:
        self.distill_calls += 1


@pytest.mark.asyncio
async def test_sink_ingests_execution_graph_only() -> None:
    engine = FakeFlowEngine()
    sink = SymphonyFlowObservationSink(engine)  # type: ignore[arg-type]
    execution_graph = {
        "trace_id": "trace-1",
        "outcome": "success",
        "graph": {
            "nodes": {"skill:a": {}, "skill:b": {}, "skill:c": {}},
            "edges": [
                {
                    "source": "skill:a",
                    "target": "skill:b",
                    "metadata": {"success": True},
                },
                {
                    "source": "skill:b",
                    "target": "skill:c",
                    "metadata": {"success": False},
                },
            ],
        },
    }

    await sink.submit(
        SimpleNamespace(
            planned_graph={"nodes": {"skill:planned": {}}},
            execution_graph=execution_graph,
        )
    )

    assert len(engine.payloads) == 1
    assert engine.payloads[0]["graph"]["edges"] == [execution_graph["graph"]["edges"][0]]
    assert set(engine.payloads[0]["graph"]["nodes"]) == {"skill:a", "skill:b"}
    assert engine.distill_calls == 1


@pytest.mark.asyncio
async def test_sink_ignores_empty_execution_graph() -> None:
    engine = FakeFlowEngine()
    sink = SymphonyFlowObservationSink(engine)  # type: ignore[arg-type]

    await sink.submit(SimpleNamespace(execution_graph={}))
    await sink.submit(SimpleNamespace(execution_graph=None))

    assert engine.payloads == []


@pytest.mark.asyncio
async def test_sink_ignores_failed_outcome_and_graph_without_success_edges() -> None:
    engine = FakeFlowEngine()
    sink = SymphonyFlowObservationSink(engine)  # type: ignore[arg-type]
    graph = {
        "nodes": {"skill:a": {}, "skill:b": {}},
        "edges": [
            {
                "source": "skill:a",
                "target": "skill:b",
                "metadata": {"success": False},
            }
        ],
    }

    await sink.submit(SimpleNamespace(execution_graph={"outcome": "failed", "graph": graph}))
    await sink.submit(SimpleNamespace(execution_graph={"outcome": "success", "graph": graph}))

    assert engine.payloads == []
    assert engine.distill_calls == 0


def test_factory_disabled_returns_none(tmp_path) -> None:
    config = SymphonyFlowConfig(enabled=False)

    assert build_symphony_flow_observation_sink(tmp_path / "flow", config=config) is None


def test_factory_reuses_engine_for_same_flow_dir(tmp_path) -> None:
    first = build_symphony_flow_observation_sink(tmp_path / "flow")
    second = build_symphony_flow_observation_sink(tmp_path / "flow")

    assert first is not None
    assert second is not None
    assert first.engine is second.engine


def test_factory_engine_initialization_failure_returns_none(monkeypatch, tmp_path) -> None:
    class BrokenFlowEngine:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise OSError("flow store unavailable")

    monkeypatch.setattr(observation_module, "SymphonyFlowEngine", BrokenFlowEngine)

    assert build_symphony_flow_observation_sink(tmp_path / "flow") is None


@pytest.mark.asyncio
async def test_sink_persists_normalized_execution_graph_and_is_idempotent(
    tmp_path,
) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    sink = SymphonyFlowObservationSink(engine)
    payload = {
        "trace_id": "trace-1",
        "query": "q",
        "outcome": "success",
        "graph": {
            "id": "execution-1",
            "type": "execution_graph",
            "directed": True,
            "nodes": {
                "skill:a": {
                    "label": "skill",
                    "metadata": {"version": "1"},
                },
                "skill:b": {
                    "label": "skill",
                    "metadata": {"version": "1"},
                },
            },
            "edges": [
                {
                    "source": "skill:a",
                    "target": "skill:b",
                    "relation": "can_feed",
                    "metadata": {"success": True},
                }
            ],
        },
    }

    await sink.submit(SimpleNamespace(execution_graph=payload))
    await sink.submit(SimpleNamespace(execution_graph=payload))

    assert len(engine.store.read_evidence()) == 1


@pytest.mark.asyncio
async def test_default_demo_thresholds_create_verified_recipe_from_one_trace(
    tmp_path,
) -> None:
    sink = build_symphony_flow_observation_sink(tmp_path / "flow")
    assert sink is not None
    payload = {
        "trace_id": "trace-single-demo",
        "query": "q",
        "outcome": "success",
        "graph": {
            "id": "execution-single-demo",
            "type": "execution_graph",
            "directed": True,
            "nodes": {
                "skill:a": {"label": "skill", "metadata": {"version": "1"}},
                "skill:b": {"label": "skill", "metadata": {"version": "1"}},
            },
            "edges": [
                {
                    "source": "skill:a",
                    "target": "skill:b",
                    "relation": "can_feed",
                    "metadata": {"success": True},
                }
            ],
        },
    }

    await sink.submit(
        SimpleNamespace(
            submission_id="submission-demo",
            planned_graph=None,
            execution_graph=payload,
        )
    )

    recipe_ids = sink.engine.list_recipes()
    assert len(recipe_ids) == 1
    recipe = sink.engine.get_recipe(recipe_ids[0])
    assert recipe is not None
    assert recipe.status == "active"
    assert recipe.grade == "verified"
    packages = sink.engine.store.list_packages()
    assert len(packages) == 1
    assert packages[0]["target_kind"] == "skill"
