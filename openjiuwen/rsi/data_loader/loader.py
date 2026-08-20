# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load existing evaluation dataset cases from disk."""

from __future__ import annotations

import json
from collections.abc import Iterator
from json import JSONDecodeError
from pathlib import Path

from openjiuwen.rsi.config import DataLoaderConfig
from openjiuwen.rsi.data_loader.batch_planner import BatchPlanner
from openjiuwen.rsi.data_loader.plan_store import BatchPlanStore
from openjiuwen.rsi.data_loader.profiler import DatasetProfiler
from openjiuwen.rsi.schema import CaseMapping


class DataLoader:
    """Load a dataset directory as batches of case mappings."""

    def __init__(self, config: DataLoaderConfig) -> None:
        if config.batch_size < 1:
            raise ValueError("data_loader.batch_size must be greater than or equal to 1")
        self.config = config
        self.batch_plan_path = ""
        self.dataset_profile_path = ""
        self._profiler = DatasetProfiler()
        self._batch_planner = BatchPlanner()
        self._plan_store = BatchPlanStore()

    def load(self, dataset_dir: str, epoch: int = 1) -> Iterator[list[CaseMapping]]:
        """Load dataset cases from JSON files and yield batches.

        Supported dataset file shapes:
        - a single case object: ``{"case_id": "...", ...}``
        - a list of case objects: ``[{...}, {...}]``
        - an object with a ``cases`` list: ``{"cases": [{...}]}``

        Returns a one-shot iterator; call ``load()`` again for a fresh traversal.
        """
        root = Path(dataset_dir).expanduser().resolve()

        if not root.is_dir():
            raise FileNotFoundError(f"dataset directory not found: {root}")

        dataset_files = sorted(path for path in root.glob(self.config.file_pattern) if path.is_file())
        if not dataset_files:
            raise FileNotFoundError(f"dataset json files not found: {root / self.config.file_pattern}")

        cases: list[CaseMapping] = []
        for dataset_file in dataset_files:
            for case_index, case in enumerate(_load_json_cases(dataset_file), start=1):
                loaded_case: CaseMapping = dict(case)
                loaded_case.setdefault("case_path", str(dataset_file))
                loaded_case.setdefault("case_index", case_index)
                cases.append(loaded_case)

        profile = self._profiler.profile(cases, self.config.batch_balance_keys)
        batches = self._batch_planner.plan(cases, self.config.batch_size)
        self.dataset_profile_path = self._plan_store.write_dataset_profile(root, profile)
        self.batch_plan_path = self._plan_store.write_batch_plan(
            root=root,
            epoch=epoch,
            batch_size=self.config.batch_size,
            balance_keys=self.config.batch_balance_keys,
            profile=profile,
            batches=batches,
        )
        for batch in batches:
            yield batch


def _load_json_cases(path: Path) -> list[CaseMapping]:
    """Load one or more case mappings from a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except JSONDecodeError as exc:
        raise ValueError(f"dataset json is invalid: {path}") from exc

    if isinstance(data, dict):
        if isinstance(data.get("cases"), list):
            raw_cases = data["cases"]
        elif "cases" in data:
            raise ValueError(f"dataset cases must be a list: {path}")
        else:
            raw_cases = [data]
    elif isinstance(data, list):
        raw_cases = data
    else:
        raise ValueError(f"dataset json must be a case object, case list, or object with cases: {path}")

    cases: list[CaseMapping] = []
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"dataset case must be a mapping: {path}#{index}")
        cases.append(case)
    return cases


__all__ = [
    "DataLoader",
]
