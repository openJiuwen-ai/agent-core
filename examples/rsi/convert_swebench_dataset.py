# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Export legacy SWE cases as a portable four-field dataset packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from openjiuwen.rsi import load_cases
from openjiuwen.rsi.harness_rsi.data_loader.case_files import copy_dataset_files, task_input


def convert(source: Path, destination: Path) -> Path:
    """Retain every existing verifier/runtime option; never overwrite a packet."""
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"output directory already exists: {destination}")
    cases = load_cases([str(source)])
    prepared = []
    for case in cases:
        if case.get("reference"):
            raise ValueError("input must be legacy SWE cases without an existing reference declaration")
        config = dict(case.get("swebench") or {})
        if not config:
            raise ValueError(f"case {case['case_id']} has no SWE-bench configuration")
        if not isinstance(task_input(case), str):
            raise TypeError("SWE-bench task input must be a string")
        official = Path(config["official_dataset_path"])
        if not official.is_absolute():
            official = source.parent / official
        records = json.loads(official.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise TypeError("official SWE dataset must be a list")
        selected = [item for item in records if isinstance(item, dict) and item.get("instance_id") == case["case_id"]]
        if len(selected) != 1 or config.get("instance_id") != case["case_id"]:
            raise ValueError(f"official instance does not match {case['case_id']}")
        prepared.append((case, config, selected))

    destination.mkdir(parents=True)
    target = destination / "cases.json"
    copy_dataset_files(cases, source, target)
    exported = []
    for case, config, records in prepared:
        identifier = hashlib.sha256(case["case_id"].encode("utf-8")).hexdigest()[:16]
        private = destination / "references" / identifier
        private.mkdir(parents=True, exist_ok=False)
        official = private / "official.json"
        verifier = private / "verifier.json"
        config["official_dataset_path"] = official.relative_to(destination).as_posix()
        official.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        verifier.write_text(json.dumps({"adapter": "swebench_official", "config": config},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        exported.append({
            "case_id": case["case_id"], "input": task_input(case), "assets": case.get("assets", []),
            "reference": {"files": [verifier.relative_to(destination).as_posix(),
                                      official.relative_to(destination).as_posix()]},
        })
    target.write_text(json.dumps({"cases": exported}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_cases([str(target)])
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="New packet directory")
    args = parser.parse_args()
    print(convert(args.source, args.destination))


if __name__ == "__main__":
    main()
