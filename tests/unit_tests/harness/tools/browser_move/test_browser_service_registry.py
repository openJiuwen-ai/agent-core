#!/usr/bin/env python
# coding: utf-8
"""Tests for process-level browser service lifecycle ownership."""
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    _ACTIVE_BROWSER_RUNTIMES,
    reset_active_browser_runtimes,
    reset_managed_browser_runtime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service_registry import (
    BROWSER_SERVICE_REGISTRY,
    BrowserServiceRegistry,
)


def _make_service(*, key: str = "shared") -> BrowserService:
    mcp_cfg = McpServerConfig(
        server_id=f"test-playwright-{key}",
        server_name=f"test-playwright-{key}",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )
    return BrowserService(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
        instance=BrowserInstanceConfig(key=key, driver_mode="managed"),
    )


@pytest.fixture(autouse=True)
def _clear_process_registry():
    BROWSER_SERVICE_REGISTRY.clear()
    _ACTIVE_BROWSER_RUNTIMES.clear()
    yield
    BROWSER_SERVICE_REGISTRY.clear()
    _ACTIVE_BROWSER_RUNTIMES.clear()


def test_registry_assigns_one_heartbeat_owner_and_transfers_it() -> None:
    registry = BrowserServiceRegistry()
    identity = ("browser", "profile", "headed")
    first = MagicMock()
    second = MagicMock()

    registry.acquire(identity, first)
    registry.acquire(identity, second)
    assert registry.activate_binding(identity, first) is True
    assert registry.activate_binding(identity, second) is False
    assert registry.is_heartbeat_owner(identity, first) is True

    first_release = registry.release(identity, first)
    assert first_release.close_mcp_binding is False
    assert first_release.next_heartbeat_owner is second
    assert registry.is_heartbeat_owner(identity, second) is True

    second_release = registry.release(identity, second)
    assert second_release.close_mcp_binding is True
    assert second_release.next_heartbeat_owner is None


@pytest.mark.asyncio
async def test_release_last_task_binding_preserves_managed_chrome() -> None:
    service = _make_service()
    service.acquire_task_binding()
    assert BROWSER_SERVICE_REGISTRY.activate_binding(
        service.lifecycle_identity,
        service,
    ) is True
    service.started = True
    service._browser_agent = MagicMock()
    service._heartbeat_task = None
    driver = MagicMock()
    service._managed_driver = driver
    BROWSER_SERVICE_REGISTRY.register_managed_driver(
        service.lifecycle_identity,
        service,
        driver,
    )

    with patch.object(
        service,
        "_remove_registered_mcp_server",
        AsyncMock(),
    ) as remove_binding:
        await service.release_task_binding()

    remove_binding.assert_awaited_once()
    driver.stop.assert_not_called()
    assert service._managed_driver is driver
    assert service._browser_agent is None
    assert service._heartbeat_task is None
    assert service.started is False


@pytest.mark.asyncio
async def test_explicit_reset_stops_browser_preserved_after_task_release() -> None:
    runtime = object.__new__(BrowserAgentRuntime)
    runtime._service = _make_service()
    runtime._page_generation = 0
    runtime._last_observed_url = "https://example.com"
    runtime._service.acquire_task_binding()
    runtime._service.started = True
    runtime._service._heartbeat_task = None
    driver = MagicMock()
    runtime._service._managed_driver = driver
    _ACTIVE_BROWSER_RUNTIMES.add(runtime)

    with patch.object(
        runtime._service,
        "_remove_registered_mcp_server",
        AsyncMock(),
    ):
        await runtime.release_task_resources()
        reset_count = await reset_active_browser_runtimes()

    assert reset_count == 1
    driver.stop.assert_called_once()
    assert runtime._service._managed_driver is None


