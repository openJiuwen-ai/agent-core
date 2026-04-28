# -*- coding: utf-8 -*-
"""Unit tests: LSPServerManager diagnostic wiring — open_file and change_file.

Covers:
  - _ensure_diagnostic_handler() registers the publishDiagnostics handler
  - open_file() registers handler and sends textDocument/didOpen
  - open_file() initialises doc version to 0
  - change_file() registers handler and sends textDocument/didChange
  - change_file() increments document version on each call
  - change_file() accepts explicit content (does not read from disk)
  - change_file() reads from disk when content is None
  - get_pending_diagnostics() delegates to LspDiagnosticRegistry
  - End-to-end: notification routed through manager handler → registry
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.lsp.core.diagnostic_registry import LspDiagnosticRegistry
from openjiuwen.harness.lsp.core.manager import LSPServerManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registry():
    LspDiagnosticRegistry.reset()
    yield
    LspDiagnosticRegistry.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_server(server_id: str = "pyright") -> MagicMock:
    """Create a minimal mock LSPServerInstance with handler tracking."""
    server = MagicMock()
    server.config = MagicMock()
    server.config.server_id = server_id
    server.running = True
    server._handlers: dict[str, list] = {}
    server.send_notification = AsyncMock()

    def _add_handler(method: str, handler) -> None:
        server._handlers.setdefault(method, []).append(handler)

    server.add_notification_handler = MagicMock(side_effect=_add_handler)
    return server


def _make_manager() -> LSPServerManager:
    """Create a bare LSPServerManager without real server configs."""
    m = LSPServerManager()
    m._configs = {}
    m._instances = {}
    m._spawning = {}
    m._extension_map = {}
    m._workspace_root = "/workspace"
    m._diag_handler_instances = set()
    m._doc_versions = {}
    return m


def _get_handler(manager: LSPServerManager, server: MagicMock):
    """Ensure handler is registered and return the publishDiagnostics handler."""
    manager._ensure_diagnostic_handler(server)
    return server._handlers["textDocument/publishDiagnostics"][0]


# ===========================================================================
# 1. _ensure_diagnostic_handler()
# ===========================================================================

class TestEnsureDiagnosticHandler:
    def test_registers_publish_diagnostics_handler(self):
        manager = _make_manager()
        server = _make_mock_server()
        manager._ensure_diagnostic_handler(server)
        server.add_notification_handler.assert_called_once()
        method = server.add_notification_handler.call_args[0][0]
        assert method == "textDocument/publishDiagnostics"

    def test_handler_id_stored_after_registration(self):
        manager = _make_manager()
        server = _make_mock_server()
        manager._ensure_diagnostic_handler(server)
        assert id(server) in manager._diag_handler_instances

    def test_idempotent_second_call_does_not_re_register(self):
        manager = _make_manager()
        server = _make_mock_server()
        manager._ensure_diagnostic_handler(server)
        manager._ensure_diagnostic_handler(server)
        assert server.add_notification_handler.call_count == 1

    def test_different_server_instances_each_get_handler(self):
        manager = _make_manager()
        server_a = _make_mock_server("pyright")
        server_b = _make_mock_server("ruff")
        manager._ensure_diagnostic_handler(server_a)
        manager._ensure_diagnostic_handler(server_b)
        assert server_a.add_notification_handler.call_count == 1
        assert server_b.add_notification_handler.call_count == 1

    def test_handler_uses_server_id_as_server_name(self):
        manager = _make_manager()
        server = _make_mock_server("my-lsp")
        handler = _get_handler(manager, server)

        handler({
            "uri": "file:///workspace/a.py",
            "diagnostics": [
                {"message": "err", "severity": 1,
                 "range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}}}
            ],
        })

        result = LspDiagnosticRegistry.get_instance().get_and_clear()
        assert result[0].server_name == "my-lsp"



# ===========================================================================
# 2. open_file()
# ===========================================================================

class TestOpenFile:
    @pytest.mark.asyncio
    async def test_open_file_registers_diagnostic_handler(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="# content"),
        ):
            await manager.open_file("/workspace/a.py", "python")
        assert id(server) in manager._diag_handler_instances

    @pytest.mark.asyncio
    async def test_open_file_sends_did_open_notification(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="x = 1"),
        ):
            await manager.open_file("/workspace/a.py", "python")
        server.send_notification.assert_called_once()
        method = server.send_notification.call_args[0][0]
        assert method == "textDocument/didOpen"

    @pytest.mark.asyncio
    async def test_open_file_sets_version_zero(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value=""),
        ):
            await manager.open_file("/workspace/a.py", "python")
        # Inspect the sent params
        params = server.send_notification.call_args[0][1]
        assert params["textDocument"]["version"] == 0

    @pytest.mark.asyncio
    async def test_open_file_no_server_returns_gracefully(self):
        manager = _make_manager()
        with patch.object(manager, "get_or_start_server", return_value=None):
            await manager.open_file("/workspace/unknown.xyz", "text")  # must not raise

    @pytest.mark.asyncio
    async def test_open_file_handler_registered_once_on_multiple_calls(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value=""),
        ):
            await manager.open_file("/workspace/a.py", "python")
            await manager.open_file("/workspace/b.py", "python")
        assert server.add_notification_handler.call_count == 1


# ===========================================================================
# 3. change_file()
# ===========================================================================

class TestChangeFile:
    @pytest.mark.asyncio
    async def test_change_file_registers_diagnostic_handler(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="new content"),
        ):
            await manager.change_file("/workspace/a.py", "python")
        assert id(server) in manager._diag_handler_instances

    @pytest.mark.asyncio
    async def test_change_file_sends_did_change_notification(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="updated"),
        ):
            await manager.change_file("/workspace/a.py", "python")
        server.send_notification.assert_called_once()
        method = server.send_notification.call_args[0][0]
        assert method == "textDocument/didChange"

    @pytest.mark.asyncio
    async def test_change_file_increments_version(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="v1"),
        ):
            await manager.change_file("/workspace/a.py", "python")
        params = server.send_notification.call_args[0][1]
        assert params["textDocument"]["version"] == 1

    @pytest.mark.asyncio
    async def test_change_file_version_increments_on_each_call(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="text"),
        ):
            await manager.change_file("/workspace/a.py", "python")
            await manager.change_file("/workspace/a.py", "python")
        # Second call's params
        calls = server.send_notification.call_args_list
        v1 = calls[0][0][1]["textDocument"]["version"]
        v2 = calls[1][0][1]["textDocument"]["version"]
        assert v2 == v1 + 1

    @pytest.mark.asyncio
    async def test_change_file_uses_explicit_content(self):
        manager = _make_manager()
        server = _make_mock_server()
        with patch.object(manager, "get_or_start_server", return_value=server):
            await manager.change_file("/workspace/a.py", "python", content="explicit text")
        params = server.send_notification.call_args[0][1]
        assert params["contentChanges"][0]["text"] == "explicit text"

    @pytest.mark.asyncio
    async def test_change_file_reads_disk_when_content_none(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="from disk") as mock_read,
        ):
            await manager.change_file("/workspace/a.py", "python", content=None)
        mock_read.assert_called_once()
        params = server.send_notification.call_args[0][1]
        assert params["contentChanges"][0]["text"] == "from disk"

    @pytest.mark.asyncio
    async def test_change_file_sends_full_content_change(self):
        """contentChanges must contain a single entry with 'text' and NO 'range'."""
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value="full"),
        ):
            await manager.change_file("/workspace/a.py", "python")
        params = server.send_notification.call_args[0][1]
        changes = params["contentChanges"]
        assert len(changes) == 1
        assert "text" in changes[0]
        assert "range" not in changes[0]

    @pytest.mark.asyncio
    async def test_change_file_no_server_returns_gracefully(self):
        manager = _make_manager()
        with patch.object(manager, "get_or_start_server", return_value=None):
            await manager.change_file("/workspace/unknown.xyz", "text")  # must not raise

    @pytest.mark.asyncio
    async def test_change_file_handler_registered_once_for_same_server(self):
        manager = _make_manager()
        server = _make_mock_server()
        with (
            patch.object(manager, "get_or_start_server", return_value=server),
            patch("pathlib.Path.read_text", return_value=""),
        ):
            await manager.change_file("/workspace/a.py", "python")
            await manager.change_file("/workspace/b.py", "python")
        assert server.add_notification_handler.call_count == 1



# ===========================================================================
# 4. get_pending_diagnostics()
# ===========================================================================

class TestGetPendingDiagnostics:
    def test_returns_empty_when_nothing_pending(self):
        assert LSPServerManager.get_pending_diagnostics() == []

    def test_returns_diagnostics_from_registry(self):
        reg = LspDiagnosticRegistry.get_instance()
        reg.register(
            "pyright", "file:///workspace/a.py",
            [{"message": "err", "severity": 1,
              "range": {"start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1}}}],
        )
        result = LSPServerManager.get_pending_diagnostics()
        assert len(result) == 1
        assert result[0].uri == "file:///workspace/a.py"

    def test_clears_registry_after_retrieval(self):
        reg = LspDiagnosticRegistry.get_instance()
        reg.register("pyright", "file:///workspace/a.py",
                     [{"message": "err", "severity": 1, "range": {}}])
        LSPServerManager.get_pending_diagnostics()
        assert reg.pending_count == 0

    def test_respects_max_per_file(self):
        reg = LspDiagnosticRegistry.get_instance()
        diags = [{"message": f"e{i}", "severity": 2,
                  "range": {"start": {"line": i, "character": 0},
                            "end": {"line": i, "character": 1}}}
                 for i in range(10)]
        reg.register("pyright", "file:///workspace/a.py", diags)
        result = LSPServerManager.get_pending_diagnostics(max_per_file=3, max_total=100)
        assert len(result[0].diagnostics) == 3

    def test_respects_max_total(self):
        reg = LspDiagnosticRegistry.get_instance()
        for i in range(5):
            uri = f"file:///workspace/f{i}.py"
            diags = [{"message": f"e{j}", "severity": 2,
                      "range": {"start": {"line": j, "character": 0},
                                "end": {"line": j, "character": 1}}}
                     for j in range(5)]
            reg.register("pyright", uri, diags)
        result = LSPServerManager.get_pending_diagnostics(max_per_file=5, max_total=8)
        assert sum(len(f.diagnostics) for f in result) <= 8


# ===========================================================================
# 5. End-to-end: notification flow manager → registry
# ===========================================================================

class TestEndToEnd:
    def _fire_notification(self, manager, server, params):
        handler = server._handlers["textDocument/publishDiagnostics"][0]
        handler(params)

    def test_open_file_handler_routes_to_registry(self):
        manager = _make_manager()
        server = _make_mock_server("pyright")
        manager._ensure_diagnostic_handler(server)

        self._fire_notification(manager, server, {
            "uri": "file:///workspace/main.py",
            "diagnostics": [
                {"message": "Name 'x' undefined", "severity": 1,
                 "range": {"start": {"line": 5, "character": 0},
                           "end": {"line": 5, "character": 1}}},
            ],
        })

        result = LSPServerManager.get_pending_diagnostics()
        assert len(result) == 1
        assert result[0].uri == "file:///workspace/main.py"
        assert result[0].diagnostics[0].severity == 1

    def test_change_file_diagnostics_routed_to_registry(self):
        """Diagnostics from didChange notification also land in the registry."""
        manager = _make_manager()
        server = _make_mock_server("ruff")
        manager._ensure_diagnostic_handler(server)

        # Simulate LSP server responding to didChange with new diagnostics
        self._fire_notification(manager, server, {
            "uri": "file:///workspace/b.py",
            "diagnostics": [
                {"message": "line too long", "severity": 2,
                 "range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 120}}},
            ],
        })

        result = LSPServerManager.get_pending_diagnostics()
        assert len(result) == 1
        assert result[0].server_name == "ruff"
        assert result[0].diagnostics[0].message == "line too long"

    def test_multiple_servers_contribute_to_registry(self):
        manager = _make_manager()
        server_a = _make_mock_server("pyright")
        server_b = _make_mock_server("ruff")
        manager._ensure_diagnostic_handler(server_a)
        manager._ensure_diagnostic_handler(server_b)

        self._fire_notification(manager, server_a, {
            "uri": "file:///workspace/a.py",
            "diagnostics": [{"message": "type error", "severity": 1,
                              "range": {"start": {"line": 0, "character": 0},
                                        "end": {"line": 0, "character": 1}}}],
        })
        self._fire_notification(manager, server_b, {
            "uri": "file:///workspace/b.py",
            "diagnostics": [{"message": "style issue", "severity": 2,
                              "range": {"start": {"line": 5, "character": 0},
                                        "end": {"line": 5, "character": 4}}}],
        })

        result = LSPServerManager.get_pending_diagnostics()
        uris = {f.uri for f in result}
        assert "file:///workspace/a.py" in uris
        assert "file:///workspace/b.py" in uris

    def test_cross_round_dedup_between_open_and_change(self):
        """Same diagnostic seen after didOpen should not reappear after didChange."""
        manager = _make_manager()
        server = _make_mock_server("pyright")
        manager._ensure_diagnostic_handler(server)

        diag = {"message": "same err", "severity": 1,
                "range": {"start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 1}}}

        # Round 1 (simulating didOpen response)
        self._fire_notification(manager, server, {
            "uri": "file:///workspace/a.py",
            "diagnostics": [diag],
        })
        r1 = LSPServerManager.get_pending_diagnostics()
        assert len(r1) == 1

        # Round 2 (simulating didChange response with same diagnostic)
        self._fire_notification(manager, server, {
            "uri": "file:///workspace/a.py",
            "diagnostics": [diag],
        })
        r2 = LSPServerManager.get_pending_diagnostics()
        assert r2 == []  # already delivered — suppressed
