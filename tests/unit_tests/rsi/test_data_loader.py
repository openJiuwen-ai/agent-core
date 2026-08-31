# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ``DataLoader``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.config import DataLoaderConfig
from openjiuwen.rsi.harness_rsi.data_loader import DataLoader


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _case_ids(batches: list[list[dict[str, object]]]) -> list[list[object]]:
    """Return loaded case IDs grouped by batch."""
    return [[case["case_id"] for case in batch] for batch in batches]


# JSON 结构加载


def test_load_single_object_json_as_one_case(tmp_path: Path) -> None:
    """DataLoader loads a single JSON object as one case."""
    _write_json(tmp_path / "001_single.json", {"case_id": "case_001"})

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["case_001"]]


def test_load_case_list_json_as_multiple_cases(tmp_path: Path) -> None:
    """DataLoader loads a JSON root list as multiple cases."""
    _write_json(
        tmp_path / "001_list.json",
        [
            {"case_id": "case_001"},
            {"case_id": "case_002"},
        ],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["case_001", "case_002"]]


def test_load_wrapped_cases_json(tmp_path: Path) -> None:
    """DataLoader loads the ``cases`` list from a wrapper object."""
    _write_json(
        tmp_path / "001_wrapped.json",
        {
            "dataset_id": "dataset_001",
            "cases": [
                {"case_id": "case_001"},
                {"case_id": "case_002"},
            ],
        },
    )

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["case_001", "case_002"]]


def test_load_mixed_json_shapes_in_sorted_file_order(tmp_path: Path) -> None:
    """DataLoader expands all supported JSON shapes in sorted file order."""
    _write_json(tmp_path / "001_single.json", {"case_id": "case_001"})
    _write_json(
        tmp_path / "002_list.json",
        [
            {"case_id": "case_002"},
            {"case_id": "case_003"},
        ],
    )
    _write_json(
        tmp_path / "003_wrapped.json",
        {"cases": [{"case_id": "case_004"}]},
    )

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["case_001", "case_002", "case_003", "case_004"]]


# Batch 切分


def test_load_yields_batches_by_batch_size(tmp_path: Path) -> None:
    """DataLoader yields fixed-size batches plus a tail batch."""
    _write_json(
        tmp_path / "001_cases.json",
        [{"case_id": f"case_{index:03d}"} for index in range(1, 6)],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=2))
    batches = list(loader.load(str(tmp_path)))

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert _case_ids(batches) == [
        ["case_001", "case_002"],
        ["case_003", "case_004"],
        ["case_005"],
    ]


def test_load_does_not_yield_empty_tail_batch_when_exactly_divisible(tmp_path: Path) -> None:
    """DataLoader does not emit an empty final batch."""
    _write_json(
        tmp_path / "001_cases.json",
        [{"case_id": f"case_{index:03d}"} for index in range(1, 5)],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=2))
    batches = list(loader.load(str(tmp_path)))

    assert [len(batch) for batch in batches] == [2, 2]
    assert _case_ids(batches) == [
        ["case_001", "case_002"],
        ["case_003", "case_004"],
    ]


def test_load_yields_single_tail_batch_when_total_less_than_batch_size(tmp_path: Path) -> None:
    """DataLoader yields one partial batch when total cases are fewer than batch_size."""
    _write_json(tmp_path / "001_cases.json", [{"case_id": "case_001"}])

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert [len(batch) for batch in batches] == [1]
    assert _case_ids(batches) == [["case_001"]]


def test_load_returns_fresh_iterator_per_call(tmp_path: Path) -> None:
    """Each ``load`` call returns a fresh one-shot iterator."""
    _write_json(
        tmp_path / "001_cases.json",
        [
            {"case_id": "case_001"},
            {"case_id": "case_002"},
        ],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=1))

    first_pass = list(loader.load(str(tmp_path)))
    second_pass = list(loader.load(str(tmp_path)))

    assert _case_ids(first_pass) == [["case_001"], ["case_002"]]
    assert _case_ids(second_pass) == [["case_001"], ["case_002"]]


