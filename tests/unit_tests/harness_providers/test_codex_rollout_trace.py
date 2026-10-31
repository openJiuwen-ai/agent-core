# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tailing Codex's opt-in rollout trace."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from openjiuwen.harness_providers.codex.rollout_trace import CodexRolloutTraceReader


@pytest.mark.level0
def test_rollout_reader_resolves_bundle_local_payloads(tmp_path: Path):
    bundle = tmp_path / "trace-trace-id-thread-id"
    payloads = bundle / "payloads"
    payloads.mkdir(parents=True)
    request = {
        "instructions": "team role",
        "input": [{"type": "message", "role": "user", "content": "inspect"}],
    }
    (payloads / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    event = {
        "schema_version": 1,
        "seq": 3,
        "wall_time_unix_ms": 1_750_000_000_000,
        "thread_id": "thread-1",
        "codex_turn_id": "turn-1",
        "payload": {
            "type": "inference_started",
            "inference_call_id": "inference-1",
            "request_payload": {
                "raw_payload_id": "request-1",
                "kind": {"type": "inference_request"},
                "path": "payloads/request.json",
            },
        },
    }
    (bundle / "trace.jsonl").write_text(
        f"{json.dumps(event)}\n",
        encoding="utf-8",
    )
    received: list[dict] = []
    reader = CodexRolloutTraceReader(root=tmp_path, callback=received.append)

    count = reader._poll_once()

    assert count == 1
    assert len(received) == 1
    assert received[0]["resolved_payloads"]["request_payload"] == request


@pytest.mark.level0
def test_rollout_reader_removes_only_abandoned_owned_roots(
    tmp_path: Path,
    monkeypatch,
):
    from openjiuwen.harness_providers.codex import rollout_trace

    dead = tmp_path / "openjiuwen-codex-rollout-dead"
    live = tmp_path / "openjiuwen-codex-rollout-live"
    dead.mkdir()
    live.mkdir()
    (dead / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 111}),
        encoding="utf-8",
    )
    (live / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 222}),
        encoding="utf-8",
    )
    old = time.time() - 120
    os.utime(dead, (old, old))
    os.utime(live, (old, old))
    monkeypatch.setattr(
        rollout_trace,
        "_pid_is_running",
        lambda pid: pid == 222,
    )

    removed = rollout_trace._cleanup_stale_roots(
        base_dir=tmp_path,
        now=time.time(),
    )

    assert removed == 1
    assert not dead.exists()
    assert live.exists()


@pytest.mark.level0
def test_rollout_reader_skips_trace_roots_owned_by_other_accounts(
    tmp_path: Path,
    monkeypatch,
):
    """The temp root is shared, so the glob also matches other accounts' roots.

    Those must be left alone: the recorded pid belongs to another account's
    session, so this process cannot tell whether the root is still live.
    """
    from openjiuwen.harness_providers.codex import rollout_trace

    if not hasattr(os, "getuid"):
        pytest.skip("file ownership is not comparable on this platform")

    foreign = tmp_path / "openjiuwen-codex-rollout-foreign"
    foreign.mkdir()
    (foreign / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 111}),
        encoding="utf-8",
    )
    old = time.time() - 120
    os.utime(foreign, (old, old))
    monkeypatch.setattr(rollout_trace, "_pid_is_running", lambda pid: False)
    # Stand in for a root created by a different account.
    monkeypatch.setattr(os, "getuid", lambda: os.stat(foreign).st_uid + 1)

    removed = rollout_trace._cleanup_stale_roots(
        base_dir=tmp_path,
        now=time.time(),
    )

    assert removed == 0
    assert foreign.exists()


@pytest.mark.level0
def test_rollout_reader_still_removes_own_abandoned_roots(
    tmp_path: Path,
    monkeypatch,
):
    from openjiuwen.harness_providers.codex import rollout_trace

    owned = tmp_path / "openjiuwen-codex-rollout-owned"
    owned.mkdir()
    (owned / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 111}),
        encoding="utf-8",
    )
    old = time.time() - 120
    os.utime(owned, (old, old))
    monkeypatch.setattr(rollout_trace, "_pid_is_running", lambda pid: False)

    removed = rollout_trace._cleanup_stale_roots(
        base_dir=tmp_path,
        now=time.time(),
    )

    assert removed == 1
    assert not owned.exists()


@pytest.mark.level0
def test_rollout_reader_treats_pid_probe_os_error_as_abandoned(
    tmp_path: Path,
    monkeypatch,
):
    from openjiuwen.harness_providers.codex import rollout_trace

    root = tmp_path / "openjiuwen-codex-rollout-os-error"
    root.mkdir()
    (root / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 333}),
        encoding="utf-8",
    )
    old = time.time() - 120
    os.utime(root, (old, old))

    def raise_os_error(pid: int, signal_number: int) -> None:
        raise OSError(11, "bad executable format")

    monkeypatch.setattr(rollout_trace.os, "kill", raise_os_error)

    removed = rollout_trace._cleanup_stale_roots(
        base_dir=tmp_path,
        now=time.time(),
    )

    assert removed == 1
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rollout_reader_drains_and_removes_root_on_close(tmp_path: Path):
    root = tmp_path / "reader"
    bundle = root / "trace-trace-id-thread-id"
    bundle.mkdir(parents=True)
    event = {
        "seq": 1,
        "payload": {"type": "codex_turn_started"},
    }
    (bundle / "trace.jsonl").write_text(
        f"{json.dumps(event)}\n",
        encoding="utf-8",
    )
    received: list[dict] = []
    reader = CodexRolloutTraceReader(root=root, callback=received.append)

    await reader.aclose()

    assert len(received) == 1
    assert not root.exists()
