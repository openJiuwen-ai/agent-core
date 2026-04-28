# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenViking memory provider — session-based context database."""

import asyncio
import json
import os
from typing import Any, Optional

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.external.provider import MemoryProvider


class _VikingClient:
    """Thin HTTP wrapper for the OpenViking REST API."""

    def __init__(self, endpoint: str, api_key: str = "",
                 account: str = "default", user: str = "default",
                 agent: str = "hermes"):
        import httpx
        headers = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": account,
            "X-OpenViking-User": user,
            "X-OpenViking-Agent": agent,
        }
        if api_key:
            headers["X-API-Key"] = api_key
        self._http = httpx.Client(
            base_url=endpoint.rstrip("/"), headers=headers, timeout=30.0,
        )
        self._account = account
        self._user = user
        self._agent = agent

    def health(self) -> bool:
        try:
            r = self._http.get("/health")
            return r.status_code == 200
        except Exception:
            return False

    def post(self, path: str, body: dict) -> dict:
        r = self._http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        r = self._http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def close(self):
        self._http.close()


VIKING_SEARCH_SCHEMA = {
    "name": "viking_search",
    "description": "在知识库中进行全域搜索.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询词."},
            "mode": {
                "type": "string",
                "enum": ["auto", "fast", "deep"],
                "description": "搜索模式（默认：auto）.",
            },
            "top_k": {"type": "integer", "description": "最大返回结果数（默认10）."},
        },
        "required": ["query"],
    },
}

VIKING_READ_SCHEMA = {
    "name": "viking_read",
    "description": "读取 viking:// URI 上的内容.",
    "parameters": {
        "type": "object",
        "properties": {
            "uri": {"type": "string", "description": "要读取的 viking:// URI."},
            "detail": {
                "type": "string",
                "enum": ["abstract", "overview", "full"],
                "description": "详情级别（默认：overview）.",
            },
        },
        "required": ["uri"],
    },
}

VIKING_BROWSE_SCHEMA = {
    "name": "viking_browse",
    "description": "浏览知识库结构.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "tree", "stat"],
                "description": "浏览操作.",
            },
            "path": {"type": "string", "description": "浏览路径（默认：/）."},
        },
        "required": ["action"],
    },
}

VIKING_REMEMBER_SCHEMA = {
    "name": "viking_remember",
    "description": "显式存储一个事实或偏好.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的事实."},
            "category": {
                "type": "string",
                "enum": ["preference", "entity", "event", "case", "pattern"],
                "description": "记忆类别.",
            },
        },
        "required": ["content"],
    },
}

VIKING_ADD_RESOURCE_SCHEMA = {
    "name": "viking_add_resource",
    "description": "索引一个 URL 或文档以供后续搜索.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要索引的 URL 或文件路径."},
            "title": {"type": "string", "description": "可选标题."},
        },
        "required": ["url"],
    },
}


