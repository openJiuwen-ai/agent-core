# coding: utf-8
"""LiteratureGroundingRail —— 文献锚定 Rail（框架贡献点 1）。

职责:
1. 向 Agent 注册 ``arxiv_search`` / ``arxiv_fetch`` / ``register_citations`` 三个工具，
   让科研 Agent 能检索、核验并登记真实文献。
2. 维护一份 ``CitationRegistry``（落盘在工作区），写作阶段通过
   ``validate_citations`` 强制只有注册过的 bibtex key 才可被引用，从机制上
   阻断"幻觉参考文献"。

Rail 只新增工具、不改写 prompt，符合 Harness 的 Rail 扩展范式。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from openjiuwen.contrib.scholar_rails.arxiv_tool import (
    CitationRegistry,
    fetch_arxiv_by_id,
    search_arxiv,
)
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.harness.rails.base import DeepAgentRail


class LiteratureGroundingRail(DeepAgentRail):
    """把可验证的 arXiv 文献能力挂到 DeepAgent 上。

    Args:
        registry_path: 引用注册表落盘路径（工作区内的 JSON 文件）。
        max_results:   arxiv_search 默认返回条数。
    """

    priority = 40

    def __init__(self, registry_path: str | Path, *, max_results: int = 8) -> None:
        super().__init__()
        self._registry_path = Path(registry_path)
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_results = max_results
        self._registry = (
            CitationRegistry.load(self._registry_path) if self._registry_path.exists() else CitationRegistry()
        )
        self._tools: list[Any] = []

    # ------------------------------------------------------------- lifecycle
    def init(self, agent) -> None:  # noqa: D102
        super().init(agent) if hasattr(super(), "init") else None
        self._tools = self._build_tools()
        for tool in self._tools:
            agent.ability_manager.add_ability(tool.card, tool)
            if agent.ability_manager.get(tool.card.name) is not tool.card:
                raise RuntimeError(f"literature tool registration failed: {tool.card.name}")

    def uninit(self, agent) -> None:  # noqa: D102
        for tool in self._tools:
            # Do not remove another rail's later replacement of the same name.
            if agent.ability_manager.get(tool.card.name) is tool.card:
                agent.ability_manager.remove_ability(tool.card.name)
        self._tools = []
        super().uninit(agent) if hasattr(super(), "uninit") else None

    # ---------------------------------------------------------------- tools
    def _build_tools(self) -> list[Any]:
        """构建四个底层能力函数对应的原生 Tool 对象。

        openJiuwen 的 Tool 需要 ToolCard + 可调用入口。这里用最小封装，
        通过 ``ability_manager.add_ability(card, tool)`` 注册元数据和执行实例。
        """
        # 嵌套闭包经局部绑定访问内部状态，避免跨对象受保护成员访问。
        registry = self._registry
        registry_path = self._registry_path
        default_max_results = self._max_results

        def _fn_tool(name: str, description: str, parameters: dict, fn):
            async def invoke(**kwargs):
                # arXiv rate limiting and network I/O must not block the agent loop.
                return await asyncio.to_thread(fn, **kwargs)

            return LocalFunction(
                ToolCard(
                    name=name,
                    description=description,
                    input_params=parameters,
                    parallel_safe=False,
                    idempotent=True,
                ),
                invoke,
            )

        def arxiv_search(query: str, max_results: Optional[int] = None) -> str:
            records = search_arxiv(query, max_results=max_results or default_max_results)
            return json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2)

        def arxiv_fetch(arxiv_id: str) -> str:
            rec = fetch_arxiv_by_id(arxiv_id)
            return json.dumps(rec.to_dict() if rec else {}, ensure_ascii=False, indent=2)

        def register_citations(arxiv_ids: list[str]) -> str:
            """核验并登记一组 arxiv id，返回分配的 bibtex key 列表。"""
            keys = []
            for aid in arxiv_ids:
                rec = fetch_arxiv_by_id(aid)
                if rec is None:
                    continue
                keys.append(registry.add_record(rec))
            registry.persist(registry_path)
            return json.dumps({"registered": keys}, ensure_ascii=False)

        def list_citable_keys() -> str:
            return json.dumps({"citable_keys": registry.keys()}, ensure_ascii=False)

        return [
            _fn_tool(
                "arxiv_search",
                "Search arXiv for real, verifiable papers. Returns JSON list of records "
                "with arxiv_id/title/authors/year/abstract. Use ONLY these records as citations.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "arXiv query string"},
                        "max_results": {"type": "integer", "description": "max results", "default": 8},
                    },
                    "required": ["query"],
                },
                arxiv_search,
            ),
            _fn_tool(
                "arxiv_fetch",
                "Fetch a single arXiv record by id to verify its existence and metadata.",
                {
                    "type": "object",
                    "properties": {"arxiv_id": {"type": "string", "description": "e.g. 1706.03762"}},
                    "required": ["arxiv_id"],
                },
                arxiv_fetch,
            ),
            _fn_tool(
                "register_citations",
                "Verify & register arXiv ids into the citation registry; returns assigned bibtex keys. "
                "Only registered keys may be cited in the final paper.",
                {
                    "type": "object",
                    "properties": {
                        "arxiv_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "list of arxiv ids to verify+register",
                        }
                    },
                    "required": ["arxiv_ids"],
                },
                register_citations,
            ),
            _fn_tool(
                "list_citable_keys",
                "List all bibtex keys currently allowed for citation.",
                {"type": "object", "properties": {}},
                list_citable_keys,
            ),
        ]

    # ----------------------------------------------------------- public API
    @property
    def registry(self) -> CitationRegistry:  # noqa: D102
        return self._registry

    def validate_citations(self, keys: list[str]) -> tuple[list[str], list[str]]:  # noqa: D102
        return self._registry.validate_keys(keys)
