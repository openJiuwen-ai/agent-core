"""将执行图证据接入 SymphonyFlowEngine。"""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from openjiuwen.symphony.orchestration.config import SymphonyFlowConfig

from .engine import SymphonyFlowEngine
from .models import TARGET_KIND_SKILL

_ENGINE_CACHE: dict[str, SymphonyFlowEngine] = {}
_ENGINE_CACHE_LOCK = threading.RLock()
LOGGER = logging.getLogger(__name__)


class SymphonyFlowObservationSink:
    """把任务成功且边成功的执行图写入 FlowEngine 并触发蒸馏。"""

    def __init__(self, engine: SymphonyFlowEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> SymphonyFlowEngine:
        """Return the shared engine, primarily for diagnostics and tests."""

        return self._engine

    async def submit(self, submission: Any) -> None:
        """接收执行图 submission；只沉淀任务成功且边成功的执行证据。"""

        execution_graph = getattr(submission, "execution_graph", None)
        projected = _successful_execution_graph(execution_graph)
        if projected is None or not self._engine.ingest(projected):
            return

        report = await self._engine.distill()
        recipe_ids = list(
            dict.fromkeys(
                [
                    *getattr(report, "recipes_saved", []),
                    *getattr(report, "recipes_unchanged", []),
                ]
            )
        )
        for recipe_id in recipe_ids:
            recipe = self._engine.get_recipe(recipe_id)
            if recipe is None:
                continue
            self._engine.prepare_install(
                recipe_id,
                target_kind=TARGET_KIND_SKILL,
            )


def _successful_execution_graph(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("outcome") != "success":
        return None
    graph = value.get("graph")
    if not isinstance(graph, dict):
        return None
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict):
        return None
    edges = [
        deepcopy(edge)
        for edge in graph.get("edges") or []
        if isinstance(edge, dict) and isinstance(edge.get("metadata"), dict) and edge["metadata"].get("success") is True
    ]
    if not edges:
        return None
    endpoint_ids: set[str] = set()
    for edge in edges:
        for endpoint in (edge.get("source"), edge.get("target")):
            if str(endpoint or "").strip():
                endpoint_ids.add(str(endpoint))
    projected = deepcopy(value)
    projected["graph"]["edges"] = edges
    projected["graph"]["nodes"] = {
        node_id: deepcopy(node) for node_id, node in nodes.items() if str(node_id) in endpoint_ids
    }
    return projected


def build_symphony_flow_observation_sink(
    flow_dir: str | Path,
    *,
    config: SymphonyFlowConfig | None = None,
) -> SymphonyFlowObservationSink | None:
    """Build a process-shared sink for the given flow directory.

    Returns ``None`` when the flow gate is disabled or the engine cannot be
    initialized; engines are cached per flow directory.
    """

    resolved_config = config or SymphonyFlowConfig()
    if not resolved_config.enabled:
        return None

    resolved_dir = Path(flow_dir).resolve()
    cache_key = str(resolved_dir)
    with _ENGINE_CACHE_LOCK:
        engine = _ENGINE_CACHE.get(cache_key)
        if engine is None:
            try:
                engine = SymphonyFlowEngine(resolved_dir, config=resolved_config)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Symphony FlowEngine initialization failed: %s", exc)
                return None
            _ENGINE_CACHE[cache_key] = engine
    return SymphonyFlowObservationSink(engine)
