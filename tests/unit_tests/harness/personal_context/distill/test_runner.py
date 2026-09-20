"""Unit tests for run_distill_job under PersonalContext home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.distill.analyzer import LlmAnalyzer
from openjiuwen.harness.personal_context.distill.corpus import (
    FixtureCorpus,
    default_fixture_messages,
)
from openjiuwen.harness.personal_context.distill.profile import current_json_path
from openjiuwen.harness.personal_context.distill.runner import run_distill_job
from openjiuwen.harness.personal_context.distill.store import get_cursor_ms, get_job
from openjiuwen.harness.personal_context.distill.types import CorpusMessage

FIXTURE_END_MS = 1_700_000_010_000


class _FakeLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "Persona 分析 Prompt" in system:
            return '口头禅：["先看现有实现"]\n'
        if "Persona 生成模板" in system:
            return "# 本人 — Persona\n\n## Layer 2：表达风格\n- 先看现有实现再给建议\n"
        if "Work Skill 分析 Prompt" in system:
            return "负责领域：前端\n"
        if "Work Skill 生成模板" in system:
            return "# 本人 — Work Skill\n\n## 职责范围\n- 前端相关改动\n"
        return "fallback\n"


class _FailingAnalyzer:
    async def analyze(self, messages):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_run_distill_job_success_writes_version_and_activates_current(tmp_path: Path):
    home = str(tmp_path)
    llm = _FakeLlm()
    result = await run_distill_job(
        home,
        window_end_ms=FIXTURE_END_MS,
        force_full_window=True,
        corpus=FixtureCorpus(default_fixture_messages()),
        analyzer=LlmAnalyzer(llm),
    )
    assert result.status == "success"
    assert result.message_count == 4
    assert len(llm.calls) == 4
    root = Path(result.distilled_dir or "")
    assert root.is_dir()
    assert str(root).replace("\\", "/").endswith(f"im/profiles/versions/{result.job_id}")
    assert (root / "persona.md").is_file()
    assert (root / "work.md").is_file()
    assert (root / "meta.json").is_file()
    assert current_json_path(home).is_file()
    pointer = json.loads(current_json_path(home).read_text(encoding="utf-8"))
    assert pointer["job_id"] == result.job_id
    assert pointer["source"] == "distill"
    assert get_cursor_ms(home) == FIXTURE_END_MS
    job = get_job(home, result.job_id)
    assert job is not None
    assert job["status"] == "success"
    assert "persona_id" not in job


@pytest.mark.asyncio
async def test_run_distill_job_empty_window_success_advances_cursor(tmp_path: Path):
    home = str(tmp_path)
    result = await run_distill_job(
        home,
        window_end_ms=1000,
        force_full_window=True,
        corpus=FixtureCorpus([]),
        llm=_FakeLlm(),
    )
    assert result.status == "success"
    assert result.message_count == 0
    assert result.distilled_dir is None
    assert not (tmp_path / "im" / "profiles").exists()
    assert get_cursor_ms(home) == 1000


@pytest.mark.asyncio
async def test_run_distill_job_activate_failure_does_not_advance_cursor(
    tmp_path: Path,
    monkeypatch,
):
    home = str(tmp_path)
    await run_distill_job(
        home,
        window_end_ms=500,
        force_full_window=True,
        corpus=FixtureCorpus([]),
        llm=_FakeLlm(),
    )
    assert get_cursor_ms(home) == 500

    def _boom(home_arg, job_id, *, source="distill"):
        raise RuntimeError("activate failed")

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.distill.runner.activate_profile_version",
        _boom,
    )
    result = await run_distill_job(
        home,
        window_end_ms=FIXTURE_END_MS,
        force_full_window=True,
        corpus=FixtureCorpus(default_fixture_messages()),
        analyzer=LlmAnalyzer(_FakeLlm()),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert "activate failed" in result.error
    assert get_cursor_ms(home) == 500
    assert not current_json_path(home).exists()
    version_root = tmp_path / "im" / "profiles" / "versions" / result.job_id
    assert version_root.is_dir()


@pytest.mark.asyncio
async def test_run_distill_job_failure_does_not_advance_cursor(tmp_path: Path):
    home = str(tmp_path)
    await run_distill_job(
        home,
        window_end_ms=500,
        force_full_window=True,
        corpus=FixtureCorpus([]),
        llm=_FakeLlm(),
    )
    assert get_cursor_ms(home) == 500

    result = await run_distill_job(
        home,
        window_end_ms=FIXTURE_END_MS,
        force_full_window=True,
        corpus=FixtureCorpus(default_fixture_messages()),
        analyzer=_FailingAnalyzer(),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert get_cursor_ms(home) == 500
    assert not current_json_path(home).exists()


@pytest.mark.asyncio
async def test_force_full_window_ignores_cursor(tmp_path: Path):
    home = str(tmp_path)
    cursor_end = 1_700_000_004_500
    await run_distill_job(
        home,
        window_end_ms=cursor_end,
        force_full_window=True,
        corpus=FixtureCorpus(default_fixture_messages()),
        analyzer=LlmAnalyzer(_FakeLlm()),
    )
    assert get_cursor_ms(home) == cursor_end

    late = [
        CorpusMessage(
            id="late",
            channel_id="welink",
            conversation_id="c",
            content_text="后续消息",
            sent_at_ms=cursor_end + 1000,
            is_self=True,
            learning_eligible=1,
        )
    ]
    result = await run_distill_job(
        home,
        window_end_ms=cursor_end + 5000,
        force_full_window=False,
        corpus=FixtureCorpus(default_fixture_messages() + late),
        analyzer=LlmAnalyzer(_FakeLlm()),
    )
    assert result.status == "success"
    assert result.window_start_ms == cursor_end
    assert result.message_count == 1

    result2 = await run_distill_job(
        home,
        window_end_ms=FIXTURE_END_MS,
        force_full_window=True,
        corpus=FixtureCorpus(default_fixture_messages()),
        analyzer=LlmAnalyzer(_FakeLlm()),
    )
    assert result2.window_start_ms == 0
    assert result2.message_count == 4
