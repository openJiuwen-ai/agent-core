#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Stdio MCP Server Example
========================
This server exposes text processing tools over the Stdio transport.
It communicates via stdin/stdout (JSON-RPC over stdio).

Requirements:
    pip install fastmcp

Run (directly, for testing):
    python server.py

Note:
    Normally this server is launched as a subprocess by the MCP client
    (see client_direct.py). You do NOT need to start it manually.

Connect/Disconnect behaviour:
    With Stdio transport, each process invocation corresponds to exactly one
    client connection. The lifespan context manager below therefore doubles as
    a connect/disconnect log:
      - on enter  → the client has connected (subprocess started)
      - on exit   → the client has disconnected (subprocess terminating)
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastmcp import FastMCP

# For Stdio transport, stdout is reserved exclusively for JSON-RPC messages.
# Using the openjiuwen logger (which writes to stdout by default) would corrupt
# the MCP protocol.  Use a plain stdlib logger directed to stderr instead.
_log = logging.getLogger(__name__)
if not _log.handlers:
    _log.addHandler(logging.StreamHandler(sys.stderr))
    _log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app):
    """
    Server-level lifecycle hook.

    For the Stdio transport, the server process is started by the client and
    exits when the client closes the connection, so startup == connect and
    shutdown == disconnect.
    """
    _log.info("Stdio MCP server started — client connected via stdin/stdout")
    yield
    _log.info("Stdio MCP server stopped — client disconnected")


mcp = FastMCP(name="text-processor-stdio-server", lifespan=lifespan)


@mcp.tool()
def word_count(text: str) -> int:
    """Count the number of words in the given text."""
    return len(text.split())


@mcp.tool()
def char_count(text: str) -> int:
    """Count the number of characters (including spaces) in the given text."""
    return len(text)


@mcp.tool()
def reverse_text(text: str) -> str:
    """Reverse the characters of the given text."""
    return text[::-1]


@mcp.tool()
def to_uppercase(text: str) -> str:
    """Convert the given text to uppercase."""
    return text.upper()


@mcp.tool()
def to_lowercase(text: str) -> str:
    """Convert the given text to lowercase."""
    return text.lower()


@mcp.tool()
def count_lines(text: str) -> int:
    """Count the number of lines in the given text."""
    return len(text.splitlines())


if __name__ == "__main__":
    # Stdio transport reads from stdin and writes to stdout
    mcp.run(transport="stdio")
