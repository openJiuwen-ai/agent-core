# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openjiuwen.agent_evolving.checkpointing.evolution_suggestions_ledger import (
    improvement_summary_from_record,
    record_evolution_counts,
    record_generated_suggestion,
    resolve_ledger_path,
)


def test_resolve_ledger_path_from_skills_dir(tmp_path: Path):
    office = tmp_path / ".office-claw"
    skills = office / "skills"
    skills.mkdir(parents=True)
    path = resolve_ledger_path(skills)
    assert path == office / "evolution-suggestions-ledger.json"


def test_resolve_ledger_path_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    path = resolve_ledger_path()
    assert path == tmp_path / ".office-claw" / "evolution-suggestions-ledger.json"


def test_improvement_summary_prefers_record_summary_not_content():
    record = SimpleNamespace(
        summary="用户问出行建议时要综合给建议",
        change=SimpleNamespace(content="## 长正文不要用", summary="chg-sum"),
    )
    assert improvement_summary_from_record(record) == "用户问出行建议时要综合给建议"


def test_improvement_summary_falls_back_to_change_summary():
    record = SimpleNamespace(
        summary="",
        change=SimpleNamespace(content="## 长正文", summary="短摘要"),
    )
    assert improvement_summary_from_record(record) == "短摘要"


def test_record_generated_suggestion_writes_id_summary_timestamp(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    record = SimpleNamespace(
        id="ev_uv",
        timestamp="2026-08-10T11:20:00+00:00",
        summary="用户问出行建议时，要一次性查天气、降水、UV指数并综合给建议，别只给天气就完事。",
        change=SimpleNamespace(
            content="## 出行建议生成\n长正文",
            summary="短摘要备用",
        ),
    )
    record_generated_suggestion("weather", record)

    ledger_path = tmp_path / ".office-claw" / "evolution-suggestions-ledger.json"
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert data["skills"][0]["skillName"] == "weather"
    item = data["skills"][0]["generated"][0]
    assert item == {
        "id": "ev_uv",
        "summary": "用户问出行建议时，要一次性查天气、降水、UV指数并综合给建议，别只给天气就完事。",
        "timestamp": "2026-08-10T11:20:00+00:00",
    }
    assert "content" not in item
    assert "root_cause" not in item


def test_record_generated_two_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    r1 = SimpleNamespace(
        id="ev_1",
        timestamp="t1",
        summary="a",
        change=SimpleNamespace(content="LONG", summary=""),
    )
    r2 = SimpleNamespace(
        id="ev_2",
        timestamp="t2",
        summary="b",
        change=SimpleNamespace(content="LONG2", summary=""),
    )
    record_generated_suggestion("s1", r1)
    record_generated_suggestion("s2", r2)
    data = json.loads(
        (tmp_path / ".office-claw" / "evolution-suggestions-ledger.json").read_text(encoding="utf-8")
    )
    names = {s["skillName"] for s in data["skills"]}
    assert names == {"s1", "s2"}


def test_merge_same_id_updates_summary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    r1 = SimpleNamespace(
        id="ev_1",
        timestamp="t1",
        summary="old",
        change=SimpleNamespace(content="LONG", summary=""),
    )
    r2 = SimpleNamespace(
        id="ev_1",
        timestamp="t2",
        summary="new",
        change=SimpleNamespace(content="LONG2", summary=""),
    )
    record_generated_suggestion("s1", r1)
    record_generated_suggestion("s1", r2)
    data = json.loads(
        (tmp_path / ".office-claw" / "evolution-suggestions-ledger.json").read_text(encoding="utf-8")
    )
    generated = data["skills"][0]["generated"]
    assert len(generated) == 1
    assert generated[0]["summary"] == "new"


def test_record_evolution_counts_backfills_missing_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    ledger_path = tmp_path / ".office-claw" / "evolution-suggestions-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps({"version": 1, "updated_at": "t0", "skills": [{"skillName": "s1", "generated": []}]})
        + "\n",
        encoding="utf-8",
    )

    record_evolution_counts("s1", triggered=True)

    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    bucket = data["skills"][0]
    assert bucket["triggerCount"] == 1
    assert bucket["experienceSuccessCount"] == 0


def test_record_evolution_counts_increments_separately(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    record_evolution_counts("weather", triggered=True)
    record_evolution_counts("weather", experience_succeeded=True)
    record_evolution_counts("weather", triggered=True)

    data = json.loads(
        (tmp_path / ".office-claw" / "evolution-suggestions-ledger.json").read_text(encoding="utf-8")
    )
    bucket = data["skills"][0]
    assert bucket["skillName"] == "weather"
    assert bucket["triggerCount"] == 2
    assert bucket["experienceSuccessCount"] == 1


def test_record_evolution_counts_noop_without_flags(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OFFICE_CLAW_CONFIG_ROOT", str(tmp_path))
    record_evolution_counts("weather")
    assert not (tmp_path / ".office-claw" / "evolution-suggestions-ledger.json").exists()
