from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import openjiuwen.harness.personal_context.personal_context as personal_context_module
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.personal_context import PersonalContext
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error


def _config(
    *,
    enabled: bool = True,
    fetching_enabled: bool = True,
    service_enabled: bool = True,
) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "enabled": enabled,
            "fetching_enabled": fetching_enabled,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": "notes",
                    "provider": "local_files",
                    "enabled": service_enabled,
                    "interval_seconds": 60,
                    "source": {"root_dir": str(Path.cwd())},
                    "credentials": {},
                }
            ],
        }
    )


def _feishu_config(*resources_by_service: tuple[str, ...]) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "enabled": False,
            "fetching_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": f"feishu-{index}",
                    "provider": "feishu",
                    "enabled": True,
                    "interval_seconds": 60,
                    "source": {
                        "mode": "account",
                        "resources": list(resources),
                        **({"document_ids": ["doc-1"]} if "docs" in resources else {}),
                    },
                    "credentials": {},
                }
                for index, resources in enumerate(resources_by_service, start=1)
            ],
        }
    )


def _mock_authorization_io(
    monkeypatch: pytest.MonkeyPatch,
    *,
    granted_scopes: set[str],
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    status = AsyncMock(return_value=(False, set(granted_scopes)))
    begin = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr(personal_context_module, "_lark_cli_auth_status", status)
    monkeypatch.setattr(personal_context_module, "_lark_cli_begin_authorization", begin)
    monkeypatch.setattr(personal_context_module, "_lark_cli_finish_authorization", finish)
    return status, begin, finish


def _cursor_path(home: Path, service_id: str = "notes") -> Path:
    return home / "state" / "cursors" / f"{service_id}.json"


def test_remove_and_restore_fetch_cursor_preserve_raw_bytes(tmp_path: Path) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.parent.mkdir(parents=True)
    original = b"not-json\x00\xff\nraw-cursor"
    cursor.write_bytes(original)

    payload = personal_context.remove_fetch_cursor("notes")

    assert payload == original
    assert not cursor.exists()
    personal_context.restore_fetch_cursor("notes", payload)
    assert cursor.read_bytes() == original


def test_remove_and_restore_fetch_cursor_keep_missing_cursor_absent(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)

    assert personal_context.remove_fetch_cursor("notes") is None
    personal_context.restore_fetch_cursor("notes", None)

    assert not cursor.exists()


def test_restore_fetch_cursor_none_removes_existing_regular_cursor(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.parent.mkdir(parents=True)
    cursor.write_bytes(b"stale-cursor")

    personal_context.restore_fetch_cursor("notes", None)

    assert not cursor.exists()


def test_restore_fetch_cursor_none_removes_oversized_stale_cursor(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.parent.mkdir(parents=True)
    cursor.write_bytes(b"x" * (personal_context_module._MAX_CURSOR_BYTES + 1))

    personal_context.restore_fetch_cursor("notes", None)

    assert not cursor.exists()
    assert list(cursor.parent.glob(f".{cursor.name}.*")) == []


def test_restore_fetch_cursor_accepts_exact_size_limit_and_rejects_oversize(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    maximum = b"x" * personal_context_module._MAX_CURSOR_BYTES

    personal_context.restore_fetch_cursor("notes", maximum)
    assert personal_context.remove_fetch_cursor("notes") == maximum
    with pytest.raises(BaseError):
        personal_context.restore_fetch_cursor("notes", maximum + b"x")
    with pytest.raises(BaseError):
        personal_context.restore_fetch_cursor("notes", "not-bytes")  # type: ignore[arg-type]

    assert not cursor.exists()
    assert list(cursor.parent.glob(f".{cursor.name}.*")) == []


def test_remove_fetch_cursor_rejects_oversize_without_deleting_file(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.parent.mkdir(parents=True)
    payload = b"secret-body" + b"x" * personal_context_module._MAX_CURSOR_BYTES
    cursor.write_bytes(payload)

    with pytest.raises(BaseError) as caught:
        personal_context.remove_fetch_cursor("notes")

    assert cursor.read_bytes() == payload
    assert "secret-body" not in str(caught.value)


def test_cursor_transaction_rejects_non_file_and_cleans_restore_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.mkdir(parents=True)

    with pytest.raises(BaseError):
        personal_context.remove_fetch_cursor("notes")
    with pytest.raises(BaseError):
        personal_context.restore_fetch_cursor("notes", b"raw")
    with pytest.raises(BaseError):
        personal_context.restore_fetch_cursor("notes", None)
    assert cursor.is_dir()

    cursor.rmdir()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("sensitive cursor path")

    monkeypatch.setattr(personal_context_module.os, "replace", fail_replace)
    with pytest.raises(BaseError) as caught:
        personal_context.restore_fetch_cursor("notes", b"secret cursor body")

    assert not cursor.exists()
    assert list(cursor.parent.glob(f".{cursor.name}.*")) == []
    assert "secret cursor body" not in str(caught.value)
    assert str(cursor) not in str(caught.value)


def test_cursor_transaction_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    cursor = _cursor_path(tmp_path)
    cursor.parent.mkdir(parents=True)
    outside = tmp_path / "outside-secret.json"
    outside.write_bytes(b"outside-secret")
    try:
        cursor.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(BaseError) as remove_error:
        personal_context.remove_fetch_cursor("notes")
    with pytest.raises(BaseError) as restore_error:
        personal_context.restore_fetch_cursor("notes", b"replacement-secret")
    with pytest.raises(BaseError) as delete_error:
        personal_context.restore_fetch_cursor("notes", None)

    assert cursor.is_symlink()
    assert outside.read_bytes() == b"outside-secret"
    for error in (remove_error.value, restore_error.value, delete_error.value):
        assert "outside-secret" not in str(error)
        assert str(outside) not in str(error)


@pytest.mark.parametrize("service_id", ["../escape", "nested/service", ".."])
def test_cursor_transaction_rejects_unsafe_service_id(
    tmp_path: Path,
    service_id: str,
) -> None:
    personal_context = PersonalContext(home=tmp_path)

    with pytest.raises(BaseError):
        personal_context.remove_fetch_cursor(service_id)
    with pytest.raises(BaseError):
        personal_context.restore_fetch_cursor(service_id, b"raw")

    assert not (tmp_path.parent / "escape.json").exists()


@pytest.mark.asyncio
async def test_construct_does_not_start_and_activate_requires_configuration(tmp_path: Path) -> None:
    personal_context = PersonalContext(home=tmp_path)
    status = await personal_context.snapshot()
    assert status.state == "CREATED"
    assert not status.configured
    with pytest.raises(BaseError):
        await personal_context.activate_runtime()


@pytest.mark.asyncio
async def test_authorize_feishu_returns_authorized_when_lark_cli_scope_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.personal_context as personal_context_module

    async def ready_auth_status(_scopes: tuple[str, ...]) -> tuple[bool, set[str]]:
        return _ready_auth_status()

    monkeypatch.setattr(personal_context_module, "_lark_cli_auth_status", ready_auth_status)
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(
        PersonalContextConfig.from_dict(
            {
                "enabled": True,
                "fetching_enabled": True,
                "strategy_profile": "rules",
                "fetch_services": [
                    {
                        "service_id": "feishu",
                        "provider": "feishu",
                        "enabled": True,
                        "interval_seconds": 60,
                        "source": {"mode": "wiki_space", "wiki_space_id": "space-1"},
                        "credentials": {},
                    }
                ],
            }
        )
    )

    result = await personal_context.authorize_provider("feishu")

    assert result == {
        "provider": "feishu",
        "state": "authorized",
        "verification_url": None,
        "expires_at": None,
        "error": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("granted_scopes", "authorization_error", "expected_state", "expected_error"),
    [
        (set(), None, "not_authorized", None),
        ({"docs:document.content:read"}, None, "authorization_required", None),
        (
            {"docs:document.content:read", "calendar:calendar.event:read"},
            None,
            "authorized",
            None,
        ),
        (
            {"docs:document.content:read", "calendar:calendar.event:read"},
            "Feishu authorization failed",
            "authorization_failed",
            "Feishu authorization failed",
        ),
    ],
)
async def test_get_authorization_status_normalizes_static_states_without_authorization_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    granted_scopes: set[str],
    authorization_error: str | None,
    expected_state: str,
    expected_error: str | None,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs", "calendar")))
    personal_context._authorization_error = authorization_error
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=granted_scopes)

    result = await personal_context.get_authorization_status("feishu")

    assert result == {
        "provider": "feishu",
        "state": expected_state,
        "verification_url": None,
        "expires_at": None,
        "error": expected_error,
    }
    if authorization_error is None:
        status.assert_awaited_once()
    else:
        status.assert_not_awaited()
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_authorization_status_reports_authorizing_without_side_effects_and_task_is_reclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    _status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_authorization() -> None:
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)

    task = asyncio.create_task(pending_authorization())
    personal_context._authorization_task = task
    personal_context._authorization_challenge = {
        "verification_url": "https://open.feishu.cn/authorize",
        "expires_at": "2026-08-18T12:00:00Z",
        "expires_monotonic": asyncio.get_running_loop().time() + 60.0,
    }
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)

        result = await personal_context.get_authorization_status("feishu")

        assert result == {
            "provider": "feishu",
            "state": "authorizing",
            "verification_url": "https://open.feishu.cn/authorize",
            "expires_at": "2026-08-18T12:00:00Z",
            "error": None,
        }
        begin.assert_not_awaited()
        finish.assert_not_awaited()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_get_authorization_status_returns_stable_failure_when_read_only_status_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    status.side_effect = RuntimeError(
        "token=top-secret device_code=device-secret https://example.invalid/auth?token=top-secret C:/private/user"
    )

    result = await personal_context.get_authorization_status("feishu")

    assert result == {
        "provider": "feishu",
        "state": "authorization_failed",
        "verification_url": None,
        "expires_at": None,
        "error": "Feishu authorization status is unavailable",
    }
    serialized = repr(result)
    for secret in ("top-secret", "device-secret", "example.invalid", "C:/private/user"):
        assert secret not in serialized
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_authorization_status_rejects_unknown_or_unconfigured_provider_without_cli_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())

    with pytest.raises(BaseError):
        await personal_context.get_authorization_status("feishu")
    await personal_context.set_configuration(_config(enabled=False))
    with pytest.raises(BaseError):
        await personal_context.get_authorization_status("github")
    with pytest.raises(BaseError):
        await personal_context.get_authorization_status("feishu")

    status.assert_not_awaited()
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_finished_authorization_task_records_only_stable_sanitized_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize?device_code=device-secret",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = RuntimeError(
        "token=top-secret device_code=device-secret https://example.invalid/auth?token=top-secret C:/private/user"
    )

    started = await personal_context.authorize_provider("feishu")
    task = personal_context._authorization_task
    assert task is not None
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
    assert personal_context._authorization_task is None
    assert personal_context._authorization_challenge is None
    status.reset_mock()
    begin.reset_mock()
    finish.reset_mock()

    result = await personal_context.get_authorization_status("feishu")

    assert started["state"] == "authorizing"
    assert result == {
        "provider": "feishu",
        "state": "authorization_failed",
        "verification_url": None,
        "expires_at": None,
        "error": "Feishu authorization failed",
    }
    serialized = repr(result)
    for secret in ("top-secret", "device-secret", "example.invalid", "C:/private/user"):
        assert secret not in serialized
    status.assert_not_awaited()
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_authorization_task_releases_challenge_and_rechecks_real_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    finish_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def complete_authorization(_device_code: str, *, timeout_seconds: float) -> None:
        del timeout_seconds
        finish_started.set()
        await asyncio.wait_for(finish_release.wait(), timeout=5.0)

    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = complete_authorization
    await personal_context.authorize_provider("feishu")
    task = personal_context._authorization_task
    assert task is not None
    await asyncio.wait_for(finish_started.wait(), timeout=1.0)

    finish_release.set()
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    assert personal_context._authorization_task is None
    assert personal_context._authorization_challenge is None
    assert personal_context._authorization_error is None
    status.reset_mock()
    begin.reset_mock()
    finish.reset_mock()
    status.return_value = (True, {"docs:document.content:read"})

    result = await personal_context.get_authorization_status("feishu")

    assert result == {
        "provider": "feishu",
        "state": "authorized",
        "verification_url": None,
        "expires_at": None,
        "error": None,
    }
    status.assert_awaited_once()
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_authorization_task_releases_challenge_and_propagates_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    finish_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def wait_for_authorization(_device_code: str, *, timeout_seconds: float) -> None:
        del timeout_seconds
        finish_started.set()
        await asyncio.wait_for(finish_release.wait(), timeout=5.0)

    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = wait_for_authorization
    await personal_context.authorize_provider("feishu")
    task = personal_context._authorization_task
    assert task is not None
    try:
        await asyncio.wait_for(finish_started.wait(), timeout=1.0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)

        assert personal_context._authorization_task is None
        assert personal_context._authorization_challenge is None
        assert personal_context._authorization_error is None
        status.reset_mock()
        begin.reset_mock()
        finish.reset_mock()

        result = await personal_context.get_authorization_status("feishu")

        assert result == {
            "provider": "feishu",
            "state": "not_authorized",
            "verification_url": None,
            "expires_at": None,
            "error": None,
        }
        status.assert_awaited_once()
        begin.assert_not_awaited()
        finish.assert_not_awaited()
    finally:
        finish_release.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_shared_feishu_scopes_require_explicit_supplemental_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    documents_scope = "docs:document.content:read"
    calendar_scope = "calendar:calendar.event:read"
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes={documents_scope})
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_authorization(_device_code: str, *, timeout_seconds: float) -> None:
        del timeout_seconds
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)

    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = wait_for_authorization

    await personal_context.set_configuration(_feishu_config(("docs",)))
    assert (await personal_context.get_authorization_status("feishu"))["state"] == "authorized"
    begin.assert_not_awaited()
    finish.assert_not_awaited()

    await personal_context.set_configuration(_feishu_config(("docs",), ("calendar",)))
    supplemental = await personal_context.get_authorization_status("feishu")
    assert supplemental["state"] == "authorization_required"
    required_scopes = status.await_args.args[0]
    assert set(required_scopes) == {documents_scope, calendar_scope}
    assert [service.service_id for service in personal_context._config.fetch_services] == ["feishu-1", "feishu-2"]
    begin.assert_not_awaited()
    finish.assert_not_awaited()

    authorization = await personal_context.authorize_provider("feishu")
    task = personal_context._authorization_task
    assert task is not None
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert authorization == {
            "provider": "feishu",
            "state": "authorizing",
            "verification_url": "https://open.feishu.cn/authorize",
            "expires_at": "2026-08-18T12:00:00Z",
            "error": None,
        }
        begin.assert_awaited_once_with(tuple(sorted({documents_scope, calendar_scope})))
        finish.assert_awaited_once()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_authorization_and_configuration_change_are_linearized_without_registering_stale_scope_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    documents_config = _feishu_config(("docs",))
    expanded_config = _feishu_config(("docs",), ("calendar",))
    await personal_context.set_configuration(documents_config)
    original_required_scopes = personal_context._required_authorization_scopes
    scopes_ready = asyncio.Event()
    release_scopes = asyncio.Event()
    finish_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def pause_after_required_scopes(provider: str) -> tuple[str, ...]:
        scopes = await original_required_scopes(provider)
        scopes_ready.set()
        await asyncio.wait_for(release_scopes.wait(), timeout=5.0)
        return scopes

    async def wait_for_authorization(_device_code: str, *, timeout_seconds: float) -> None:
        del timeout_seconds
        finish_started.set()
        await asyncio.wait_for(finish_release.wait(), timeout=5.0)

    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = wait_for_authorization
    monkeypatch.setattr(personal_context, "_required_authorization_scopes", pause_after_required_scopes)

    authorization = asyncio.create_task(personal_context.authorize_provider("feishu"))
    configuration = None
    try:
        await asyncio.wait_for(scopes_ready.wait(), timeout=1.0)
        configuration = asyncio.create_task(personal_context.set_configuration(expanded_config))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(configuration), timeout=0.05)

        release_scopes.set()
        await asyncio.wait_for(authorization, timeout=1.0)
        await asyncio.wait_for(configuration, timeout=1.0)

        assert personal_context._config == expanded_config
        assert personal_context._authorization_task is None
        assert personal_context._authorization_challenge is None
        assert personal_context._authorization_error is None
        begin.assert_awaited_once_with(("docs:document.content:read",))
        finish.assert_awaited_once()
        status.assert_awaited_once_with(("docs:document.content:read",))
    finally:
        release_scopes.set()
        finish_release.set()
        for task in (authorization, configuration):
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1.0)
        await personal_context._cancel_authorization()


