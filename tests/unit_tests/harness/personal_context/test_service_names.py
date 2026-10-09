"""Service names remain identities while filesystem components stay bounded."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.context_pipeline import ContextPipelineService
from openjiuwen.harness.personal_context.fetch import gitcode, github
from openjiuwen.harness.personal_context.models import FetchBatch
from openjiuwen.harness.personal_context.personal_context import PersonalContext


def _config(name: str) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": False,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "fetch_services": [
                {
                    "service_id": name,
                    "provider": "local_files",
                    "enabled": False,
                    "interval_seconds": 60,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": "~/notes"},
                    "credentials": {},
                }
            ],
        }
    )


NAMES = ["中文 名称", "../escape", "..", ".", "a/b\\c:*?<>|", "CON", "notes.", "中" * 500, "😀" * 250]


@pytest.mark.parametrize("name", NAMES)
def test_config_accepts_display_names(name: str) -> None:
    assert _config(name).fetch_services[0].service_id == name


@pytest.mark.parametrize("name", ["", " \t\n", "a" * 501, "中" * 501, "😀" * 251])
def test_config_rejects_empty_or_overlong_names(name: str) -> None:
    with pytest.raises(BaseError):
        _config(name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.asyncio
async def test_named_service_cursor_roundtrip_and_rollback(tmp_path: Path, name: str) -> None:
    core = PersonalContext(home=tmp_path)
    await core.set_configuration(_config(name))
    core._write_cursor(name, {"offset": 42})
    (path,) = (tmp_path / "state" / "cursors").iterdir()
    assert len(path.name) < 128
    assert json.loads(path.read_text(encoding="utf-8"))["service_id"] == name
    assert core._read_cursor(name) == {"offset": 42}
    saved = core.remove_fetch_cursor(name)
    assert saved is not None
    assert not path.exists()
    core.restore_fetch_cursor(name, saved)
    assert core._read_cursor(name) == {"offset": 42}
    core.restore_fetch_cursor(name, None)
    assert not path.exists()
    assert not (tmp_path / "state" / "escape.json").exists()


@pytest.mark.parametrize("name", NAMES)
def test_run_history_uses_bounded_path(tmp_path: Path, name: str) -> None:
    core = PersonalContext(home=tmp_path)
    core._write_run_history(name, [])
    (path,) = (tmp_path / "state" / "run-history").iterdir()
    assert len(path.name) < 128
    assert core._read_run_history(name) == []


@pytest.mark.parametrize("provider", [github, gitcode])
@pytest.mark.parametrize("name", NAMES)
def test_provider_cache_stays_in_one_bounded_service_directory(tmp_path: Path, name: str, provider) -> None:
    root = provider._service_root(tmp_path, name)
    assert root.parent == tmp_path / "materialized-sources" / provider.__name__.rsplit(".", 1)[-1]
    assert len(root.name) < 128
    root.mkdir(parents=True)
    (root / "proof.txt").write_text("safe", encoding="utf-8")
    assert provider._service_root(tmp_path, name) == root


@pytest.mark.asyncio
async def test_pipeline_keeps_name_in_run_identity_and_cleans_hashed_sandbox(tmp_path: Path) -> None:
    name = "中文 / " + "长" * 495
    queue: asyncio.Queue[object] = asyncio.Queue()
    pipeline = ContextPipelineService(home=tmp_path, config=_config(name), input_queue=queue)
    await pipeline.start()
    try:
        completion = asyncio.get_running_loop().create_future()
        await queue.put(("batch", name, "run-1", FetchBatch(batch_id="batch-1"), completion))
        await asyncio.wait_for(asyncio.shield(completion), timeout=5)
        assert (name, "run-1") in pipeline._run_states
        sandbox = pipeline._run_states[(name, "run-1")]["sandbox"]
        assert sandbox.parent.parent == tmp_path / "workspace" / "sandboxes"
        assert len(sandbox.parent.name) < 128
        pipeline.invalidate_run(name, "run-1")
        assert (name, "run-1") in pipeline._invalidated_run_keys
    finally:
        await pipeline.stop(timeout_seconds=1)
    assert not sandbox.exists()
    sandbox.mkdir(parents=True)
    (sandbox / "stale.txt").write_text("stale", encoding="utf-8")
    await pipeline.start()
    try:
        assert not sandbox.exists()
    finally:
        await pipeline.stop(timeout_seconds=1)


def test_storage_namespace_does_not_collide_with_literal_name(tmp_path: Path) -> None:
    core = PersonalContext(home=tmp_path)
    first = core._run_history_path("中文")
    second = core._run_history_path(first.stem)
    assert first != second


def test_legacy_service_paths_are_unchanged(tmp_path: Path) -> None:
    core = PersonalContext(home=tmp_path)
    core.restore_fetch_cursor("notes", b"legacy")
    assert (tmp_path / "state" / "cursors" / "notes.json").read_bytes() == b"legacy"
    assert core._run_history_path("notes") == tmp_path / "state" / "run-history" / "notes.json"
    for provider in (github, gitcode):
        assert provider._service_root(tmp_path, "notes").name == "notes"
