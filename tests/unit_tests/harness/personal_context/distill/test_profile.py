"""Unit tests for publish / activate / resolve / save / delete under im/profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.distill import profile as profile_mod
from openjiuwen.harness.personal_context.distill.profile import (
    META_FILENAME,
    OWNER_FILENAME,
    PERSONA_FILENAME,
    WORK_FILENAME,
    activate_profile_version,
    current_json_path,
    delete_distilled_profile,
    publish_distilled,
    resolve_current_profile,
    save_distilled_profile,
    version_dir,
)
from openjiuwen.harness.personal_context.distill.store import (
    finish_job,
    get_cursor_ms,
)


def test_publish_writes_version_dir_and_meta(tmp_path: Path):
    home = str(tmp_path)
    root = publish_distilled(
        home,
        "job-1",
        persona_md="## Layer 2\n- a\n",
        work_md="## 职责范围\n- 前端\n",
        meta={
            "window_start_ms": 1,
            "window_end_ms": 2,
            "message_count": 4,
            "sampled": False,
            "channels": ["welink"],
            "conversation_count": 1,
            "analyzer": "LlmAnalyzer",
        },
        merge_with_existing=False,
    )
    assert root == version_dir(home, "job-1")
    assert (root / PERSONA_FILENAME).is_file()
    assert (root / WORK_FILENAME).is_file()
    meta_text = (root / META_FILENAME).read_text(encoding="utf-8")
    assert "job-1" in meta_text
    assert "window_start_ms" in meta_text
    assert not current_json_path(home).exists()


def test_publish_merges_from_current_pointer(tmp_path: Path):
    home = str(tmp_path)
    first = publish_distilled(
        home,
        "job-old",
        persona_md="## 职责\n- 前端\n",
        work_md="## 流程\n- a\n",
        meta={"job_id": "job-old"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-old", source="distill")
    before_current = current_json_path(home).read_text(encoding="utf-8")

    root = publish_distilled(
        home,
        "job-new",
        persona_md="## 职责\n- 后端\n",
        work_md="## 流程\n- a\n",
        meta={"job_id": "job-new"},
        merge_with_existing=True,
    )
    persona = (root / PERSONA_FILENAME).read_text(encoding="utf-8")
    assert "前端" in persona
    assert "待确认" in persona
    assert "后端" in persona
    assert first != root
    assert current_json_path(home).read_text(encoding="utf-8") == before_current


def test_publish_clips_long_content(tmp_path: Path):
    home = str(tmp_path)
    long_body = "x" * 13000
    root = publish_distilled(
        home,
        "job-clip",
        persona_md=f"## A\n{long_body}\n",
        work_md="## B\n- ok\n",
        meta={"job_id": "job-clip"},
        merge_with_existing=False,
    )
    persona = (root / PERSONA_FILENAME).read_text(encoding="utf-8")
    assert "…（已截断）" in persona
    assert len(persona) < 13050


def test_activate_and_resolve_current_profile(tmp_path: Path):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-a",
        persona_md="## Persona\n- alpha\n",
        work_md="## Work\n- a\n",
        meta={"job_id": "job-a", "message_count": 2},
        merge_with_existing=False,
    )
    pointer = activate_profile_version(home, "job-a", source="distill")
    assert pointer["job_id"] == "job-a"
    assert pointer["source"] == "distill"
    assert isinstance(pointer["published_at_ms"], int)

    resolved = resolve_current_profile(home)
    assert resolved is not None
    assert resolved["job_id"] == "job-a"
    assert "alpha" in resolved["persona_md"]
    assert "a" in resolved["work_md"]
    assert resolved["meta"]["message_count"] == 2
    assert resolved["source"] == "distill"


def test_activate_switches_pointer_keeps_old_version(tmp_path: Path):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-old",
        persona_md="## P\n- old\n",
        work_md="## W\n- old\n",
        meta={"job_id": "job-old"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-old")
    publish_distilled(
        home,
        "job-new",
        persona_md="## P\n- new\n",
        work_md="## W\n- new\n",
        meta={"job_id": "job-new"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-new")

    resolved = resolve_current_profile(home)
    assert resolved is not None
    assert resolved["job_id"] == "job-new"
    assert "new" in resolved["persona_md"]
    assert version_dir(home, "job-old").is_dir()
    assert version_dir(home, "job-new").is_dir()


def test_activate_rejects_incomplete_version_keeps_old_pointer(tmp_path: Path):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-old",
        persona_md="## P\n- old\n",
        work_md="## W\n- old\n",
        meta={"job_id": "job-old"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-old")
    before = current_json_path(home).read_text(encoding="utf-8")

    incomplete = version_dir(home, "job-bad")
    incomplete.mkdir(parents=True)
    (incomplete / PERSONA_FILENAME).write_text("## only persona\n", encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete"):
        activate_profile_version(home, "job-bad")
    assert current_json_path(home).read_text(encoding="utf-8") == before
    resolved = resolve_current_profile(home)
    assert resolved is not None
    assert resolved["job_id"] == "job-old"


def test_publish_without_activate_keeps_old_current(tmp_path: Path):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-old",
        persona_md="## P\n- old\n",
        work_md="## W\n- old\n",
        meta={"job_id": "job-old"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-old")
    publish_distilled(
        home,
        "job-draft",
        persona_md="## P\n- draft\n",
        work_md="## W\n- draft\n",
        meta={"job_id": "job-draft"},
        merge_with_existing=False,
    )
    resolved = resolve_current_profile(home)
    assert resolved is not None
    assert resolved["job_id"] == "job-old"
    assert "old" in resolved["persona_md"]


def test_activate_atomic_write_failure_keeps_old_pointer(tmp_path: Path, monkeypatch):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-old",
        persona_md="## P\n- old\n",
        work_md="## W\n- old\n",
        meta={"job_id": "job-old"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-old")
    before = current_json_path(home).read_text(encoding="utf-8")

    publish_distilled(
        home,
        "job-new",
        persona_md="## P\n- new\n",
        work_md="## W\n- new\n",
        meta={"job_id": "job-new"},
        merge_with_existing=False,
    )

    def _boom(path, payload):
        raise OSError("atomic write failed")

    monkeypatch.setattr(profile_mod, "atomic_write_json", _boom)
    with pytest.raises(OSError, match="atomic write failed"):
        activate_profile_version(home, "job-new")
    assert current_json_path(home).read_text(encoding="utf-8") == before
    resolved = resolve_current_profile(home)
    assert resolved is not None
    assert resolved["job_id"] == "job-old"


def test_save_distilled_profile_manual_edit_does_not_change_cursor(tmp_path: Path):
    home = str(tmp_path)
    finish_job(
        home,
        "seed-job",
        status="success",
        covered_through_ms=12345,
    )
    assert get_cursor_ms(home) == 12345

    result = save_distilled_profile(
        home,
        persona_md="## Manual\n- edited\n",
        work_md="## Work\n- edited\n",
    )
    assert result["source"] == "manual_edit"
    assert "edited" in result["persona_md"]
    assert result["meta"]["source"] == "manual_edit"
    assert get_cursor_ms(home) == 12345
    pointer = json.loads(current_json_path(home).read_text(encoding="utf-8"))
    assert pointer["source"] == "manual_edit"
    assert pointer["job_id"] == result["job_id"]


def test_delete_distilled_profile_clears_current_keeps_owner_and_versions(tmp_path: Path):
    home = str(tmp_path)
    publish_distilled(
        home,
        "job-1",
        persona_md="## P\n- x\n",
        work_md="## W\n- y\n",
        meta={"job_id": "job-1"},
        merge_with_existing=False,
    )
    activate_profile_version(home, "job-1")
    finish_job(home, "job-1", status="success", covered_through_ms=999)
    owner = profile_mod.profiles_root(home) / OWNER_FILENAME
    owner.write_text("# owner lock\n", encoding="utf-8")

    out = delete_distilled_profile(home)
    assert out == {"deleted": True, "cursor_ms": 0}
    assert resolve_current_profile(home) is None
    assert not current_json_path(home).exists()
    assert owner.is_file()
    assert version_dir(home, "job-1").is_dir()
    assert get_cursor_ms(home) == 0
