# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for the jiuwenbox sandbox provider.

All tests in this module require a running jiuwenbox service and are gated
on ``RUN_JIUWENBOX_TEST=1`` (per-function via the ``requires_jiuwenbox``
marker). Each test verifies behaviour by inspecting real sandbox state —
either through the jiuwenbox HTTP API (``/api/v1/sandboxes/...``) or via
``SysOperation.fs()`` / ``shell()`` calls that hit the actual container.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb
from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import clear_jiuwenbox_shared_sandbox


# Single source of truth for "this test needs a real jiuwenbox service".
requires_jiuwenbox = pytest.mark.skipif(
    os.environ.get("RUN_JIUWENBOX_TEST") != "1",
    reason="Requires running Jiuwenbox sandbox",
)


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


def _list_sandbox_ids(client: httpx.Client) -> set[str]:
    response = client.get("/api/v1/sandboxes")
    response.raise_for_status()
    return {item["id"] for item in response.json()}


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_shared_{uuid.uuid4().hex[:8]}"
        card_added = False
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=card_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=base_url,
                    sandbox_type="jiuwenbox",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


async def _await_sandbox_file(
    sys_op: SysOperation,
    path: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.1,
):
    """Poll-read ``path`` from the sandbox until it succeeds or times out.

    Background uploads scheduled by ``_upload_preserve_files_best_effort``
    (the fire-and-forget ``loop.create_task(...)`` path used when
    ``_get_sandbox_id`` runs on the event-loop thread) can race with the
    next ``read_file``. Tests that depend on a freshly auto-uploaded
    preserve file must wait for the background task to land bytes in the
    sandbox.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = await sys_op.fs().read_file(path)
    while last.code != StatusCode.SUCCESS.code and loop.time() < deadline:
        await asyncio.sleep(interval)
        last = await sys_op.fs().read_file(path)
    return last


def _build_card(
    *,
    card_id: str,
    base_url: str,
    extra_params: dict[str, Any] | None = None,
) -> SysOperationCard:
    """Build a SysOperationCard pinned to ``base_url`` with optional extra_params."""
    return SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id=card_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=base_url,
                sandbox_type="jiuwenbox",
                idle_ttl_seconds=600,
                extra_params=dict(extra_params or {}),
            ),
            timeout_seconds=30,
        ),
    )


# ===========================================================================
# Existing e2e tests for shared sandbox lifecycle and policy injection.
# ===========================================================================


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_fs_code_shell_share_auto_created_sandbox(sys_op: SysOperation):
    marker = uuid.uuid4().hex[:8]

    fs_path = f"/tmp/jiuwenbox_fs_{marker}.txt"
    fs_content = "fs-visible-to-shell-and-code"
    write_res = await sys_op.fs().write_file(fs_path, fs_content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    shell_read = await sys_op.shell().execute_cmd(f"cat {fs_path}")
    assert shell_read.code == StatusCode.SUCCESS.code
    assert shell_read.data.exit_code == 0
    assert shell_read.data.stdout == fs_content

    code_read = await sys_op.code().execute_code(
        code=f"from pathlib import Path; print(Path({fs_path!r}).read_text())",
        language="python",
    )
    assert code_read.code == StatusCode.SUCCESS.code
    assert code_read.data.exit_code == 0
    assert code_read.data.stdout.strip() == fs_content

    shell_path = f"/tmp/jiuwenbox_shell_{marker}.txt"
    shell_write = await sys_op.shell().execute_cmd(f"printf shell-visible-to-fs > {shell_path}")
    assert shell_write.code == StatusCode.SUCCESS.code
    assert shell_write.data.exit_code == 0

    fs_read = await sys_op.fs().read_file(shell_path)
    assert fs_read.code == StatusCode.SUCCESS.code
    assert fs_read.data.content == "shell-visible-to-fs"

    code_path = f"/tmp/jiuwenbox_code_{marker}.txt"
    code_write = await sys_op.code().execute_code(
        code=f"from pathlib import Path; Path({code_path!r}).write_text('code-visible-to-shell')",
        language="python",
    )
    assert code_write.code == StatusCode.SUCCESS.code
    assert code_write.data.exit_code == 0

    shell_code_read = await sys_op.shell().execute_cmd(f"cat {code_path}")
    assert shell_code_read.code == StatusCode.SUCCESS.code
    assert shell_code_read.data.exit_code == 0
    assert shell_code_read.data.stdout == "code-visible-to-shell"


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_extra_params_shares_sandbox_id_after_memory_cache_cleared(
    sys_op: SysOperation,
    server_endpoint: str,
):
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid.uuid4().hex[:8]
    file_path = f"/tmp/jiuwenbox_extra_{marker}.txt"
    file_content = "visible-via-extra-params-fs"

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

    write_res = await sys_op.fs().write_file(file_path, file_content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    launcher_config = sys_op._run_config.config.launcher_config
    sandbox_id = launcher_config.extra_params.get("sandbox_id")
    assert isinstance(sandbox_id, str) and sandbox_id

    # Remove in-memory cache on purpose. We will explicitly reuse sandbox_id
    # through a brand-new SysOperation card's launcher_config.extra_params.
    clear_jiuwenbox_shared_sandbox(base_url)

    second_card_id = f"jiuwenbox_extra_reuse_{marker}"
    second_card_added = False
    second_card = _build_card(
        card_id=second_card_id,
        base_url=base_url,
        extra_params={"sandbox_id": sandbox_id},
    )

    try:
        add_res = Runner.resource_mgr.add_sys_operation(second_card)
        assert add_res.is_ok()
        second_card_added = True

        second_sys_op = Runner.resource_mgr.get_sys_operation(second_card_id)

        shell_read = await second_sys_op.shell().execute_cmd(f"cat {file_path}")
        assert shell_read.code == StatusCode.SUCCESS.code
        assert shell_read.data.exit_code == 0
        assert shell_read.data.stdout == file_content

        code_read = await second_sys_op.code().execute_code(
            code=f"from pathlib import Path; print(Path({file_path!r}).read_text())",
            language="python",
        )
        assert code_read.code == StatusCode.SUCCESS.code
        assert code_read.data.exit_code == 0
        assert code_read.data.stdout.strip() == file_content
    finally:
        if second_card_added:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=second_card_id)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        after_ids = _list_sandbox_ids(client)
    assert sandbox_id in after_ids
    assert len(after_ids - before_ids) == 1


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_extra_params_policy_and_policy_mode_are_used_to_create_sandbox(
    server_endpoint: str,
    monkeypatch,
):
    base_url = _normalize_endpoint(server_endpoint)
    marker = uuid.uuid4().hex[:8]
    policy_name = f"jiuwenbox-extra-policy-{marker}"
    extra_dir = f"/tmp/jiuwenbox-extra-policy-{marker}"

    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_extra_policy_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            extra_params={
                "policy_mode": "append",
                "policy": {
                    "name": policy_name,
                    "filesystem_policy": {
                        "directories": [{
                            "path": extra_dir,
                            "permissions": "0777",
                        }],
                        "read_write": [extra_dir],
                    },
                },
            },
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True

            sys_op = Runner.resource_mgr.get_sys_operation(card_id)
            file_path = f"{extra_dir}/created.txt"
            write_res = await sys_op.fs().write_file(
                file_path,
                "created-with-extra-policy",
                prepend_newline=False,
            )
            assert write_res.code == StatusCode.SUCCESS.code

            launcher_config = sys_op._run_config.config.launcher_config
            sandbox_id = launcher_config.extra_params.get("sandbox_id")
            assert isinstance(sandbox_id, str) and sandbox_id

            policy_resp = client.get(f"/api/v1/policies/{sandbox_id}")
            assert policy_resp.status_code == 200
            policy = policy_resp.json()
            assert policy["name"] == policy_name
            assert {"path": extra_dir, "permissions": "0777"} in policy["filesystem_policy"]["directories"]
            assert extra_dir in policy["filesystem_policy"]["read_write"]
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


# ===========================================================================
# E2E tests: ``force_recreate_jiuwenbox_sandbox``.
# ===========================================================================


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_force_recreate_creates_new_sandbox_on_remote_and_replaces_stale_cache(
    sys_op: SysOperation,
    server_endpoint: str,
):
    """``force_recreate`` must:

    - actually create a fresh sandbox on the remote service;
    - clear stale ``_shared_sandbox_ids`` entries scoped to the same base URL;
    - write the new sandbox_id back into the shared cache so subsequent
      providers reuse it.
    """
    base_url = _normalize_endpoint(server_endpoint)

    # Ensure ``sys_op`` has materialised its sandbox so the shared cache has
    # an entry to be replaced.
    write_res = await sys_op.fs().write_file(
        f"/tmp/jiuwenbox_force_pre_{uuid.uuid4().hex[:8]}.txt",
        "pre-recreate",
        prepend_newline=False,
    )
    assert write_res.code == StatusCode.SUCCESS.code

    cache = jb._JiuwenBoxProviderMixin._shared_sandbox_ids
    original_id = cache.get(base_url.rstrip("/"))
    assert isinstance(original_id, str) and original_id

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        ids_before_recreate = _list_sandbox_ids(client)
        assert original_id in ids_before_recreate

        new_id = await jb.force_recreate_jiuwenbox_sandbox(base_url)

        # New id is fresh and different from the original.
        assert isinstance(new_id, str) and new_id
        assert new_id != original_id

        # Remote service really has the new sandbox.
        ids_after_recreate = _list_sandbox_ids(client)
        assert new_id in ids_after_recreate

        # Shared cache now points at the new id (no leftover ``base_url``
        # entry pointing at the original).
        assert cache.get(base_url.rstrip("/")) == new_id


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_force_recreate_with_policy_applies_policy_to_new_sandbox(
    server_endpoint: str,
    monkeypatch,
):
    """``force_recreate`` with ``policy`` / ``policy_mode`` must produce a
    sandbox whose remote policy reflects the request.
    """
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    marker = uuid.uuid4().hex[:8]
    policy_name = f"jiuwenbox-force-policy-{marker}"
    extra_dir = f"/tmp/jiuwenbox-force-policy-{marker}"

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)
        try:
            new_id = await jb.force_recreate_jiuwenbox_sandbox(
                base_url,
                policy={
                    "name": policy_name,
                    "filesystem_policy": {
                        "directories": [{
                            "path": extra_dir,
                            "permissions": "0777",
                        }],
                        "read_write": [extra_dir],
                    },
                },
                policy_mode="append",
            )
            assert isinstance(new_id, str) and new_id
            assert new_id in _list_sandbox_ids(client)

            policy_resp = client.get(f"/api/v1/policies/{new_id}")
            assert policy_resp.status_code == 200
            policy = policy_resp.json()
            assert policy["name"] == policy_name
            assert {"path": extra_dir, "permissions": "0777"} in policy["filesystem_policy"]["directories"]
            assert extra_dir in policy["filesystem_policy"]["read_write"]
        finally:
            clear_jiuwenbox_shared_sandbox(base_url)
            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_force_recreate_uploads_preserve_files_into_new_sandbox(
    server_endpoint: str,
    monkeypatch,
    tmp_path: Path,
):
    """``force_recreate`` with ``preserve_files_upload`` should make the host
    files appear inside the brand-new sandbox (verified via the jiuwenbox
    ``download`` HTTP API).
    """
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    marker = uuid.uuid4().hex[:8]
    sandbox_target = f"/tmp/jiuwenbox_preserve_force_{marker}.txt"
    payload = b"preserve-payload-via-force-recreate"

    host_file = tmp_path / "AGENT.md"
    host_file.write_bytes(payload)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)
        try:
            new_id = await jb.force_recreate_jiuwenbox_sandbox(
                base_url,
                preserve_files_upload=[
                    {
                        "host_path": str(host_file),
                        "sandbox_path": sandbox_target,
                        "kind": "file",
                    }
                ],
            )
            assert new_id in _list_sandbox_ids(client)

            # Remote sandbox really has the file with the host bytes.
            download_resp = client.get(
                f"/api/v1/sandboxes/{new_id}/download",
                params={"sandbox_path": sandbox_target},
            )
            assert download_resp.status_code == 200, download_resp.text
            assert download_resp.content == payload
        finally:
            clear_jiuwenbox_shared_sandbox(base_url)
            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


# ===========================================================================
# E2E tests: provider auto-uploads ``preserve_files_upload`` when it creates
# a new sandbox during ``_get_sandbox_id``.
# ===========================================================================


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_preserve_file_uploaded_when_provider_creates_new_sandbox(
    server_endpoint: str,
    monkeypatch,
    tmp_path: Path,
):
    """Configuring ``preserve_files_upload`` in ``extra_params`` makes the
    host file land inside the auto-created sandbox the first time any
    provider operation is invoked.
    """
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    marker = uuid.uuid4().hex[:8]
    sandbox_target = f"/tmp/jiuwenbox_preserve_provider_{marker}.txt"
    payload = "preserve-uploaded-by-provider"

    host_file = tmp_path / "AGENT.md"
    host_file.write_text(payload)

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_preserve_file_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            extra_params={
                "preserve_files_upload": [
                    {
                        "host_path": str(host_file),
                        "sandbox_path": sandbox_target,
                        "kind": "file",
                    }
                ]
            },
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True

            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            # First operation triggers ``_get_sandbox_id`` which creates the
            # sandbox AND schedules the preserve-file upload as a background
            # task (``loop.create_task``). The next ``read_file`` races with
            # that task, so poll until the file lands.
            read_res = await _await_sandbox_file(sys_op, sandbox_target)
            assert read_res.code == StatusCode.SUCCESS.code
            assert read_res.data.content == payload

            # Cross-check via shell exec: file really exists in the sandbox.
            cat_res = await sys_op.shell().execute_cmd(f"cat {sandbox_target}")
            assert cat_res.code == StatusCode.SUCCESS.code
            assert cat_res.data.exit_code == 0
            assert cat_res.data.stdout == payload

            launcher = sys_op._run_config.config.launcher_config
            sandbox_id = launcher.extra_params.get("sandbox_id")
            assert isinstance(sandbox_id, str) and sandbox_id
            assert sandbox_id in _list_sandbox_ids(client)
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_preserve_directory_recurses_into_sandbox(
    server_endpoint: str,
    monkeypatch,
    tmp_path: Path,
):
    """A ``directory`` preserve entry should recurse: every regular file under
    the host directory must appear under the sandbox path with the same
    relative layout.
    """
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    marker = uuid.uuid4().hex[:8]
    sandbox_root = f"/tmp/jiuwenbox_preserve_dir_{marker}"

    # Build a small host directory tree.
    host_dir = tmp_path / "memory"
    (host_dir / "sub").mkdir(parents=True)
    (host_dir / "top.txt").write_text("top-content")
    (host_dir / "sub" / "child.txt").write_text("child-content")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        card_id = f"jiuwenbox_preserve_dir_{marker}"
        card_added = False
        card = _build_card(
            card_id=card_id,
            base_url=base_url,
            extra_params={
                "preserve_files_upload": [
                    {
                        "host_path": str(host_dir),
                        "sandbox_path": sandbox_root,
                        "kind": "directory",
                    }
                ]
            },
        )

        try:
            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            card_added = True

            sys_op = Runner.resource_mgr.get_sys_operation(card_id)

            # Wait for the background preserve upload to finish (see
            # ``_await_sandbox_file`` for the race rationale).
            top_res = await _await_sandbox_file(sys_op, f"{sandbox_root}/top.txt")
            assert top_res.code == StatusCode.SUCCESS.code
            assert top_res.data.content == "top-content"

            child_res = await _await_sandbox_file(sys_op, f"{sandbox_root}/sub/child.txt")
            assert child_res.code == StatusCode.SUCCESS.code
            assert child_res.data.content == "child-content"
        finally:
            if card_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")


@pytest.mark.asyncio
@requires_jiuwenbox
async def test_preserve_file_not_re_uploaded_when_sandbox_id_already_cached(
    server_endpoint: str,
    monkeypatch,
    tmp_path: Path,
):
    """Once a sandbox_id is in the shared cache for ``base_url``, sibling
    providers must reuse it *without* re-uploading the preserve file. We
    verify by mutating the host file after the cache is seeded; if the
    second provider re-uploaded, the sandbox would receive the new bytes.
    """
    base_url = _normalize_endpoint(server_endpoint)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    clear_jiuwenbox_shared_sandbox(base_url)

    marker = uuid.uuid4().hex[:8]
    sandbox_target = f"/tmp/jiuwenbox_preserve_cached_{marker}.txt"
    initial_payload = "initial-bytes"

    host_file = tmp_path / "AGENT.md"
    host_file.write_text(initial_payload)

    extra_params = {
        "preserve_files_upload": [
            {
                "host_path": str(host_file),
                "sandbox_path": sandbox_target,
                "kind": "file",
            }
        ]
    }

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        before_ids = _list_sandbox_ids(client)

        await Runner.start()
        first_card_id = f"jiuwenbox_preserve_cached_first_{marker}"
        second_card_id = f"jiuwenbox_preserve_cached_second_{marker}"
        first_added = False
        second_added = False

        try:
            assert Runner.resource_mgr.add_sys_operation(
                _build_card(card_id=first_card_id, base_url=base_url, extra_params=extra_params)
            ).is_ok()
            first_added = True

            first_op = Runner.resource_mgr.get_sys_operation(first_card_id)

            # Trigger sandbox creation + preserve upload via the first card.
            # The upload is scheduled as a background task, so poll until the
            # file is visible inside the sandbox.
            res = await _await_sandbox_file(first_op, sandbox_target)
            assert res.code == StatusCode.SUCCESS.code
            assert res.data.content == initial_payload

            launcher = first_op._run_config.config.launcher_config
            cached_sandbox_id = launcher.extra_params.get("sandbox_id")
            assert isinstance(cached_sandbox_id, str) and cached_sandbox_id

            # Mutate host file. If the second provider re-uploads on its
            # ``_get_sandbox_id`` path, the sandbox copy would change too.
            host_file.write_text("mutated-bytes-should-not-appear-in-sandbox")

            # Add a second card — same base_url + no policy options ⇒ same
            # shared scope key ⇒ reuses the existing sandbox_id from cache.
            assert Runner.resource_mgr.add_sys_operation(
                _build_card(card_id=second_card_id, base_url=base_url, extra_params=extra_params)
            ).is_ok()
            second_added = True

            second_op = Runner.resource_mgr.get_sys_operation(second_card_id)
            res2 = await second_op.fs().read_file(sandbox_target)
            assert res2.code == StatusCode.SUCCESS.code
            # Sandbox content unchanged ⇒ preserve was NOT re-uploaded.
            assert res2.data.content == initial_payload
        finally:
            if first_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=first_card_id)
            if second_added:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=second_card_id)
            await Runner.stop()
            clear_jiuwenbox_shared_sandbox(base_url)

            after_ids = _list_sandbox_ids(client)
            for sandbox_id in after_ids - before_ids:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")