class OpenVikingMemoryProvider(MemoryProvider):
    """Full bidirectional memory via OpenViking context database.

    Tools: viking_search, viking_read, viking_browse, viking_remember, viking_add_resource
    Session-based: sync_turn records turns, on_session_end commits session.
    """

    def __init__(self, *, endpoint: str = "", api_key: str = "",
                 account: str = "", user: str = "", agent: str = ""):
        self._endpoint = endpoint or os.environ.get("OPENVIKING_ENDPOINT", "")
        self._api_key = api_key or os.environ.get("OPENVIKING_API_KEY", "")
        self._account = account or os.environ.get("OPENVIKING_ACCOUNT", "default")
        self._user = user or os.environ.get("OPENVIKING_USER", "default")
        self._agent = agent or os.environ.get("OPENVIKING_AGENT", "hermes")
        self._client: Optional[_VikingClient] = None
        self._session_id = ""

    @property
    def name(self) -> str:
        return "openviking"

    def is_available(self) -> bool:
        return bool(self._endpoint)

    @property
    def is_initialized(self) -> bool:
        return self._client is not None

    async def initialize(self, **kwargs) -> None:
        self._session_id = kwargs.get("session_id", "")
        try:
            self._client = _VikingClient(
                self._endpoint, self._api_key,
                account=self._account, user=self._user, agent=self._agent,
            )
            healthy = await asyncio.to_thread(self._client.health)
            if not healthy:
                logger.warning("OpenViking at %s not reachable", self._endpoint)
                self._client = None
        except ImportError:
            logger.warning("httpx not installed — OpenViking disabled")
            self._client = None
        except Exception as e:
            logger.warning("OpenViking init failed: %s", e)
            self._client = None

    def system_prompt_block(self) -> str:
        return (
            "# OpenViking Memory\n\n"
            "Use `viking_search` to find knowledge (modes: auto/fast/deep).\n"
            "Use `viking_read` to read content at a viking:// URI (levels: abstract/overview/full).\n"
            "Use `viking_browse` to navigate the knowledge structure.\n"
            "Use `viking_remember` to explicitly store facts.\n"
            "Use `viking_add_resource` to index URLs/documents."
        )

    async def prefetch(self, query: str, **kwargs) -> str:
        if not self._client or not query:
            return ""
        try:
            resp = await asyncio.to_thread(
                self._client.post, "/api/v1/search/find",
                {"query": query, "top_k": 5},
            )
            result = resp.get("result", {})
            parts = []
            for ctx_type in ("memories", "resources"):
                items = result.get(ctx_type, [])
                for item in items[:3]:
                    uri = item.get("uri", "")
                    abstract = item.get("abstract", "")
                    score = item.get("score", 0)
                    if abstract:
                        parts.append(f"- [{score:.2f}] {abstract} ({uri})")
            if not parts:
                return ""
            return "## OpenViking Context\n" + "\n".join(parts)
        except Exception as e:
            logger.debug("OpenViking prefetch failed: %s", e)
            return ""

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        if not self._client:
            return
        sid = kwargs.get("session_id", self._session_id)
        try:
            await asyncio.to_thread(
                self._client.post, f"/api/v1/sessions/{sid}/messages",
                {"role": "user", "content": user_msg[:4000]},
            )
            await asyncio.to_thread(
                self._client.post, f"/api/v1/sessions/{sid}/messages",
                {"role": "assistant", "content": assistant_msg[:4000]},
            )
        except Exception as e:
            logger.debug("OpenViking sync failed: %s", e)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            VIKING_SEARCH_SCHEMA,
            VIKING_READ_SCHEMA,
            VIKING_BROWSE_SCHEMA,
            VIKING_REMEMBER_SCHEMA,
            VIKING_ADD_RESOURCE_SCHEMA,
        ]

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._client:
            return json.dumps({"error": "OpenViking not connected"})
        try:
            if tool_name == "viking_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "query is required"})
                payload: dict[str, Any] = {"query": query}
                mode = args.get("mode", "auto")
                if mode != "auto":
                    payload["mode"] = mode
                if args.get("scope"):
                    payload["target_uri"] = args["scope"]
                if args.get("limit"):
                    payload["top_k"] = args["limit"]
                resp = await asyncio.to_thread(
                    self._client.post, "/api/v1/search/find", payload,
                )
                result = resp.get("result", {})
                scored_entries = []
                for ctx_type in ("memories", "resources", "skills"):
                    items = result.get(ctx_type, [])
                    for item in items:
                        raw_score = item.get("score")
                        sort_score = raw_score if raw_score is not None else 0.0
                        entry = {
                            "uri": item.get("uri", ""),
                            "type": ctx_type.rstrip("s"),
                            "score": round(raw_score, 3) if raw_score is not None else 0.0,
                            "abstract": item.get("abstract", ""),
                        }
                        if item.get("relations"):
                            entry["related"] = [r.get("uri") for r in item["relations"][:3]]
                        scored_entries.append((sort_score, entry))
                scored_entries.sort(key=lambda x: x[0], reverse=True)
                formatted = [entry for _, entry in scored_entries]
                result = {
                    "results": formatted,
                    "total": resp.get("result", {}).get("total", len(formatted)),
                }
            elif tool_name == "viking_read":
                uri = args.get("uri", "")
                if not uri:
                    return json.dumps({"error": "uri is required"})
                level = args.get("level", args.get("detail", "overview"))
                if level == "abstract":
                    result = await asyncio.to_thread(
                        self._client.get, "/api/v1/content/abstract", {"uri": uri},
                    )
                elif level == "full":
                    result = await asyncio.to_thread(
                        self._client.get, "/api/v1/content/read", {"uri": uri},
                    )
                else:
                    result = await asyncio.to_thread(
                        self._client.get, "/api/v1/content/overview", {"uri": uri},
                    )
                content = result.get("result", "")
                if not isinstance(content, str):
                    content = content.get("content", "")
                if len(content) > 8000:
                    content = content[:8000] + "\n\n[... truncated, use a more specific URI or abstract level]"
                result = {"uri": uri, "level": level, "content": content}
            elif tool_name == "viking_browse":
                action = args.get("action", "list")
                browse_path = args.get("path", "viking://")
                endpoint_map = {
                    "tree": "/api/v1/fs/tree",
                    "list": "/api/v1/fs/ls",
                    "stat": "/api/v1/fs/stat",
                }
                path = endpoint_map.get(action, "/api/v1/fs/ls")
                result = await asyncio.to_thread(
                    self._client.get, path, {"uri": browse_path},
                )
                entries = result.get("result", {})
                if action in ("list", "tree") and isinstance(entries, list):
                    formatted = []
                    for e in entries[:50]:
                        formatted.append({
                            "name": e.get("rel_path", e.get("name", "")),
                            "uri": e.get("uri", ""),
                            "type": "dir" if e.get("isDir") else "file",
                            "abstract": e.get("abstract", ""),
                        })
                    result = {"path": browse_path, "entries": formatted}
            elif tool_name == "viking_remember":
                content = args.get("content", "")
                if not content:
                    return json.dumps({"error": "content is required"})
                category = args.get("category", "")
                text = f"[Remember] {content}"
                if category:
                    text = f"[Remember — {category}] {content}"
                await asyncio.to_thread(
                    self._client.post,
                    f"/api/v1/sessions/{self._session_id}/messages",
                    {
                        "role": "user",
                        "parts": [{"type": "text", "text": text}],
                    },
                )
                result = {
                    "status": "stored",
                    "message": "Memory recorded. Will be extracted and indexed on session commit.",
                }
            elif tool_name == "viking_add_resource":
                url = args.get("url", "")
                if not url:
                    return json.dumps({"error": "url is required"})
                payload: dict[str, Any] = {"path": url}
                if args.get("reason"):
                    payload["reason"] = args["reason"]
                result = await asyncio.to_thread(
                    self._client.post, "/api/v1/resources", payload,
                )
                res_data = result.get("result", {})
                result = {
                    "status": "added",
                    "root_uri": res_data.get("root_uri", ""),
                    "message": "Resource queued for processing. Use viking_search after a moment to find it.",
                }
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._client or not self._session_id:
            return
        try:
            await asyncio.to_thread(
                self._client.post, f"/api/v1/sessions/{self._session_id}/commit", {},
            )
        except Exception as e:
            logger.debug("OpenViking session commit failed: %s", e)

    async def shutdown(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug("OpenViking client close failed: %s", e)
            self._client = None
