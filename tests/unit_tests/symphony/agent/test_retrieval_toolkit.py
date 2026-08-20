import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.symphony.agent import (
    AgenticSkillRetrievalToolkit,
    SkillIndexBuildConfig,
    SkillRecord,
    scan_skill_records,
)
from openjiuwen.symphony.agent.retrieval_toolkit import _load_capability_category_paths, _record_hashes
from openjiuwen.symphony.retrieval.search.artifacts.loading import CatalogRecord


def _write_index_artifacts(index_dir: Path, *, catalog: list[dict[str, object]] | None = None) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "tree_index.yaml").write_text("nodes: []\n", encoding="utf-8")
    catalog_text = "".join(f"{json.dumps(item)}\n" for item in catalog or [])
    (index_dir / "catalog.jsonl").write_text(catalog_text, encoding="utf-8")
    (index_dir / "manifest.json").write_text("{}\n", encoding="utf-8")


def test_toolkit_disables_equivalence_grouping_by_default() -> None:
    assert not SkillIndexBuildConfig().equivalence_enabled
    assert SkillIndexBuildConfig().equivalence_min_lexical_similarity == 0.0


def test_scan_skill_records_reads_skill_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "pdf-summary"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: PDF Summary\ndescription: Summarize PDF documents.\n---\n\n# Instructions\n",
        encoding="utf-8",
    )

    records = scan_skill_records(tmp_path)

    assert records == [
        SkillRecord(
            name="PDF Summary",
            worker_id="pdf-summary",
            description="Summarize PDF documents.",
            skill_md_path=str(skill_dir / "SKILL.md"),
            enabled=True,
            metadata={"name": "PDF Summary", "description": "Summarize PDF documents."},
            content="# Instructions",
            content_hash=records[0].content_hash,
        )
    ]


def test_toolkit_reports_missing_index(tmp_path: Path) -> None:
    toolkit = AgenticSkillRetrievalToolkit(index_root=tmp_path)

    status = toolkit.load_index_status()

    assert status["status"] == "idle"
    assert status["index_exists"] is False
    assert status["index_dir"] == str(tmp_path / "index")
    assert status["capability_category_paths"] == []


def test_build_plan_tracks_added_and_updated_capability_ids(tmp_path: Path) -> None:
    previous = [
        SkillRecord(name="Changed", worker_id="changed", content="old"),
        SkillRecord(name="Removed", worker_id="removed"),
    ]
    current = [
        SkillRecord(name="Changed", worker_id="changed", content="new"),
        SkillRecord(name="Added", worker_id="added"),
    ]
    toolkit = AgenticSkillRetrievalToolkit(index_root=tmp_path, skills=current)
    _write_index_artifacts(tmp_path / "index")
    (tmp_path / "state.json").write_text(
        json.dumps({"record_hashes": _record_hashes(previous)}),
        encoding="utf-8",
    )

    plan = toolkit._select_build_plan(records=current, force=False)

    assert plan.operation == "build"
    assert plan.response_worker_ids == ("added", "changed")
    assert toolkit._select_build_plan(records=current, force=True).response_worker_ids == ()
    delete_plan = toolkit._select_build_plan(records=[previous[0]], force=False)
    assert delete_plan.operation == "delete"
    assert delete_plan.response_worker_ids == ()


def test_category_path_loader_returns_variable_depth_paths_in_request_order(tmp_path: Path) -> None:
    loaded = SimpleNamespace(
        catalog_records=(
            CatalogRecord(
                choice_id="Two levels",
                payload="L1.L2.two-levels",
                worker_id="two-levels",
                branch_path=("L1", "L2"),
            ),
            CatalogRecord(
                choice_id="Three levels",
                payload="L1.L2.L3.three-levels",
                worker_id="three-levels",
                branch_path=("L1", "L2", "L3"),
            ),
        )
    )

    with patch(
        "openjiuwen.symphony.agent.retrieval_toolkit.load_retriever_index",
        return_value=loaded,
    ):
        paths = _load_capability_category_paths(
            tmp_path,
            worker_ids=("three-levels", "two-levels", "three-levels"),
        )

    assert paths == [
        {"capability_id": "three-levels", "category_path": ["L1", "L2", "L3"]},
        {"capability_id": "two-levels", "category_path": ["L1", "L2"]},
    ]