@pytest.mark.asyncio
async def test_changed_configuration_clears_old_authorization_failure_before_rechecking_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_feishu_config(("docs",)))
    personal_context._authorization_error = "Feishu authorization failed"
    full_scopes = {"docs:document.content:read", "calendar:calendar.event:read"}
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=full_scopes)

    await personal_context.set_configuration(_feishu_config(("docs",), ("calendar",)))
    result = await personal_context.get_authorization_status("feishu")

    assert result["state"] == "authorized"
    assert personal_context._authorization_error is None
    status.assert_awaited_once_with(tuple(sorted(full_scopes)))
    begin.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_identical_configuration_keeps_active_authorization_task_and_challenge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    config = _feishu_config(("docs",))
    await personal_context.set_configuration(config)
    status, begin, finish = _mock_authorization_io(monkeypatch, granted_scopes=set())
    finish_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def wait_for_authorization(_device_code: str, *, timeout_seconds: float) -> None:
        del timeout_seconds
        finish_started.set()
        await asyncio.wait_for(finish_release.wait(), timeout=5.0)

    begin.return_value = (
        "device-secret",
        "https://open.feishu.cn/authorize",
        "2026-08-18T12:00:00Z",
    )
    finish.side_effect = wait_for_authorization
    await personal_context.authorize_provider("feishu")
    task = personal_context._authorization_task
    challenge = personal_context._authorization_challenge
    assert task is not None
    try:
        await asyncio.wait_for(finish_started.wait(), timeout=1.0)

        await personal_context.set_configuration(config.model_copy(deep=True))

        assert personal_context._authorization_task is task
        assert personal_context._authorization_challenge is challenge
        assert not task.done()
        status.assert_awaited_once()
        begin.assert_awaited_once()
        finish.assert_awaited_once()
    finally:
        finish_release.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)