@pytest.mark.asyncio
async def test_identity_reset_stops_only_matching_idle_managed_browser(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BROWSER_PROFILE_NAME", "jiuwenclaw")
    monkeypatch.delenv("BROWSER_MANAGED_ARGS", raising=False)
    headed = _make_service(key="")
    headed.acquire_task_binding()
    headed.started = True
    headed._heartbeat_task = None
    headed_driver = MagicMock()
    headed_driver.owns_process = True
    headed._managed_driver = headed_driver
    BROWSER_SERVICE_REGISTRY.register_managed_driver(
        headed.lifecycle_identity,
        headed,
        headed_driver,
    )
    await headed.release_task_binding()

    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "--headless=new")
    headless = _make_service(key="")
    headless.acquire_task_binding()
    headless.started = True
    headless._heartbeat_task = None
    headless_driver = MagicMock()
    headless_driver.owns_process = True
    headless._managed_driver = headless_driver
    BROWSER_SERVICE_REGISTRY.register_managed_driver(
        headless.lifecycle_identity,
        headless,
        headless_driver,
    )
    await headless.release_task_binding()

    reset_count = await reset_managed_browser_runtime(
        browser_key="",
        profile_name="jiuwenclaw",
        display_mode="headed",
        browser_binary="",
    )

    assert reset_count == 1
    headed_driver.stop.assert_called_once()
    headless_driver.stop.assert_not_called()


@pytest.mark.asyncio
async def test_reused_service_waits_for_last_concurrent_task_reference() -> None:
    service = _make_service()
    service.acquire_task_binding()
    service.acquire_task_binding()
    BROWSER_SERVICE_REGISTRY.activate_binding(
        service.lifecycle_identity,
        service,
    )
    service.started = True
    service._browser_agent = MagicMock()

    with patch.object(
        service,
        "_remove_registered_mcp_server",
        AsyncMock(),
    ) as remove_binding:
        await service.release_task_binding()
        remove_binding.assert_not_awaited()
        assert service.started is True
        assert service._browser_agent is not None

        await service.release_task_binding()
        remove_binding.assert_awaited_once()
        assert service.started is False
        assert service._browser_agent is None


@pytest.mark.asyncio
async def test_releasing_heartbeat_owner_keeps_shared_binding_alive() -> None:
    first = _make_service()
    second = _make_service()
    for service in (first, second):
        service.acquire_task_binding()
        service.started = True
        BROWSER_SERVICE_REGISTRY.activate_binding(
            service.lifecycle_identity,
            service,
        )

    first._heartbeat_task = None
    second._heartbeat_task = None
    with patch.object(
        first,
        "_remove_registered_mcp_server",
        AsyncMock(),
    ) as first_remove, patch.object(
        second,
        "_start_heartbeat",
        MagicMock(),
    ) as second_start:
        await first.release_task_binding()

    first_remove.assert_not_awaited()
    second_start.assert_called_once()
    assert BROWSER_SERVICE_REGISTRY.is_heartbeat_owner(
        second.lifecycle_identity,
        second,
    ) is True

    with patch.object(
        second,
        "_remove_registered_mcp_server",
        AsyncMock(),
    ) as second_remove:
        await second.release_task_binding()
    second_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_stops_only_matching_browser_identity() -> None:
    first = _make_service(key="first")
    second = _make_service(key="second")
    first_driver = MagicMock()
    second_driver = MagicMock()
    for service, driver in ((first, first_driver), (second, second_driver)):
        service.acquire_task_binding()
        service.started = True
        service._managed_driver = driver
        BROWSER_SERVICE_REGISTRY.activate_binding(
            service.lifecycle_identity,
            service,
        )
        BROWSER_SERVICE_REGISTRY.register_managed_driver(
            service.lifecycle_identity,
            service,
            driver,
        )

    with patch.object(first, "_remove_registered_mcp_server", AsyncMock()):
        await first.reset()

    first_driver.stop.assert_called_once()
    second_driver.stop.assert_not_called()
    assert first._managed_driver is None
    assert second._managed_driver is second_driver


def test_display_mode_is_part_of_lifecycle_identity(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "--headless=new")
    headless = _make_service()
    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "")
    headed = _make_service()

    assert headless.lifecycle_identity != headed.lifecycle_identity
    assert headless.lifecycle_identity.display_mode == "headless"
    assert headed.lifecycle_identity.display_mode == "headed"