# file_pattern 行为


def test_load_respects_file_pattern(tmp_path: Path) -> None:
    """DataLoader only loads files matched by config.file_pattern."""
    _write_json(tmp_path / "train_001.json", [{"case_id": "train_case"}])
    _write_json(tmp_path / "dev_001.json", [{"case_id": "dev_case"}])

    loader = DataLoader(DataLoaderConfig(file_pattern="dev_*.json", batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["dev_case"]]


def test_load_ignores_directories_matching_file_pattern(tmp_path: Path) -> None:
    """DataLoader ignores directories even when their names match file_pattern."""
    (tmp_path / "001_fake.json").mkdir()
    _write_json(tmp_path / "002_real.json", [{"case_id": "real_case"}])

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert _case_ids(batches) == [["real_case"]]


# Orchestrator 边界测试


# Iterator 语义


def test_load_returns_iterator_not_list(tmp_path: Path) -> None:
    """DataLoader.load() returns an Iterator, not a materialized list."""
    _write_json(tmp_path / "001_cases.json", [{"case_id": "case_001"}])

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    result = loader.load(str(tmp_path))

    assert not isinstance(result, list)
    assert hasattr(result, "__iter__")
    assert hasattr(result, "__next__")


# setdefault 不覆盖


def test_load_preserves_existing_case_index(tmp_path: Path) -> None:
    """DataLoader uses setdefault so existing case_index is not overwritten."""
    _write_json(
        tmp_path / "001_cases.json",
        [{"case_id": "case_001", "case_index": 99}],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load(str(tmp_path)))

    assert batches[0][0]["case_index"] == 99


def test_load_files_ignores_neighboring_dataset_json(tmp_path: Path) -> None:
    """Explicit request files isolate one split from neighboring JSON files."""
    requested = tmp_path / "validation.json"
    _write_json(requested, [{"case_id": "validation_case"}])
    _write_json(tmp_path / "evaluation.json", [{"case_id": "evaluation_case"}])

    loader = DataLoader(DataLoaderConfig(batch_size=10))
    batches = list(loader.load_files([str(requested)]))

    assert _case_ids(batches) == [["validation_case"]]


# 错误路径


def test_init_raises_for_invalid_batch_size() -> None:
    """Direct DataLoader construction validates batch size."""
    with pytest.raises(ValueError, match="batch_size"):
        DataLoader(DataLoaderConfig(batch_size=0))


def test_load_writes_profile_and_curriculum_balanced_batch_plan(tmp_path: Path) -> None:
    """DataLoader profiles incoming cases and yields a deterministic balanced plan."""
    _write_json(
        tmp_path / "cases.json",
        [
            {
                "case_id": "hard_reasoning",
                "difficulty": "hard",
                "dimension": "reasoning",
                "source": "swe",
                "task_type": "coding_fix",
            },
            {
                "case_id": "easy_reasoning",
                "difficulty": "easy",
                "dimension": "reasoning",
                "source": "manual",
                "task_type": "qa",
            },
            {
                "case_id": "medium_tool",
                "difficulty": "medium",
                "dimension": "tool_use",
                "source": "terminal_bench",
                "task_type": "terminal",
            },
            {
                "case_id": "easy_tool",
                "difficulty": "easy",
                "dimension": "tool_use",
                "source": "terminal_bench",
                "task_type": "terminal",
            },
        ],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=2))
    batches = list(loader.load(str(tmp_path), epoch=1))

    assert [[case["case_id"] for case in batch] for batch in batches] == [
        ["easy_reasoning", "easy_tool"],
        ["medium_tool", "hard_reasoning"],
    ]
    assert loader.batch_plan_path == str((tmp_path / "batch_plan.yaml").resolve())

    profile = yaml.safe_load((tmp_path / "dataset_profile.yaml").read_text(encoding="utf-8"))
    assert profile["summary"]["total_cases"] == 4
    assert profile["summary"]["difficulty"] == {"easy": 2, "medium": 1, "hard": 1}
    assert profile["summary"]["dimension"] == {"reasoning": 2, "tool_use": 2}

    plan = yaml.safe_load((tmp_path / "batch_plan.yaml").read_text(encoding="utf-8"))
    assert plan["strategy"] == "curriculum_balanced"
    assert plan["epoch"] == 1
    assert plan["batch_size"] == 2
    assert plan["batches"][0]["metadata"]["difficulty_stage"] == "easy"
    assert [case["case_id"] for case in plan["batches"][0]["cases"]] == [
        "easy_reasoning",
        "easy_tool",
    ]


