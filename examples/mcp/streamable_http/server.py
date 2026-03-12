#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Streamable HTTP MCP Server Example
====================================
This server exposes an in-memory note-taking service over the
Streamable HTTP MCP transport.

Requirements:
    pip install fastmcp

Run:
    python server.py

The server starts on http://127.0.0.1:3002/mcp
"""

from contextlib import asynccontextmanager
from typing import List

from starlette.middleware import Middleware
from fastmcp import FastMCP

from openjiuwen.core.common.logging import logger


class StreamableHttpConnectionLogger:
    """
    Pure ASGI middleware that logs when MCP clients connect and disconnect.

    Streamable HTTP uses the /mcp path. Each request that initiates an MCP
    session is a client connecting; the connection ends when the session closes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only track MCP client connections on the /mcp endpoint
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            client = scope.get("client")
            addr = f"{client[0]}:{client[1]}" if client else "unknown"
            logger.info(f"MCP client connected via Streamable HTTP from {addr}")
            try:
                await self.app(scope, receive, send)
            finally:
                logger.info(f"MCP client disconnected: {addr}")
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    """Server-level lifecycle: fires once when the server starts and stops."""
    logger.info("Streamable HTTP MCP server started — ready to accept client connections")
    yield
    logger.info("Streamable HTTP MCP server stopped — all client connections closed")


mcp = FastMCP(
    name="notes-streamable-http-server",
    lifespan=lifespan,
)

# Simple in-memory store shared across tool calls during the server session
_notes: List[str] = []


@mcp.tool()
def add_note(content: str) -> str:
    """Add a new note. Returns the ID (index) of the created note."""
    _notes.append(content)
    note_id = len(_notes) - 1
    return f"Note added with ID {note_id}"


@mcp.tool()
def get_note(note_id: int) -> str:
    """Retrieve a note by its ID."""
    if note_id < 0 or note_id >= len(_notes):
        return f"Error: note with ID {note_id} does not exist"
    return _notes[note_id]


@mcp.tool()
def list_notes() -> List[str]:
    """List all notes with their IDs."""
    if not _notes:
        return ["No notes yet."]
    return [f"[{i}] {note}" for i, note in enumerate(_notes)]


@mcp.tool()
def delete_note(note_id: int) -> str:
    """Delete a note by its ID."""
    if note_id < 0 or note_id >= len(_notes):
        return f"Error: note with ID {note_id} does not exist"
    removed = _notes.pop(note_id)
    return f"Deleted note: '{removed}'"


@mcp.tool()
def clear_notes() -> str:
    """Delete all notes."""
    count = len(_notes)
    _notes.clear()
    return f"Cleared {count} note(s)"


if __name__ == "__main__":
    logger.info("Starting Streamable HTTP MCP server on http://127.0.0.1:3002/mcp ...")
    mcp.run(transport="streamable-http", host="127.0.0.1", port=3002,
            middleware=[Middleware(StreamableHttpConnectionLogger)])
