"""Suite-wide guard proving that offline verification cannot use the network."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator

import pytest

from examples.adaptive_multi_agent_collab.cli import configure_logging

configure_logging()


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def blocked(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access is forbidden in collaboration offline tests")

    async def blocked_async(*args: object, **kwargs: object) -> None:
        blocked(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "gethostbyname", blocked)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", blocked_async)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", blocked_async)
    yield
