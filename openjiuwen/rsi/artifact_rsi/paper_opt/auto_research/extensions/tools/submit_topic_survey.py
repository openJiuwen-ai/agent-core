"""Structured submission tool for the Topic Survey Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.foundation.tool.base import Tool, ToolCard

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import TopicSurveyDraft


class SubmitTopicSurveyTool(Tool):
    """Accept one validated topic-survey draft for a survey request."""

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                id="submit_topic_survey",
                name="submit_topic_survey",
                description=(
                    "Submit the final topic survey after every listed source has been "
                    "saved under the host-provided download directory. Call exactly once."
                ),
                input_params=TopicSurveyDraft.model_json_schema(),
                parallel_safe=False,
                idempotent=False,
            )
        )
        self._request_id: str | None = None
        self._submission: TopicSurveyDraft | None = None

    def reset(self, *, request_id: str) -> None:
        self._request_id = request_id
        self._submission = None

    def require_submission(self, *, request_id: str) -> TopicSurveyDraft:
        if self._request_id != request_id or self._submission is None:
            raise RuntimeError(f"submit_topic_survey was not called for request={request_id!r}")
        return self._submission

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        if self._request_id is None:
            raise RuntimeError("submit_topic_survey was not reset for the current request")
        if self._submission is not None:
            raise RuntimeError("submit_topic_survey may be called only once per request")
        self._submission = (
            inputs if isinstance(inputs, TopicSurveyDraft) else TopicSurveyDraft.model_validate(inputs)
        )
        return {"success": True, "message": "Topic survey accepted by host."}

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield await self.invoke(inputs, **kwargs)


__all__ = ["SubmitTopicSurveyTool"]
