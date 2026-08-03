# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TeamEvaluator, CaseRunner, LocalExecutionBackend, and helpers."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import openjiuwen.rsi.evaluator.judger.llm_as_judge as llm_as_judge
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home, team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn import cleanup_shared_resources
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator import (
    CaseRunner,
    ExactMatchJudger,
    JudgeResult,
    LocalExecutionBackend,
    MetricsCollector,
    TeamEvaluator,
)
from openjiuwen.rsi.evaluator import case_backend as case_backend_module
from openjiuwen.rsi.evaluator import case_runner as case_runner_module
from openjiuwen.rsi.evaluator.case_backend import (
    CaseExecutionResult,
    SingleHarnessExecutionBackend,
    _expected_artifact_snapshot,
    _team_task_board_terminal_for_delivery,
    build_backend,
    build_local_team_case_input,
)
from openjiuwen.rsi.evaluator.case_runner import (
    _case_result_status,
    _cleanup_scratch,
    _expected_artifact_files,
    _harvest_artifacts,
    _harvest_changed_workspace_files,
    _messages_from_role_trajectory,
)
from openjiuwen.rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.evaluator.eval_team_rail import (
    EvalTeamRail,
    _harvest_workspace_artifacts,
)
from openjiuwen.rsi.evaluator.judger.llm_as_judge import run_llm_judge
from openjiuwen.rsi.evaluator.runtime_adapters import RSISkillUseRail
from openjiuwen.rsi.evaluator.team_factory import (
    TeamSkillTeamFactory,
    _build_spec_from_config,
)
from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.schema import DatasetArtifact


