#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SSE (Server-Sent Events) MCP Server Example
============================================
This server exposes a set of calculator tools over the SSE transport.

Requirements:
    pip install fastmcp

Run:
    python server.py

The server starts on http://127.0.0.1:3001/sse
"""

from contextlib import asynccontextmanager

from starlette.middleware import Middleware
from fastmcp import FastMCP

from openjiuwen.core.common.logging import logger


class SseConnectionLogger:
    """
    Pure ASGI middleware that logs when MCP clients connect and disconnect.

    SSE uses a persistent HTTP streaming connection on the /sse path.
    Each new request to /sse is a new client connecting; the connection
    closes when the client disconnects or the server shuts down.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only track SSE client connections; pass all other scopes through unchanged
        if scope["type"] == "http" and scope.get("path") == "/sse":
            client = scope.get("client")
            addr = f"{client[0]}:{client[1]}" if client else "unknown"
            logger.info(f"MCP client connected via SSE from {addr}")
            try:
                await self.app(scope, receive, send)
            finally:
                logger.info(f"MCP client disconnected: {addr}")
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    """Server-level lifecycle: fires once when the server starts and stops."""
    logger.info("SSE MCP server started — ready to accept client connections")
    yield
    logger.info("SSE MCP server stopped — all client connections closed")


mcp = FastMCP(
    name="calculator-sse-server",
    lifespan=lifespan,
)


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


@mcp.tool()
def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers together."""
    return a * b


@mcp.tool()
def divide(a: float, b: float) -> float:
    """Divide a by b. Returns an error message if b is zero."""
    if b == 0:
        return "Error: division by zero"
    return a / b


@mcp.tool()
def power(base: float, exponent: float) -> float:
    """Raise base to the power of exponent."""
    return base ** exponent


if __name__ == "__main__":
    logger.info("Starting SSE MCP server on http://127.0.0.1:3001/sse ...")
    mcp.run(transport="sse", host="127.0.0.1", port=3001, middleware=[Middleware(SseConnectionLogger)])
