from __future__ import annotations

import asyncio
import errno
import json
import logging

import aiohttp
import pytest

from openjiuwen.harness.personal_context.fetch import retry as retry_module


async def _no_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_retry_provider_read_returns_first_success_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(retry_module, "_sleep", record_sleep)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    result = await retry_module.retry_provider_read(
        operation,
        provider="feishu",
        operation_name="cli_read",
        classify=retry_module.classify_transport_error,
    )

    assert result == "ok"
    assert attempts == 1
    assert delays == []
    assert [record for record in caplog.records if record.name == retry_module.__name__] == []


@pytest.mark.asyncio
async def test_retry_provider_read_recovers_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient")
        return "ok"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(retry_module, "_sleep", record_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    result = await retry_module.retry_provider_read(
        operation,
        provider="feishu",
        operation_name="cli_read",
        classify=retry_module.classify_transport_error,
    )

    records = [record for record in caplog.records if record.name == retry_module.__name__]
    assert result == "ok"
    assert attempts == 2
    assert delays == [1.0]
    assert [record.outcome for record in records] == ["retrying", "recovered"]
    assert [record.reason_kind for record in records] == ["timeout", "timeout"]


@pytest.mark.asyncio
async def test_retry_provider_read_exhausts_after_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    delays: list[float] = []
    expected = TimeoutError("still unavailable")

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise expected

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(retry_module, "_sleep", record_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    with pytest.raises(TimeoutError) as caught:
        await retry_module.retry_provider_read(
            operation,
            provider="feishu",
            operation_name="cli_read",
            classify=retry_module.classify_transport_error,
        )

    records = [record for record in caplog.records if record.name == retry_module.__name__]
    assert caught.value is expected
    assert attempts == 3
    assert delays == [1.0, 2.0]
    assert [record.outcome for record in records] == ["retrying", "retrying", "exhausted"]


@pytest.mark.asyncio
async def test_retry_provider_read_does_not_retry_unclassified_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid schema")

    monkeypatch.setattr(retry_module, "_sleep", _no_sleep)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    with pytest.raises(ValueError, match="invalid schema"):
        await retry_module.retry_provider_read(
            operation,
            provider="github",
            operation_name="rest_json",
            classify=retry_module.classify_transport_error,
        )

    assert attempts == 1
    assert [record for record in caplog.records if record.name == retry_module.__name__] == []


@pytest.mark.asyncio
async def test_retry_provider_read_rejects_non_allowlisted_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("contains-secret")

    monkeypatch.setattr(retry_module, "_sleep", _no_sleep)

    with pytest.raises(RuntimeError, match="contains-secret"):
        await retry_module.retry_provider_read(
            operation,
            provider="feishu",
            operation_name="cli_read",
            classify=lambda _exc: "contains-secret",
        )

    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_provider_read_propagates_cancellation_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(retry_module, "_sleep", _no_sleep)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    with pytest.raises(asyncio.CancelledError):
        await retry_module.retry_provider_read(
            operation,
            provider="feishu",
            operation_name="cli_read",
            classify=retry_module.classify_transport_error,
        )

    assert attempts == 1
    assert [record for record in caplog.records if record.name == retry_module.__name__] == []


@pytest.mark.asyncio
async def test_retry_log_replaces_unsafe_provider_and_operation_labels(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient")
        return "ok"

    monkeypatch.setattr(retry_module, "_sleep", _no_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    caplog.set_level(logging.INFO, logger=retry_module.__name__)

    await retry_module.retry_provider_read(
        operation,
        provider="https://secret.example/token",
        operation_name="C:\\private\\note.md",
        classify=retry_module.classify_transport_error,
    )

    records = [record for record in caplog.records if record.name == retry_module.__name__]
    assert [(record.provider, record.operation) for record in records] == [
        ("invalid", "invalid"),
        ("invalid", "invalid"),
    ]
    assert "secret.example" not in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize(
    ("status", "expected"),
    [(408, "http_408"), (429, "http_429"), (500, "http_5xx"), (599, "http_5xx"), (404, None)],
)
def test_retry_reason_from_http_status(status: int, expected: str | None) -> None:
    assert retry_module.retry_reason_from_http_status(status) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (aiohttp.ClientConnectionError("reset"), "connection"),
        (aiohttp.ClientPayloadError("truncated"), "connection"),
        (TimeoutError("timeout"), "timeout"),
    ],
)
def test_classify_transport_error(error: BaseException, expected: str) -> None:
    assert retry_module.classify_transport_error(error) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OSError(errno.EBUSY, "busy"), "file_busy"),
        (OSError(getattr(errno, "ESTALE", 116), "changed"), "file_changed"),
        (PermissionError(errno.EACCES, "denied"), None),
    ],
)
def test_classify_file_error(error: BaseException, expected: str | None) -> None:
    assert retry_module.classify_file_error(error) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (EOFError("empty"), "empty_response"),
        (json.JSONDecodeError("bad", "x", 0), "invalid_json"),
        (ValueError("schema"), None),
    ],
)
def test_classify_payload_error(error: BaseException, expected: str | None) -> None:
    assert retry_module.classify_payload_error(error) == expected
