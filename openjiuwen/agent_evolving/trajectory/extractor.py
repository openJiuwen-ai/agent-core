# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TrajectoryExtractor: offline trajectory extractor.

Extracts complete Trajectory from Session.tracer() spans.
Uses TrajectoryBuilder internally for assembly.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.trajectory.builder import TrajectoryBuilder
from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    StepDetail,
    StepKind,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)


def _dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert datetime to milliseconds since epoch."""
    return int(dt.timestamp() * 1000) if dt else None


class TrajectoryExtractor:
    """Extract Trajectory from Session.tracer() spans."""

    def __init__(self, resource_manager: Any = None) -> None:
        """Initialize extractor.

        Args:
            resource_manager: Used to query Tool metadata
        """
        self._resource_manager = resource_manager

    def extract(
        self,
        session: Any,
        case_id: Optional[str] = None,
    ) -> Trajectory:
        """Extract trajectory using TrajectoryBuilder.

        Args:
            session: Agent Session object
            case_id: Optional case identifier for the trajectory

        Returns:
            Assembled Trajectory
        """
        tracer = self._get_tracer(session)
        spans = self._get_agent_spans(tracer)

        effective_case_id = case_id or "unknown"
        builder = TrajectoryBuilder(
            session_id=effective_case_id,
            source="offline",
            case_id=effective_case_id,
        )

        for span in spans:
            step = self._build_step(span)
            builder.record_step(step)

        return builder.build()

    def _build_step(self, span: Any) -> TrajectoryStep:
        """Convert Span to TrajectoryStep."""
        kind = self._classify_kind(span)
        base_meta = getattr(span, "meta_data", None) or {}
        detail = self._build_detail(span, kind)
        full_meta = self._build_meta(span, base_meta, kind, detail)

        return TrajectoryStep(
            kind=kind,
            error=getattr(span, "error", None),
            start_time_ms=_dt_to_ms(getattr(span, "start_time", None)),
            end_time_ms=_dt_to_ms(getattr(span, "end_time", None)),
            detail=detail,
            meta=full_meta,
        )

    def _build_detail(
        self, span: Any, kind: StepKind
    ) -> Optional[StepDetail]:
        """Build StepDetail from span data."""
        if kind == "llm":
            return self._build_llm_detail(span)
        elif kind == "tool":
            return self._build_tool_detail(span)
        return None

    def _build_llm_detail(self, span: Any) -> Optional[LLMCallDetail]:
        """Build LLMCallDetail from span.on_invoke_data."""
        on_invoke = getattr(span, "on_invoke_data", None) or []
        if not on_invoke:
            return None

        llm_params = None
        for record in on_invoke:
            if isinstance(record, dict) and "llm_params" in record:
                llm_params = record["llm_params"]
                break

        if not llm_params:
            return None

        outputs = self._extract_outputs(span)
        response = self._parse_llm_response(outputs)

        usage = None
        if response and isinstance(response, dict):
            usage = response.get("usage")
        if not usage and isinstance(llm_params, dict):
            usage = llm_params.get("usage")

        return LLMCallDetail(
            model=llm_params.get("model", ""),
            messages=llm_params.get("messages", []),
            tools=llm_params.get("tools"),
            response=response,
            usage=usage,
        )

    def _build_tool_detail(self, span: Any) -> ToolCallDetail:
        """Build ToolCallDetail from span data."""
        tool_name = getattr(span, "name", "") or ""
        tool_description: Optional[str] = None
        tool_schema: Optional[Dict[str, Any]] = None

        if self._resource_manager is not None and tool_name:
            try:
                tool_info = self._resource_manager.get_tool_infos(tool_name)
                if tool_info:
                    tool_description = tool_info.description or None
                    params = tool_info.parameters
                    if params:
                        tool_schema = (
                            params
                            if isinstance(params, dict)
                            else params.model_json_schema()
                        )
            except Exception:
                logger.exception(
                    "[TrajectoryExtractor] failed to get tool info for %s",
                    tool_name,
                )

        return ToolCallDetail(
            tool_name=tool_name,
            call_args=self._extract_inputs(span),
            call_result=self._extract_outputs(span),
            tool_description=tool_description,
            tool_schema=tool_schema,
        )

    def _build_meta(
        self,
        span: Any,
        base_meta: Dict[str, Any],
        kind: StepKind,
        detail: Optional[StepDetail],
    ) -> Dict[str, Any]:
        """Build meta with operator_id and backup I/O."""
        meta = copy.deepcopy(base_meta)

        meta["operator_id"] = self._get_operator_id(span, base_meta)

        agent_id = getattr(span, "agent_id", None) or base_meta.get("agent_id")
        if agent_id:
            meta["agent_id"] = agent_id

        if kind not in ("llm", "tool"):
            meta["inputs"] = self._extract_inputs(span)
            meta["outputs"] = self._extract_outputs(span)

        meta["span_name"] = getattr(span, "name", None)
        meta["invoke_id"] = getattr(span, "invoke_id", None)
        meta["parent_invoke_id"] = getattr(span, "parent_invoke_id", None)
        meta["child_invokes"] = getattr(span, "child_invokes_id", None)

        return meta

    @staticmethod
    def _parse_llm_response(outputs: Any) -> Optional[Dict[str, Any]]:
        """Parse LLM response from outputs."""
        if isinstance(outputs, dict):
            return outputs
        if hasattr(outputs, "model_dump"):
            return outputs.model_dump()
        if hasattr(outputs, "__dict__"):
            return outputs.__dict__
        return None

    @staticmethod
    def _get_tracer(session: Any) -> Any:
        """Get tracer from session."""
        tracer = getattr(session, "tracer", None)
        return tracer() if callable(tracer) else tracer

    @staticmethod
    def _get_agent_spans(tracer: Any) -> List[Any]:
        """Extract agent spans from tracer."""
        if tracer is None:
            return []
        agent_sm = getattr(tracer, "tracer_agent_span_manager", None)
        if agent_sm is None:
            return []
        get_spans = getattr(agent_sm, "get_all_spans", None)
        if not callable(get_spans):
            return []
        result = get_spans()
        return result if isinstance(result, list) else []

    @staticmethod
    def _classify_kind(span: Any) -> StepKind:
        """Classify span kind based on invoke_type."""
        invoke_type = getattr(span, "invoke_type", None)
        invoke_str = str(invoke_type) if invoke_type else ""

        if invoke_str == "plugin":
            return "tool"  # type: ignore[return-value]
        if invoke_str in ("llm", "workflow", "memory"):
            return invoke_str  # type: ignore[return-value]
        return "agent"  # type: ignore[return-value]

    @staticmethod
    def _get_operator_id(span: Any, meta: Dict[str, Any]) -> Optional[str]:
        """Extract operator ID from span attributes."""
        return (
            getattr(span, "operator_id", None)
            or getattr(span, "llm_call_id", None)
            or meta.get("operator_id")
            or getattr(span, "name", None)
        )

    @staticmethod
    def _extract_inputs(span: Any) -> Any:
        """Extract inputs from span, unwrapping if nested."""
        raw = getattr(span, "inputs", None)
        if isinstance(raw, dict) and "inputs" in raw:
            return raw["inputs"]
        return raw

    @staticmethod
    def _extract_outputs(span: Any) -> Any:
        """Extract outputs from span, unwrapping if nested."""
        raw = getattr(span, "outputs", None)
        if isinstance(raw, dict) and "outputs" in raw:
            return raw["outputs"]
        return raw
