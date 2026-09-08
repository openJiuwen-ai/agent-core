"""Web research tool rail for the Topic Survey Agent.

The rail composes OpenJiuwen's public web tools with one constrained downloader.
The downloader writes raw PDF/HTML only to the host-selected survey directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.web import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    is_paid_search_enabled,
)

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.download_survey_source import (
    DownloadSurveySourceTool,
)

# These domains are used only when a task explicitly selects ``domestic``
# search scope. A missing task-scoped proxy does not select this allowlist:
# proxy configuration controls transport, while source selection stays global
# and the agent is reminded to prefer directly downloadable sources.
DOMESTIC_SOURCE_DOMAINS = (
    "scholar.baidu.com",
    "xueshu.baidu.com",
    "cnki.net",
    "wanfangdata.com.cn",
)

# ``free_search`` is always registered: it does not require a provider API key.
# ``paid_search`` is optional and only appears when both requested and configured.
REQUIRED_TOOL_NAMES = frozenset({"free_search", "fetch_webpage", "download_survey_source"})
ALLOWED_TOOL_NAMES = REQUIRED_TOOL_NAMES | frozenset({"paid_search"})
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "powershell",
        "code",
    }
)


class TopicSurveyToolsRail(DeepAgentRail):
    """Register web-search, webpage-fetch, and raw-source download tools."""

    priority = 100

    def __init__(
        self,
        *,
        download_dir: Path,
        project_root: Path,
        include_paid_search: bool = False,
        proxy_url: str | None = None,
        allowed_domains: tuple[str, ...] | None = None,
        free_search_engines: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self.tools: list[Any] | None = None
        self._download_dir = download_dir
        self._project_root = project_root
        self._include_paid_search = include_paid_search
        self._proxy_url = str(proxy_url or "").strip() or None
        self._allowed_domains = allowed_domains
        self._free_search_engines = free_search_engines

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        tools: list[Any] = [
            WebFreeSearchTool(
                language=lang,
                agent_id=agent_id,
                proxy_url=self._proxy_url,
                allowed_domains=self._allowed_domains,
                enabled_engines=self._free_search_engines,
            ),
            WebFetchWebpageTool(
                language=lang,
                agent_id=agent_id,
                proxy_url=self._proxy_url,
                allowed_domains=self._allowed_domains,
            ),
            DownloadSurveySourceTool(
                download_dir=self._download_dir,
                project_root=self._project_root,
                proxy_url=self._proxy_url,
                allowed_domains=self._allowed_domains,
            ),
        ]
        if self._include_paid_search and is_paid_search_enabled():
            tools.append(
                WebPaidSearchTool(
                    language=lang,
                    agent_id=agent_id,
                    proxy_url=self._proxy_url,
                )
            )

        names = {getattr(tool.card, "name", None) for tool in tools}
        unexpected = names - ALLOWED_TOOL_NAMES
        missing = REQUIRED_TOOL_NAMES - names
        if unexpected:
            raise RuntimeError(f"unexpected survey tools registered: {sorted(unexpected)}")
        if missing:
            raise RuntimeError(f"required survey tools missing: {sorted(missing)}")
        if names & FORBIDDEN_TOOL_NAMES:
            raise RuntimeError("forbidden mutation/execution tools leaked into survey rail")

        self.tools = tools
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if not self.tools:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)


__all__ = [
    "ALLOWED_TOOL_NAMES",
    "FORBIDDEN_TOOL_NAMES",
    "REQUIRED_TOOL_NAMES",
    "DOMESTIC_SOURCE_DOMAINS",
    "TopicSurveyToolsRail",
]
