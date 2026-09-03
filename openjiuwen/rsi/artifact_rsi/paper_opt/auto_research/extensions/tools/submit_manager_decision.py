"""Structured submission tool for the manager decision agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.foundation.tool.base import Tool, ToolCard

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import ManagerDecision


class SubmitManagerDecisionTool(Tool):
    """Accept exactly one validated ManagerDecision per routing round."""

    def __init__(self) -> None:
        card = ToolCard(
            id="submit_manager_decision",
            name="submit_manager_decision",
            description=(
                "Submit the next control decision for this manager round. "
                "Call exactly once. Use EXECUTE with a bounded contract, or "
                "DONE / BLOCKED with no contract. Do not invent artifact paths, "
                "run IDs, or module constructor arguments."
            ),
            input_params=ManagerDecision.model_json_schema(),
            parallel_safe=False,
            idempotent=False,
        )
        super().__init__(card)
        self._pending_key: tuple[str, str] | None = None
        self._submission: ManagerDecision | None = None
        self._submission_key: tuple[str, str] | None = None

    def reset(self, *, session_id: str, request_id: str) -> None:
        self._pending_key = (session_id, request_id)
        self._submission = None
        self._submission_key = None

    def get_submission(
        self,
        *,
        session_id: str,
        request_id: str,
    ) -> ManagerDecision | None:
        key = (session_id, request_id)
        if self._submission is None or self._submission_key != key:
            return None
        return self._submission

    def require_submission(
        self,
        *,
        session_id: str,
        request_id: str,
    ) -> ManagerDecision:
        decision = self.get_submission(session_id=session_id, request_id=request_id)
        if decision is None:
            raise RuntimeError(
                "submit_manager_decision was not called with a valid decision "
                f"for session={session_id!r} request={request_id!r}"
            )
        return decision

    async def invoke(self, inputs: Any, **kwargs) -> dict[str, Any]:
        if self._pending_key is None:
            raise RuntimeError("submit_manager_decision was not reset for the current round")
        if self._submission is not None and self._submission_key == self._pending_key:
            raise RuntimeError("submit_manager_decision may be called only once per round")
        if isinstance(inputs, ManagerDecision):
            decision = inputs
        elif isinstance(inputs, dict):
            decision = ManagerDecision.model_validate(inputs)
        else:
            raise TypeError("submit_manager_decision expects ManagerDecision fields")
        self._submission = decision
        self._submission_key = self._pending_key
        session_id, request_id = self._pending_key
        return {
            "success": True,
            "message": "Manager decision accepted by host.",
            "session_id": session_id,
            "request_id": request_id,
            "signal": decision.signal,
        }

    async def stream(self, inputs: Any, **kwargs) -> AsyncIterator[dict[str, Any]]:
        result = await self.invoke(inputs, **kwargs)
        yield result


__all__ = ["SubmitManagerDecisionTool"]
