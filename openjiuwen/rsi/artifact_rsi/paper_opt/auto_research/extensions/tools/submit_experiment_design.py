"""Structured submission tool for Experiment Design Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.foundation.tool.base import Tool, ToolCard

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentDesignDraft


class SubmitExperimentDesignTool(Tool):
    """Accept exactly one validated ExperimentDesignDraft per user turn."""

    def __init__(self) -> None:
        card = ToolCard(
            id="submit_experiment_design",
            name="submit_experiment_design",
            description=(
                "Submit the final experiment design draft for this turn. "
                "Call exactly once after gathering evidence. "
                "Do not invent revision numbers, timestamps, output paths, "
                "or provenance fields — the host owns those."
            ),
            # Released openjiuwen serializes tool parameters as JSON for token
            # counting; pass a schema dict (built-in tools do the same).
            input_params=ExperimentDesignDraft.model_json_schema(),
            parallel_safe=False,
            idempotent=False,
        )
        super().__init__(card)
        self._pending_key: tuple[str, str] | None = None
        self._submission: ExperimentDesignDraft | None = None
        self._submission_key: tuple[str, str] | None = None

    def reset(self, *, session_id: str, request_id: str) -> None:
        """Prepare the tool for a new create/update turn."""
        self._pending_key = (session_id, request_id)
        self._submission = None
        self._submission_key = None

    def get_submission(
        self,
        *,
        session_id: str,
        request_id: str,
    ) -> ExperimentDesignDraft | None:
        key = (session_id, request_id)
        if self._submission is None or self._submission_key != key:
            return None
        return self._submission

    def require_submission(
        self,
        *,
        session_id: str,
        request_id: str,
    ) -> ExperimentDesignDraft:
        draft = self.get_submission(session_id=session_id, request_id=request_id)
        if draft is None:
            raise RuntimeError(
                "submit_experiment_design was not called with a valid draft "
                f"for session={session_id!r} request={request_id!r}"
            )
        return draft

    async def invoke(self, inputs: Any, **kwargs) -> dict[str, Any]:
        if self._pending_key is None:
            raise RuntimeError("submit_experiment_design was not reset for the current turn")

        if self._submission is not None and self._submission_key == self._pending_key:
            raise RuntimeError(
                "submit_experiment_design may be called only once per user turn"
            )

        if isinstance(inputs, ExperimentDesignDraft):
            draft = inputs
        elif isinstance(inputs, dict):
            draft = ExperimentDesignDraft.model_validate(inputs)
        else:
            raise TypeError(
                "submit_experiment_design expects ExperimentDesignDraft fields"
            )

        self._submission = draft
        self._submission_key = self._pending_key
        session_id, request_id = self._pending_key
        return {
            "success": True,
            "message": "Experiment design draft accepted by host.",
            "session_id": session_id,
            "request_id": request_id,
        }

    async def stream(self, inputs: Any, **kwargs) -> AsyncIterator[dict[str, Any]]:
        result = await self.invoke(inputs, **kwargs)
        yield result


__all__ = ["SubmitExperimentDesignTool"]
