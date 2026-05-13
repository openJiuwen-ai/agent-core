# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""**Separate process**: **a2a-sdk** (1.0.0) client hits **three** openjiuwen A2A servers (multi-port).

Run **after** ``server_three_openjiuwen_agents_a2a.py`` in another terminal / another process.

For each server base URL, runs *invoke-style* (drain events, print last) and *stream-style*
(print each ``StreamResponse``).

**Process 1** — multi-server::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_three_openjiuwen_agents_a2a.py

**Process 2 (this file)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_open_a2a_sdk_three_jiuwen_servers.py
"""

from __future__ import annotations

import asyncio
import uuid

from google.protobuf.json_format import MessageToDict

from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse

# Must match ``AGENT_SERVERS`` ports in ``server_three_openjiuwen_agents_a2a.py``
SERVER_AGENT_BASE_URLS: tuple[str, ...] = (
    "http://127.0.0.1:8781",
    "http://127.0.0.1:8782",
    "http://127.0.0.1:8783",
)


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
    print(f"  {label}: {payload}")


async def _invoke_style(client, request: SendMessageRequest) -> None:
    last: StreamResponse | None = None
    n = 0
    async for event in client.send_message(request):
        last = event
        n += 1
    print(f"  invoke-style: {n} event(s); last:")
    if last is not None:
        _print_response("last", last)


async def _stream_style(client, request: SendMessageRequest) -> None:
    i = 0
    async for event in client.send_message(request):
        _print_response(f"stream[{i}]", event)
        i += 1


async def _exercise_one_server(base_url: str, index: int) -> None:
    factory = ClientFactory(ClientConfig())
    client = await factory.create_from_url(base_url)
    label = f"server-{index}"
    try:
        print(f"\n{'=' * 20} {label} — {base_url} {'=' * 20}")
        await _invoke_style(
            client,
            _build_request(f"invoke from a2a-sdk to {label}", f"multi-invoke-{index}"),
        )
        await _stream_style(
            client,
            _build_request(f"stream from a2a-sdk to {label}", f"multi-stream-{index}"),
        )
    finally:
        await client.close()


async def main() -> None:
    for i, base in enumerate(SERVER_AGENT_BASE_URLS, start=1):
        await _exercise_one_server(base, i)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
