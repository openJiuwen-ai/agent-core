# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.harness.schema.code_graph import CodeGraphRunState
from openjiuwen.harness.tools.code_graph.session import (
    MAX_LOCALIZATION_SESSIONS,
    create_localization,
    drop_localization,
    localization_session_count,
    refine_localization,
    reset_localization_sessions,
)

pytestmark = pytest.mark.level0


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:
    reset_localization_sessions()
    yield
    reset_localization_sessions()


def test_remember_payload_drops_source_text() -> None:
    state = CodeGraphRunState()
    state.remember_payload(
        {
            "evidence_id": "read:pkg/a.py:1:4:abcd",
            "file": "pkg/a.py",
            "symbol_id": "pkg/a.py::foo",
            "name": "foo",
            "kind": "function",
            "start_line": 1,
            "end_line": 4,
            "content": "def foo():\n    return 1\n",
            "matches": [{"symbol_id": "pkg/a.py::foo", "file": "pkg/a.py", "content": "nope"}],
        }
    )
    stored = state.read_evidence["read:pkg/a.py:1:4:abcd"]
    assert stored["file"] == "pkg/a.py"
    assert stored["symbol_id"] == "pkg/a.py::foo"
    assert "content" not in stored
    assert "matches" not in stored
    assert "content" not in state.candidates["pkg/a.py::foo"]


def test_localization_sessions_evict_oldest(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        "openjiuwen.harness.tools.code_graph.session.MAX_LOCALIZATION_SESSIONS",
        2,
    )
    first = create_localization(str(tmp_path), "task-a")
    create_localization(str(tmp_path), "task-b")
    create_localization(str(tmp_path), "task-c")
    assert localization_session_count() == 2
    with pytest.raises(KeyError):
        refine_localization(first.artifact_id)


def test_drop_localization_removes_one_episode(tmp_path) -> None:
    keep = create_localization(str(tmp_path), "keep")
    drop = create_localization(str(tmp_path), "drop")
    drop_localization(artifact_id=drop.artifact_id, session_key=drop.key)
    assert localization_session_count() == 1
    refine_localization(keep.artifact_id)
    with pytest.raises(KeyError):
        refine_localization(drop.artifact_id)


def test_max_sessions_constant_is_bounded() -> None:
    assert MAX_LOCALIZATION_SESSIONS <= 64