@pytest.mark.parametrize(
    ("records", "worker_id", "error"),
    [
        ((), "missing", "missing category paths"),
        (
            (CatalogRecord(choice_id="Empty", payload="empty", worker_id="empty"),),
            "empty",
            "empty category path",
        ),
        (
            (
                CatalogRecord(
                    choice_id="First",
                    payload="first",
                    worker_id="duplicate",
                    branch_path=("L1",),
                ),
                CatalogRecord(
                    choice_id="Second",
                    payload="second",
                    worker_id="duplicate",
                    branch_path=("L2",),
                ),
            ),
            "duplicate",
            "duplicate catalog records",
        ),
    ],
)
def test_category_path_loader_rejects_invalid_final_catalog(
    tmp_path: Path,
    records: tuple[CatalogRecord, ...],
    worker_id: str,
    error: str,
) -> None:
    loaded = SimpleNamespace(catalog_records=records)

    with (
        patch(
            "openjiuwen.symphony.agent.retrieval_toolkit.load_retriever_index",
            return_value=loaded,
        ),
        pytest.raises(RuntimeError, match=error),
    ):
        _load_capability_category_paths(tmp_path, worker_ids=(worker_id,))


def test_incremental_build_returns_and_persists_final_category_path(tmp_path: Path) -> None:
    previous = [SkillRecord(name="Existing", worker_id="existing")]
    current = [*previous, SkillRecord(name="New", worker_id="new")]
    toolkit = AgenticSkillRetrievalToolkit(index_root=tmp_path, skills=current)
    _write_index_artifacts(tmp_path / "index")
    (tmp_path / "state.json").write_text(
        json.dumps({"record_hashes": _record_hashes(previous)}),
        encoding="utf-8",
    )

    def fake_builder(**kwargs: object) -> None:
        output_dir = Path(str(kwargs["output_dir"]))
        _write_index_artifacts(
            output_dir,
            catalog=[
                {
                    "name": "New",
                    "cid": "Productivity.Documents.New",
                    "worker_id": "new",
                    "branch_path": ["Productivity", "Documents"],
                }
            ],
        )

    with patch(
        "openjiuwen.symphony.agent.retrieval_toolkit._run_index_builder",
        side_effect=fake_builder,
    ):
        result = toolkit.build_index()

    expected = [{"capability_id": "new", "category_path": ["Productivity", "Documents"]}]
    assert result["success"] is True
    assert result["data"]["capability_category_paths"] == expected
    assert toolkit.check_build_status()["data"]["capability_category_paths"] == expected
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["build"]["capability_category_paths"] == expected


def test_missing_incremental_category_path_preserves_previous_index(tmp_path: Path) -> None:
    previous = [SkillRecord(name="Existing", worker_id="existing")]
    current = [*previous, SkillRecord(name="New", worker_id="new")]
    toolkit = AgenticSkillRetrievalToolkit(index_root=tmp_path, skills=current)
    index_dir = tmp_path / "index"
    _write_index_artifacts(index_dir)
    (index_dir / "tree_index.yaml").write_text("old-tree\n", encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "record_hashes": _record_hashes(previous),
                "fingerprint": "previous",
                "indexed_count": 1,
            }
        ),
        encoding="utf-8",
    )

    def fake_builder(**kwargs: object) -> None:
        _write_index_artifacts(Path(str(kwargs["output_dir"])))

    with patch(
        "openjiuwen.symphony.agent.retrieval_toolkit._run_index_builder",
        side_effect=fake_builder,
    ):
        result = toolkit.build_index()

    assert result["success"] is False
    assert "missing category paths" in result["result"]
    assert result["data"]["previous_index_preserved"] is True
    assert (index_dir / "tree_index.yaml").read_text(encoding="utf-8") == "old-tree\n"
    assert toolkit.check_build_status()["data"]["capability_category_paths"] == []
