# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating Query Rewriter (QR) usage in a multi-turn retrieval scenario.

Query Rewriter rewrites short or elliptical user queries into standalone queries using
conversation context, and optionally compresses long history to control token usage.
This example shows:
  - Building LLM config and an in-memory session context compatible with QR.
  - Simulating multi-turn dialogue: user query → rewrite → standalone_query for retrieval.
  - Optional history compression when message count reaches compress_range.
  - Correct integration: rewrite() before retrieval; then add user and assistant messages to context.

Prerequisites:
  - .env in this directory (see .env.example). Set QR_LLM_* or API_BASE/API_KEY/MODEL_NAME/MODEL_PROVIDER
    so configs.QR_LLM_MODEL_CONFIG is set.
  - Run with agent-core on PYTHONPATH (see below).
"""

import asyncio
from typing import AsyncIterator, List, Optional

from configs import QR_LLM_MODEL_CONFIG
from utils.output import write_output

from openjiuwen.core.context_engine.base import (
    ContextStats,
    ContextWindow,
    ModelContext,
)
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage, AssistantMessage
from openjiuwen.core.foundation.tool import Tool, ToolCard, ToolInfo
from openjiuwen.core.retrieval.query_rewriter.query_rewriter import QueryRewriter


class _ZeroTokenCounter(TokenCounter):
    """Token counter that returns 0; used by InMemoryContext for examples."""

    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        return 0

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        return 0

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        return 0


class _NoOpReloaderTool(Tool):
    """Stub reloader tool for InMemoryContext; QR does not use it."""

    async def invoke(self, inputs, **kwargs):
        return {}

    async def stream(self, inputs, **kwargs) -> AsyncIterator:
        if False:
            yield


class InMemoryContext(ModelContext):
    """
    In-memory ModelContext for examples and tests. QR uses only get_messages and set_messages;
    other ModelContext methods are stubbed so this class is a valid concrete implementation.
    """

    def __init__(self, initial_messages: Optional[List[BaseMessage]] = None):
        self._messages: List[BaseMessage] = list(initial_messages or [])
        self._token_counter = _ZeroTokenCounter()
        self._reloader_tool = _NoOpReloaderTool(
            ToolCard(id="qr_example_reloader", name="reloader", description="Unused in QR example")
        )

    def __len__(self) -> int:
        return len(self._messages)

    def get_messages(
        self,
        size: Optional[int] = None,
        with_history: bool = True,
    ) -> List[BaseMessage]:
        if size is None:
            return self._messages.copy()
        return self._messages[-size:] if size > 0 else []

    def set_messages(self, messages: List[BaseMessage], with_history: bool = True) -> None:
        self._messages = list(messages)

    def pop_messages(self, size: int = 1, with_history: bool = True) -> List[BaseMessage]:
        if size <= 0:
            return []
        popped = self._messages[-size:]
        self._messages = self._messages[:-size]
        return popped

    def clear_messages(self, with_history: bool = True) -> None:
        self._messages.clear()

    async def add_messages(
        self,
        message: BaseMessage | List[BaseMessage],
    ) -> List[BaseMessage]:
        to_add = message if isinstance(message, list) else [message]
        self._messages.extend(to_add)
        return to_add

    async def get_context_window(
        self,
        system_messages: Optional[List[BaseMessage]] = None,
        tools: Optional[List] = None,
        window_size: Optional[int] = None,
        dialogue_round: Optional[int] = None,
        **kwargs,
    ) -> ContextWindow:
        return ContextWindow(
            system_messages=system_messages or [],
            context_messages=self._messages.copy(),
            tools=tools or [],
        )

    def statistic(self) -> ContextStats:
        return ContextStats(total_messages=len(self._messages))

    def session_id(self) -> str:
        return "qr_example_session"

    def context_id(self) -> str:
        return "qr_example_context"

    def token_counter(self) -> TokenCounter:
        return self._token_counter

    def reloader_tool(self) -> Tool:
        return self._reloader_tool


# Example dialogue: user asks about "it" / "that" (referring to previous topic).
# With compress_range=4, history length reaches 4 after 2 rounds, so the 3rd rewrite may trigger
# compression (history replaced by one system summary). One extra round makes this visible.
EXAMPLE_TURNS = [
    ("user", "What is our project's tech stack?"),
    ("assistant", "The project uses Python 3.11, FastAPI, LangChain and Chroma. Frontend plans to use React."),
    ("user", "What about deployment?"),
    ("assistant", "Deployment uses Docker containers, Kubernetes in production, and GitHub Actions for CI."),
    ("user", "Does it support multi-tenancy?"),  # elliptical: "it" refers to the system
    ("assistant", "Multi-tenancy is not implemented yet; a future release may add it."),
    ("user", "What about logging and monitoring?"),  # one more round so compression is clearly observable
    ("assistant", "Logging uses ELK; monitoring uses Prometheus + Grafana."),
]


async def run_single_rewrite(
    qr: QueryRewriter,
    ctx: InMemoryContext,
    user_query: str,
    turn_label: str,
) -> None:
    """Run one rewrite and print result; caller is responsible for adding messages to context afterward."""
    write_output("[%s] User: %s", turn_label, user_query)
    result = await qr.rewrite(user_query)
    standalone = result.get("standalone_query", "")
    intention = result.get("intention", "")
    before = result.get("before", user_query)
    write_output("[%s] Rewrite result:", turn_label)
    write_output("  before (raw):     %s", before)
    write_output("  standalone_query: %s", standalone)
    write_output("  intention:        %s", intention)
    if result.get("typo"):
        write_output("  typo (if any):    %s", result["typo"])
    # Detect compression: QR replaces history with a single system message when triggered
    msgs = ctx.get_messages(with_history=True)
    if len(msgs) == 1 and getattr(msgs[0], "role", None) == "system":
        write_output("  [Compression triggered; history replaced with summary]")
    write_output("")
    return result


async def main():
    """Run Query Rewriter example: multi-turn context and optional compression."""
    if QR_LLM_MODEL_CONFIG is None:
        write_output(
            "QR LLM config missing. Set QR_LLM_API_BASE, QR_LLM_API_KEY, QR_LLM_MODEL "
            "(or API_BASE, API_KEY, MODEL_NAME, MODEL_PROVIDER) in .env; see .env.example"
        )
        return

    # Session context: QR reads history from here and may replace it with compressed summary
    ctx = InMemoryContext()
    compress_range = 4
    qr = QueryRewriter(
        cfg=QR_LLM_MODEL_CONFIG,
        ctx=ctx,
        compress_range=compress_range,
        prompt_lang="zh",
    )

    write_output("=" * 60)
    write_output("Query Rewriter (QR) example: multi-turn + standalone query for retrieval")
    write_output("compress_range=%d (history >= %d triggers compression)", compress_range, compress_range)
    write_output("=" * 60)

    # Simulate multi-turn: for each user message, rewrite first (for retrieval), then append user + assistant
    for i, (role, content) in enumerate(EXAMPLE_TURNS):
        if role == "user":
            turn_label = "Turn %d" % (i // 2 + 1)
            await run_single_rewrite(qr, ctx, content, turn_label)
            await ctx.add_messages(UserMessage(content=content))
        else:
            await ctx.add_messages(AssistantMessage(content=content))

    # One more elliptical query to show post-context rewrite
    final_query = "Can you summarize that again?"
    write_output("Final elliptical query (after full dialogue):")
    await run_single_rewrite(qr, ctx, final_query, "Final")
    await ctx.add_messages(UserMessage(content=final_query))
    await ctx.add_messages(
        AssistantMessage(
            content=(
                "Tech stack: Python/FastAPI/LangChain/Chroma, React planned. "
                "Deployment: Docker/K8s, CI with GitHub Actions. "
                "Multi-tenancy not implemented yet."
            )
        )
    )

    write_output("=" * 60)
    write_output("Summary")
    write_output("=" * 60)
    write_output(
        "QR consumes context via get_messages/set_messages. Caller must add user and assistant "
        "messages after each turn. Use rewrite(query) to obtain standalone_query for retrieval."
    )
    write_output("Context message count after example: %d", len(ctx))


if __name__ == "__main__":
    asyncio.run(main())
