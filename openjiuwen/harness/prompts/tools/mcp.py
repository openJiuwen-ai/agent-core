# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Bilingual descriptions and input params for MCP resource tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

# ---------------------------------------------------------------------------
# list_mcp_resources
# ---------------------------------------------------------------------------

_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": "列出指定 MCP 服务器上可用的资源列表。",
    "en": "List available resources exposed by the specified MCP server.",
}

_LIST_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "cn": "MCP 服务器的 server_id",
        "en": "The server_id of the MCP server",
    },
}


def get_list_mcp_resources_input_params(language: str = "cn") -> Dict[str, Any]:
    p = _LIST_PARAMS
    return {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": p["server_id"].get(language, p["server_id"]["cn"]),
            },
        },
        "required": ["server_id"],
    }


class ListMcpResourcesMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "list_mcp_resources"

    def get_description(self, language: str = "cn") -> str:
        return _LIST_DESCRIPTION.get(language, _LIST_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_list_mcp_resources_input_params(language)


# ---------------------------------------------------------------------------
# read_mcp_resource
# ---------------------------------------------------------------------------

_READ_DESCRIPTION: Dict[str, str] = {
    "cn": "读取指定 MCP 服务器上某个资源的内容。",
    "en": "Read the content of a specific resource from the specified MCP server.",
}

_READ_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "cn": "MCP 服务器的 server_id",
        "en": "The server_id of the MCP server",
    },
    "uri": {
        "cn": "要读取的资源 URI",
        "en": "The URI of the resource to read",
    },
}


def get_read_mcp_resource_input_params(language: str = "cn") -> Dict[str, Any]:
    p = _READ_PARAMS
    return {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": p["server_id"].get(language, p["server_id"]["cn"]),
            },
            "uri": {
                "type": "string",
                "description": p["uri"].get(language, p["uri"]["cn"]),
            },
        },
        "required": ["server_id", "uri"],
    }


class ReadMcpResourceMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "read_mcp_resource"

    def get_description(self, language: str = "cn") -> str:
        return _READ_DESCRIPTION.get(language, _READ_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_read_mcp_resource_input_params(language)
