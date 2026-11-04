# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Invocation-local retrieval limits shared by concurrent web tools."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.harness.tools.web._common import _safe_int
from openjiuwen.harness.tools.web._quality import WebContentError

WEB_RESEARCH_BUDGET_KEY = "web_research_budget"


def _limit(name: str, default: int) -> int:
    return max(1, min(_safe_int(os.environ.get(name), default), 100))


@dataclass
class WebResearchBudget:
    """Mutable only within one invocation; reservations never await."""

    searches_per_engine: int = 2
    max_engines: int = 2
    max_fetches: int = 3
    timeout_seconds: int = 90
    searches: dict[str, int] = field(default_factory=dict)
    fetches: int = 0
    started_at: float | None = None
    original_task: str = ""

    @classmethod
    def from_env(cls) -> WebResearchBudget:
        return cls(
            searches_per_engine=_limit("WEB_RESEARCH_SEARCHES_PER_ENGINE", 2),
            max_engines=_limit("WEB_RESEARCH_MAX_ENGINES", 2),
            max_fetches=_limit("WEB_RESEARCH_MAX_FETCHES", 3),
            timeout_seconds=max(5, min(_safe_int(os.environ.get("WEB_RESEARCH_TIMEOUT_SECONDS"), 90), 600)),
        )

    def remaining(self, timeout: float) -> float:
        if self.started_at is None:
            self.started_at = time.monotonic()
        remaining = self.timeout_seconds - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise WebContentError("budget_exhausted", "Retrieval deadline reached. Report supported facts and missing evidence; do not retry.")
        return min(timeout, remaining)

    def reserve_search(self, engine: str) -> None:
        self.remaining(self.timeout_seconds)
        provider = "duckduckgo" if engine == "duckduckgo-jina" else engine
        if provider not in self.searches and len(self.searches) >= self.max_engines:
            raise WebContentError("budget_exhausted", "Search source limit reached. Do not switch to another source or paid search without user approval.")
        attempts = self.searches.get(provider, 0)
        if attempts >= self.searches_per_engine:
            raise WebContentError("budget_exhausted", f"Search attempt limit reached for {provider}. Reuse evidence or report the missing information; do not repeat this search.")
        self.searches[provider] = attempts + 1

    def reserve_fetch(self) -> None:
        self.remaining(self.timeout_seconds)
        if self.fetches >= self.max_fetches:
            raise WebContentError("budget_exhausted", "Page fetch limit reached. Answer only from verified evidence, identify missing fields, and stop retrieval.")
        self.fetches += 1


def get_web_budget(context: Any) -> WebResearchBudget | None:
    extra = getattr(context, "extra", None)
    budget = extra.get(WEB_RESEARCH_BUDGET_KEY) if isinstance(extra, dict) else None
    return budget if isinstance(budget, WebResearchBudget) else None
