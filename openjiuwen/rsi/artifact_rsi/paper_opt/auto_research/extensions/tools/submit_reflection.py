"""Structured submission tool for the Reflection Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import (
    ReflectionJudgment,
)


class SubmitReflectionTool(Tool):
    """Accept one validated reflection judgment for the current request."""

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                id="submit_reflection",
                name="submit_reflection",
                description=(
                    "Submit the structured scientific judgment for this experiment round. "
                    "Call exactly once after reading the preloaded evidence. "
                    "Do not write files; the host renders the markdown artifact."
                ),
                input_params=CallableSchemaExtractor.get_base_model_schema(ReflectionJudgment),
                parallel_safe=False,
                idempotent=False,
            )
        )
        self._request_id: str | None = None
        self._submission: ReflectionJudgment | None = None

    def reset(self, *, request_id: str) -> None:
        self._request_id = request_id
        self._submission = None

    def require_submission(self, *, request_id: str) -> ReflectionJudgment:
        if self._request_id != request_id or self._submission is None:
            raise RuntimeError(f"submit_reflection was not called for request={request_id!r}")
        return self._submission

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        if self._request_id is None:
            raise RuntimeError("submit_reflection was not reset for the current request")
        if self._submission is not None:
            raise RuntimeError("submit_reflection may be called only once per request")
        self._submission = (
            inputs
            if isinstance(inputs, ReflectionJudgment)
            else ReflectionJudgment.model_validate(inputs)
        )
        return {"success": True, "message": "Reflection judgment accepted by host."}

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield await self.invoke(inputs, **kwargs)


__all__ = ["SubmitReflectionTool"]
