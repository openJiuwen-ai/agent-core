# coding: utf-8
"""Shared test helpers for context_engine unit tests."""

from openjiuwen.core.foundation.llm import AssistantMessage


def make_stream_side_effect(response):
    """Convert a response object into an async gen function for mocking model.stream.

    Usage::

        mock_model.stream = MagicMock(side_effect=make_stream_side_effect(response))

    For side_effect lists (retry scenarios), call the returned function to get
    an async generator object::

        side_effect=[Exception("..."), make_stream_side_effect(response)()]
    """
    content = getattr(response, "content", "") or ""
    parser_content = getattr(response, "parser_content", None)
    tool_calls = getattr(response, "tool_calls", None)
    if not isinstance(tool_calls, list):
        tool_calls = None
    chunk = AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        parser_content=parser_content,
        finish_reason="stop",
    )

    async def _gen(*args, **kwargs):
        yield chunk

    return _gen
