# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.openapi_client import OpenApiClient


@pytest.mark.asyncio
async def test_get_tool_schema_distinguishes_empty_schema_from_missing_tool():
    client = OpenApiClient(McpServerConfig(
        server_name="openapi-server",
        server_path="openapi.json",
        client_type="openapi",
    ))
    client._tool_manager.tools["ping"] = SimpleNamespace(
        description="Ping",
        parameters={},
    )

    with patch.object(client._client, "aclose", AsyncMock()):
        assert await client.get_tool_schema("ping") == {}
        assert await client.get_tool_schema("missing") is None
        await client.disconnect()
