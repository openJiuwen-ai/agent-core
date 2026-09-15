"""Reporting skills are copied into the paper workspace so the sandbox can read them."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_workspace_dir,
    set_project_root,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import (
    ReportingAgent,
    _MATERIALIZED_SKILLS_DIRNAME,
    _SKILLS_DIR,
)


def test_build_paper_agent_copies_skills_into_workspace(tmp_path, monkeypatch):
    set_project_root(tmp_path)
    try:
        captured: dict = {}

        def fake_create_deep_agent(model, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("openjiuwen.harness.create_deep_agent", fake_create_deep_agent)

        run_id = "rsi-test-r1"
        workspace = paper_workspace_dir(run_id)
        workspace.mkdir(parents=True, exist_ok=True)
        ReportingAgent({}, model=object())._build_paper_agent(run_id=run_id)

        skills_root = workspace / _MATERIALIZED_SKILLS_DIRNAME
        copied = skills_root / "ts-plan" / "SKILL.md"
        package = _SKILLS_DIR / "ts-plan" / "SKILL.md"
        original = package.read_text(encoding="utf-8")
        copied.write_text(original + "\n# mutated\n", encoding="utf-8")

        assert copied.is_file()
        assert str(skills_root) in captured["system_prompt"]
        assert all(Path(path).is_relative_to(skills_root) for path in captured["skills"])
        assert package.read_text(encoding="utf-8") == original
    finally:
        set_project_root(None)


def test_method_figure_disabled_omits_ts_figure(tmp_path):
    agent = ReportingAgent({"reporting": {"method_figure": {"enabled": False}}})
    dest = agent._materialize_skills(tmp_path / "paper", agent._enabled_skill_dirs())
    assert not (dest / "ts-figure").exists()
    assert (dest / "ts-plan" / "SKILL.md").is_file()
