# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import ToolMetadataProvider


def _schema_free_search(lang: str) -> Dict[str, Any]:
    desc = {
        "cn": "搜索查询文本。查询最新、当前、今年、实时、近期信息时，必须使用系统提示中的当前年份或日期。",
        "en": (
            "Free search query text. For latest/current/this-year/recent information, "
            "use the current year or date from the system prompt."
        ),
    }[lang]
    max_desc = {"cn": "最大结果数（1-20）。", "en": "Maximum number of results (1-20)."}[lang]
    timeout_desc = {"cn": "请求超时时间（秒，5-60）。", "en": "Request timeout in seconds (5-60)."}[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": desc},
            "max_results": {"type": "integer", "description": max_desc, "default": 8},
            "timeout_seconds": {"type": "integer", "description": timeout_desc, "default": 20},
        },
        "required": ["query"],
    }


def _schema_paid_search(lang: str) -> Dict[str, Any]:
    qd = {"cn": "付费搜索查询文本。", "en": "Paid search query text."}[lang]
    pd = {
        "cn": "Provider: auto|bocha|perplexity|serper|jina。",
        "en": "Provider: auto|bocha|perplexity|serper|jina.",
    }[lang]
    md = {"cn": "最大 URL 数（1-20）。", "en": "Maximum number of URLs (1-20)."}[lang]
    td = {"cn": "请求超时时间（秒，10-120）。", "en": "Request timeout in seconds (10-120)."}[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": qd},
            "provider": {"type": "string", "description": pd, "default": "auto"},
            "max_results": {"type": "integer", "description": md, "default": 8},
            "timeout_seconds": {"type": "integer", "description": td, "default": 45},
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
                "免费搜索，返回结果 URL 和摘要。如果前几条结果看起来相关但还不足以直接回答任务，"
                "应先抓取前 1-3 条中的至少 2 条；如果第一条抓取失败、是动态壳页或内容仍然不完整，"
                "就继续抓下一条，而不是立刻继续改写搜索词。"
                "当用户询问最新、当前、今年、实时、近期等信息时，query 必须使用系统提示中的当前年份或日期；"
            ),
            "en": (
                "Free search. Input a query and return result URLs with snippets. "
                "If the top results look relevant but do not directly answer the task, "
                "you must fetch at least 2 of the top 1-3 results first. "
                "If the first fetch fails, is a dynamic shell page, or is still incomplete, "
                "continue with the next result instead of searching again immediately. "
                "For latest/current/this-year/recent information, the query must use the current year "
                "or date from the system prompt."
            ),
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_free_search(language)


class PaidSearchMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "paid_search"

    def get_description(self, language: str = "cn") -> str:
        return {
            "cn": (
                "付费搜索，支持 provider=auto|bocha|perplexity|serper|jina。"
                "当用户询问最新、当前、今年、实时、近期等信息时，query 必须使用系统提示中的当前年份或日期；"
            ),
            "en": (
                "Paid search via Bocha/Perplexity/SERPER/JINA. Support provider=auto|bocha|perplexity|serper|jina. "
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
                "抓取网页文本，返回状态码、标题和正文文本。通常配合 free_search 使用：先搜索，再抓取"
                "前几个结果页，而不是只依赖搜索摘要。可设置 max_chars=0 关闭截断，也可以调大 "
                "timeout_seconds 处理慢站点。"
            ),
            "en": (
                "Fetch webpage text content from a URL and return status, title, and plain text. "
                "Usually used after free_search: search first, then fetch the top few result pages "
                "instead of reasoning only from snippets. Set max_chars=0 to disable clipping and "
                "use a larger timeout_seconds for slow pages."
            ),
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_fetch_webpage(language)
