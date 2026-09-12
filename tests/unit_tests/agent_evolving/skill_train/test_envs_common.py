# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.envs.io_helpers import (
    format_skill_section,
    load_csv_or_json_split,
    write_prediction_artifacts,
)
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout


@pytest.mark.level0
def test_format_skill_section() -> None:
    assert format_skill_section("") == ""
    assert format_skill_section("  ") == ""
    assert format_skill_section("do X") == "## Skill\ndo X\n\n"


@pytest.mark.level0
def test_write_prediction_artifacts(tmp_path: Path) -> None:
    pred_dir = write_prediction_artifacts(
        str(tmp_path),
        "item/1",
        system_prompt="sys",
        user_prompt="user",
        conversation=[{"role": "user", "content": "hi"}],
    )
    assert (Path(pred_dir) / "target_system_prompt.txt").read_text(encoding="utf-8") == "sys"
    assert (Path(pred_dir) / "target_user_prompt.txt").read_text(encoding="utf-8") == "user"
    conversation = json.loads((Path(pred_dir) / "conversation.json").read_text(encoding="utf-8"))
    assert conversation[0]["content"] == "hi"


@pytest.mark.level0
def test_load_csv_or_json_split_json(tmp_path: Path) -> None:
    split = tmp_path / "train"
    split.mkdir()
    (split / "data.json").write_text(
        json.dumps([{"id": "1", "question": "q1", "answer": "a"}]),
        encoding="utf-8",
    )

    def _normalize(row: dict) -> dict:
        return {"id": str(row["id"]), "question": str(row["question"])}

    items = load_csv_or_json_split(str(split), _normalize, env_label="demo split")
    assert items == [{"id": "1", "question": "q1"}]


@pytest.mark.level0
def test_run_parallel_rollout_resume(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    out_root.mkdir()
    results_path = out_root / "results.jsonl"
    results_path.write_text(
        json.dumps({"id": "a", "hard": 1, "soft": 1.0}) + "\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def _process(item: dict) -> dict:
        seen.append(str(item["id"]))
        return {"id": str(item["id"]), "hard": 0, "soft": 0.0}

    results = run_parallel_rollout(
        [{"id": "a"}, {"id": "b"}],
        str(out_root),
        process_one=_process,
        workers=2,
        task_timeout=None,
    )
    assert seen == ["b"]
    assert [row["id"] for row in results] == ["a", "b"]
    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


@pytest.mark.level0
def test_run_parallel_rollout_timeout(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    block = threading.Event()

    def _process(item: dict) -> dict:
        block.wait(timeout=30)
        return {"id": str(item["id"]), "hard": 1, "soft": 1.0}

    def _timeout(item: dict) -> dict:
        return {
            "id": str(item["id"]),
            "hard": 0,
            "soft": 0.0,
            "phase": "timeout",
            "fail_reason": "task-timeout-1s",
            "agent_ok": False,
        }

    results = run_parallel_rollout(
        [{"id": "slow"}],
        str(out_root),
        process_one=_process,
        workers=1,
        task_timeout=1,
        make_timeout_result=_timeout,
        poll_interval=0.1,
    )
    block.set()
    assert len(results) == 1
    assert results[0]["phase"] == "timeout"
    assert results[0]["id"] == "slow"
