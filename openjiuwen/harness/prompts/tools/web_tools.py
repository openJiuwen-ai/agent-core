# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


def _schema_free_search(lang: str) -> Dict[str, Any]:
    desc = {
        "cn": "搜索查询文本。保留实体及意图（例如股票代码和股价）；需要时添加日期，但日期关键词不能替代来源时间核验。",
        "en": (
            "Free search query with the entity and intent (for example a ticker and stock price). "
            "Add dates when useful; date keywords do not verify source freshness."
        ),
    }[lang]
    max_desc = {"cn": "最大结果数（1-20）。", "en": "Maximum number of results (1-20)."}[lang]
    timeout_desc = {"cn": "请求超时时间（秒，5-60）。", "en": "Request timeout in seconds (5-60)."}[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": desc},
            "provider": {
                "type": "string", "enum": ["auto", "bing", "duckduckgo"], "default": "auto",
                "description": {
                    "cn": "只使用已启用的引擎。用户指定 Bing 等引擎时必须指定 provider；auto 才允许在已配置免费源之间回退。",
                    "en": "Only enabled engines may run. Set provider when the user names an engine; only auto permits fallback among configured free sources.",
                }[lang],
            },
            "max_results": {"type": "integer", "description": max_desc, "default": 8},
            "timeout_seconds": {"type": "integer", "description": timeout_desc, "default": 20},
        },
        "required": ["query"],
    }


def _schema_paid_search(lang: str) -> Dict[str, Any]:
    # Import lazily: web tool implementations also import this metadata package.
    from openjiuwen.harness.tools.web._common import _configured_paid_search_providers

    providers = ["auto", *_configured_paid_search_providers()]
    choices = "|".join(providers)
    qd = {"cn": "付费搜索查询文本。", "en": "Paid search query text."}[lang]
    pd = {
        "cn": f"可用供应商：{choices}。优先使用 auto，仅在已配置供应商中选择。",
        "en": f"Available providers: {choices}. Prefer auto; it selects only configured providers.",
    }[lang]
    md = {"cn": "最大 URL 数（1-20）。", "en": "Maximum number of URLs (1-20)."}[lang]
    td = {"cn": "请求超时时间（秒，30-300）。", "en": "Request timeout in seconds (30-300)."}[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": qd},
            "provider": {"type": "string", "description": pd, "default": "auto", "enum": providers},
            "max_results": {"type": "integer", "description": md, "default": 8},
            "timeout_seconds": {
                "type": "integer",
                "description": td,
                "default": 180,
                "minimum": 30,
                "maximum": 300,
            },
        },
        "required": ["query"],
    }


def _schema_fetch_webpage(lang: str) -> Dict[str, Any]:
    ud = {"cn": "要抓取的网页 URL。", "en": "Webpage URL to fetch."}[lang]
    cd = {
        "cn": "返回内容最大字符数；设为 0 表示不截断。",
        "en": "Maximum content characters. Set to 0 to disable clipping.",
    }[lang]
    td = {
        "cn": "请求超时时间（秒）；慢站点可适当调大。",
        "en": "Request timeout in seconds. Larger values can be used for slow websites.",
    }[lang]
    return {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": ud},
            "max_chars": {"type": "integer", "description": cd, "default": 20000},
            "timeout_seconds": {"type": "integer", "description": td, "default": 45},
        },
        "required": ["url"],
    }


class FreeSearchMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "free_search"

    def get_description(self, language: str = "cn") -> str:
        return {
            "cn": (
                "如果 paid_search 可用或已配置 API，优先使用 paid_search；free_search 仅作为兜底或用户明确要求免费搜索时使用。"
                "返回经过相关性筛选的候选 URL 和摘要，不代表事实已验证。按需要抓取相关来源，证据足够即停止，不要求至少抓两页。"
                "优先从权威数据页核验天气、行情；新闻需核验文章发布时间。区分实体和指数，不猜测文章 URL 或补全缺失数字。"
                "遇到 low_relevance、blocked、empty 或 budget_exhausted 时遵守返回的限制，说明缺少什么证据，不反复改写查询。"
                "用户指定免费搜索或特定引擎时不要自行切换到付费工具。"
            ),
            "en": (
                "Free search. If paid_search is available/configured, call paid_search first; "
                "use free_search only as fallback or when the user explicitly asks for free search. "
                "Return relevance-filtered discovery URLs, not verified facts. Fetch a relevant source only as needed; "
                "stop when evidence is sufficient, without a mandatory second fetch. Prefer authoritative data pages "
                "for weather/quotes and dated articles for news. Verify entity and observation time; do not guess "
                "article URLs or fill missing values. Respect low_relevance/blocked/empty/budget_exhausted statuses "
                "and explain missing evidence instead of repeatedly rewriting queries. Do not switch to paid "
                "search when the user requested a free or specific engine."
            ),
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_free_search(language)


class PaidSearchMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "paid_search"

    def get_description(self, language: str = "cn") -> str:
        choices = "|".join(_schema_paid_search(language)["properties"]["provider"]["enum"])
        return {
            "cn": (
                "配置 API 时这是首选联网搜索工具；对搜索、最新、当前信息任务应先调用 paid_search，再考虑 free_search 兜底。"
                f"付费搜索，可用 provider={choices}；优先使用 auto，不要指定未列出的供应商。"
                "当用户询问最新、当前、今年、实时、近期等信息时，query 必须使用系统提示中的当前年份或日期；"
            ),
            "en": (
                f"Paid search with provider={choices}. Prefer auto; do not select unlisted providers. "
                "When available, this is the preferred web search tool; call it before free_search for search, latest, "
                "current, or recent-information tasks. "
                "For latest/current/this-year/recent information, the query must use the current year "
                "or date from the system prompt. "
            ),
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_paid_search(language)


class FetchWebpageMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "fetch_webpage"

    def get_description(self, language: str = "cn") -> str:
        return {
            "cn": (
                "按需要读取原始来源，返回状态码、标题和正文文本。通常先通过搜索发现相关 URL，"
                "不要只依赖摘要或猜测文章 URL；证据足够即可停止，不必抓取多个页面。可设置 max_chars=0 关闭截断，也可以调大 "
                "timeout_seconds 处理慢站点。"
                "适用场景：文档、博客、新闻、API 参考等普通网页。"
                "Content status 与 HTTP 状态不同；验证码、WAF、登录限制或动态空壳不是有效证据。"
                "保留来源链接与表格字段；缺失值不得用相邻数字补全，也不能把过期行情说成实时行情。"
                "代码仓地址（GitHub/GitLab/Gitee/Gitcode/Bitbucket 等）一般不适合用本工具——"
                "网页只能看到渲染后的目录页;要读源码、看历史、跨文件搜索，"
                "更顺手的方式是用 shell 工具(bash 或 powershell)执行 `git clone` 拉到本地。"
            ),
            "en": (
                "Fetch webpage text content from a URL and return status, title, and plain text. "
                "Read relevant sources only as needed, usually after search discovers their URLs. "
                "Do not reason only from snippets or guess article URLs; stop when evidence is sufficient. "
                "Set max_chars=0 to disable clipping and "
                "use a larger timeout_seconds for slow pages. "
                "Best fit: documentation, blog posts, news, API references, and similar general web content. "
                "Content status is distinct from HTTP status: challenges, authentication and empty shells are not evidence. "
                "Preserve source links and field associations; never fill missing fields with adjacent numbers "
                "or present stale observations as live data. "
                "Git repository URLs (GitHub/GitLab/Gitee/Gitcode/Bitbucket, etc.) are usually a poor fit - "
                "the webpage only shows a rendered file tree, while reading source, history, or searching across "
                "files is far easier after a local `git clone` via the shell tool (bash or powershell)."
            ),
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_fetch_webpage(language)
