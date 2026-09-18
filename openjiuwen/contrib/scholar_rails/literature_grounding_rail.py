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

import json
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.base import DeepAgentRail

from openjiuwen.contrib.scholar_rails.arxiv_tool import (
    CitationRegistry,
    fetch_arxiv_by_id,
    search_arxiv,
)


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
            CitationRegistry.load(self._registry_path)
            if self._registry_path.exists()
            else CitationRegistry()
        )
        self._tools: list[Any] = []

    # ------------------------------------------------------------- lifecycle
    def init(self, agent) -> None:  # noqa: D102
        super().init(agent) if hasattr(super(), "init") else None
        self._tools = self._build_tools()
        for tool in self._tools:
            try:
                agent.ability_manager.add(tool)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LiteratureGroundingRail] register tool failed: %s", exc)

    def uninit(self, agent) -> None:  # noqa: D102
        for tool in self._tools:
            try:
                agent.ability_manager.remove(tool)
            except Exception:  # noqa: BLE001
                pass
        self._tools = []
        super().uninit(agent) if hasattr(super(), "uninit") else None

    # ---------------------------------------------------------------- tools
    def _build_tools(self) -> list[Any]:
        """构建三个底层能力函数对应的 Tool 对象。

        openJiuwen 的 Tool 需要 ToolCard + 可调用入口。这里用最小封装，
        通过 ``ability_manager.add`` 注册（与 SysOperationRail 同一机制）。
        """
        rail = self

        class _FnTool:
            """最小 Tool 封装：name/description/parameters + async invoke。"""

            def __init__(self, name: str, description: str, parameters: dict, fn):
                self.name = name
                self.description = description
                self.parameters = parameters
                self._fn = fn

            async def invoke(self, **kwargs):  # noqa: D102
                return self._fn(**kwargs)

            # openJiuwen ability_manager 期望的元数据属性
            @property
            def card(self):  # noqa: D102
                return {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                }

        def arxiv_search(query: str, max_results: Optional[int] = None) -> str:
            records = search_arxiv(query, max_results=max_results or rail._max_results)
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
                keys.append(rail._registry.add_record(rec))
            rail._registry.persist(rail._registry_path)
            return json.dumps({"registered": keys}, ensure_ascii=False)

        def list_citable_keys() -> str:
            return json.dumps({"citable_keys": rail._registry.keys()}, ensure_ascii=False)

        return [
            _FnTool(
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
            _FnTool(
                "arxiv_fetch",
                "Fetch a single arXiv record by id to verify its existence and metadata.",
                {
                    "type": "object",
                    "properties": {"arxiv_id": {"type": "string", "description": "e.g. 1706.03762"}},
                    "required": ["arxiv_id"],
                },
                arxiv_fetch,
            ),
            _FnTool(
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
            _FnTool(
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
