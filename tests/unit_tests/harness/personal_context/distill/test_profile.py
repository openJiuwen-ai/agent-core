"""Unit tests for publish_distilled under im/profiles/versions."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.harness.personal_context.distill.profile import (
    META_FILENAME,
    PERSONA_FILENAME,
    WORK_FILENAME,
    current_json_path,
    publish_distilled,
    version_dir,
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
    current_json_path(home).parent.mkdir(parents=True, exist_ok=True)
    current_json_path(home).write_text(
        json.dumps({"job_id": "job-old"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
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
