# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import Tool, ToolCard

LOAD_QA_INDEX_TOOL_NAME = "load_qa_index"


class LoadQaIndexInput(BaseModel):
    qa_id: str = Field(..., description="要展开的历史 QA 块 ID，例如 qa_001")


def _model_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


class LoadQaIndexTool(Tool):
    TOOL_ID = "LoadQaIndexTool"

    def __init__(
        self,
        render_fn: Callable[..., Awaitable[Any] | Any],
        *,
        agent_id: str | None = None,
    ) -> None:
        tool_id = f"{self.TOOL_ID}_{agent_id}" if agent_id else self.TOOL_ID
        super().__init__(
            ToolCard(
                id=tool_id,
                name=LOAD_QA_INDEX_TOOL_NAME,
                description=(
                    "展开指定历史 QA 块内的索引与消息：返回该 qa_id 的「概览 + 逐 message/round 目录」；"
                    "未压缩块返回整块原文。不是选块工具——qa_id 须来自 Catalog 或用户指定；"
                    "目录中 [[OFFLOAD: handle=…]] 用 reload_original_context_messages 取回该 round 原文。"
                ),
                input_params=_model_schema(LoadQaIndexInput),
            )
        )
        self._render_fn = render_fn

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> dict[str, Any]:
        parsed = LoadQaIndexInput(**(inputs or {}))
        result = self._render_fn(parsed.qa_id, **kwargs)
        if hasattr(result, "__await__"):
            content = await result
        else:
            content = result
        return {"content": content, "qa_id": parsed.qa_id}

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {}
