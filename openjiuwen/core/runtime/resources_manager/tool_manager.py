# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from copy import deepcopy
from typing import List, Optional, Tuple, Union, Callable

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.tracer.decorator import decorate_tool_with_trace
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.mcp.base import (
    ToolServerConfig,
    McpToolInfo,
    McpToolClient,
    SseClient,
    StdioClient,
    PlaywrightClient,
    MCPTool
)
from openjiuwen.core.common.logging import logger

ToolProvider = Callable[[], Tool]


class ToolMgr(AbstractManager[Tool]):
    def __init__(self) -> None:
        super().__init__()
        self._tool_infos: dict[str, ToolInfo] = {}
        self._server_tool_infos: dict[str, List[McpToolInfo]] = {}
        self._server_configs: dict[str, ToolServerConfig] = {}
        self._mcp_clients: dict[str, McpToolClient] = {}

    def add_tool(self, tool_id: str, tool: Union[Tool, ToolProvider]) -> None:
        self._validate_id(tool_id, StatusCode.RUNTIME_TOOL_GET_FAILED, "tool")
        self._validate_resource(tool, StatusCode.RUNTIME_TOOL_GET_FAILED, "tool is invalid, can not be None")

        # Define validation function for non-callable tools
        def validate_tool(tool_obj):
            # Store tool info
            if hasattr(tool_obj, "get_tool_info"):
                self._tool_infos[tool_id] = tool_obj.get_tool_info()
            else:
                logger.warning(f"Tool {tool_id} does not have a get_tool_info method, add tool failed")
            return tool_obj

        self._add_resource(tool_id, tool, StatusCode.RUNTIME_TOOL_GET_FAILED, validate_tool)

    def add_tools(self, tools: List[Tuple[str, Union[Tool, ToolProvider]]]):
        if not tools:
            return
        for tool_id, tool in tools:
            self.add_tool(tool_id, tool)

    def _get_all_tool_ids(self, name: str):
        yield name
        for server_name in self._server_configs.keys():
            if name.startswith(server_name) and len(name) > len(server_name):
                new_tool_key = server_name + "." + name[len(server_name) + 1:]
                yield new_tool_key

    def _find_tool_by_name(self, name: str) -> Optional[Tool]:
        self._validate_id(name, StatusCode.RUNTIME_TOOL_GET_FAILED, "name")

        # Define function to create tool from provider
        def create_tool_from_provider(provider):
            tool = provider()
            # Store tool info
            if hasattr(tool, "get_tool_info"):
                if tool.get_tool_info() is None:
                    logger.warn(f"add a tool without tool_info, tool_name={name}")
                else:
                    self._tool_infos[name] = tool.get_tool_info()
            else:
                self._tool_infos[name] = ToolInfo()
            return tool

        for tool_id in self._get_all_tool_ids(name):
            resource = self._get_resource(tool_id, StatusCode.RUNTIME_TOOL_GET_FAILED, create_tool_from_provider)
            if resource:
                return resource
        return None

    def get_tool(self, tool_id: str, runtime=None) -> Optional[Tool]:
        # Validate ID using base class method
        self._validate_id(tool_id, StatusCode.RUNTIME_TOOL_GET_FAILED, "tool")

        try:
            tool = self._find_tool_by_name(tool_id)
            return decorate_tool_with_trace(tool, runtime)
        except JiuWenBaseException:
            raise
        except Exception as e:
            self._handle_exception(e, StatusCode.RUNTIME_TOOL_GET_FAILED, "get")
            return None

    def remove_tool(self, tool_id: str) -> Optional[Tool]:
        if tool_id is None:
            return None

        try:
            tool = self._remove_resource(tool_id, StatusCode.RUNTIME_TOOL_GET_FAILED)
            self._tool_infos.pop(tool_id, None)
            return tool
        except Exception as e:
            self._handle_exception(e, StatusCode.RUNTIME_TOOL_GET_FAILED, "remove")
            return None

    def get_tool_infos(self, tool_ids: List[str] = None, *, tool_server_name: str = None, name_delimiter: str = None) \
            -> Optional[List[Union[ToolInfo, McpToolInfo]]]:
        try:
            delimiter = self._normalize_delimiter(name_delimiter, default_delimiter=".")
            if tool_server_name:
                server_tools = self._server_tool_infos.get(tool_server_name)
                if server_tools:
                    result = []

                    for tool in server_tools:
                        result.append(self._normalize_mcp_tool_info(tool, delimiter))
                    return result
                else:
                    return None
            if tool_ids is not None and not isinstance(tool_ids, list):
                raise JiuWenBaseException(
                    StatusCode.RUNTIME_TOOL_TOOL_INFO_GET_FAILED.code,
                    StatusCode.RUNTIME_TOOL_TOOL_INFO_GET_FAILED.errmsg.format(
                        reason=f"tool_ids must be a list, got {type(tool_ids).__name__}"
                    )
                )
            if not tool_ids:
                return [self._normalize_mcp_tool_info(info, delimiter) for info in self._tool_infos.values() if
                        info is not None]

            infos = []
            for tool_id in tool_ids:
                self._validate_id(tool_id, StatusCode.RUNTIME_TOOL_TOOL_INFO_GET_FAILED, "tool")
                infos.append(self._normalize_mcp_tool_info(self._tool_infos.get(tool_id), delimiter))
            return infos
        except JiuWenBaseException:
            raise
        except Exception as e:
            self._handle_exception(e, StatusCode.RUNTIME_TOOL_TOOL_INFO_GET_FAILED, "get_tool_info")
            return None

    async def add_tool_servers(self, server_config: Union[ToolServerConfig, List[ToolServerConfig]]) -> List[bool]:
        """
        注册 MCP 服务器（同步连接、阻塞至完成）。
        返回：与传入顺序一一对应的注册结果列表，True=成功。
        """
        configs = [server_config] if isinstance(server_config, ToolServerConfig) else (server_config or [])
        if not configs:
            return []

        results: List[bool] = []
        for cfg in configs:
            try:
                if self._server_configs.get(cfg.server_name) is not None:
                    results.append(False)
                    logger.exception(f"Register MCP server {cfg.server_name} failed: already added")
                    continue
                result = await self._connect_and_register_server(cfg)
                results.append(result)
            except Exception as e:
                logger.exception(f"Register MCP server {cfg.server_name} failed: {e}")
                results.append(False)
        return results

    async def _connect_and_register_server(self, config: ToolServerConfig) -> bool:
        client = self._create_client(config)
        connected = await client.connect()
        if not connected:
            logger.error(f"Failed to connect to MCP server: {config.server_name}")
            return connected

        self._mcp_clients[config.server_name] = client
        self._server_configs[config.server_name] = config

        tools = await client.list_tools()
        self._server_tool_infos[config.server_name] = tools

        for mcp_tool_info in tools:
            tool_id = f'{config.server_name}.{mcp_tool_info.name}'
            mcp_tool_info.server_name = config.server_name
            mcp_tool = MCPTool(
                mcp_client=client,
                tool_info=mcp_tool_info
            )
            # 注册到 ToolMgr
            self.add_tool(tool_id, mcp_tool)
            logger.info(f"Registered MCP tool: {tool_id}")
        return True

    def _create_client(self, config: ToolServerConfig) -> McpToolClient:
        if config.client_type == "sse":
            return SseClient(config.server_path, config.server_name)
        elif config.client_type == "stdio":
            return StdioClient(config.server_path, config.server_name, config.params)
        elif config.client_type == "playwright":
            return PlaywrightClient(config.server_path, config.server_name)
        elif config.client_type == "openapi":
            from openjiuwen.core.utils.tool.mcp.openapi_client import OpenApiClient
            return OpenApiClient(config.server_path, config.server_name)
        else:
            raise ValueError(f"Unsupported MCP client type: {config.client_type}")

    async def remove_tool_server(self, tool_server_name: str):
        """移除 MCP 服务器"""
        if tool_server_name not in self._mcp_clients:
            logger.warning(f"MCP server '{tool_server_name}' not found.")
            return

        # 移除该服务器下的所有工具
        tools = self._server_tool_infos.pop(tool_server_name, [])
        for tool_info in tools:
            tool_id = f"{tool_server_name}.{tool_info.name}"
            self.remove_tool(tool_id)

        # 断开客户端连接 - 直接调用，不创建新任务
        client = self._mcp_clients.pop(tool_server_name)
        await client.disconnect()  # 直接 await，确保在同一个任务中
        self._server_configs.pop(tool_server_name, None)
        logger.info(f"Removed MCP server: {tool_server_name}")

    @staticmethod
    def _normalize_delimiter(raw_delimiter: str | None, *, default_delimiter: str = "."):
        normalized_delimiter = raw_delimiter if raw_delimiter else default_delimiter
        if len(normalized_delimiter) > 1:
            logger.warning(f"Invalid delimiter '{raw_delimiter}', expected single character,"
                           f" using default {default_delimiter}")
            normalized_delimiter = default_delimiter
        return normalized_delimiter

    @staticmethod
    def _normalize_mcp_tool_info(tool_info: ToolInfo, delimiter: str):
        if not isinstance(tool_info, McpToolInfo):
            return tool_info

        copy_tool_info = deepcopy(tool_info)
        copy_tool_info.name = f'{tool_info.server_name}{delimiter}{tool_info.name}'
        return copy_tool_info

    async def stop(self):
        for client in self._mcp_clients.values():
            try:
                await client.disconnect()
            except Exception:
                continue
