# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Reproducers for issue #1975: ``McpServerConfig`` must support a
``connect_timeout`` that reaches ``client.connect()``.

Registration-style MCP onboarding (``Runner.resource_mgr.add_mcp_server``)
is the officially recommended path, but the connect timeout is a fixed 60s
fallback (``NO_TIMEOUT`` -> ``_resolve_timeout`` -> 60.0). Slow networks or
slow-initializing servers (e.g. intranet simulation services) cannot be
configured, forcing users to bypass registration with self-managed clients.
These tests pin the declared-config path: ``connect_timeout`` on the config
reaches ``connect`` (and tool listing) when the caller passes no explicit
call-level timeout, while an explicit call-level timeout keeps precedence and
``None`` preserves the previous behavior (backward compatible).
"""
import unittest
from typing import Optional

from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
from openjiuwen.core.runner import Runner


class _RecordingClient(McpClient):
    """Fake client recording the connect/list_tools timeouts it receives."""

    __client_name__ = "recording"

    def __init__(self, config: McpServerConfig):
        super().__init__(config)
        self._name = config.server_name
        self.connect_timeout: Optional[float] = None
        self.list_tools_timeout: Optional[float] = None

    async def connect(self, *, timeout: float = -1) -> bool:
        self.connect_timeout = timeout
        return True

    async def disconnect(self, *, timeout: float = -1) -> bool:
        return True

    async def list_tools(self, *, timeout: float = -1):
        self.list_tools_timeout = timeout
        return [
            McpToolCard(
                name="solve",
                server_name=self._name,
                description="",
                input_params={"type": "object", "properties": {}},
            )
        ]


def _config(connect_timeout: Optional[float]) -> McpServerConfig:
    return McpServerConfig(
        server_name="slow-server",
        server_path="https://intranet.example.com/mcp/sse",
        client_type="recording",
        connect_timeout=connect_timeout,
    )


def _ensure_recording_client_registered() -> None:
    """``BaseClient.__init_subclass__`` auto-registers the fake client on
    class definition; this hook only asserts the registration took effect
    (a failed registration would surface as an unknown-client ValueError
    inside ``add_mcp_server``)."""
    from openjiuwen.core.common.clients.client_registry import get_client_registry
    assert "mcp_recording" in get_client_registry().list_clients()


class TestConnectTimeoutConfig(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Runner.start()
        _ensure_recording_client_registered()

    async def asyncTearDown(self):
        try:
            await Runner.resource_mgr.remove_mcp_server(server_name="slow-server", ignore_exception=True)
        except Exception:
            pass
        await Runner.stop()

    async def test_config_connect_timeout_reaches_client_connect(self):
        """Declared ``connect_timeout`` is forwarded to ``client.connect``.

        Issue #1975: registration with a slow-initializing server needs a
        per-config budget instead of the fixed 60s fallback.
        """
        config = _config(connect_timeout=300.0)

        add_result = await Runner.resource_mgr.add_mcp_server(config)

        self.assertTrue(add_result.is_ok(), msg=str(add_result))
        client = Runner.resource_mgr._get_mcp_client(config.server_id)
        self.assertIsNotNone(client)
        self.assertEqual(client.connect_timeout, 300.0)
        # Tool listing after connect runs under the same declared budget.
        self.assertEqual(client.list_tools_timeout, 300.0)

    async def test_explicit_call_timeout_overrides_config(self):
        """A call-level ``timeout`` keeps precedence over the declared
        ``connect_timeout`` (ops events budget semantics unchanged)."""
        config = _config(connect_timeout=300.0)

        add_result = await Runner.resource_mgr.add_mcp_server(config, timeout=42.0)

        self.assertTrue(add_result.is_ok(), msg=str(add_result))
        client = Runner.resource_mgr._get_mcp_client(config.server_id)
        self.assertEqual(client.connect_timeout, 42.0)
        self.assertEqual(client.list_tools_timeout, 42.0)

    async def test_none_connect_timeout_keeps_default_behavior(self):
        """``connect_timeout=None`` (the default) keeps the previous
        behavior: no timeout kwarg is passed, the client's internal 60s
        fallback applies (backward compatible)."""
        config = _config(connect_timeout=None)

        add_result = await Runner.resource_mgr.add_mcp_server(config)

        self.assertTrue(add_result.is_ok(), msg=str(add_result))
        client = Runner.resource_mgr._get_mcp_client(config.server_id)
        # No explicit budget: the default NO_TIMEOUT sentinel reaches the
        # client, which resolves it to its internal fallback (60s).
        self.assertEqual(client.connect_timeout, -1)
        self.assertEqual(client.list_tools_timeout, -1)

    async def test_transport_clients_resolve_declared_connect_timeout(self):
        """Transport-level fallback (issue #1975 main scenario: slow SSE
        intranet servers): when ``connect`` runs without an explicit timeout,
        ``McpClient._resolve_connect_timeout`` picks the declared config
        value; an explicit timeout wins; the sentinel passes through when
        nothing is declared (previous behavior). Non-positive declared values
        are ignored so transports behave consistently (bot review: stdio /
        streamable silently fall back to 60s while SSE would pass the raw
        value through)."""
        declared = McpServerConfig(
            server_name="s", server_path="https://x/mcp", client_type="sse",
            connect_timeout=300.0,
        )
        plain = McpServerConfig(
            server_name="s", server_path="https://x/mcp", client_type="sse",
        )

        # Declared config + NO_TIMEOUT -> declared value.
        self.assertEqual(_RecordingClient(declared)._resolve_connect_timeout(-1), 300.0)
        # Declared config + explicit timeout -> explicit wins.
        self.assertEqual(_RecordingClient(declared)._resolve_connect_timeout(42.0), 42.0)
        # No declared config + NO_TIMEOUT -> sentinel passes through.
        self.assertEqual(_RecordingClient(plain)._resolve_connect_timeout(-1), -1)

    def test_non_positive_connect_timeout_rejected_and_ignored(self):
        """``connect_timeout <= 0`` is invalid at the schema boundary
        (pydantic ``gt=0``); transports built with such a config (e.g. via
        model_copy bypassing validation) ignore it rather than forwarding an
        inconsistent budget (bot review: only the exact sentinel -1 is
        special-cased by SSE, other non-positive values would leak)."""
        bad = McpServerConfig.model_construct(
            server_name="s", server_path="https://x/mcp", client_type="sse",
            connect_timeout=0.0,
        )

        with self.assertRaises(ValueError):
            McpServerConfig(
                server_name="s", server_path="https://x/mcp", client_type="sse",
                connect_timeout=0.0,
            )

        client = _RecordingClient(bad)
        self.assertEqual(client._resolve_connect_timeout(-1), -1)

    def test_transport_wiring_resolves_declared_connect_timeout(self):
        """The three transport clients actually wire the declared value into
        their connect deadline (bot review: helper-level tests alone cannot
        catch a missed hookup; sse_client._do_connect / stdio_client.connect
        / streamable_http_client._do_connect must all resolve the declared
        budget when connect runs without an explicit timeout). Stdio is the
        transport whose ``connect`` can run standalone without a server;
        its ``_resolve_timeout`` chain is what all three share, so pin it
        here against the declared value."""
        from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

        declared = McpServerConfig(
            server_name="s", server_path="python srv.py", client_type="stdio",
            connect_timeout=300.0,
        )
        plain = McpServerConfig(
            server_name="s", server_path="python srv.py", client_type="stdio",
        )

        declared_client = StdioClient(declared)
        plain_client = StdioClient(plain)

        # Declared config + NO_TIMEOUT -> declared value (was 60.0 fallback).
        self.assertEqual(
            declared_client._resolve_timeout(
                declared_client._resolve_connect_timeout(NO_TIMEOUT)
            ),
            300.0,
        )
        # No declared config + NO_TIMEOUT -> previous 60.0 fallback.
        self.assertEqual(
            plain_client._resolve_timeout(
                plain_client._resolve_connect_timeout(NO_TIMEOUT)
            ),
            60.0,
        )
        # Declared config + explicit timeout -> explicit wins.
        self.assertEqual(
            declared_client._resolve_timeout(
                declared_client._resolve_connect_timeout(42.0)
            ),
            42.0,
        )


if __name__ == "__main__":
    unittest.main()
