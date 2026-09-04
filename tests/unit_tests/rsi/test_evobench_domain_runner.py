"""Tests for exact Evo-Bench General/Office release partitions."""

from __future__ import annotations

import json
from pathlib import Path

from examples.rsi.evobench.domain_runner import EXPECTED_COUNTS, EXPECTED_SOURCES, materialize_domain_suites


def test_materialize_domain_suites_preserves_exact_release_partitions(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    suite_dir = root / "benchmark" / "suites"
    suite_dir.mkdir(parents=True)
    for split in ("validation", "evaluation"):
        tasks = []
        for (task_split, domain), source_counts in EXPECTED_SOURCES.items():
            if task_split != split:
                continue
            for source, count in source_counts.items():
                tasks.extend({"id": f"{source}-{split}-{domain}-{index}", "domain": domain} for index in range(count))
        tasks.append({"id": f"search-{split}", "domain": "search"})
        (suite_dir / f"evobench_{split}.json").write_text(
            json.dumps({"name": split, "assets_dir": "../assets/gdpval", split: tasks}),
            encoding="utf-8",
        )

    outputs = materialize_domain_suites(root)

    for key, expected_count in EXPECTED_COUNTS.items():
        split, domain = key
        payload = json.loads(outputs[key].read_text(encoding="utf-8"))
        assert len(payload[split]) == expected_count
        assert {task["domain"] for task in payload[split]} == {domain}
        assert payload["assets_dir"] == "../assets/gdpval"
        assert "search" not in {task["domain"] for task in payload[split]}


def test_materialize_domain_suites_rejects_wrong_release_count(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    suite_dir = root / "benchmark" / "suites"
    suite_dir.mkdir(parents=True)
    for split in ("validation", "evaluation"):
        (suite_dir / f"evobench_{split}.json").write_text(
            json.dumps({split: [{"id": f"claw-{split}", "domain": "general"}]}),
            encoding="utf-8",
        )

    try:
        materialize_domain_suites(root)
    except ValueError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("wrong release count must be rejected")
