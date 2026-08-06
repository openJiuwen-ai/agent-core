from pathlib import Path

from openjiuwen.symphony.agent import AgenticSkillRetrievalToolkit, SkillRecord, scan_skill_records


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