def test_load_records_metadata_warnings_for_missing_balance_fields(tmp_path: Path) -> None:
    """Missing case metadata is explicit in profile and batch plan warnings."""
    _write_json(
        tmp_path / "cases.json",
        [
            {"case_id": "complete", "difficulty": "easy", "dimension": "reasoning"},
            {"case_id": "missing_metadata"},
        ],
    )

    loader = DataLoader(DataLoaderConfig(batch_size=2))
    list(loader.load(str(tmp_path)))

    profile = yaml.safe_load((tmp_path / "dataset_profile.yaml").read_text(encoding="utf-8"))
    plan = yaml.safe_load((tmp_path / "batch_plan.yaml").read_text(encoding="utf-8"))

    assert profile["warnings"] == [
        {
            "case_id": "complete",
            "missing_fields": ["source", "task_type"],
        },
        {
            "case_id": "missing_metadata",
            "missing_fields": ["dimension", "difficulty", "source", "task_type"],
        },
    ]
    assert plan["metadata"]["quality"] == "low_quality_fallback"
    assert plan["warnings"] == profile["warnings"]


def test_load_raises_when_dataset_dir_missing(tmp_path: Path) -> None:
    """Missing dataset directory is reported clearly."""
    loader = DataLoader(DataLoaderConfig(batch_size=10))

    with pytest.raises(FileNotFoundError, match="dataset directory not found"):
        list(loader.load(str(tmp_path / "missing")))


def test_load_raises_when_no_json_files(tmp_path: Path) -> None:
    """An empty dataset directory is invalid."""
    (tmp_path / "readme.txt").write_text("not json", encoding="utf-8")
    loader = DataLoader(DataLoaderConfig(batch_size=10))

    with pytest.raises(FileNotFoundError, match="dataset json files not found"):
        list(loader.load(str(tmp_path)))


def test_load_raises_for_invalid_json(tmp_path: Path) -> None:
    """Invalid JSON includes the source file in the error."""
    (tmp_path / "001_bad.json").write_text("{invalid json", encoding="utf-8")
    loader = DataLoader(DataLoaderConfig(batch_size=10))

    with pytest.raises(ValueError, match="dataset json is invalid"):
        list(loader.load(str(tmp_path)))


def test_load_raises_for_non_mapping_case(tmp_path: Path) -> None:
    """Every loaded case must be a JSON object."""
    _write_json(tmp_path / "001_cases.json", ["string_case", {"case_id": "case_001"}])
    loader = DataLoader(DataLoaderConfig(batch_size=10))

    with pytest.raises(ValueError, match="dataset case must be a mapping"):
        list(loader.load(str(tmp_path)))


def test_load_raises_for_non_list_cases_field(tmp_path: Path) -> None:
    """A wrapper object's cases field must be a list."""
    _write_json(tmp_path / "001_cases.json", {"cases": "not_a_list"})
    loader = DataLoader(DataLoaderConfig(batch_size=10))

    with pytest.raises(ValueError, match="dataset cases must be a list"):
        list(loader.load(str(tmp_path)))
