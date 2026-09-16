# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for swarmflow runner journal/WAL path wiring (``_resolve_journal_path``)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.workflow.runner import (
    _resolve_journal_path,
    _resolve_legacy_resume,
    _resolve_wal_path,
)
from openjiuwen.core.common.exception.errors import BaseError


def teardown_function():
    paths.reset_openjiuwen_home()


def _write_script(tmp_path: Path, name: str | None) -> str:
    """Write a minimal swarmflow script and return its path."""
    if name is None:
        meta = '{"description": "d"}'
    else:
        meta = f'{{"name": "{name}", "description": "d"}}'
    script = tmp_path / "wf.py"
    script.write_text(f"META = {meta}\nasync def run(args):\n    return 1\n", encoding="utf-8")
    return str(script)


def test_resolve_journal_path_maps_to_session_workflow(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    result = _resolve_journal_path(script, "demo-team", "sess-1")

    expected = paths.workflow_journal_path("demo-team", "sess-1", "myflow")
    assert Path(result) == expected
    assert expected.parent.is_dir()  # parent dir is created for Journal.save


def test_resolve_journal_path_is_per_run_with_run_id(tmp_path):
    """A run_id splits the journal per-run: journal-{run_id}.jsonl.

    Two concurrent runs of the same workflow then snapshot to separate files
    and never overwrite each other (the journal race on a shared file).
    """
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    shared = _resolve_journal_path(script, "demo-team", "sess-1")
    per_run = _resolve_journal_path(script, "demo-team", "sess-1", "wf_abc123")

    assert Path(per_run).name == "journal-wf_abc123.jsonl"
    assert per_run != shared
    assert Path(per_run).parent == Path(shared).parent
    assert Path(per_run).parent.is_dir()


def test_resolve_wal_path_is_per_run_with_run_id(tmp_path):
    """A run_id splits the WAL per-run: wal/{run_id}.wal (with the wal/ dir created)."""
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    shared = _resolve_wal_path(script, "demo-team", "sess-1")
    per_run = _resolve_wal_path(script, "demo-team", "sess-1", "wf_abc123")

    assert Path(per_run) == Path(shared).parent / "wal" / "wf_abc123.wal"
    assert Path(per_run).parent.is_dir()  # wal/ dir is created before the append

    # Legacy shape: no run_id → the shared sidecar journal.jsonl.wal.
    assert Path(shared).name == "journal.jsonl.wal"


def test_resolve_wal_path_returns_none_without_meta_name(tmp_path):
    """An unreadable / nameless META disables the WAL rather than breaking the launch."""
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, None)

    assert _resolve_wal_path(script, "demo-team", "sess-1", "wf_abc123") is None


def test_resolve_journal_path_defaults_blank_session(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    result = _resolve_journal_path(script, "demo-team", "")

    assert Path(result) == paths.workflow_journal_path("demo-team", "default", "myflow")


def test_resolve_journal_path_requires_meta_name(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, None)

    with pytest.raises(BaseError):
        _resolve_journal_path(script, "demo-team", "sess-1")


# ---------------------------------------------------------------------------
# legacy shared-journal read-side back-compat
# ---------------------------------------------------------------------------

def test_resolve_legacy_resume_maps_to_shared_journal_with_run_id(tmp_path):
    """With a run_id the legacy seed is the shared journal.jsonl; without one None."""
    script = _write_script(tmp_path, "demo")
    paths.configure_openjiuwen_home(tmp_path / "home")

    legacy = _resolve_legacy_resume(script, "demo-team", "sess-1", "wf_abc123")
    shared_journal = paths.workflow_journal_path("demo-team", "sess-1", "demo")
    assert legacy == str(shared_journal)

    # No run_id → the caller already uses the shared path itself; no seed.
    assert _resolve_legacy_resume(script, "demo-team", "sess-1", None) is None


def test_resolve_legacy_resume_returns_none_without_meta_name(tmp_path):
    """An unreadable META yields None (the seed must never break the launch)."""
    script = tmp_path / "broken.py"
    script.write_text("META = {}\n", encoding="utf-8")
    paths.configure_openjiuwen_home(tmp_path / "home")
    assert _resolve_legacy_resume(str(script), "demo-team", "sess-1", "wf_abc123") is None