def _ready_auth_status() -> tuple[bool, set[str]]:
    return True, {"wiki:node:retrieve", "docs:document.content:read", "drive:file:download"}


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_create_runtime_tasks(tmp_path: Path) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(enabled=False))
    await personal_context.activate_runtime()
    status = await personal_context.snapshot()
    assert status.state == "CONFIGURED"
    assert not status.pipeline_running
    assert personal_context._fetch_tasks == {}


@pytest.mark.asyncio
async def test_fetching_disabled_starts_pipeline_without_provider_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RunningPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = False

        async def start(self) -> None:
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.personal_context.ContextPipelineService",
        RunningPipeline,
    )
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(fetching_enabled=False))
    await personal_context.activate_runtime()

    status = await personal_context.snapshot()
    assert status.state == "RUNNING"
    assert status.pipeline_running is True
    assert status.fetching_enabled is False
    assert status.fetch_service_states == {"notes": "STOPPED"}
    assert personal_context._fetch_tasks == {}

    with pytest.raises(BaseError):
        await personal_context.start_fetch_service("notes")
    await personal_context.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_concurrent_activate_waits_for_one_pipeline_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    instances = 0

    class FakePipeline:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal instances
            instances += 1
            self.running = False

        async def start(self) -> None:
            started.set()
            await release.wait()
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.harness.personal_context.personal_context.ContextPipelineService", FakePipeline)
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    first = asyncio.create_task(personal_context.activate_runtime())
    await started.wait()
    second = asyncio.create_task(personal_context.activate_runtime())
    await asyncio.sleep(0)
    assert instances == 1
    release.set()
    await asyncio.gather(first, second)
    assert (await personal_context.snapshot()).state == "RUNNING"
    await personal_context.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_deactivate_stops_fetch_services_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    personal_context._state = "RUNNING"
    completed_tasks = [asyncio.create_task(asyncio.sleep(0)) for _ in range(2)]
    await asyncio.gather(*completed_tasks)
    personal_context._fetch_tasks = {"first": completed_tasks[0], "second": completed_tasks[1]}

    release = asyncio.Event()
    both_started = asyncio.Event()
    active = 0
    peak = 0

    async def stop_fetch_service(service_id: str, *, timeout_seconds: float) -> None:
        del service_id, timeout_seconds
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await release.wait()
        active -= 1

    monkeypatch.setattr(personal_context, "stop_fetch_service", stop_fetch_service)
    stopping = asyncio.create_task(personal_context.deactivate_runtime(timeout_seconds=1))
    await both_started.wait()
    assert peak == 2
    release.set()
    await stopping
    assert (await personal_context.snapshot()).state == "STOPPED"


