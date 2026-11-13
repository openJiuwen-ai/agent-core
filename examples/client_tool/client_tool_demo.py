# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Minimal client tool: the model calls it, the connected client runs it.

The agent never executes ``read_clipboard`` itself. When the model calls it,
the run pauses with a ``client_tool`` interrupt; this script plays the host:
it routes every pending interrupt by ``kind``, runs the client tool "on the
client" (a local function standing in for a browser or editor front end),
approves permission asks, and resumes with ``InteractiveInput.answer``.

Run (needs an OpenAI-compatible model)::

    API_KEY=... MODEL_NAME=gpt-4.1-mini API_BASE=https://api.openai.com/v1 \\
        python examples/client_tool/client_tool_demo.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import ClientTool, ClientToolUnavailable, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt import (
    InterruptKind,
    ToolCallInterruptRequest,
    interrupt_answer,
    is_approval_kind,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent


class ReadClipboardTool(ClientTool):
    """Reads the clipboard of the user's machine; only the client can do that."""

    def __init__(self) -> None:
        super().__init__(ToolCard(
            id="read_clipboard",
            name="read_clipboard",
            description="Read the text currently on the user's clipboard.",
            input_params={
                "type": "object",
                "properties": {"max_chars": {"type": "integer", "description": "Longest text to return."}},
                "required": [],
            },
        ))

    def build_request(self, arguments, context):
        # Decide what the client must do; refuse calls it cannot serve without contacting it.
        max_chars = arguments.get("max_chars", 2000)
        if not isinstance(max_chars, int) or max_chars <= 0:
            raise ClientToolUnavailable("max_chars must be a positive integer.")
        return {"action": "read_clipboard", "max_chars": max_chars, "conversation": context.session_id}


def run_on_client(request) -> dict:
    """Stands in for the front end executing the call and reporting back."""
    if request.client_request.get("action") == "read_clipboard":
        return {"content": "Meeting moved to 3 pm, room 4.12"}
    return {"status": "error", "is_error": True, "content": "unknown action"}


def answer_pending(result: dict) -> InteractiveInput:
    """Route each pending interrupt by its kind and answer it by its interrupt id."""
    answers = InteractiveInput()
    for item in result["state"]:
        request = item.payload.value
        if not isinstance(request, ToolCallInterruptRequest):
            # A workflow interaction has no interrupt id or kind; it is answered by its payload id.
            answers.update(item.payload.id, "ok")
            continue
        kind = request.interrupt_kind
        if kind == InterruptKind.CLIENT_TOOL:
            print(f"[host] client runs {request.tool_name} with {request.client_request}")
            answer = run_on_client(request)
        elif is_approval_kind(kind):
            print(f"[host] approving {kind} for {request.tool_name}")
            answer = interrupt_answer(kind, approved=True)
        else:
            print(f"[host] answering {kind or 'undeclared'} interrupt: {request.message}")
            answer = interrupt_answer(kind, text="ok")
        answers.answer(request.interrupt_id, answer)
    return answers


async def main() -> None:
    api_key = os.getenv("API_KEY", "").strip()
    if not api_key:
        print("Set API_KEY (and optionally MODEL_NAME / API_BASE) to run this demo.")
        return
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key=api_key,
            api_base=os.getenv("API_BASE", "https://api.openai.com/v1").strip(),
        ),
        model_config=ModelRequestConfig(model=os.getenv("MODEL_NAME", "gpt-4.1-mini").strip()),
    )

    await Runner.start()
    try:
        agent = create_deep_agent(
            model=model,
            card=AgentCard(id=f"client_tool_demo_{uuid.uuid4().hex[:8]}", name="ClientToolDemo"),
            workspace=tempfile.mkdtemp(prefix="client_tool_demo_"),
            tools=[ReadClipboardTool()],
            enable_task_loop=False,
            system_prompt="Use read_clipboard when the user refers to what they copied.",
        )
        conversation_id = "client-tool-demo"
        query = "What did I just copy? Summarize it in one line."
        result = await Runner.run_agent(agent, {"query": query, "conversation_id": conversation_id})
        while result.get("result_type") == "interrupt":
            resume = answer_pending(result)
            result = await Runner.run_agent(agent, {"query": resume, "conversation_id": conversation_id})
        print("[agent]", result.get("output"))
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
