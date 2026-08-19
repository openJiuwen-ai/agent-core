# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the cross-process lock guarding team Skill visibility writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_teams.skill.file_lock import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    FileLockTimeout,
    cross_process_file_lock,
    lock_path_for,
)
from tests.test_logger import logger as test_logger


@pytest.mark.level0
def test_lock_path_is_a_hidden_sidecar(tmp_path: Path):
    """The lock is a sibling of the payload, never the payload itself."""
    target = tmp_path / "skills-visibility.json"

    sidecar = lock_path_for(target)

    assert sidecar == tmp_path / ".skills-visibility.json.lock"
    assert sidecar != target


@pytest.mark.level0
def test_lock_creates_the_parent_directory(tmp_path: Path):
    """A workspace that has not been materialized yet can still be locked."""
    target = tmp_path / "not" / "yet" / "skills-visibility.json"

    with cross_process_file_lock(target) as sidecar:
        assert sidecar.parent.is_dir()
        assert sidecar.is_file()


@pytest.mark.level0
def test_lock_leaves_the_protected_file_untouched(tmp_path: Path):
    """Locking must not open or create the payload: os.replace needs it free."""
    target = tmp_path / "skills-visibility.json"

    with cross_process_file_lock(target):
        assert not target.exists()
        # The writer replaces the payload while holding the lock; that only
        # works because no handle on the payload is open.
        target.write_text("{}", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "{}"
    test_logger.info("payload written under the sidecar lock: %s", target)


@pytest.mark.level0
def test_lock_timeout_is_a_timeout_error():
    """Callers may catch the built-in TimeoutError."""
    assert issubclass(FileLockTimeout, TimeoutError)
    assert DEFAULT_LOCK_TIMEOUT_SECONDS == pytest.approx(30.0)


@pytest.mark.level1
def test_lock_is_released_after_the_block(tmp_path: Path):
    """A second acquisition after the block returns immediately."""
    target = tmp_path / "skills-visibility.json"

    with cross_process_file_lock(target, timeout=1.0):
        pass
    with cross_process_file_lock(target, timeout=1.0) as sidecar:
        assert sidecar == lock_path_for(target)


@pytest.mark.level1
def test_body_exception_propagates_and_releases(tmp_path: Path):
    """User errors surface as themselves, not as an acquisition timeout."""
    target = tmp_path / "skills-visibility.json"

    with pytest.raises(ValueError, match="boom"):
        with cross_process_file_lock(target, timeout=1.0):
            raise ValueError("boom")

    with cross_process_file_lock(target, timeout=1.0):
        pass