@pytest.mark.asyncio
async def test_snapshot_reports_failed_when_running_pipeline_has_stopped(tmp_path: Path) -> None:
    class StoppedPipeline:
        def is_running(self) -> bool:
            return False

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    personal_context._state = "RUNNING"
    personal_context._pipeline_service = StoppedPipeline()  # type: ignore[assignment]
    status = await personal_context.snapshot()
    assert status.state == "FAILED"
    assert status.last_error is not None
    assert status.last_error["operation"] == "snapshot"


@pytest.mark.asyncio
async def test_deactivate_during_starting_consumes_internal_activation_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class DelayedPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = False

        async def start(self) -> None:
            started.set()
            await asyncio.Event().wait()

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.harness.personal_context.personal_context.ContextPipelineService", DelayedPipeline)
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    activation = asyncio.create_task(personal_context.activate_runtime())
    await started.wait()
    await personal_context.deactivate_runtime(timeout_seconds=1)
    with pytest.raises(asyncio.CancelledError):
        await activation
    assert (await personal_context.snapshot()).state == "STOPPED"


@pytest.mark.asyncio
async def test_deactivate_reports_activation_failure_instead_of_ignoring_it(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def fail_during_cancellation() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("activation cleanup failed") from exc

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    activation = asyncio.create_task(fail_during_cancellation())
    await started.wait()
    personal_context._state = "STARTING"
    personal_context._activation_task = activation

    with pytest.raises(BaseError):
        await personal_context.deactivate_runtime(timeout_seconds=1)

    status = await personal_context.snapshot()
    assert status.state == "FAILED"
    assert status.last_error is not None
    assert status.last_error["operation"] == "deactivate_runtime"


@pytest.mark.asyncio
async def test_deactivate_retains_pipeline_when_stop_does_not_finish(tmp_path: Path) -> None:
    release = asyncio.Event()
    stop_started = asyncio.Event()

    class StubbornPipeline:
        def __init__(self) -> None:
            self.running = True

        def is_running(self) -> bool:
            return self.running

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            if release.is_set():
                self.running = False
                return
            stop_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            self.running = False

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    personal_context._state = "RUNNING"
    pipeline = StubbornPipeline()
    personal_context._pipeline_service = pipeline  # type: ignore[assignment]
    stopping = asyncio.create_task(personal_context.deactivate_runtime(timeout_seconds=0.01))
    await stop_started.wait()
    with pytest.raises(BaseError):
        await stopping
    assert personal_context._pipeline_service is pipeline
    assert (await personal_context.snapshot()).state == "FAILED"

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await personal_context.deactivate_runtime(timeout_seconds=1)
    assert personal_context._pipeline_service is None


@pytest.mark.asyncio
async def test_deactivate_stop_error_sets_failed_when_pipeline_reports_running(tmp_path: Path) -> None:
    class FailedPipeline:
        def is_running(self) -> bool:
            return True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            raise build_error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, error_msg="pipeline stop timed out")

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config())
    personal_context._state = "RUNNING"
    pipeline = FailedPipeline()
    personal_context._pipeline_service = pipeline  # type: ignore[assignment]
    with pytest.raises(BaseError):
        await personal_context.deactivate_runtime(timeout_seconds=0.01)
    assert personal_context._pipeline_service is pipeline
    assert (await personal_context.snapshot()).state == "FAILED"