@pytest.mark.asyncio
async def test_eval_team_rail_snapshots_artifacts_after_file_write(tmp_path: Path) -> None:
    workspace = tmp_path / "team-workspace"
    artifact = workspace / "artifacts" / "index.html"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("<html>complete</html>", encoding="utf-8")
    dest = tmp_path / "case" / "artifacts"
    rail = EvalTeamRail(team_name="demo", workspace_dir=workspace, dest_dir=dest)
    ctx = SimpleNamespace(inputs=SimpleNamespace(tool_name="write_file"))

    await rail.after_tool_call(ctx)
    artifact.parent.parent.replace(tmp_path / "deleted-workspace")

    assert (dest / "index.html").read_text(encoding="utf-8") == "<html>complete</html>"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _write_team_skill(tmp_path: Path, team_name: str = "math_team") -> Path:
    skill_dir = tmp_path / "team_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {team_name}\n---\n# team\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_model_config(tmp_path: Path) -> Path:
    path = tmp_path / f"model_{uuid.uuid4().hex}.yaml"
    path.write_text(
        "\n".join(
            [
                "model:",
                "  model_client_config:",
                "    client_provider: OpenAI",
                "    api_base: http://127.0.0.1:8000/v1",
                "    api_key: test-key",
                "    verify_ssl: false",
                "  model_request_config:",
                "    model: glm-5",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class _FakeBackend:
    """Minimal CaseExecutionBackend for orchestration tests."""

    def __init__(self, *, team_name: str = "") -> None:
        self.sessions: list[str] = []
        self.cleaned: list[tuple[str, str]] = []
        self._team_name = team_name

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        **kwargs: Any,
    ) -> CaseExecutionResult:
        self.sessions.append(session_id)
        case_input = case.get("input") or case.get("inputs") or ""
        team_spec = _TeamSpecStub(self._team_name) if self._team_name else None
        return CaseExecutionResult(
            response={
                "team_name": self._team_name or "math_team",
                "input": case_input,
                "answer": f"已完成：{case_input}",
            },
            execution_status="passed",
            team_spec=team_spec,
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        self.cleaned.append((team_name, session_id))


class _TeamSpecStub:
    team_name: str

    def __init__(self, team_name: str) -> None:
        self.team_name = team_name

    def resolve_db_config(self):
        class _DbConfig:
            db_type = "memory"

        return _DbConfig()


# ---------------------------------------------------------------------------
# case_runner helpers: _harvest_artifacts / _cleanup_scratch
# ---------------------------------------------------------------------------


class TestCaseRunnerHelpers:
    def test_harvest_artifacts_copies_entire_workspace_artifacts_tree(self, tmp_path: Path) -> None:
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        (artifacts_src / "code").mkdir(parents=True)
        (artifacts_src / "reports").mkdir(parents=True)
        (artifacts_src / "code" / "main.py").write_text("print(1)\n", encoding="utf-8")
        (artifacts_src / "reports" / "summary.md").write_text("# ok\n", encoding="utf-8")

        dest_dir = tmp_path / "artifacts"
        refs = _harvest_artifacts(workspace_dir=workspace_dir, dest_dir=dest_dir)

        assert refs == {
            "harvested": ["code/main.py", "reports/summary.md"],
            "missing": [],
        }
        assert (dest_dir / "code" / "main.py").read_text(encoding="utf-8") == "print(1)\n"
        assert (artifacts_src / "code" / "main.py").exists()

    def test_harvest_artifacts_noop_when_workspace_artifacts_missing(self, tmp_path: Path) -> None:
        refs = _harvest_artifacts(
            workspace_dir=tmp_path / "workspace",
            dest_dir=tmp_path / "artifacts",
        )
        assert refs == {"harvested": [], "missing": []}
        assert not (tmp_path / "artifacts").exists()

    def test_harvest_artifacts_normalizes_nested_web_deliverables(
        self,
        tmp_path: Path,
    ) -> None:
        """Nested artifact layout should expose canonical files at artifacts root."""
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        (artifacts_src / "code").mkdir(parents=True)
        (artifacts_src / "docs").mkdir(parents=True)
        (artifacts_src / "code" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (artifacts_src / "code" / "styles.css").write_text("body {}\n", encoding="utf-8")
        (artifacts_src / "docs" / "content_brief.md").write_text("# brief\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "artifacts",
        )

        assert "code/index.html" in refs["harvested"]
        assert "docs/content_brief.md" in refs["harvested"]
        assert "index.html" in refs["harvested"]
        assert "styles.css" in refs["harvested"]
        assert "content_brief.md" in refs["harvested"]
        assert (tmp_path / "artifacts" / "index.html").read_text(encoding="utf-8") == "<html></html>\n"
        assert (tmp_path / "artifacts" / "styles.css").read_text(encoding="utf-8") == "body {}\n"
        assert (tmp_path / "artifacts" / "content_brief.md").read_text(encoding="utf-8") == "# brief\n"

    def test_harvest_artifacts_preserves_supporting_deliverables(
        self,
        tmp_path: Path,
    ) -> None:
        """Expected files validate completion without hiding team evidence."""
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        (artifacts_src / "code").mkdir(parents=True)
        (artifacts_src / "docs").mkdir(parents=True)
        (artifacts_src / "code" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (artifacts_src / "code" / "styles.css").write_text("body {}\n", encoding="utf-8")
        (artifacts_src / "docs" / "content_brief.md").write_text("# brief\n", encoding="utf-8")
        (artifacts_src / "docs" / "market-analysis.md").write_text("# draft\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "artifacts",
            expected_files=["index.html", "styles.css", "content_brief.md"],
        )

        assert refs == {
            "harvested": [
                "content_brief.md",
                "docs/market-analysis.md",
                "index.html",
                "styles.css",
            ],
            "missing": [],
        }
        assert (tmp_path / "artifacts" / "docs" / "market-analysis.md").is_file()

    def test_harvest_artifacts_exposes_dynamic_expected_files_from_nested_layout(
        self,
        tmp_path: Path,
    ) -> None:
        """Expected final files must be rooted even when agents wrote them under code/."""
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        (artifacts_src / "code").mkdir(parents=True)
        (artifacts_src / "code" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (artifacts_src / "code" / "styles.css").write_text("body {}\n", encoding="utf-8")
        (artifacts_src / "code" / "game.js").write_text("console.log('game')\n", encoding="utf-8")
        (artifacts_src / "reports").mkdir()
        (artifacts_src / "reports" / "verdict.md").write_text("PASS\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "artifacts",
            expected_files=["index.html", "styles.css", "game.js"],
        )

        assert refs == {
            "harvested": ["game.js", "index.html", "reports/verdict.md", "styles.css"],
            "missing": [],
        }
        assert (tmp_path / "artifacts" / "game.js").read_text(encoding="utf-8") == ("console.log('game')\n")
        assert not (tmp_path / "artifacts" / "code").exists()

    def test_harvest_artifacts_preserves_member_local_validation_report(
        self,
        tmp_path: Path,
    ) -> None:
        """Strict final-file harvest must still retain proof artifacts from members."""
        team_home = tmp_path / "demo_team"
        workspace_dir = team_home / "team-workspace"
        artifacts_src = workspace_dir / "artifacts"
        artifacts_src.mkdir(parents=True)
        (artifacts_src / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (artifacts_src / "styles.css").write_text("body {}\n", encoding="utf-8")
        (artifacts_src / "game.js").write_text("console.log('game')\n", encoding="utf-8")
        member_artifacts = team_home / "workspaces" / "tester_workspace" / "artifacts"
        member_artifacts.mkdir(parents=True)
        (member_artifacts / "integration_test_report.md").write_text(
            "# Integration Test Report\n\nFinal Verdict: PASS\n",
            encoding="utf-8",
        )

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "artifacts",
            expected_files=["index.html", "styles.css", "game.js"],
        )

        assert refs == {
            "harvested": [
                "game.js",
                "index.html",
                "integration_test_report.md",
                "styles.css",
            ],
            "missing": [],
        }
        assert (tmp_path / "artifacts" / "integration_test_report.md").is_file()

    def test_harvest_artifacts_normalizes_preharvested_nested_deliverables(
        self,
        tmp_path: Path,
    ) -> None:
        """EvalTeamRail pre-harvested content still needs the same canonical view."""
        dest_dir = tmp_path / "artifacts"
        (dest_dir / "code").mkdir(parents=True)
        (dest_dir / "code" / "index.html").write_text("<html></html>\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=tmp_path / "deleted_workspace",
            dest_dir=dest_dir,
        )

        assert "code/index.html" in refs["harvested"]
        assert "index.html" in refs["harvested"]
        assert (dest_dir / "index.html").is_file()

    def test_harvest_artifacts_refreshes_preharvested_deliverables_from_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        """Normal harvest should replace stale rail pre-harvested deliverables."""
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        artifacts_src.mkdir(parents=True)
        (artifacts_src / "index.html").write_text(
            "<html><body><section>complete</section></body></html>\n",
            encoding="utf-8",
        )
        (artifacts_src / "styles.css").write_text("body { color: #111; }\n", encoding="utf-8")
        (artifacts_src / "content_brief.md").write_text("# complete brief\n", encoding="utf-8")

        dest_dir = tmp_path / "artifacts"
        dest_dir.mkdir(parents=True)
        (dest_dir / "index.html").write_text("<html><head></head><body>\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=dest_dir,
            expected_files=["index.html", "styles.css", "content_brief.md"],
        )

        assert refs == {
            "harvested": ["content_brief.md", "index.html", "styles.css"],
            "missing": [],
        }
        assert (dest_dir / "index.html").read_text(encoding="utf-8") == (
            "<html><body><section>complete</section></body></html>\n"
        )

    def test_harvest_artifacts_prefers_complete_web_deliverable_candidate(
        self,
        tmp_path: Path,
    ) -> None:
        """Artifact selection should not keep an incomplete root deliverable."""
        workspace_dir = tmp_path / "workspace"
        artifacts_src = workspace_dir / "artifacts"
        (artifacts_src / "code").mkdir(parents=True)
        (artifacts_src / "index.html").write_text("<html><head></head><body>\n", encoding="utf-8")
        (artifacts_src / "code" / "index.html").write_text(
            "<html><body><section>complete</section></body></html>\n",
            encoding="utf-8",
        )
        (artifacts_src / "styles.css").write_text("body { color: #111; }\n", encoding="utf-8")
        (artifacts_src / "content_brief.md").write_text("# complete brief\n", encoding="utf-8")

        refs = _harvest_artifacts(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "artifacts",
            expected_files=["index.html", "styles.css", "content_brief.md"],
        )

        assert refs == {
            "harvested": ["content_brief.md", "index.html", "styles.css"],
            "missing": [],
        }
        assert (tmp_path / "artifacts" / "index.html").read_text(encoding="utf-8") == (
            "<html><body><section>complete</section></body></html>\n"
        )

    def test_local_team_case_input_uses_readable_chinese_contract(self) -> None:
        prompt = build_local_team_case_input({"input": {"user_message": "制作中文网页"}})

        assert "这是一个自动评测 case" in prompt
        assert "调用 build_team" in prompt
        assert "调用 clean_team" in prompt
        assert "制作中文网页" in prompt
        assert "杩" not in prompt

    def test_eval_team_rail_harvests_better_member_mount_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        """Pre-harvest should recover better deliverables stranded in member mounts."""
        team_home = tmp_path / "demo_team"
        workspace_dir = team_home / "team-workspace"
        (workspace_dir / "artifacts").mkdir(parents=True)
        (workspace_dir / "artifacts" / "index.html").write_text(
            "<html><head></head><body>\n",
            encoding="utf-8",
        )
        member_artifacts = team_home / "workspaces" / "designer_workspace" / ".team" / "demo_team" / "artifacts"
        member_artifacts.mkdir(parents=True)
        (member_artifacts / "index.html").write_text(
            "<html><body><section>complete</section></body></html>\n",
            encoding="utf-8",
        )

        _harvest_workspace_artifacts(workspace_dir, tmp_path / "artifacts")

        assert (tmp_path / "artifacts" / "index.html").read_text(encoding="utf-8") == (
            "<html><body><section>complete</section></body></html>\n"
        )

    def test_harvest_changed_workspace_files_normalizes_direct_outputs(
        self,
        tmp_path: Path,
    ) -> None:
        """Standalone outputs copied from workspace_changes should use the same contract."""
        workspace_dir = tmp_path / "workspace"
        (workspace_dir / "artifacts" / "docs").mkdir(parents=True)
        (workspace_dir / "artifacts" / "docs" / "content_brief.md").write_text(
            "# brief\n",
            encoding="utf-8",
        )

        refs = _harvest_changed_workspace_files(
            workspace_dir=workspace_dir,
            dest_dir=tmp_path / "case_artifacts",
            workspace_changes={"added": ["artifacts/docs/content_brief.md"]},
        )

        assert "artifacts/docs/content_brief.md" in refs["harvested"]
        assert "content_brief.md" in refs["harvested"]
        assert (tmp_path / "case_artifacts" / "content_brief.md").is_file()

    def test_expected_artifact_files_detects_explicit_web_contract(self) -> None:
        expected = _expected_artifact_files(
            {
                "input": {"user_message": "请输出 index.html、styles.css 和 game.js。"},
                "reference": {"required_behaviors": [{"description": "index.html and styles.css are inspectable"}]},
            }
        )

        assert expected == ["index.html", "styles.css", "game.js"]

    def test_local_team_case_input_makes_expected_artifact_root_authoritative(self) -> None:
        prompt = build_local_team_case_input(
            {
                "input": {"user_message": "输出 index.html、styles.css、game.js 三个文件。"},
                "reference": {
                    "deliverables": ["index.html", "styles.css", "game.js"],
                },
            }
        )

        assert "最终交付物必须直接写入 .team/<team_name>/artifacts/ 根目录" in prompt
        assert ".team/<team_name>/artifacts/index.html" in prompt
        assert ".team/<team_name>/artifacts/styles.css" in prompt
        assert ".team/<team_name>/artifacts/game.js" in prompt
        assert "artifacts/code、artifacts/docs、artifacts/reports 仅作为辅助目录" in prompt
        assert "最终交付物验收路径以 artifacts 根目录文件为准" in prompt

    def test_delivery_snapshot_accepts_expected_files_from_nested_layout(
        self,
        tmp_path: Path,
    ) -> None:
        artifacts_dir = tmp_path / "artifacts"
        (artifacts_dir / "code").mkdir(parents=True)
        (artifacts_dir / "code" / "game.js").write_text("console.log('game')\n", encoding="utf-8")

        snapshot = _expected_artifact_snapshot(artifacts_dir, ["game.js"])

        assert snapshot is not None
        assert snapshot[0][0] == "game.js"

    def test_role_trace_keeps_tail_evidence_after_long_wait_prefix(self) -> None:
        steps: list[dict[str, Any]] = []
        for index in range(30):
            steps.append(
                {
                    "detail": {
                        "response": {
                            "role": "assistant",
                            "content": f"waiting for upstream task {index}",
                        }
                    }
                }
            )
        steps.extend(
            [
                {
                    "detail": {
                        "tool_name": "write_file",
                        "call_args": {"file_path": "artifacts/integration_test_report.md"},
                        "call_result": {"success": True},
                    }
                },
                {
                    "detail": {
                        "tool_name": "claim_task",
                        "call_args": {"task_id": "task-integration-test", "status": "completed"},
                        "call_result": {"success": True},
                    }
                },
                {
                    "detail": {
                        "tool_name": "send_message",
                        "call_args": {"to": "team_leader", "content": "Final Verdict: PASS"},
                        "call_result": {"success": True},
                    }
                },
            ]
        )

        messages = _messages_from_role_trajectory({"steps": steps})
        serialized = json.dumps(messages, ensure_ascii=False)

        assert "waiting for upstream task 0" in serialized
        assert "integration_test_report.md" in serialized
        assert "claim_task" in serialized
        assert "Final Verdict: PASS" in serialized

    @pytest.mark.asyncio
    async def test_cleanup_scratch_closes_case_db_before_removing_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []
        db_path = tmp_path / "team.db"
        db_path.write_text("", encoding="utf-8")

        class _DbConfig:
            db_type = "sqlite"
            connection_string = str(db_path)

        class _TeamSpec:
            def resolve_db_config(self):
                return _DbConfig()

        class _Db:
            async def close(self) -> None:
                events.append("close")

        monkeypatch.setattr(
            "openjiuwen.rsi.evaluator.case_runner.get_shared_db",
            lambda _config: _Db(),
        )

        await _cleanup_scratch(tmp_path, _TeamSpec())

        assert events == ["close"]
        assert not db_path.exists()


# ---------------------------------------------------------------------------
# CaseRunner
# ---------------------------------------------------------------------------


class TestCaseRunner:
    @pytest.mark.asyncio
    async def test_infrastructure_failure_aborts_evaluation_round(self, tmp_path: Path) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                return CaseExecutionResult(response="done", execution_status="passed")

            async def cleanup(self, team_name: str, session_id: str) -> None:
                raise AssertionError("cleanup should not run without team_spec")

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                raise EvaluationInfrastructureError("broken verifier image")

        runner = CaseRunner(backend=_Backend(), judger=_Judger())

        with pytest.raises(EvaluationInfrastructureError, match="broken verifier image"):
            await runner.execute(
                case={"case_id": "pvlib__pvlib-python-1072", "input": "fix"},
                output_dir=str(tmp_path / "case"),
            )

    @pytest.mark.asyncio
    async def test_execute_writes_result_and_trace(self, tmp_path: Path) -> None:
        class _Backend:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                self.calls.append(kwargs)
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                raise AssertionError("cleanup should not run without team_spec")

        backend = _Backend()
        runner = CaseRunner(backend=backend)
        output_dir = tmp_path / "case_001"

        case_ref = await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
            team_skill_ref_path=str(tmp_path / "team_skill"),
            harness_refs={"alice": "harness"},
        )

        assert backend.calls[0]["output_dir"] == str(output_dir.resolve())
        assert backend.calls[0]["harness_refs"] == {"alice": "harness"}
        assert case_ref.case_id == "case_001"
        assert (output_dir / "result.json").is_file()
        assert (output_dir / "trace.json").is_file()
        trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
        assert trace["trajectory_dir"] == str((output_dir / "tr").resolve())

    @pytest.mark.asyncio
    async def test_harvests_artifacts_before_judging_and_cleans_last(self, tmp_path: Path) -> None:
        events: list[str] = []

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                events.append("execute")
                artifact_dir = case_runner_module.team_home("math_team") / "team-workspace" / "artifacts"
                artifact_dir.mkdir(parents=True)
                (artifact_dir / "report.txt").write_text("ready\n", encoding="utf-8")
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("math_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                assert team_name == "math_team"
                events.append("cleanup")

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                events.append("judge")
                assert "execution_result" in kwargs
                assert "response" not in kwargs
                assert kwargs["execution_result"].response == {"answer": "done"}
                output_dir = Path(str(kwargs["output_dir"]))
                assert (output_dir / "artifacts" / "report.txt").read_text(encoding="utf-8") == "ready\n"
                assert not (output_dir / "result.json").exists()
                return JudgeResult(method="recording", score=1.0, passed=True)

        runner = CaseRunner(backend=_Backend(), judger=_Judger())
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        assert events == ["execute", "judge", "cleanup"]
        assert result["artifacts"] == {"harvested": ["report.txt"], "missing": []}
        assert not (output_dir / ".agent_teams").exists()
        assert (output_dir / "artifacts" / "report.txt").is_file()

    @pytest.mark.asyncio
    async def test_execute_uses_short_runtime_home_without_moving_case_outputs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []
        configured_homes: list[Path] = []

        monkeypatch.setattr(case_runner_module, "_needs_short_runtime_home", lambda _case_dir: True)

        original_configure = case_runner_module.configure_openjiuwen_home

        def _recording_configure(path: str | Path) -> None:
            configured_homes.append(Path(path))
            original_configure(path)

        monkeypatch.setattr(case_runner_module, "configure_openjiuwen_home", _recording_configure)

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                events.append("execute")
                assert Path(str(kwargs["output_dir"])) == output_dir.resolve()
                artifact_dir = case_runner_module.team_home("math_team") / "team-workspace" / "artifacts"
                artifact_dir.mkdir(parents=True)
                (artifact_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("math_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                assert team_name == "math_team"
                events.append("cleanup")

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                events.append("judge")
                output_dir_arg = Path(str(kwargs["output_dir"]))
                assert output_dir_arg == output_dir.resolve()
                assert (output_dir_arg / "artifacts" / "index.html").is_file()
                return JudgeResult(method="recording", score=1.0, passed=True)

        runner = CaseRunner(backend=_Backend(), judger=_Judger())
        output_dir = tmp_path / "very" / "deep" / "evaluation" / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        assert events == ["execute", "judge", "cleanup"]
        assert configured_homes
        runtime_home = configured_homes[0]
        assert runtime_home != output_dir.resolve()
        assert runtime_home.name
        assert not (runtime_home / ".agent_teams").exists()
        assert (output_dir / "result.json").is_file()
        assert (output_dir / "trace.json").is_file()
        assert (output_dir / "artifacts" / "index.html").is_file()

    @pytest.mark.asyncio
    async def test_cleans_runtime_when_judger_fails(self, tmp_path: Path) -> None:
        events: list[str] = []

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                events.append("execute")
                output_dir = Path(str(kwargs["output_dir"]))
                # Create .agent_teams scratch dir to verify _cleanup_scratch removes it.
                (output_dir / ".agent_teams").mkdir(parents=True)
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("math_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                events.append("cleanup")

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                events.append("judge")
                raise RuntimeError("judge boom")

        runner = CaseRunner(backend=_Backend(), judger=_Judger())
        output_dir = tmp_path / "case_001"

        case_ref = await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        assert events == ["execute", "judge", "cleanup"]
        assert not (output_dir / ".agent_teams").exists()
        assert case_ref.status == "error"
        assert case_ref.score == 0.0
        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
        assert result["status"] == "error"
        assert "judge boom" in result["error"]
        assert result["evaluation"]["passed"] is False
        assert trace["status"] == "error"
        assert (output_dir / "tr" / "trajectory_events.jsonl").is_file()
        normalized_trace_path = output_dir / "judge" / "normalized_trace.json"
        assert normalized_trace_path.is_file()
        normalized_trace = json.loads(normalized_trace_path.read_text(encoding="utf-8"))
        assert normalized_trace["traces"][0]["messages"]

    @pytest.mark.asyncio
    async def test_backend_exception_writes_error_artifacts(self, tmp_path: Path) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                raise RuntimeError("backend boom")

            async def cleanup(self, team_name: str, session_id: str) -> None:
                raise AssertionError("cleanup should not run without team name")

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        case_ref = await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
        assert case_ref.status == "error"
        assert case_ref.score == 0.0
        assert result["status"] == "error"
        assert result["score"] == 0.0
        assert result["evaluation"]["method"] == "error"
        assert result["evaluation"]["passed"] is False
        assert result["error"] == "backend boom"
        assert trace["status"] == "error"
        assert trace["evaluation"]["passed"] is False
        assert (output_dir / "tr" / "trajectory_events.jsonl").is_file()
        normalized_trace_path = output_dir / "judge" / "normalized_trace.json"
        assert normalized_trace_path.is_file()
        normalized_trace = json.loads(normalized_trace_path.read_text(encoding="utf-8"))
        messages = normalized_trace["traces"][0]["messages"]
        assert any("backend boom" in json.dumps(message) for message in messages)
        assert trace["behavior_trace"]["normalized_trace_path"] == str(normalized_trace_path)

    @pytest.mark.asyncio
    async def test_prefers_backend_judge_result_over_configured_judger(
        self,
        tmp_path: Path,
    ) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    judge_result=JudgeResult(
                        method="backend",
                        score=0.75,
                        passed=True,
                        reason="from backend",
                    ),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                raise AssertionError("configured judger must not run when backend supplies result")

        runner = CaseRunner(backend=_Backend(), judger=_Judger())
        output_dir = tmp_path / "case_001"

        case_ref = await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        assert case_ref.score == 0.75
        assert result["evaluation"]["method"] == "backend"
        assert result["evaluation"]["reason"] == "from backend"
        assert result["status"] == "passed"
        assert result["execution_status"] == "passed"

    def test_case_result_status_uses_judge_result_not_execution_completion(self) -> None:
        status = _case_result_status(
            execution_status="passed",
            judge_result=JudgeResult(
                method="llm_as_judge",
                score=0.98,
                passed=False,
                reason="below case threshold",
            ),
        )

        assert status == "failed"

    @pytest.mark.asyncio
    async def test_single_harness_changed_files_and_pre_judge_trace_are_available_to_judger(
        self,
        tmp_path: Path,
    ) -> None:
        events: list[str] = []

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                output_dir = Path(str(kwargs["output_dir"]))
                workspace = output_dir / "workspace"
                workspace.mkdir(parents=True)
                (workspace / "deck_outline.json").write_text('{"slides": 8}\n', encoding="utf-8")
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    workspace_dir=str(workspace),
                    metadata={
                        "workspace_changes": {
                            "added": ["deck_outline.json"],
                            "modified": [],
                            "removed": [],
                        }
                    },
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        class _Judger:
            async def judge(self, **kwargs: Any) -> JudgeResult:
                events.append("judge")
                output_dir = Path(str(kwargs["output_dir"]))
                assert (output_dir / "artifacts" / "deck_outline.json").is_file()
                assert (output_dir / "tr" / "trajectory_events.jsonl").is_file()
                normalized_trace_path = output_dir / "judge" / "normalized_trace.json"
                assert normalized_trace_path.is_file()
                normalized_trace = json.loads(normalized_trace_path.read_text(encoding="utf-8"))
                messages = normalized_trace["traces"][0]["messages"]
                assert any("deck_outline.json" in json.dumps(message) for message in messages)
                return JudgeResult(method="recording", score=1.0, passed=True)

        runner = CaseRunner(backend=_Backend(), judger=_Judger())
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        assert events == ["judge"]
        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        assert result["artifacts"] == {"harvested": ["deck_outline.json"], "missing": []}

    @pytest.mark.asyncio
    async def test_normalized_trace_uses_backend_member_role_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    metadata={
                        "member_role": "presentation_designer",
                        "member_id": "presentation_designer",
                        "workspace_changes": {
                            "added": ["slides.md"],
                            "modified": [],
                            "removed": [],
                        },
                    },
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        normalized_trace = json.loads((output_dir / "judge" / "normalized_trace.json").read_text(encoding="utf-8"))
        trace = normalized_trace["traces"][0]
        assert trace["member_id"] == "presentation_designer"
        assert trace["member_role"] == "presentation_designer"
        assert trace["trace_id"] == "case_001__presentation_designer__case"

    @pytest.mark.asyncio
    async def test_normalized_trace_uses_collected_team_role_trajectories(
        self,
        tmp_path: Path,
    ) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                trajectory_dir = Path(str(kwargs["output_dir"])) / "tr"
                trajectory_dir.mkdir(parents=True)
                (trajectory_dir / "content-writer.jsonl").write_text(
                    json.dumps(
                        {
                            "execution_id": "content_writer_exec",
                            "steps": [
                                {
                                    "kind": "llm",
                                    "detail": {
                                        "messages": [
                                            {
                                                "role": "user",
                                                "content": "write index.html",
                                            }
                                        ],
                                        "response": {
                                            "role": "assistant",
                                            "content": "I need to write index.html",
                                        },
                                    },
                                }
                            ],
                            "meta": {
                                "member_id": "content-writer",
                                "member_role": "content-writer",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return CaseExecutionResult(
                    response={"answer": "team done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("web_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        normalized_trace = json.loads((output_dir / "judge" / "normalized_trace.json").read_text(encoding="utf-8"))
        traces = normalized_trace["traces"]
        roles = {trace["member_role"] for trace in traces}
        assert "content-writer" in roles
        assert "solver" not in roles
        content_writer_trace = next(trace for trace in traces if trace["member_role"] == "content-writer")
        assert content_writer_trace["trace_id"] == "case_001__content-writer__trajectory"
        assert any("write index.html" in json.dumps(message) for message in content_writer_trace["messages"])

    @pytest.mark.asyncio
    async def test_normalized_trace_reads_nested_role_trajectory_store(
        self,
        tmp_path: Path,
    ) -> None:
        """Older trajectory stores write ``tr/<role>/trajectories_default.jsonl``."""

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                trajectory_dir = Path(str(kwargs["output_dir"])) / "tr" / "team_leader"
                trajectory_dir.mkdir(parents=True)
                (trajectory_dir / "trajectories_default.jsonl").write_text(
                    json.dumps(
                        {
                            "execution_id": "leader_exec",
                            "steps": [
                                {
                                    "kind": "llm",
                                    "detail": {
                                        "messages": [
                                            {
                                                "role": "assistant",
                                                "content": "created tasks for page builder",
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return CaseExecutionResult(
                    response={"answer": "team done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("web_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        normalized_trace = json.loads((output_dir / "judge" / "normalized_trace.json").read_text(encoding="utf-8"))
        team_leader_trace = next(trace for trace in normalized_trace["traces"] if trace["member_role"] == "team_leader")
        assert team_leader_trace["trace_id"] == "case_001__team_leader__trajectory"
        assert any("created tasks for page builder" in json.dumps(message) for message in team_leader_trace["messages"])

    @pytest.mark.asyncio
    async def test_normalized_trace_records_harvested_artifact_refs(
        self,
        tmp_path: Path,
    ) -> None:
        """Analyzer evidence should show which deliverables were actually collected."""

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                workspace_dir = tmp_path / "runtime_workspace"
                (workspace_dir / "artifacts" / "code").mkdir(parents=True)
                (workspace_dir / "artifacts" / "code" / "index.html").write_text(
                    "<html></html>\n",
                    encoding="utf-8",
                )
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    workspace_dir=str(workspace_dir),
                    metadata={"member_role": "page_builder", "member_id": "page_builder"},
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        normalized_trace = json.loads((output_dir / "judge" / "normalized_trace.json").read_text(encoding="utf-8"))
        case_trace = next(trace for trace in normalized_trace["traces"] if trace["member_role"] == "page_builder")
        serialized_messages = json.dumps(case_trace["messages"], ensure_ascii=False)
        assert "artifact_harvest" in serialized_messages
        assert "code/index.html" in serialized_messages
        assert "index.html" in serialized_messages

    @pytest.mark.asyncio
    async def test_normalized_trace_keeps_bounded_tail_for_large_role_trajectory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(case_runner_module, "_MAX_ROLE_TRAJECTORY_FILE_BYTES", 64)

        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                trajectory_dir = Path(str(kwargs["output_dir"])) / "tr"
                trajectory_dir.mkdir(parents=True)
                (trajectory_dir / "team_leader.jsonl").write_text(
                    "x" * 256 + "team_leader declared index.html, styles.css, and "
                    "content_brief.md complete without observed file writes\n",
                    encoding="utf-8",
                )
                return CaseExecutionResult(
                    response={"answer": "team done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("web_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        normalized_trace = json.loads((output_dir / "judge" / "normalized_trace.json").read_text(encoding="utf-8"))
        team_leader_trace = next(trace for trace in normalized_trace["traces"] if trace["member_role"] == "team_leader")
        assert "solver" not in {trace["member_role"] for trace in normalized_trace["traces"]}
        assert any("declared index.html" in json.dumps(message) for message in team_leader_trace["messages"])

    @pytest.mark.asyncio
    async def test_default_score_when_no_judger(self, tmp_path: Path) -> None:
        class _Backend:
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="failed",
                    error="boom",
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        runner = CaseRunner(backend=_Backend(), judger=None)
        output_dir = tmp_path / "case_001"

        case_ref = await runner.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(output_dir),
        )

        assert case_ref.score == 0.0
        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        assert result["evaluation"]["method"] == "none"
        assert result["evaluation"]["passed"] is False


# ---------------------------------------------------------------------------
# LLM-as-judge evidence runtime
# ---------------------------------------------------------------------------


class TestLlmAsJudgeEvidenceRuntime:
    def test_llm_judge_uses_case_pass_threshold(self) -> None:
        threshold = llm_as_judge._case_success_threshold(
            {
                "reference": {
                    "judge_rubric": {
                        "pass_threshold": 0.8,
                    }
                }
            },
            EvaluatorConfig(success_score=1.0),
        )

        assert threshold == 0.8

    def test_judge_prompt_declares_nested_artifact_contract(self) -> None:
        """Judge must not assume deliverables live in the case root."""
        prompt = llm_as_judge.build_judge_prompt(
            case={"input": {"user_message": "build a page"}},
            response={"answer": "done"},
            behaviors=[
                {
                    "id": "artifact_contract",
                    "description": "writes page artifacts",
                    "weight": 1.0,
                    "rubric": "index.html and content_brief.md are inspectable",
                }
            ],
            forbidden=[],
            trace_path="judge/normalized_trace.json",
            artifacts_dir="artifacts",
        )

        assert "artifacts/code/" in prompt
        assert "artifacts/docs/" in prompt
        assert "Do not look for deliverables in the case root" in prompt

    def test_judge_prompt_scores_case_capabilities_not_artifact_existence(self) -> None:
        """Judge remains generic: the case behavior rubric defines the capability being scored."""
        prompt = llm_as_judge.build_judge_prompt(
            case={"input": {"user_message": "build a usable artifact"}},
            response={"answer": "done"},
            behaviors=[
                {
                    "id": "capability_integration",
                    "description": "the artifact satisfies the case-specific capability contract",
                    "weight": 1.0,
                    "rubric": "score the behavior from evidence of task capability, not file existence alone",
                }
            ],
            forbidden=[],
            trace_path="judge/normalized_trace.json",
            artifacts_dir="artifacts",
        )

        assert "Treat each required behavior as a task capability contract" in prompt
        assert "Artifact existence is supporting evidence" in prompt
        assert "If files exist but the behavior rubric is not satisfied, assign a low score" in prompt

    def test_judge_system_prompt_does_not_allow_rail_surface_hint(self) -> None:
        """Analysis hints should stay within optimizer-supported member surfaces."""
        assert "skill|tool|prompt_section|rail|empty" not in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "skill|tool|prompt_section|empty" in llm_as_judge._JUDGE_SYSTEM_PROMPT

    def test_judge_system_prompt_emits_dataset_generation_gaps(self) -> None:
        """Seed evaluation needs structured gaps for targeted dataset generation."""
        assert '"quality_gaps"' in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "visual descendants" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "potentially clickable" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert '"dataset_budget"' in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "data_needed_to_fix" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "Surface hint guidelines" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "deterministic executable capability" in llm_as_judge._JUDGE_SYSTEM_PROMPT

    def test_judge_system_prompt_requires_end_to_end_gap_discovery(self) -> None:
        """Seed judge should identify delivery-quality gaps, not only rubric failures."""
        prompt = llm_as_judge._JUDGE_SYSTEM_PROMPT

        assert "End-to-end quality axes" in prompt
        assert "functional_effectiveness" in prompt
        assert "user_visible_output_quality" in prompt
        assert "runtime_correctness_and_validation" in prompt
        assert "completion_and_acceptance_contract" in prompt
        assert '"why_it_matters"' in prompt
        assert '"missing_capability"' in prompt
        assert "passed-but-imperfect" in prompt

    def test_judge_prompt_guides_generic_end_to_end_quality_review(self) -> None:
        """Case prompt should ask for generic delivery-quality review before gaps."""
        prompt = llm_as_judge.build_judge_prompt(
            case={"input": {"user_message": "ship a complete artifact"}},
            response={"answer": "done"},
            behaviors=[
                {
                    "id": "final_delivery",
                    "description": "the delivered artifact satisfies the user's goal",
                    "weight": 1.0,
                    "rubric": "score from end-to-end user value and artifact evidence",
                }
            ],
            forbidden=[],
            trace_path="judge/normalized_trace.json",
            artifacts_dir="artifacts",
        )

        assert "### End-to-end quality review" in prompt
        assert "Required behaviors are minimum task capability contracts" in prompt
        assert "Score 1.0 only when" in prompt
        assert "emit quality_gaps" in prompt

    def test_judge_prompt_requires_independent_quality_contract(self) -> None:
        """Judge should not let agent-generated plans define the quality bar."""
        prompt = llm_as_judge.build_judge_prompt(
            case={"input": {"user_message": "build a browser game"}},
            response={"answer": "done"},
            behaviors=[
                {
                    "id": "core_task_semantics",
                    "description": "the game rules produce a coherent user experience",
                    "weight": 1.0,
                    "rubric": "score from user-facing gameplay semantics",
                }
            ],
            forbidden=[],
            trace_path="judge/normalized_trace.json",
            artifacts_dir="artifacts",
        )

        assert "independent task quality contract" in prompt
        assert "agent-generated plans, rules, QA reports, or summaries" in prompt
        assert "cannot redefine the user's quality bar" in prompt

    def test_gap_budget_prompt_requires_artifact_quality_gap_type(self) -> None:
        """Gap generation should distinguish weak artifacts from missing evidence."""
        prompt = llm_as_judge._build_gap_budget_segment_prompt(
            evidence_prompt="evidence",
            behavior_payload={
                "overall_reason": "usable but shallow",
                "behaviors": [
                    {
                        "id": "end_to_end_quality",
                        "score": 0.75,
                        "reason": "runs but the main experience is shallow",
                        "failure_reason": "only one action path is available",
                        "missing_capability": "richer task-specific interaction design",
                        "evidence": "interaction evidence",
                    }
                ],
                "forbidden_hits": [],
            },
        )

        assert "gap_type" in prompt
        assert "artifact_quality_gap" in prompt
        assert "verification_gap" in prompt
        assert "Prefer artifact_quality_gap" in prompt

    def test_judge_prompt_requires_exceptional_evidence_for_perfect_score(self) -> None:
        """A judge score of 1.0 must mean the final deliverable is materially complete."""
        prompt = llm_as_judge.build_judge_prompt(
            case={"input": {"user_message": "ship a browser game"}},
            response={"answer": "done"},
            behaviors=[
                {
                    "id": "end_to_end_quality",
                    "description": "the final artifact is usable, polished, and complete",
                    "weight": 1.0,
                    "rubric": "score from actual deliverable quality",
                }
            ],
            forbidden=[],
            trace_path="judge/normalized_trace.json",
            artifacts_dir="artifacts",
        )

        assert "A behavior score of 1.0 means" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "interaction_closure evidence" in llm_as_judge._JUDGE_SYSTEM_PROMPT
        assert "not enough for a perfect score" in prompt
        assert "self-reported QA" in prompt

    def test_normalized_judge_trace_excludes_model_reasoning(self) -> None:
        """Judge evidence must use visible behavior, not provider reasoning text."""
        trace = llm_as_judge._normalize_one_record(
            {
                "execution_id": "exec_001",
                "steps": [
                    {
                        "kind": "llm",
                        "detail": {
                            "messages": [
                                {"role": "user", "content": "write the file"},
                            ],
                            "response": {
                                "role": "assistant",
                                "content": "I will write the file.",
                                "reasoning_content": "hidden chain of thought",
                            },
                        },
                    }
                ],
                "meta": {"member_id": "designer"},
            }
        )

        assistant_turns = [message for message in trace["messages"] if message.get("role") == "assistant"]
        assert assistant_turns
        assert "reasoning_excerpt" not in assistant_turns[-1]
        assert "reasoning_content" not in assistant_turns[-1]

    def test_direct_judge_prompt_summarizes_self_report_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        """Judge evidence should expose support artifacts without raw self-claims."""
        case_dir = tmp_path / "case_001"
        artifacts_dir = case_dir / "artifacts"
        judge_dir = case_dir / "judge"
        artifacts_dir.mkdir(parents=True)
        judge_dir.mkdir()
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(
                {
                    "traces": [
                        {
                            "trace_id": "case__playtest-validator__trajectory",
                            "member_role": "playtest-validator",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": (
                                        "test_report.md says D1 Fireball targeting is minor, "
                                        "D2 auto-targeting is cosmetic, and all tests pass"
                                    ),
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (artifacts_dir / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
        (artifacts_dir / "game.js").write_text("console.log('game')", encoding="utf-8")
        (artifacts_dir / "test_report.md").write_text(
            "Self-reported QA: all tests passed and the game is perfect.",
            encoding="utf-8",
        )
        (artifacts_dir / "review_conclusion.md").write_text(
            "Review conclusion: ready for delivery.",
            encoding="utf-8",
        )
        (artifacts_dir / "final_delivery_summary.md").write_text(
            "Final delivery summary: complete.",
            encoding="utf-8",
        )

        prompt = llm_as_judge._build_direct_judge_prompt(
            base_prompt="judge this",
            case_dir=case_dir,
        )

        assert "##### artifacts/index.html" in prompt
        assert "##### artifacts/game.js" in prompt
        assert "Self-reported QA" not in prompt
        assert "Review conclusion" not in prompt
        assert "Final delivery summary" not in prompt
        assert "D1 Fireball" not in prompt
        assert "D2 auto-targeting" not in prompt
        assert "omitted self-reported/supporting artifacts" in prompt
        assert "Deterministic supporting artifact evidence" in prompt
        assert "test_report.md" in prompt
        assert "supporting_artifacts" in prompt
        assert "validation_terms" in prompt

    def test_direct_judge_prompt_reports_web_dom_contract_mismatch(self, tmp_path: Path) -> None:
        """HTML/JS ID mismatches are primary runtime evidence, not a model guess."""
        case_dir = tmp_path / "case_001"
        artifacts_dir = case_dir / "artifacts"
        judge_dir = case_dir / "judge"
        artifacts_dir.mkdir(parents=True)
        judge_dir.mkdir()
        (judge_dir / "normalized_trace.json").write_text('{"traces":[]}', encoding="utf-8")
        (artifacts_dir / "index.html").write_text(
            """
            <html><body>
              <div id="enemy-hp-bar"></div>
              <div id="player-hp-bar"></div>
              <div id="player-hand"></div>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text(
            """
            function $(id) { return document.getElementById(id); }
            function renderHero(who) {
              const hpBar = $(`${who}-hp-bar`);
              hpBar.style.width = '100%';
            }
            function renderHand() { $('player-hand').innerHTML = ''; }
            function render() {
              renderHero('player');
              renderHero('ai');
              renderHand();
            }
            render();
            """,
            encoding="utf-8",
        )

        prompt = llm_as_judge._build_direct_judge_prompt(
            base_prompt="judge this",
            case_dir=case_dir,
        )

        assert "Deterministic artifact contract evidence" in prompt
        assert "missing_dom_ids" in prompt
        assert "ai-hp-bar" in prompt
        assert "material_runtime_blocker" in prompt

    def test_direct_judge_prompt_reports_generic_interaction_evidence(self, tmp_path: Path) -> None:
        """Interactive deliverables should expose effect/depth evidence to the judge."""
        case_dir = tmp_path / "case_001"
        artifacts_dir = case_dir / "artifacts"
        judge_dir = case_dir / "judge"
        artifacts_dir.mkdir(parents=True)
        judge_dir.mkdir()
        (judge_dir / "normalized_trace.json").write_text('{"traces":[]}', encoding="utf-8")
        (artifacts_dir / "index.html").write_text(
            """
            <html><body>
              <button id="play-card">Play Card</button>
              <button id="end-turn">End Turn</button>
              <div id="turn"></div>
              <div id="result"></div>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text(
            """
            document.getElementById('play-card').addEventListener('click', () => {
              document.getElementById('turn').textContent = 'card played';
            });
            document.getElementById('end-turn').addEventListener('click', () => {
              document.getElementById('result').textContent = 'you win';
            });
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "styles.css").write_text(
            ".board { display: grid; }\n.card { border: 1px solid #333; }\n",
            encoding="utf-8",
        )
        support_dir = artifacts_dir / "node_modules" / "example"
        support_dir.mkdir(parents=True)
        (support_dir / "large.js").write_text("x" * 80_000, encoding="utf-8")

        prompt = llm_as_judge._build_direct_judge_prompt(
            base_prompt="judge this",
            case_dir=case_dir,
        )

        assert "Deterministic interaction/effect evidence" in prompt
        assert "interactive_control_count" in prompt
        assert "effect_handler_count" in prompt
        assert "decision_surface_hint" in prompt
        assert "##### artifacts/styles.css" in prompt
        assert "node_modules/example/large.js" not in prompt

    def test_interaction_evidence_reports_action_state_feedback_outcome_closure(
        self,
        tmp_path: Path,
    ) -> None:
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "index.html").write_text(
            """
            <html><body>
              <button id="play">Play</button>
              <div id="score"></div>
              <div id="result"></div>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text(
            """
            const gameState = { score: 0, phase: 'ready' };
            function render() {
              document.getElementById('score').textContent = String(gameState.score);
            }
            function showWin() {
              document.getElementById('result').textContent = 'win';
              document.getElementById('result').classList.add('visible');
            }
            document.getElementById('play').addEventListener('click', () => {
              gameState.score += 1;
              gameState.phase = 'complete';
              render();
              showWin();
            });
            """,
            encoding="utf-8",
        )

        evidence = llm_as_judge._build_deterministic_interaction_evidence(artifacts_dir)

        closure = evidence["interaction_closure"]
        assert closure["state_mutation_path_count"] == 1
        assert closure["dom_update_path_count"] == 1
        assert closure["render_call_path_count"] == 1
        assert closure["terminal_outcome_path_count"] == 1
        assert closure["closure_hint"] == "action_state_feedback_outcome_detected"

    def test_interaction_evidence_flags_controls_without_handlers(
        self,
        tmp_path: Path,
    ) -> None:
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "index.html").write_text(
            """
            <html><body>
              <button id="attack">Attack</button>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text("console.log('ready');", encoding="utf-8")

        evidence = llm_as_judge._build_deterministic_interaction_evidence(artifacts_dir)

        closure = evidence["interaction_closure"]
        assert closure["potential_noop_controls"] == ["button id=attack text=Attack"]
        assert closure["closure_hint"] == "controls_without_event_binding"

    def test_interaction_evidence_follows_nested_handler_calls_to_terminal_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "index.html").write_text(
            """
            <html><body>
              <button id="resolve">Resolve</button>
              <div id="result"></div>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text(
            """
            const state = { phase: 'ready' };
            function finalize() {
              document.getElementById('result').textContent = 'win';
            }
            function handleResolve() {
              state.phase = 'ended';
              finalize();
            }
            document.getElementById('resolve').addEventListener('click', () => {
              handleResolve();
            });
            """,
            encoding="utf-8",
        )

        evidence = llm_as_judge._build_deterministic_interaction_evidence(artifacts_dir)

        closure = evidence["interaction_closure"]
        assert closure["terminal_outcome_path_count"] == 1
        assert closure["closure_hint"] == "action_state_feedback_outcome_detected"

    def test_direct_judge_prompt_summarizes_structure_beyond_text_excerpt(
        self,
        tmp_path: Path,
    ) -> None:
        """Long artifacts should expose structural evidence after the excerpt boundary."""
        case_dir = tmp_path / "case_001"
        artifacts_dir = case_dir / "artifacts"
        judge_dir = case_dir / "judge"
        artifacts_dir.mkdir(parents=True)
        judge_dir.mkdir()
        (judge_dir / "normalized_trace.json").write_text('{"traces":[]}', encoding="utf-8")
        (artifacts_dir / "index.html").write_text(
            """
            <html><head>
              <link rel="stylesheet" href="styles.css">
            </head><body>
              <button id="play">Play</button>
              <div id="target"></div>
              <script src="game.js"></script>
            </body></html>
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "game.js").write_text(
            ("const filler = 'x';\n" * 900)
            + """
            function runAITurn() {
              document.getElementById('target').textContent = 'ai acted';
            }
            document.getElementById('play').addEventListener('click', runAITurn);
            """,
            encoding="utf-8",
        )
        (artifacts_dir / "styles.css").write_text(
            (".filler { color: white; }\n" * 900)
            + """
            @media (max-width: 768px) { .board { grid-template-columns: 1fr; } }
            @media (prefers-reduced-motion: reduce) { * { animation: none; } }
            .card:focus-visible { outline: 2px solid gold; }
            """,
            encoding="utf-8",
        )

        prompt = llm_as_judge._build_direct_judge_prompt(
            base_prompt="judge this",
            case_dir=case_dir,
        )

        assert "Deterministic artifact evidence coverage" in prompt
        assert "files_truncated" in prompt
        assert "full_file_static_summary_available" in prompt
        assert "[truncated: kept" not in prompt
        assert "game.js" in prompt
        assert "function runAITurn()" in prompt
        assert "runAITurn" in prompt
        assert "styles.css" in prompt
        assert "prefers-reduced-motion" in prompt
        assert "max-width: 768px" in prompt

    def test_gap_budget_prompt_routes_truncation_to_verification_gap(self) -> None:
        """Evidence coverage gaps should not be converted into artifact quality gaps."""
        prompt = llm_as_judge._build_gap_budget_segment_prompt(
            evidence_prompt=('coverage={"files_truncated":["artifacts/game.js"],"evidence_confidence":"medium"}'),
            behavior_payload={
                "overall_reason": "implementation looks good but evidence is partial",
                "behaviors": [
                    {
                        "id": "core_task_semantics",
                        "score": 0.85,
                        "reason": "main behavior appears present",
                        "failure_reason": "game.js was truncated in evidence",
                        "missing_capability": "complete runtime evidence",
                        "evidence": "artifact evidence coverage",
                    }
                ],
                "forbidden_hits": [],
            },
        )

        assert "truncated" in prompt
        assert "coverage" in prompt
        assert "must be verification_gap" in prompt
        assert "raw excerpt truncation alone" in prompt

    @pytest.mark.asyncio
    async def test_llm_judge_discards_gap_that_contradicts_behavior_scores(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The gap-budget segment cannot silently overrule accepted scores."""

        async def fake_run_judge(self, *, output_dir: str, prompt: str) -> str:
            return json.dumps(
                {
                    "overall_reason": "good but incomplete",
                    "behaviors": [
                        {
                            "id": "end_to_end_quality",
                            "score": 1.0,
                            "reason": "looks good",
                            "failure_reason": "",
                            "missing_capability": "",
                            "suggested_surface_hint": "",
                            "evidence": "artifacts/index.html",
                        }
                    ],
                    "forbidden_hits": [],
                    "quality_gaps": [
                        {
                            "id": "weak_interaction_loop",
                            "dimension": "interaction completeness",
                            "severity": "medium",
                            "affected_roles": ["frontend-engineer"],
                            "likely_surfaces": ["skill"],
                            "evidence": "end_to_end_quality",
                            "data_needed_to_fix": "Generate cases that exercise full interaction loops.",
                        }
                    ],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(llm_as_judge.LlmAsJudgeJudger, "_run_judge", fake_run_judge)
        judger = llm_as_judge.LlmAsJudgeJudger(EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path))))

        result = await judger.judge(
            case={
                "reference": {
                    "required_behaviors": [
                        {
                            "id": "end_to_end_quality",
                            "description": "complete delivery",
                            "weight": 1.0,
                        }
                    ],
                    "judge_rubric": {"pass_threshold": 0.8},
                }
            },
            execution_result=CaseExecutionResult(
                response={"answer": "done"},
                execution_status="passed",
            ),
            output_dir=str(tmp_path),
        )

        assert result.score == 1.0
        assert result.metadata["parsed"]["overall_score"] == result.score
        assert result.metadata["parsed"]["quality_gaps"] == []
        assert result.metadata["parsed"]["discarded_quality_gaps"][0]["id"] == ("weak_interaction_loop")

    def test_core_quality_gap_caps_score_at_core_failure_ceiling(self) -> None:
        """Core task semantics failures should override artifact-level success."""
        ceiling = llm_as_judge._quality_gap_score_ceiling(
            {
                "quality_gaps": [
                    {
                        "id": "core_task_semantics_gap",
                        "dimension": "core_task_semantics",
                        "severity": "high",
                    }
                ]
            }
        )

        assert ceiling == 0.65

    def test_unanchored_gap_cannot_contradict_all_behavior_scores(self) -> None:
        parsed = llm_as_judge._discard_unanchored_contradictory_gaps(
            {
                "behaviors": [
                    {"id": "rules", "score": 0.95},
                    {"id": "tooltips", "score": 0.90},
                ],
                "quality_gaps": [
                    {
                        "id": "invented_core_bug",
                        "dimension": "runtime_correctness_and_validation",
                        "severity": "high",
                        "evidence": "artifacts/game.js allegedly misses a reset",
                    },
                    {
                        "id": "minor_validation_gap",
                        "dimension": "validation_depth",
                        "severity": "low",
                        "evidence": "browser smoke evidence",
                    },
                ],
                "dataset_budget": {
                    "total_cases": 3,
                    "case_groups": [
                        {"source_gap": "invented_core_bug", "case_count": 2},
                        {"source_gap": "minor_validation_gap", "case_count": 1},
                    ],
                },
            }
        )

        assert [gap["id"] for gap in parsed["quality_gaps"]] == ["minor_validation_gap"]
        assert parsed["discarded_quality_gaps"][0]["id"] == "invented_core_bug"
        assert parsed["dataset_budget"] == {
            "total_cases": 1,
            "case_groups": [{"source_gap": "minor_validation_gap", "case_count": 1}],
        }

    def test_anchored_gap_severity_is_normalized_instead_of_discarded(self) -> None:
        parsed = llm_as_judge._discard_unanchored_contradictory_gaps(
            {
                "behaviors": [
                    {
                        "id": "core_task_semantics",
                        "score": 0.78,
                        "failure_reason": "Turn state is not cleaned up.",
                        "missing_capability": "Multi-turn state cleanup.",
                    },
                    {"id": "validation_depth", "score": 0.72},
                ],
                "quality_gaps": [
                    {
                        "id": "turn_state_cleanup_bug",
                        "dimension": "core_task_semantics",
                        "severity": "high",
                        "evidence": "artifacts/game.js",
                    }
                ],
                "dataset_budget": {
                    "total_cases": 2,
                    "case_groups": [{"source_gap": "turn_state_cleanup_bug", "case_count": 2}],
                },
            }
        )

        assert parsed["quality_gaps"] == [
            {
                "id": "turn_state_cleanup_bug",
                "dimension": "core_task_semantics",
                "severity": "medium",
                "evidence": "artifacts/game.js",
                "original_severity": "high",
                "consistency_status": "severity_normalized_to_behavior_scores",
                "consistency_reason": (
                    "severity reduced from high to medium to remain consistent "
                    "with minimum accepted behavior score 0.72"
                ),
            }
        ]
        assert "discarded_quality_gaps" not in parsed
        assert parsed["dataset_budget"]["total_cases"] == 2

    def test_behavior_id_reference_cannot_bypass_score_consistency(self) -> None:
        parsed = llm_as_judge._discard_unanchored_contradictory_gaps(
            {
                "behaviors": [{"id": "end_to_end_quality", "score": 1.0}],
                "quality_gaps": [
                    {
                        "id": "interaction_gap",
                        "dimension": "interaction completeness",
                        "severity": "medium",
                        "evidence": "end_to_end_quality lacks a complete interaction loop",
                    }
                ],
            }
        )

        assert parsed["quality_gaps"] == []
        assert parsed["discarded_quality_gaps"][0]["id"] == "interaction_gap"

    def test_judge_agent_model_config_expands_env_vars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model_path = tmp_path / "judge_model.yaml"
        model_path.write_text(
            yaml.safe_dump(
                {
                    "model_client_config": {
                        "client_provider": "OpenAI",
                        "api_key": "${TOKEN_PLAN_API_KEY}",
                        "api_base": "http://localhost",
                        "verify_ssl": False,
                    },
                    "model_request_config": {
                        "model": "judge-model",
                        "temperature": 0.0,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("TOKEN_PLAN_API_KEY", "expanded-judge-key")
        captured: dict[str, Any] = {}

        def fake_create_deep_agent(**kwargs: Any) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(llm_as_judge, "create_deep_agent", fake_create_deep_agent)

        agent = llm_as_judge.build_judge_agent(
            EvaluatorConfig(
                model_config_ref=str(tmp_path / "run_model_should_not_be_loaded.yaml"),
                judge_model_config_ref=str(model_path),
            ),
            str(tmp_path / "judge"),
        )

        assert agent is not None
        model = captured["model"]
        assert model.model_client_config.api_key == "expanded-judge-key"
        assert model.model_config.model_name == "judge-model"
        assert captured["max_iterations"] <= 8

    @pytest.mark.asyncio
    async def test_writes_only_normalized_trace_no_manifest_or_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "small.md").write_text("# small\ncontent\n", encoding="utf-8")
        (artifacts_dir / "image.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "result.json").write_text("{}", encoding="utf-8")
        (tmp_path / "trace.json").write_text("{}", encoding="utf-8")

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            assert (tmp_path / "judge" / "normalized_trace.json").is_file()
            assert not (tmp_path / "judge" / "evidence_summary.md").exists()
            assert not (tmp_path / "judge" / "artifacts_manifest.json").exists()
            assert "small.md" in prompt
            assert "# small\ncontent" in prompt
            assert "image.png" in prompt
            return '{"behaviors": [], "forbidden_hits": []}'

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        config = EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path)))
        raw = await run_llm_judge(config, str(tmp_path), "judge this")

        assert raw == '{"behaviors": [], "forbidden_hits": []}'
        judge_dir = tmp_path / "judge"
        assert (judge_dir / "normalized_trace.json").is_file()
        assert not (judge_dir / ".runtime").exists()
        assert not (judge_dir / "artifacts_manifest.json").exists()
        assert not (judge_dir / "evidence_summary.md").exists()
        assert not (judge_dir / "raw_output.txt").exists()
        assert not (judge_dir / "artifacts").exists()

    @pytest.mark.asyncio
    async def test_preserves_case_runner_normalized_trace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        judge_dir = tmp_path / "judge"
        judge_dir.mkdir()
        existing_trace = {
            "case_id": "case_001",
            "traces": [
                {
                    "trace_id": "case_001__solver__case",
                    "member_id": "solver",
                    "member_role": "solver",
                    "execution_id": "case_001",
                    "messages": [{"role": "assistant", "content": "prebuilt evidence"}],
                }
            ],
        }
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(existing_trace),
            encoding="utf-8",
        )

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            preserved = json.loads((judge_dir / "normalized_trace.json").read_text(encoding="utf-8"))
            assert preserved == existing_trace
            assert "case_001__solver__case" in prompt
            assert "prebuilt evidence" not in prompt
            return '{"behaviors": [], "forbidden_hits": []}'

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        config = EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path)))
        raw = await run_llm_judge(config, str(tmp_path), "judge this")

        assert raw == '{"behaviors": [], "forbidden_hits": []}'

    @pytest.mark.asyncio
    async def test_run_llm_judge_retries_mojibake_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Judge raw model output that is visibly garbled must be retried."""
        output_dir = Path(f".tmp_llm_judge_retry_mojibake_{uuid.uuid4().hex}")
        attempts = 0

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return "\u7487\u8702\u8d1f\u93c2\u62cc\u5165\u6d93\u5d87\u6b91\u6d93\u5d87\u6d49\u6ed5\u6939"
            return '{"behaviors": [], "forbidden_hits": []}'

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        config = EvaluatorConfig(model_config_ref="unused.yaml")
        raw = await run_llm_judge(config, str(output_dir), "judge this")

        assert raw == '{"behaviors": [], "forbidden_hits": []}'
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_run_llm_judge_retries_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient judge model timeouts should not abort the evaluation stage."""
        output_dir = Path(f".tmp_llm_judge_retry_timeout_{uuid.uuid4().hex}")
        attempts = 0

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.TimeoutError("judge model request timed out")
            return '{"behaviors": [], "forbidden_hits": []}'

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        config = EvaluatorConfig(model_config_ref="unused.yaml")
        raw = await run_llm_judge(config, str(output_dir), "judge this")

        assert raw == '{"behaviors": [], "forbidden_hits": []}'
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_run_llm_judge_uses_small_retry_budget_for_empty_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Judge empty-output retries should not inherit the high global budget."""
        output_dir = Path(f".tmp_llm_judge_retry_empty_{uuid.uuid4().hex}")
        attempts = 0

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            nonlocal attempts
            attempts += 1
            return ""

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        config = EvaluatorConfig(model_config_ref="unused.yaml")
        with pytest.raises(Exception, match="llm judge model output is empty"):
            await run_llm_judge(config, str(output_dir), "judge this")

        assert attempts == 3

    @pytest.mark.asyncio
    async def test_run_llm_judge_does_not_create_deep_agent_runtime(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LLM judge should be a bounded model call, not a long-running ReAct loop."""
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "index.html").write_text("<button>Play</button>", encoding="utf-8")

        def fail_build_judge_agent(*args: Any, **kwargs: Any) -> object:
            raise AssertionError("run_llm_judge must not create a DeepAgent judge")

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            assert "index.html" in prompt
            assert "<button>Play</button>" in prompt
            return '{"behaviors": [], "forbidden_hits": []}'

        monkeypatch.setattr(llm_as_judge, "build_judge_agent", fail_build_judge_agent)
        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        raw = await run_llm_judge(
            EvaluatorConfig(model_config_ref="unused.yaml"),
            str(tmp_path),
            "judge this",
        )

        assert raw == '{"behaviors": [], "forbidden_hits": []}'

    @pytest.mark.asyncio
    async def test_run_llm_judge_splits_gap_budget_after_behavior_scores(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Judge should combine short segmented JSON outputs instead of one huge blob."""
        prompts: list[str] = []

        async def fake_invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
            prompts.append(prompt)
            if "### Judge output segment\nbehavior_scores" in prompt:
                return json.dumps(
                    {
                        "overall_reason": "usable but incomplete",
                        "behaviors": [
                            {
                                "id": "playability",
                                "score": 0.4,
                                "reason": "target selection is missing",
                                "failure_reason": "player cannot choose an attack target",
                                "missing_capability": "targeted interaction state machine",
                                "suggested_surface_hint": "skill",
                                "evidence": "artifacts/game.js",
                            }
                        ],
                        "forbidden_hits": [],
                    },
                    ensure_ascii=False,
                )
            if "### Judge output segment\ngap_budget" in prompt:
                return json.dumps(
                    {
                        "quality_gaps": [
                            {
                                "id": "target_selection_gap",
                                "dimension": "interaction completeness",
                                "severity": "high",
                                "affected_roles": ["game-logic-engineer"],
                                "likely_surfaces": ["skill", "tool"],
                                "evidence": "playability",
                                "why_it_matters": "the core game loop cannot be completed",
                                "missing_capability": "stateful target selection",
                                "training_signal_priority": "high",
                                "data_needed_to_fix": "cases that require target selection and attack validation",
                            }
                        ],
                        "dataset_budget": {
                            "total_cases": 2,
                            "case_groups": [
                                {
                                    "source_gap": "target_selection_gap",
                                    "case_count": 2,
                                    "target_roles": ["game-logic-engineer"],
                                    "target_surfaces": ["skill", "tool"],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
            raise AssertionError("unexpected judge segment prompt")

        monkeypatch.setattr(llm_as_judge, "_invoke_judge_model", fake_invoke_judge_model)

        raw = await run_llm_judge(
            EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path))),
            str(tmp_path),
            "judge this",
        )

        parsed = json.loads(raw)
        assert parsed["behaviors"][0]["id"] == "playability"
        assert parsed["quality_gaps"][0]["id"] == "target_selection_gap"
        assert parsed["dataset_budget"]["total_cases"] == 2
        assert len(prompts) == 2
        assert "### Judge output segment\nbehavior_scores" in prompts[0]
        assert "### Judge output segment\ngap_budget" in prompts[1]

    @pytest.mark.asyncio
    async def test_llm_as_judge_limits_json_parse_retries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Unparsable judge JSON should not loop through the full model retry budget."""
        attempts = 0

        async def fake_run_judge(
            self: llm_as_judge.LlmAsJudgeJudger,
            *,
            output_dir: str,
            prompt: str,
        ) -> str:
            nonlocal attempts
            attempts += 1
            return "not json"

        monkeypatch.setattr(llm_as_judge.LlmAsJudgeJudger, "_run_judge", fake_run_judge)
        judger = llm_as_judge.LlmAsJudgeJudger(EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path))))

        result = await judger.judge(
            case={
                "case_id": "case_001",
                "input": "build",
                "reference": {
                    "required_behaviors": [
                        {"id": "complete", "description": "complete task"},
                    ]
                },
            },
            execution_result=CaseExecutionResult(
                response="done",
                execution_status="passed",
            ),
            output_dir=str(tmp_path),
        )

        assert attempts == 2
        assert result.method == "llm_as_judge"
        assert result.passed is False


# ---------------------------------------------------------------------------
# LocalExecutionBackend
# ---------------------------------------------------------------------------


class TestLocalExecutionBackend:
    def test_build_local_team_case_input_uses_positive_lifecycle_contract(self) -> None:
        case_input = build_local_team_case_input(
            {
                "case_id": "case_001",
                "input": {"user_message": ("创建一个响应式网页小游戏，输出 index.html、styles.css、game.js。")},
            }
        )

        assert "这是一个自动评测 case" in case_input
        assert "请按以下顺序完成" in case_input
        assert "调用 build_team" in case_input
        assert "调用 create_task" in case_input
        assert "调用 send_message" in case_input
        assert "调用 shutdown_member" in case_input
        assert "调用 clean_team" in case_input
        assert "质量闭环最多包含一次修复与一次复审" in case_input
        assert ".team/<team_name>/artifacts/" in case_input
        assert "index.html、styles.css、game.js" in case_input
        assert "content_brief.md" not in case_input
        assert "不要" not in case_input
        assert "不得" not in case_input

    @pytest.mark.asyncio
    async def test_maps_model_and_workspace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)
        case_output_dir = tmp_path / "case_results" / "case_001"
        config = EvaluatorConfig(model_config_ref=str(_write_model_config(tmp_path)))
        captured: dict[str, Any] = {}

        def _create_team_spec(
            self: TeamSkillTeamFactory,
            *,
            team_skill_ref_path: str | Path | None,
            harness_refs: dict[str, str],
            output_dir: str,
        ) -> TeamAgentSpec:
            spec = _build_spec_from_config(
                self.config,
                team_name="math_team",
                output_dir=output_dir,
            )
            captured["spec"] = spec
            captured["team_skill_ref_path"] = team_skill_ref_path
            captured["harness_refs"] = harness_refs
            return spec

        async def _start() -> bool:
            captured["started"] = True
            return True

        async def _run_agent_team(*, agent_team: TeamAgentSpec, inputs: Any, session: str) -> dict:
            captured["inputs"] = inputs
            captured["session"] = session
            return {"ok": True}

        async def _stop() -> None:
            captured["stopped"] = True

        monkeypatch.setattr(TeamSkillTeamFactory, "create_team_spec", _create_team_spec)
        monkeypatch.setattr(case_backend_module.Runner, "start", _start)
        monkeypatch.setattr(case_backend_module.Runner, "run_agent_team", _run_agent_team)
        monkeypatch.setattr(case_backend_module.Runner, "stop", _stop)

        backend = LocalExecutionBackend(config=config)
        result = await backend.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(case_output_dir),
            session_id="eval_case_001",
            team_skill_ref_path=str(skill_dir),
            harness_refs={"alice": "harness"},
        )

        spec = captured["spec"]
        assert result.execution_status == "passed"
        assert result.judge_result is None
        # workspace.root_path is None — auto-derived via redirected team_home at runtime.
        assert spec.workspace.root_path is None
        assert spec.workspace.enabled is True
        # Per-role stable_base=True lets AgentConfigurator derive member workspaces automatically.
        assert spec.agents["leader"].workspace.stable_base is True
        assert spec.agents["teammate"].workspace.stable_base is True
        assert spec.model_router.model_names == ["glm-5"]
        assert spec.model_router.api_base_url == "http://127.0.0.1:8000/v1"
        assert "这是一个自动评测 case" in captured["inputs"]
        assert "hello" in captured["inputs"]
        assert captured["session"] == "eval_case_001"
        assert captured["started"] is True
        assert "stopped" not in captured

    @pytest.mark.asyncio
    async def test_team_lifecycle_waits_for_natural_completion_despite_case_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            TeamSkillTeamFactory,
            "create_team_spec",
            lambda self, **kwargs: _build_spec_from_config(
                self.config,
                team_name="math_team",
                output_dir=kwargs["output_dir"],
            ),
        )
        monkeypatch.setattr(case_backend_module.Runner, "start", lambda: asyncio.sleep(0))

        async def _returns_after_case_timeout(**kwargs: Any) -> dict[str, bool]:
            await asyncio.sleep(0.03)
            return {"ok": True}

        monkeypatch.setattr(case_backend_module.Runner, "run_agent_team", _returns_after_case_timeout)
        monkeypatch.setattr(case_backend_module.Runner, "stop", lambda: asyncio.sleep(0))

        backend = LocalExecutionBackend(config=EvaluatorConfig())
        result = await backend.execute(
            case={"case_id": "case_001", "input": "x", "timeout_sec": 0.001},
            output_dir=str(tmp_path / "case_001"),
            session_id="eval_case_001",
        )

        assert result.execution_status == "passed"
        assert result.error == ""
        assert result.response == {"ok": True}
        assert "failure_type" not in result.metadata

    @pytest.mark.asyncio
    async def test_delivered_artifacts_do_not_interrupt_team_final_response(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            TeamSkillTeamFactory,
            "create_team_spec",
            lambda self, **kwargs: _build_spec_from_config(
                self.config,
                team_name="math_team",
                output_dir=kwargs["output_dir"],
            ),
        )
        monkeypatch.setattr(case_backend_module.Runner, "start", lambda: asyncio.sleep(0))
        allow_final_response = asyncio.Event()

        async def _run_agent_team(*, agent_team: TeamAgentSpec, inputs: Any, session: str) -> dict:
            artifacts_dir = team_home(agent_team.team_name) / "team-workspace" / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("index.html", "styles.css", "game.js"):
                (artifacts_dir / filename).write_text(filename, encoding="utf-8")
            await allow_final_response.wait()
            return {"final": "delivery summary"}

        async def _stop_agent_team(*, team_name: str, session_id: str) -> bool:
            raise AssertionError(f"evaluator must not stop {team_name}/{session_id} before final response")

        monkeypatch.setattr(case_backend_module.Runner, "run_agent_team", _run_agent_team)
        monkeypatch.setattr(case_backend_module.Runner, "stop_agent_team", _stop_agent_team)
        monkeypatch.setattr(case_backend_module.Runner, "stop", lambda: asyncio.sleep(0))
        configure_openjiuwen_home(tmp_path / "runtime_home")
        try:
            backend = LocalExecutionBackend(config=EvaluatorConfig())
            execution = asyncio.create_task(
                backend.execute(
                    case={
                        "case_id": "case_001",
                        "input": {"user_message": "build files"},
                        "reference": {
                            "expected_files": ["index.html", "styles.css", "game.js"],
                        },
                        "artifact_delivery_grace_sec": 0.01,
                        "artifact_delivery_poll_sec": 0.01,
                    },
                    output_dir=str(tmp_path / "case_001"),
                    session_id="eval_case_001",
                )
            )
            await asyncio.sleep(0.05)
            assert execution.done() is False
            allow_final_response.set()
            result = await asyncio.wait_for(execution, timeout=1.0)
        finally:
            reset_openjiuwen_home()

        assert result.execution_status == "passed"
        assert result.error == ""
        assert result.response == {"final": "delivery summary"}
        assert result.metadata == {}

    @pytest.mark.asyncio
    async def test_artifact_delivery_guard_requires_terminal_task_board(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _TaskDao:
            async def get_team_tasks(self, team_name: str) -> list[SimpleNamespace]:
                assert team_name == "math_team"
                return [
                    SimpleNamespace(status="completed"),
                    SimpleNamespace(status="pending"),
                ]

        class _Db:
            task = _TaskDao()

            async def initialize(self) -> None:
                return None

        monkeypatch.setattr(case_backend_module, "get_shared_db", lambda _config: _Db())

        ready = await _team_task_board_terminal_for_delivery(
            db_config=SimpleNamespace(db_type="memory"),
            team_name="math_team",
            session_id="eval_case_001",
        )

        assert ready is False

    @pytest.mark.asyncio
    async def test_artifact_delivery_guard_accepts_all_terminal_task_board(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _TaskDao:
            async def get_team_tasks(self, team_name: str) -> list[SimpleNamespace]:
                assert team_name == "math_team"
                return [
                    SimpleNamespace(status="completed"),
                    SimpleNamespace(status="cancelled"),
                ]

        class _Db:
            task = _TaskDao()

            async def initialize(self) -> None:
                return None

        monkeypatch.setattr(case_backend_module, "get_shared_db", lambda _config: _Db())

        ready = await _team_task_board_terminal_for_delivery(
            db_config=SimpleNamespace(db_type="memory"),
            team_name="math_team",
            session_id="eval_case_001",
        )

        assert ready is True

    @pytest.mark.asyncio
    async def test_cleanup_falls_back_to_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []

        async def _raise_missing(*, team_name: str, session_ids: list[str], force: bool) -> None:
            events.append("delete_attempt")
            raise RuntimeError(
                "Cannot resolve team session release info for any supplied sessions: "
                "['eval_case_001'], aborting delete_team"
            )

        async def _release(session_id: str, *, force: bool = False) -> None:
            events.append(f"release:{session_id}:{force}")

        async def _stop() -> None:
            events.append("stop")

        monkeypatch.setattr(case_backend_module.Runner, "delete_agent_team", _raise_missing)
        monkeypatch.setattr(case_backend_module.Runner, "release", _release)
        monkeypatch.setattr(case_backend_module.Runner, "stop", _stop)

        backend = LocalExecutionBackend(config=EvaluatorConfig())
        await backend.cleanup("math_team", "eval_case_001")

        assert events == ["delete_attempt", "release:eval_case_001:True", "stop"]

    @pytest.mark.asyncio
    async def test_marks_failed_on_runner_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            TeamSkillTeamFactory,
            "create_team_spec",
            lambda self, **kwargs: _build_spec_from_config(
                self.config,
                team_name="math_team",
                output_dir=kwargs["output_dir"],
            ),
        )
        monkeypatch.setattr(case_backend_module.Runner, "start", lambda: asyncio.sleep(0))
        monkeypatch.setattr(
            case_backend_module.Runner,
            "run_agent_team",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("runner failed")),
        )
        monkeypatch.setattr(case_backend_module.Runner, "stop", lambda: asyncio.sleep(0))

        backend = LocalExecutionBackend(config=EvaluatorConfig())
        result = await backend.execute(
            case={"case_id": "case_001", "input": "x"},
            output_dir=str(tmp_path / "case_001"),
            session_id="eval_case_001",
        )

        assert result.execution_status == "failed"
        assert "runner failed" in result.error


class TestSingleHarnessExecutionBackend:
    def test_shell_only_execution_uses_pipefail(self) -> None:
        rails = case_backend_module._single_harness_rails(None, shell_only=True)

        assert len(rails) == 1
        assert rails[0]._shell_only is True
        assert rails[0]._bash_pipefail is True

    def test_candidate_treatment_rail_is_mounted_only_when_case_requests_it(
        self,
    ) -> None:
        natural = case_backend_module._single_harness_rails(None, shell_only=True)
        treated = case_backend_module._single_harness_rails(
            None,
            shell_only=True,
            controlled_skill_name="enum_contract_verify",
        )

        assert not any(isinstance(rail, case_backend_module.ControlledSkillTreatmentRail) for rail in natural)
        treatment = next(rail for rail in treated if isinstance(rail, case_backend_module.ControlledSkillTreatmentRail))
        assert treatment.skill_name == "enum_contract_verify"

    @pytest.mark.asyncio
    async def test_reports_original_error_when_single_harness_refs_are_ambiguous(
        self,
        tmp_path: Path,
    ) -> None:
        backend = SingleHarnessExecutionBackend(config=EvaluatorConfig(model_config_ref="model.yaml"))

        result = await backend.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(tmp_path / "case_001"),
            session_id="eval_case_001",
            harness_refs={
                "designer": str(tmp_path / "designer_harness"),
                "engineer": str(tmp_path / "engineer_harness"),
            },
        )

        assert result.execution_status == "failed"
        assert "single_harness backend requires exactly one harness ref" in result.error
        assert result.metadata["member_role"] == ""

    @pytest.mark.asyncio
    async def test_mounts_team_skill_rail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)
        harness_dir = tmp_path / "harness"
        harness_dir.mkdir()
        captured: dict[str, Any] = {}

        class _FakeAgent:
            def add_rail(self, rail: Any) -> None:
                captured.setdefault("added_rails", []).append(rail)

            async def load_plugin(self, path: str) -> None:
                captured["loaded_harness_path"] = path

            def find_rails_by_type(self, rail_types: tuple[type, ...]) -> list[Any]:
                return [rail for rail in captured["create_deep_agent_kwargs"]["rails"] if isinstance(rail, rail_types)]

            def strip_rails_by_type(self, _rail_types: tuple[type, ...]) -> int:
                return 0

        def _create_deep_agent(**kwargs: Any) -> _FakeAgent:
            captured["create_deep_agent_kwargs"] = kwargs
            return _FakeAgent()

        async def _start() -> bool:
            captured["started"] = True
            return True

        async def _run_agent(agent: Any, inputs: Any, session: str) -> dict[str, Any]:
            captured["run_agent_inputs"] = inputs
            captured["session"] = session
            return {"ok": True}

        async def _stop() -> None:
            captured["stopped"] = True

        monkeypatch.setattr(case_backend_module, "load_member_optimizer_model", lambda _path: object())
        monkeypatch.setattr(case_backend_module, "create_deep_agent", _create_deep_agent)
        monkeypatch.setattr(case_backend_module.Runner, "start", _start)
        monkeypatch.setattr(case_backend_module.Runner, "run_agent", _run_agent)
        monkeypatch.setattr(case_backend_module.Runner, "stop", _stop)

        backend = SingleHarnessExecutionBackend(config=EvaluatorConfig(model_config_ref="model.yaml"))
        result = await backend.execute(
            case={"case_id": "case_001", "input": "hello"},
            output_dir=str(tmp_path / "case_001"),
            session_id="eval_case_001",
            team_skill_ref_path=str(skill_dir),
            harness_refs={"solver": str(harness_dir)},
        )

        rails = captured["create_deep_agent_kwargs"]["rails"]
        skill_rails = [rail for rail in rails if isinstance(rail, RSISkillUseRail)]
        assert result.execution_status == "passed"
        assert captured["loaded_harness_path"] == str(harness_dir)
        assert captured["run_agent_inputs"] == {"query": "hello"}
        assert captured["session"] == "eval_case_001"
        trajectory_rails = [rail for rail in captured["added_rails"] if rail.__class__.__name__ == "TrajectoryRail"]
        assert len(trajectory_rails) == 1
        assert Path(trajectory_rails[0].trajectory_store._base_dir) == (tmp_path / "case_001" / "tr")
        assert trajectory_rails[0].trajectory_store.role_name == "solver"
        assert "# Team Skill Contract" not in captured["create_deep_agent_kwargs"]["system_prompt"]
        assert "name: math_team" not in captured["create_deep_agent_kwargs"]["system_prompt"]
        assert len(skill_rails) == 1
        assert skill_rails[0].skill_mode == skill_rails[0].SKILL_MODE_ALL
        assert Path(skill_rails[0].skills_dir) == skill_dir.parent
        assert skill_rails[0].enabled_skills == {skill_dir.name}
        assert result.metadata["team_skill"]["ref_path"] == str(skill_dir)
        assert result.metadata["team_skill"]["enabled_skill"] == skill_dir.name
        assert result.metadata["team_skill"]["skill_mode"] == "all"
        assert result.metadata["team_skill"]["skill_md_sha256"]


# ---------------------------------------------------------------------------
# case_backend.build_backend
# ---------------------------------------------------------------------------


class TestCaseBackend:
    def test_build_backend_rejects_unknown_type(self) -> None:
        config = EvaluatorConfig(backend="kubernetes")
        with pytest.raises(ValueError, match="unknown backend type"):
            build_backend(config)


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    @pytest.mark.asyncio
    async def test_collect_aggregates_case_results(self, tmp_path: Path) -> None:
        case_a = tmp_path / "case_results" / "case_001"
        case_b = tmp_path / "case_results" / "case_002"
        case_a.mkdir(parents=True)
        case_b.mkdir(parents=True)
        (case_a / "result.json").write_text(
            json.dumps({"status": "passed", "score": 1.0}),
            encoding="utf-8",
        )
        (case_b / "result.json").write_text(
            json.dumps({"status": "failed", "score": 0.0}),
            encoding="utf-8",
        )

        summary_path = await MetricsCollector().collect(
            str(tmp_path / "case_results"),
            str(tmp_path / "summary.json"),
        )
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))

        assert summary["total_cases"] == 2
        assert summary["passed_cases"] == 1
        assert summary["failed_cases"] == 1
        assert summary["average_score"] == 0.5

    @pytest.mark.asyncio
    async def test_collect_uses_judger_passed_for_pass_fail_counts(self, tmp_path: Path) -> None:
        case_a = tmp_path / "case_results" / "case_001"
        case_b = tmp_path / "case_results" / "case_002"
        case_a.mkdir(parents=True)
        case_b.mkdir(parents=True)
        (case_a / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "score": 0.0,
                    "evaluation": {"passed": False},
                }
            ),
            encoding="utf-8",
        )
        (case_b / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "score": 1.0,
                    "evaluation": {"passed": True},
                }
            ),
            encoding="utf-8",
        )

        summary_path = await MetricsCollector().collect(
            str(tmp_path / "case_results"),
            str(tmp_path / "summary.json"),
        )
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))

        assert summary["total_cases"] == 2
        assert summary["passed_cases"] == 1
        assert summary["failed_cases"] == 1
        assert summary["average_score"] == 0.5

    @pytest.mark.asyncio
    async def test_collect_writes_evaluation_method_from_results(self, tmp_path: Path) -> None:
        case_dir = tmp_path / "case_results" / "case_001"
        case_dir.mkdir(parents=True)
        (case_dir / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "score": 1.0,
                    "evaluation": {"method": "llm_as_judge", "passed": True},
                }
            ),
            encoding="utf-8",
        )

        summary_path = await MetricsCollector().collect(
            str(tmp_path / "case_results"),
            str(tmp_path / "summary.json"),
        )
        summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))

        assert summary["evaluation_method"] == "llm_as_judge"


# ---------------------------------------------------------------------------
# TeamEvaluator
# ---------------------------------------------------------------------------


class TestTeamEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_batch_reuses_completed_cases_for_identical_inputs(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        fake_backend = _FakeBackend(team_name="math_team")
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(backend=fake_backend, judger=ExactMatchJudger())
        kwargs = {
            "cases": [
                {"case_id": "case_001", "input": "求数列通项"},
                {"case_id": "case_002", "input": "证明不等式"},
            ],
            "team_skill_ref_path": str(skill_dir),
            "harness_refs_path": "",
            "output_dir": str(eval_root),
        }

        await evaluator.evaluate_batch(**kwargs)
        assert len(fake_backend.sessions) == 2
        eval_ref_path = await evaluator.evaluate_batch(**kwargs)

        assert len(fake_backend.sessions) == 2
        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert all(case["metadata"]["resumed"] for case in eval_ref["cases"])

    @pytest.mark.asyncio
    async def test_evaluate_batch_runs_cases_and_writes_eval_ref(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        fake_backend = _FakeBackend(team_name="math_team")
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=fake_backend,
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[
                {"case_id": "case_001", "input": "求数列通项"},
                {"case_id": "case_002", "input": "证明不等式"},
            ],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert eval_ref["team_name"] == "math_team"
        assert eval_ref["team_skill_ref_path"] == str(skill_dir)
        assert eval_ref["case_traces_dir"] == eval_ref["case_results_dir"]
        assert len(fake_backend.sessions) == 2
        assert len(fake_backend.cleaned) == 2
        assert len(eval_ref["cases"]) == 2
        for case in eval_ref["cases"]:
            assert Path(case["result_path"]).is_file()
            assert Path(case["trace_path"]).is_file()
            assert Path(case["trace_path"]).parent == Path(case["result_path"]).parent
        summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
        assert summary["total_cases"] == 2

    @pytest.mark.asyncio
    async def test_evaluate_batch_rerun_starts_case_directory_clean(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        case_dir = eval_root / "cases" / "c001_39a59103"
        (case_dir / "artifacts").mkdir(parents=True)
        (case_dir / "judge").mkdir()
        (case_dir / "tr").mkdir()
        (case_dir / "result.json").write_text('{"status":"failed"}', encoding="utf-8")
        (case_dir / "trace.json").write_text('{"status":"failed"}', encoding="utf-8")
        (case_dir / "artifacts" / "stale.txt").write_text("old artifact", encoding="utf-8")
        (case_dir / "judge" / "normalized_trace.json").write_text("old judge", encoding="utf-8")
        (case_dir / "tr" / "trajectory_events.jsonl").write_text("old trace", encoding="utf-8")
        (case_dir / "team.db").write_text("old db", encoding="utf-8")
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=_FakeBackend(team_name="math_team"),
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[{"case_id": "case_001", "input": "求数列通项"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        result_path = Path(eval_ref["cases"][0]["result_path"])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["case_id"] == "case_001"
        assert result["session_id"].startswith("eval_case_001_")
        assert not (case_dir / "artifacts" / "stale.txt").exists()
        assert not (case_dir / "team.db").exists()

    @pytest.mark.asyncio
    async def test_evaluate_batch_shortens_long_case_result_directory(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        long_case_id = (
            "energy_storage_pitch_audience_alignment_005_requires_detailed_board_level_financing_storyline_revision"
        )
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=_FakeBackend(team_name="math_team"),
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[{"case_id": long_case_id, "input": "build pitch deck"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        case_ref = eval_ref["cases"][0]
        result_path = Path(case_ref["result_path"])

        assert case_ref["case_id"] == long_case_id
        assert result_path.is_file()
        assert result_path.parent.name != long_case_id
        assert result_path.parent.name.startswith("c001_")
        assert len(result_path.parent.name) <= 13

    @pytest.mark.asyncio
    async def test_evaluate_batch_uses_short_case_directory_for_regular_case_id(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=_FakeBackend(team_name="math_team"),
            judger=ExactMatchJudger(),
        )
        case_id = "ne_storage_pitch_narrative_001"

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[{"case_id": case_id, "input": "build pitch deck"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(tmp_path / "evaluations"),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        result_path = Path(eval_ref["cases"][0]["result_path"])

        assert eval_ref["cases"][0]["case_id"] == case_id
        assert result_path.parent.name != case_id
        assert result_path.parent.name.startswith("c001_")
        assert len(str(result_path.parent / "tr" / "trajectory_events.jsonl")) < 260

    @pytest.mark.asyncio
    async def test_evaluate_batch_updates_orchestrator_context(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        context_path = tmp_path / "orchestrator_context.yaml"
        context_store = OrchestratorContextStore(str(context_path))
        context_store.save(context_store.create("评测团队"))

        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(backend=_FakeBackend(team_name="math_team"))

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[{"case_id": "case_001", "input": "hello"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
            context_path=str(context_path),
        )

        loaded = context_store.load()
        assert loaded.current.eval_ref_path == eval_ref_path
        assert loaded.history.evaluations[-1].eval_ref_path == eval_ref_path

    @pytest.mark.asyncio
    async def test_evaluate_batch_exact_match_scoring(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=_FakeBackend(team_name="math_team"),
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[
                {
                    "case_id": "case_001",
                    "input": "求函数最值",
                    "reference": {
                        "answer": {
                            "team_name": "math_team",
                            "input": "求函数最值",
                            "answer": "已完成：求函数最值",
                        },
                    },
                },
                {
                    "case_id": "case_002",
                    "input": "证明不等式",
                    "reference": {"answer": "不匹配的答案"},
                },
            ],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        case_001 = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
        case_002 = json.loads(Path(eval_ref["cases"][1]["result_path"]).read_text(encoding="utf-8"))
        assert case_001["evaluation"]["method"] == "exact_match"
        assert case_001["evaluation"]["passed"] is True
        assert case_001["score"] == 1.0
        assert case_002["evaluation"]["passed"] is False
        assert case_002["score"] == 0.0
        summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
        assert summary["average_score"] == 0.5

    @pytest.mark.asyncio
    async def test_evaluate_batch_continues_when_one_case_pipeline_fails(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)
        eval_root = tmp_path / "evaluations"

        class _FlakyBackend:
            def __init__(self) -> None:
                self.cleaned: list[tuple[str, str]] = []

            async def execute(
                self,
                *,
                case: dict[str, Any],
                output_dir: str,
                session_id: str,
                **kwargs: Any,
            ) -> CaseExecutionResult:
                if case.get("case_id") == "case_fail":
                    raise RuntimeError("backend boom")
                case_input = case.get("input") or ""
                return CaseExecutionResult(
                    response={"answer": f"已完成：{case_input}"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("math_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                self.cleaned.append((team_name, session_id))

        flaky_backend = _FlakyBackend()
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(
            backend=flaky_backend,
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[
                {"case_id": "case_fail", "input": "会挂"},
                {
                    "case_id": "case_ok",
                    "input": "正常",
                    "reference": {"answer": {"answer": "已完成：正常"}},
                },
            ],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert len(eval_ref["cases"]) == 2

        failed = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
        passed = json.loads(Path(eval_ref["cases"][1]["result_path"]).read_text(encoding="utf-8"))
        assert failed["status"] == "error"
        assert "backend boom" in failed["error"]
        assert passed["status"] == "passed"
        assert passed["evaluation"]["passed"] is True

        summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
        assert summary["total_cases"] == 2
        assert summary["failed_cases"] == 1
        assert summary["passed_cases"] == 1
        assert len(flaky_backend.cleaned) == 1

    @pytest.mark.asyncio
    async def test_evaluate_batch_retries_transient_connection_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skill_dir = _write_team_skill(tmp_path)

        class _TransientBackend:
            def __init__(self) -> None:
                self.attempts = 0

            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError(
                        "SWE-bench verifier infrastructure failed: RemoteDisconnected while fetching requirements"
                    )
                return CaseExecutionResult(
                    response={"answer": "done"},
                    execution_status="passed",
                    team_spec=_TeamSpecStub("math_team"),
                )

            async def cleanup(self, team_name: str, session_id: str) -> None:
                return None

        async def _no_wait(_delay: float) -> None:
            return None

        monkeypatch.setattr(
            "openjiuwen.rsi.evaluator.team_evaluator.asyncio.sleep",
            _no_wait,
        )
        backend = _TransientBackend()
        evaluator = TeamEvaluator(
            EvaluatorConfig(
                evaluation_method="exact_match",
                transient_case_retry_limit=2,
            )
        )
        evaluator.case_runner = CaseRunner(
            backend=backend,
            judger=ExactMatchJudger(),
        )

        eval_ref_path = await evaluator.evaluate_batch(
            cases=[
                {
                    "case_id": "case_retry",
                    "input": "work",
                    "reference": {"answer": {"answer": "done"}},
                }
            ],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(tmp_path / "evaluations"),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert backend.attempts == 2
        assert eval_ref["cases"][0]["status"] == "passed"
        retry_files = list((Path(eval_ref["case_results_dir"])).glob("*_transient_retries.json"))
        assert len(retry_files) == 1
        assert json.loads(retry_files[0].read_text(encoding="utf-8"))[0]["error"] == (
            "SWE-bench verifier infrastructure failed: RemoteDisconnected while fetching requirements"
        )

    @pytest.mark.asyncio
    async def test_evaluate_loads_cases_from_dataset_artifact(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        dataset_dir = tmp_path / "datasets" / "ds_001"
        dataset_dir.mkdir(parents=True)
        dataset_file = dataset_dir / "cases.json"
        dataset_file.write_text(
            json.dumps(
                {
                    "cases": [
                        {"case_id": "case_001", "input": "a"},
                        {"case_id": "case_002", "input": "b"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        dataset = DatasetArtifact(
            dataset_id="ds_001",
            dataset_dir=str(dataset_dir),
            dataset_files=[str(dataset_file)],
        )
        eval_root = tmp_path / "evaluations"
        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(backend=_FakeBackend(team_name="math_team"))

        eval_ref_path = await evaluator.evaluate(
            dataset=dataset,
            team_skill_ref_path=str(skill_dir),
            harness_refs_path="",
            output_dir=str(eval_root),
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert eval_ref["dataset"]["dataset_id"] == "ds_001"
        assert len(eval_ref["cases"]) == 2
        summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
        assert summary["total_cases"] == 2

    @pytest.mark.asyncio
    async def test_evaluate_batch_loads_harness_refs_from_yaml(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        harness_refs_path = tmp_path / "harness_refs.yaml"
        harness_refs_path.write_text(
            yaml.safe_dump({"math-teacher": str(tmp_path / "harness")}),
            encoding="utf-8",
        )
        captured: dict[str, Any] = {}

        class _CapturingBackend(_FakeBackend):
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                captured["harness_refs"] = kwargs.get("harness_refs")
                return await super().execute(**kwargs)

        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(backend=_CapturingBackend(team_name="math_team"))

        await evaluator.evaluate_batch(
            cases=[{"case_id": "case_001", "input": "x"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path=str(harness_refs_path),
            output_dir=str(tmp_path / "evaluations"),
        )

        assert captured["harness_refs"] == {"math-teacher": str(tmp_path / "harness")}

    @pytest.mark.asyncio
    async def test_evaluate_batch_loads_canonical_harness_refs_yaml(self, tmp_path: Path) -> None:
        skill_dir = _write_team_skill(tmp_path)
        harness_refs_path = tmp_path / "harness_refs.yaml"
        harness_refs_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "harness_refs": {
                        "explainer": str(tmp_path / "harnesses" / "explainer"),
                    },
                    "roles": [
                        {
                            "role": "explainer",
                            "member_name": "explainer",
                            "description": "Explains answers with evidence.",
                            "harness_ref_path": str(tmp_path / "harnesses" / "explainer"),
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        captured: dict[str, Any] = {}

        class _CapturingBackend(_FakeBackend):
            async def execute(self, **kwargs: Any) -> CaseExecutionResult:
                captured["harness_refs"] = kwargs.get("harness_refs")
                return await super().execute(**kwargs)

        evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
        evaluator.case_runner = CaseRunner(backend=_CapturingBackend(team_name="math_team"))

        await evaluator.evaluate_batch(
            cases=[{"case_id": "case_001", "input": "x"}],
            team_skill_ref_path=str(skill_dir),
            harness_refs_path=str(harness_refs_path),
            output_dir=str(tmp_path / "evaluations"),
        )

        assert captured["harness_refs"] == {
            "explainer": str(tmp_path / "harnesses" / "explainer"),
        }


# ---------------------------------------------------------------------------
# TeamEvaluator E2E
# ---------------------------------------------------------------------------


class TestTeamEvaluatorE2E:
    _TEAM_SKILL_DIR = Path(__file__).resolve().parents[4] / "primary-school-tutoring-team_1.0.0"
    _TEAM_NAME = "primary-school-tutoring-team"

    _OPENAI_BASE_URL = os.environ.get(
        "EVAL_E2E_OPENAI_BASE_URL",
        "https://coding.dashscope.aliyuncs.com/v1",
    )
    _OPENAI_API_KEY = os.environ.get("EVAL_E2E_OPENAI_API_KEY", "")
    _MODEL_NAME = os.environ.get("EVAL_E2E_MODEL_NAME", "glm-5")
    _EVALUATE_TIMEOUT = float(os.environ.get("EVAL_E2E_TIMEOUT", "600"))

    @classmethod
    def _evaluator_config(cls) -> EvaluatorConfig:
        model_config_path = Path(os.environ.get("EVAL_E2E_MODEL_CONFIG_REF", "")).expanduser()
        if model_config_path.is_file():
            return EvaluatorConfig(model_config_ref=str(model_config_path))
        raise RuntimeError("EVAL_E2E_MODEL_CONFIG_REF must point to an evaluator model config file")

    @pytest.fixture
    def _isolated_checkpointer(self):
        original = CheckpointerFactory.get_checkpointer()
        checkpointer = InMemoryCheckpointer()
        CheckpointerFactory.set_default_checkpointer(checkpointer)
        try:
            yield checkpointer
        finally:
            CheckpointerFactory.set_default_checkpointer(original)

    @pytest.fixture
    def _isolated_team_resources(self):
        cleanup_shared_resources()
        try:
            yield
        finally:
            cleanup_shared_resources()

    @pytest.mark.level0
    @pytest.mark.asyncio
    async def test_evaluate_batch_creates_team_persists_trajectory_and_case_refs(
        self,
        tmp_path: Path,
        _isolated_checkpointer,
        _isolated_team_resources,
    ) -> None:
        """端到端：真实创建并运行 AgentTeam，校验 eval_ref 与轨迹落盘。"""
        if not os.environ.get("EVAL_E2E_MODEL_CONFIG_REF", "").strip():
            pytest.skip("set EVAL_E2E_MODEL_CONFIG_REF to run level0 e2e")
        assert (self._TEAM_SKILL_DIR / "SKILL.md").is_file(), (
            f"前置条件失败：{self._TEAM_SKILL_DIR / 'SKILL.md'} 不存在"
        )

        eval_root = tmp_path / "evaluations"
        case_id = f"case_{uuid.uuid4().hex[:8]}"
        evaluator = TeamEvaluator(self._evaluator_config())

        eval_ref_path = await asyncio.wait_for(
            evaluator.evaluate_batch(
                cases=[
                    {
                        "case_id": case_id,
                        "input": {
                            "query": (
                                "你是一个专业的小学课程辅导团队，请讲解一个小学数学加减法知识点，并给出一道对应练习题。"
                            )
                        },
                        "reference": {
                            "required_behaviors": [
                                {
                                    "id": "complete_task",
                                    "description": "必须完成用户要求的核心任务，而不是只给建议",
                                    "weight": 0.25,
                                    "rubric": {
                                        "1.0": "完整完成任务，结果可验证",
                                        "0.5": "部分完成，但遗漏关键步骤或产物",
                                        "0.0": "没有完成任务或产物不可用",
                                    },
                                },
                                {
                                    "id": "spawn_member_correctly",
                                    "description": "需要合理创建成员，spawn_member",
                                    "weight": 0.25,
                                    "rubric": {
                                        "1.0": "完整完成任务，结果可验证",
                                        "0.5": "部分完成，但遗漏关键步骤或产物",
                                        "0.0": "没有完成任务或产物不可用",
                                    },
                                },
                                {
                                    "id": "artifacts_presentable",
                                    "description": "提供至少2个 .md文件，分别包括知识点和练习题目",
                                    "weight": 0.5,
                                    "rubric": {
                                        "1.0": "完整完成任务，包括两个.md文件，结果可验证",
                                        "0.5": "部分完成，只有一个产物.md文件",
                                        "0.0": "没有完成任务或产物不可用",
                                    },
                                },
                            ],
                        },
                    }
                ],
                team_skill_ref_path=str(self._TEAM_SKILL_DIR),
                harness_refs_path="",
                output_dir=str(eval_root),
            ),
            timeout=self._EVALUATE_TIMEOUT,
        )

        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert Path(eval_ref_path).parent == eval_root.resolve()
        assert eval_ref["team_name"] == self._TEAM_NAME
        assert eval_ref["team_skill_ref_path"] == str(self._TEAM_SKILL_DIR)
        assert eval_ref["case_traces_dir"] == eval_ref["case_results_dir"]
        assert len(eval_ref["cases"]) == 1

        case_ref = eval_ref["cases"][0]
        assert case_ref["case_id"] == case_id
        result_path = Path(case_ref["result_path"])
        trace_path = Path(case_ref["trace_path"])
        assert result_path.is_file()
        assert trace_path.is_file()
        assert result_path.parent == trace_path.parent
        assert case_ref["metadata"]["team_name"] == self._TEAM_NAME

        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        result_path = Path(case_ref["result_path"])
        expected_trajectory_dir = str((result_path.parent / "tr").resolve())
        assert trace["trajectory_dir"] == expected_trajectory_dir
        trajectory_dir = result_path.parent / "tr"
        trajectory_files = list(trajectory_dir.glob("team_leader*.jsonl"))
        assert trajectory_files, f"expected team_leader trajectory jsonl under {trajectory_dir}, got none"

        summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
        assert summary["total_cases"] == 1


os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")
