# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""**a2a-sdk** (1.0.0) ``Client`` calling **openjiuwen** A2A (``Runner`` + ``AgentAdapter``) (invoke + stream-style send).

Uses ``ClientFactory.create_from_url`` so the agent card is loaded from
``<base>/.well-known/agent-card.json`` (standard discovery).

**Terminal 1** — start openjiuwen server::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_openjiuwen_a2a_for_sdk_client.py

**Terminal 2** — this script::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_open_a2a_sdk_to_jiuwen_server.py

There is no separate HTTP "invoke" RPC in the SDK: both paths use ``send_message`` and consume the
returned event iterator. This example uses *invoke-style* (collect all events, print the last) vs
*stream-style* (print each ``StreamResponse`` as it arrives).
"""

from __future__ import annotations

import asyncio
import uuid

from google.protobuf.json_format import MessageToDict

from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse

# Base URL only — card is fetched from /.well-known/agent-card.json
SERVER_AGENT_BASE_URL = "http://127.0.0.1:8772"


def _build_request(query: str, conversation_id: str | None = None) -> SendMessageRequest:
    message = Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_USER,
    )
    message.parts.append(Part(text=query))
    if conversation_id:
        message.context_id = conversation_id
    return SendMessageRequest(message=message)


def _print_response(label: str, event: StreamResponse) -> None:
    payload = MessageToDict(event, preserving_proto_field_name=True)
    print(f"{label}: {payload}")


async def _invoke_style(client, request: SendMessageRequest) -> None:
    """Drain full ``send_message`` iterator and show the final event (common 'invoke' expectation)."""
    last: StreamResponse | None = None
    n = 0
    async for event in client.send_message(request):
        last = event
        n += 1
    print(f"invoke-style: received {n} event(s); last:")
    if last is not None:
        _print_response("last", last)


async def _stream_style(client, request: SendMessageRequest) -> None:
    """Print every streaming event as it is produced."""
    i = 0
    async for event in client.send_message(request):
        _print_response(f"stream[{i}]", event)
        i += 1


async def main() -> None:
    factory = ClientFactory(ClientConfig())
    client = await factory.create_from_url(SERVER_AGENT_BASE_URL)
    try:
        print("--- invoke-style (collect until complete) ---")
        await _invoke_style(
            client,
            _build_request("hello from a2a-sdk client", "sdk-session-1"),
        )

        print("--- stream-style (print each chunk) ---")
        await _stream_style(
            client,
            _build_request("hello stream from a2a-sdk", "sdk-session-2"),
        )
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