@pytest.mark.asyncio
async def test_deactivate_retains_activation_task_when_cancel_does_not_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = True

        async def start(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            if release.is_set():
                self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.harness.personal_context.personal_context.ContextPipelineService", StubbornPipeline)
    personal_context = PersonalContext(home=tmp_path)
    empty_config = PersonalContextConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": True,
            "strategy_profile": "rules",
            "fetch_services": [],
        }
    )
    await personal_context.set_configuration(empty_config)
    activation = asyncio.create_task(personal_context.activate_runtime())
    await started.wait()
    with pytest.raises(BaseError):
        await personal_context.deactivate_runtime(timeout_seconds=0.01)
    assert personal_context._activation_task is not None
    assert (await personal_context.snapshot()).state == "FAILED"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await activation
    await personal_context.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_reactivation_replaces_pipeline_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queues: list[asyncio.Queue[object]] = []

    class QueuePipeline:
        def __init__(self, *, input_queue: asyncio.Queue[object], **_kwargs: object) -> None:
            queues.append(input_queue)
            self.running = False

        async def start(self) -> None:
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.harness.personal_context.personal_context.ContextPipelineService", QueuePipeline)
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(
        PersonalContextConfig.from_dict(
            {
                "enabled": True,
                "fetching_enabled": True,
                "strategy_profile": "rules",
                "fetch_services": [],
            }
        )
    )
    await personal_context.activate_runtime()
    first_queue = personal_context._pipeline_queue
    assert first_queue.maxsize == 8
    await personal_context.deactivate_runtime(timeout_seconds=1)
    await personal_context.activate_runtime()
    second_queue = personal_context._pipeline_queue
    assert second_queue.maxsize == 8
    assert second_queue is not first_queue
    assert queues == [first_queue, second_queue]
    await personal_context.deactivate_runtime(timeout_seconds=1)
