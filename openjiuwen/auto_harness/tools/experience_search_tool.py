# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ExperienceSearchTool — readonly experience search tool."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
)
from openjiuwen.harness.prompts.tools import (
    build_tool_card,
    register_tool_provider,
)
from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

TOOL_NAME = "experience_search"


class ExperienceSearchMetadataProvider(
    ToolMetadataProvider,
):
    """Metadata provider for ExperienceSearchTool."""

    def get_name(self) -> str:
        return TOOL_NAME

    def get_description(
        self,
        language: str = "cn",
    ) -> str:
        descriptions = {
            "cn": (
                "搜索历史经验记录。输入关键词，返回相关的"
                "成功/失败/洞察经验，帮助避免重复错误、"
                "复用已验证的方案。"
            ),
            "en": (
                "Search historical experiences by keyword and return "
                "relevant success/failure/insight entries."
            ),
        }
        return descriptions.get(language, descriptions["cn"])

    def get_input_params(
        self,
        language: str = "cn",
    ) -> Dict[str, Any]:
        query_desc = {
            "cn": "搜索关键词或主题描述",
            "en": "Search keywords or topic description",
        }
        limit_desc = {
            "cn": "最大返回条数，默认 5",
            "en": "Maximum number of returned results, default 5",
        }
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": query_desc.get(
                        language,
                        query_desc["cn"],
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": limit_desc.get(
                        language,
                        limit_desc["cn"],
                    ),
                    "default": 5,
                },
            },
            "required": ["query"],
        }


register_tool_provider(
    ExperienceSearchMetadataProvider()
)


class ExperienceSearchTool(Tool):
    """Readonly experience search tool."""

    def __init__(
        self,
        experience_dir: str,
        agent_id: Optional[str] = None,
        language: str = "cn",
    ) -> None:
        super().__init__(
            build_tool_card(
                TOOL_NAME,
                "ExperienceSearchTool",
                language,
                agent_id=agent_id,
            )
        )
        self._experience_dir = experience_dir

    async def invoke(
        self,
        inputs: Dict[str, Any],
        **kwargs: Any,
    ) -> ToolOutput:
        query = inputs.get("query", "")
        limit = inputs.get("limit", 5)

        if not query:
            return ToolOutput(
                success=False,
                error="query 参数不能为空",
            )

        try:
            store = ExperienceStore(
                self._experience_dir
            )
            results = await store.search(
                query,
                top_k=limit,
            )
            return ToolOutput(
                success=True,
                data=[
                    {
                        "type": exp.type.value,
                        "topic": exp.topic,
                        "summary": exp.summary,
                        "outcome": exp.outcome,
                    }
                    for exp in results
                ],
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc)[:200],
            )

    async def stream(
        self,
        inputs: Dict[str, Any],
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, **kwargs)
