from __future__ import annotations

from typing import Optional


class SearchStep:
    def __init__(
        self,
        *,
        level: int,
        node_id: str,
        options: list[str],
        selected: list[str],
        is_parallel: bool = False,
    ) -> None:
        self.level = level
        self.node_id = node_id
        self.options = list(options)
        self.selected = list(selected)
        self.is_parallel = is_parallel


class MultiLevelSearchResult:
    def __init__(
        self,
        *,
        query: str,
        selected_skills: list[dict],
        steps: Optional[list[SearchStep]] = None,
        llm_calls: int = 0,
        parallel_rounds: int = 0,
        early_stops: int = 0,
    ) -> None:
        self.query = query
        self.selected_skills = list(selected_skills)
        self.steps = list(steps or [])
        self.llm_calls = llm_calls
        self.parallel_rounds = parallel_rounds
        self.early_stops = early_stops
