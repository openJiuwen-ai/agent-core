#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAPI MCP — HTTP Server Example
===================================
A simple aiohttp-based Task REST API that implements the OpenAPI spec
defined in openapi.yaml.

This server is NOT an MCP server itself — it is a regular HTTP REST server
whose API is described by openapi.yaml. The MCP layer is provided by the
OpenApiClient (client_direct.py), which reads the spec and converts each API
endpoint into an MCP tool backed by HTTP calls to this server.

Requirements:
    pip install aiohttp  (already a project dependency)

Run:
    python server.py

The server starts on http://127.0.0.1:3004
"""

import json
from aiohttp import web

from openjiuwen.core.common.logging import logger

# In-memory storage
_tasks: dict[int, dict] = {}
_next_id: int = 1


# ── Lifecycle signals ─────────────────────────────────────────────────────────

async def on_startup(app: web.Application) -> None:
    """Fired once when the aiohttp server starts accepting connections."""
    logger.info("Task REST API server started — ready to accept connections")


async def on_cleanup(app: web.Application) -> None:
    """Fired once when the aiohttp server shuts down."""
    logger.info("Task REST API server stopped — all connections closed")


# ── Per-request middleware (connect / disconnect logging) ────────────────────

@web.middleware
async def request_logger(request: web.Request, handler) -> web.Response:
    """
    Log each incoming HTTP request (connect) and its completed response
    (disconnect) so that every client interaction is visible in the log.
    """
    client = request.headers.get("X-Forwarded-For", request.remote or "unknown")
    logger.info(f"Client connected:    {request.method} {request.path} from {client}")
    try:
        response = await handler(request)
        logger.info(f"Client disconnected: {request.method} {request.path} → {response.status}")
        return response
    except web.HTTPException as exc:
        logger.info(f"Client disconnected: {request.method} {request.path} → {exc.status}")
        raise


def _json_response(data, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        status=status,
        content_type="application/json",
    )


async def list_tasks(request: web.Request) -> web.Response:
    return _json_response(list(_tasks.values()))


async def create_task(request: web.Request) -> web.Response:
    global _next_id
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status=400)

    title = body.get("title", "").strip()
    if not title:
        return _json_response({"error": "'title' field is required"}, status=400)

    task = {
        "id": _next_id,
        "title": title,
        "completed": bool(body.get("completed", False)),
    }
    _tasks[_next_id] = task
    _next_id += 1
    return _json_response(task, status=201)


async def get_task(request: web.Request) -> web.Response:
    task_id = int(request.match_info["task_id"])
    task = _tasks.get(task_id)
    if task is None:
        return _json_response({"error": "Task not found"}, status=404)
    return _json_response(task)


async def update_task(request: web.Request) -> web.Response:
    task_id = int(request.match_info["task_id"])
    task = _tasks.get(task_id)
    if task is None:
        return _json_response({"error": "Task not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, status=400)

    if "title" in body:
        task["title"] = str(body["title"]).strip()
    if "completed" in body:
        task["completed"] = bool(body["completed"])

    _tasks[task_id] = task
    return _json_response(task)


async def delete_task(request: web.Request) -> web.Response:
    task_id = int(request.match_info["task_id"])
    if task_id not in _tasks:
        return _json_response({"error": "Task not found"}, status=404)
    del _tasks[task_id]
    return _json_response({"message": "Task deleted successfully"})


def create_app() -> web.Application:
    app = web.Application(middlewares=[request_logger])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/tasks", list_tasks)
    app.router.add_post("/tasks", create_task)
    app.router.add_get("/tasks/{task_id}", get_task)
    app.router.add_put("/tasks/{task_id}", update_task)
    app.router.add_delete("/tasks/{task_id}", delete_task)
    return app


if __name__ == "__main__":
    logger.info("Starting Task REST API server on http://127.0.0.1:3004 ...")
    logger.info("Endpoints:")
    logger.info("  GET    /tasks            — list all tasks")
    logger.info("  POST   /tasks            — create a task")
    logger.info("  GET    /tasks/{id}       — get a task by ID")
    logger.info("  PUT    /tasks/{id}       — update a task")
    logger.info("  DELETE /tasks/{id}       — delete a task")
    web.run_app(create_app(), host="127.0.0.1", port=3004)
