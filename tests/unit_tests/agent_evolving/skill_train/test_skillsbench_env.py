# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillsBench env: task.md loading and verifier reward scoring."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.envs.skillsbench.adapter import SkillsBenchAdapter
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.dataloader import (
    DEFAULT_SPLIT_RATIO,
    parse_task_dir,
)
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator import (
    _counts_from_ctrf,
    _counts_from_pytest_text,
    _prepare_wsl_tests,
    _scores_from_ctrf,
    container_agent_sync,
    copy_environment_inputs,
    dockerfile_workdir,
    drop_external_dockerfile_syntax,
    is_leaky_environment_entry,
    map_root_paths,
    resolve_verifier_mode,
    run_task_verifier_docker,
    run_task_verifier_local,
    run_task_verifier_wsl,
    scores_from_reward_file,
    scores_from_reward_text,
    verifier_env_from_task_md,
    windows_to_wsl_path,
)
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.rollout import (
    SkillsBenchItemConfig,
    process_one,
    skill_name_from_markdown,
)


def test_reward_text_maps_to_hard_soft() -> None:
    passed = scores_from_reward_text("1\n")
    failed = scores_from_reward_text("0\n")
    assert passed["hard"] == 1 and passed["soft"] == 1.0
    assert failed["hard"] == 0 and failed["soft"] == 0.0


def test_missing_reward_file_scores_zero(tmp_path: Path) -> None:
    scores = scores_from_reward_file(tmp_path / "reward.txt")
    assert scores["hard"] == 0
    assert scores["fail_reason"] == "reward_missing"


def test_map_root_paths_rewrites_literals(tmp_path: Path) -> None:
    mapped = map_root_paths('OUTPUT_FILE = "/root/offer_letter_filled.docx"', tmp_path)
    assert mapped == f'OUTPUT_FILE = "{tmp_path.resolve().as_posix()}/offer_letter_filled.docx"'


def test_resolve_verifier_mode_accepts_local_docker_and_wsl() -> None:
    assert resolve_verifier_mode("local") == "local"
    assert resolve_verifier_mode("DOCKER") == "docker"
    assert resolve_verifier_mode("WSL") == "wsl"


@pytest.mark.skipif(sys.platform != "win32", reason="drive-letter conversion requires a Windows host")
def test_windows_to_wsl_path_converts_drive() -> None:
    assert windows_to_wsl_path(r"E:\jiuwen\work").startswith("/mnt/e/")
    assert windows_to_wsl_path("/mnt/e/already") == "/mnt/e/already"


def test_copy_environment_inputs_skips_dockerfile_and_skills(tmp_path: Path) -> None:
    task = tmp_path / "task"
    env = task / "environment"
    (env / "skills" / "docx").mkdir(parents=True)
    (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (env / "employee_data.json").write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    copied = copy_environment_inputs(task, work)
    assert copied == ["employee_data.json"]
    assert (work / "employee_data.json").is_file()
    assert not (work / "Dockerfile").exists()
    assert not (work / "skills").exists()


def test_copy_environment_inputs_drops_answer_keys(tmp_path: Path) -> None:
    """``environment/groundtruth`` must never reach the agent cwd."""
    task = tmp_path / "task"
    env = task / "environment"
    (env / "groundtruth").mkdir(parents=True)
    (env / "groundtruth" / "answers.txt").write_text("F9=-0.08\n", encoding="utf-8")
    (env / "groundtruth" / "create_incomplete_xlsx.py").write_text("X = 1\n", encoding="utf-8")
    (env / "input.xlsx").write_bytes(b"PK\x03\x04")
    (env / "solution.md").write_text("answer is 42\n", encoding="utf-8")
    (env / "data" / "gold").mkdir(parents=True)
    (env / "data" / "gold" / "expected.json").write_text("{}", encoding="utf-8")
    (env / "data" / "raw.csv").write_text("a,b\n", encoding="utf-8")
    work = tmp_path / "work"

    copied = copy_environment_inputs(task, work)

    assert copied == ["data", "input.xlsx"]
    assert (work / "input.xlsx").is_file()
    assert (work / "data" / "raw.csv").is_file()
    assert not (work / "groundtruth").exists()
    assert not (work / "solution.md").exists()
    assert not (work / "data" / "gold").exists()


def test_is_leaky_environment_entry_names() -> None:
    for name in ("groundtruth", "answers.txt", "expected_values.json", "solution.md", "Dockerfile", "skills"):
        assert is_leaky_environment_entry(name), name
    for name in ("input.xlsx", "raw.csv", "task.md", "data"):
        assert not is_leaky_environment_entry(name), name


def test_is_leaky_environment_entry_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SKILLSBENCH_ENV_KEEP", "gold")
    monkeypatch.setenv("SKILLSBENCH_ENV_SKIP", "secret")
    assert not is_leaky_environment_entry("gold")
    assert is_leaky_environment_entry("secret")
    assert is_leaky_environment_entry("groundtruth")


def test_verifier_logs_parent_defaults_outside_predictions(tmp_path: Path) -> None:
    from openjiuwen.agent_evolving.skill_train.envs.skillsbench.rollout import (
        SkillsBenchItemConfig,
        verifier_logs_parent,
    )

    cfg = SkillsBenchItemConfig(out_root=str(tmp_path / "rollout"), skill_content="x")
    logs = verifier_logs_parent(cfg, "tiny-task")
    assert logs == tmp_path / "rollout" / "verifier_logs" / "tiny-task"
    assert "predictions" not in logs.parts

    cfg.verifier_log_root = str(tmp_path / "logs" / "verifier" / "iter_001")
    assert verifier_logs_parent(cfg, "tiny-task") == tmp_path / "logs" / "verifier" / "iter_001" / "tiny-task"



def test_copy_environment_inputs_preserves_agent_files_when_not_overwriting(tmp_path: Path) -> None:
    task = tmp_path / "task"
    env = task / "environment" / "workspace"
    env.mkdir(parents=True)
    (env / "seed.py").write_text("SEED = 1\n", encoding="utf-8")
    work = tmp_path / "work"
    (work / "workspace").mkdir(parents=True)
    (work / "workspace" / "seed.py").write_text("SEED = stale\n", encoding="utf-8")
    (work / "workspace" / "agent_solution.py").write_text("OK = 1\n", encoding="utf-8")
    copy_environment_inputs(task, work, overwrite=False)
    assert (work / "workspace" / "agent_solution.py").read_text(encoding="utf-8") == "OK = 1\n"
    assert (work / "workspace" / "seed.py").read_text(encoding="utf-8") == "SEED = stale\n"


def test_local_verifier_runs_rewritten_pytest(tmp_path: Path) -> None:
    task = tmp_path / "tiny-task"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    (task / "verifier").mkdir(parents=True)
    work.mkdir()
    (work / "answer.txt").write_text("ok\n", encoding="utf-8")
    (task / "verifier" / "test_outputs.py").write_text(
        "from pathlib import Path\n"
        "def test_answer_exists():\n"
        "    assert Path('/root/answer.txt').read_text(encoding='utf-8').strip() == 'ok'\n",
        encoding="utf-8",
    )
    scores = run_task_verifier_local(
        task_dir=task,
        agent_workdir=work,
        logs_dir=logs,
        timeout_sec=60,
    )
    assert scores["verifier_mode"] == "local"
    assert scores["hard"] == 1
    assert scores["soft"] == 1.0
    assert (logs / "verifier" / "reward.txt").read_text(encoding="utf-8").strip() == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="WSL verifier requires a Windows host (wsl.exe)")
def test_wsl_verifier_invokes_wsl_bash(tmp_path: Path, monkeypatch) -> None:
    task = tmp_path / "tiny-task"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    (task / "verifier").mkdir(parents=True)
    work.mkdir()
    (work / "answer.txt").write_text("ok\n", encoding="utf-8")
    (task / "verifier" / "test_outputs.py").write_text(
        "from pathlib import Path\n"
        "def test_answer_exists():\n"
        "    assert Path('/root/answer.txt').read_text(encoding='utf-8').strip() == 'ok'\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_runner(cmd, timeout=None, cwd=None, env=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="passed\n", stderr="")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._wsl_bin",
        lambda: "wsl.exe",
    )
    scores = run_task_verifier_wsl(
        task_dir=task,
        agent_workdir=work,
        logs_dir=logs,
        timeout_sec=60,
        runner=fake_runner,
    )
    assert scores["verifier_mode"] == "wsl"
    assert scores["hard"] == 1
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "wsl.exe"
    assert "-lc" in cmd
    script = cmd[cmd.index("-lc") + 1]
    assert '"$PY" -m pytest' in script
    assert "PYTHONDONTWRITEBYTECODE=1" in script
    assert windows_to_wsl_path(work) in script


def test_prepare_wsl_tests_copies_helper_modules_and_data(tmp_path: Path) -> None:
    """Sibling verifier helpers (oracle_helpers, JSON) must be staged with tests."""
    verifier = tmp_path / "verifier"
    staging = tmp_path / "staging"
    verifier.mkdir()
    (verifier / "test_outputs.py").write_text(
        "from oracle_helpers import answer\n"
        "from pathlib import Path\n"
        "def test_ok():\n"
        "    assert answer() == Path('/root/x').name\n",
        encoding="utf-8",
    )
    (verifier / "oracle_helpers.py").write_text(
        "def answer():\n    return 'x'\n",
        encoding="utf-8",
    )
    (verifier / "verification_params.json").write_text('{"a": 1}\n', encoding="utf-8")
    (verifier / "src").mkdir()
    (verifier / "src" / "lib.py").write_text("V = 1\n", encoding="utf-8")
    (verifier / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    tests = _prepare_wsl_tests(verifier, "/mnt/e/work", staging)
    assert [p.name for p in tests] == ["test_outputs.py"]
    assert (staging / "oracle_helpers.py").is_file()
    assert (staging / "verification_params.json").is_file()
    assert (staging / "src" / "lib.py").is_file()
    assert not (staging / "test.sh").exists()
    staged_test = (staging / "test_outputs.py").read_text(encoding="utf-8")
    assert "/mnt/e/work/x" in staged_test
    assert "/root/x" not in staged_test


@pytest.mark.skipif(sys.platform != "win32", reason="WSL verifier requires a Windows host (wsl.exe)")
def test_wsl_verifier_keeps_reward_when_cleanup_would_raise(tmp_path: Path, monkeypatch) -> None:
    """WinError 145 on ``__pycache__`` must not replace a reward of 1."""
    task = tmp_path / "tiny-task"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    (task / "verifier").mkdir(parents=True)
    work.mkdir()
    (work / "answer.txt").write_text("ok\n", encoding="utf-8")
    (task / "verifier" / "test_outputs.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )

    def fake_runner(cmd, timeout=None, cwd=None, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="passed\n", stderr="")

    def raising_rmtree(path, ignore_errors=False, **kwargs):
        if not ignore_errors:
            raise OSError(145, "目录不是空的")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._wsl_bin",
        lambda: "wsl.exe",
    )
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator.shutil.rmtree",
        raising_rmtree,
    )
    scores = run_task_verifier_wsl(
        task_dir=task,
        agent_workdir=work,
        logs_dir=logs,
        timeout_sec=60,
        runner=fake_runner,
    )
    assert scores["hard"] == 1
    assert scores["reward"] == "1"
    assert scores["fail_reason"] == ""


def test_local_verifier_imports_module_from_workdir_root(tmp_path: Path) -> None:
    """Agents may write modules at workdir root while tests path ``/root/workspace``."""
    task = tmp_path / "tfidf-like"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    (task / "verifier").mkdir(parents=True)
    (task / "environment" / "workspace").mkdir(parents=True)
    (task / "environment" / "workspace" / "seed_mod.py").write_text("X = 1\n", encoding="utf-8")
    (work / "workspace").mkdir(parents=True)
    (work / "workspace" / "seed_mod.py").write_text("X = 1\n", encoding="utf-8")
    (work / "solution_mod.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    (task / "verifier" / "test_outputs.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '/root/workspace')\n"
        "def test_import_solution():\n"
        "    from solution_mod import ok\n"
        "    assert ok() is True\n",
        encoding="utf-8",
    )
    scores = run_task_verifier_local(
        task_dir=task,
        agent_workdir=work,
        logs_dir=logs,
        timeout_sec=60,
    )
    assert scores["hard"] == 1, scores.get("verifier_output", scores.get("fail_reason"))


def test_parse_task_dir_reads_frontmatter(tmp_path: Path) -> None:
    task = tmp_path / "offer-letter-generator"
    (task / "environment" / "skills" / "docx-fill").mkdir(parents=True)
    (task / "verifier").mkdir()
    (task / "environment" / "skills" / "docx-fill" / "SKILL.md").write_text(
        "---\nname: docx-fill\n---\nfill the template\n",
        encoding="utf-8",
    )
    (task / "verifier" / "test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n", encoding="utf-8")
    (task / "task.md").write_text(
        "---\nmetadata:\n  task_type:\n  - generation\nagent:\n  timeout_sec: 120\n"
        "verifier:\n  timeout_sec: 30\n---\n\nWrite the offer letter.\n",
        encoding="utf-8",
    )
    item = parse_task_dir(task)
    assert item["id"] == "offer-letter-generator"
    assert item["question"] == "Write the offer letter."
    assert item["task_type"] == "generation"
    assert item["timeout_sec"] == 120
    assert item["verifier_timeout_sec"] == 30
    assert item["skills"][0]["name"] == "docx-fill"
    assert item["verifier_script"].endswith("test.sh")


def test_adapter_defaults_split_ratio() -> None:
    adapter = SkillsBenchAdapter(data_path="tasks")
    assert adapter.dataloader.split_ratio == DEFAULT_SPLIT_RATIO == "7:1:2"
    assert adapter.dataloader.split_mode == "ratio"


def test_container_agent_sync_copies_image_workdir() -> None:
    command = container_agent_sync("FROM python:3.12-slim\nWORKDIR /app\n")
    assert command.startswith("mkdir -p /logs/verifier &&")
    # BenchFlow aliased the verifier mount onto /tests; legacy verifiers need it.
    assert "[ -e /tests ] || ln -s /verifier /tests" in command
    assert "cp -a /agent_out/. /root/" in command
    assert "cp -a /agent_out/. /app/" in command
    assert dockerfile_workdir("WORKDIR /root\nWORKDIR /app\n") == "/app"
    root_only = container_agent_sync("FROM scratch\n")
    assert root_only.count("cp -a /agent_out/. /root/") == 1
    assert "/app/" not in root_only
    assert "[ -e /tests ] || ln -s /verifier /tests" in root_only


def test_verifier_env_from_task_md_reads_frontmatter(tmp_path: Path) -> None:
    task = tmp_path / "fix-build-agentops"
    task.mkdir()
    (task / "task.md").write_text(
        "---\nverifier:\n  timeout_sec: 240.0\n  env:\n"
        "    REPO_ID: AgentOps-AI/agentops\n    attempts: 2\n---\n\nFix the build.\n",
        encoding="utf-8",
    )
    assert verifier_env_from_task_md(task) == {
        "REPO_ID": "AgentOps-AI/agentops",
        "attempts": "2",
    }


def test_verifier_env_from_task_md_handles_missing_or_plain_task_md(tmp_path: Path) -> None:
    assert verifier_env_from_task_md(tmp_path / "missing") == {}
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "task.md").write_text("no frontmatter here\n", encoding="utf-8")
    assert verifier_env_from_task_md(plain) == {}
    no_env = tmp_path / "no-env"
    no_env.mkdir()
    (no_env / "task.md").write_text("---\nverifier:\n  timeout_sec: 30\n---\n", encoding="utf-8")
    assert verifier_env_from_task_md(no_env) == {}


def test_verifier_env_from_task_md_strips_bom(tmp_path: Path) -> None:
    bom = tmp_path / "bom"
    bom.mkdir()
    (bom / "task.md").write_bytes(b"\xef\xbb\xbf---\nverifier:\n  env:\n    REPO_ID: x/y\n---\n")
    assert verifier_env_from_task_md(bom) == {"REPO_ID": "x/y"}


def test_drop_external_dockerfile_syntax_keeps_features_that_need_it() -> None:
    pinned = "# syntax=docker/dockerfile:1.4\nFROM python:3.12-slim\n"
    assert drop_external_dockerfile_syntax(pinned) == "FROM python:3.12-slim\n"
    heredoc = "# syntax=docker/dockerfile:1.4\nRUN <<EOF\necho hi\nEOF\n"
    assert drop_external_dockerfile_syntax(heredoc) is None
    assert drop_external_dockerfile_syntax("FROM scratch\n") is None


def test_docker_build_uses_dockerfile_without_frontend_pin(tmp_path: Path, monkeypatch) -> None:
    task = tmp_path / "azure-bgp-oscillation-route-leak"
    env = task / "environment"
    env.mkdir(parents=True)
    (env / "Dockerfile").write_text(
        "# syntax=docker/dockerfile:1.4\nFROM python:3.12-slim\n",
        encoding="utf-8",
    )
    (task / "verifier").mkdir()
    (task / "verifier" / "test.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    seen: dict[str, str] = {}

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            dockerfile = Path(cmd[cmd.index("-f") + 1])
            seen["text"] = dockerfile.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, "", "registry timeout")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=tmp_path / "logs",
        runner=runner,
    )
    assert seen["text"] == "FROM python:3.12-slim\n"
    assert scores["fail_reason"] == "image_build_failed"
    assert "registry timeout" in (tmp_path / "logs" / "verifier" / "docker_build.txt").read_text(encoding="utf-8")


def _docker_task(tmp_path: Path) -> Path:
    task = tmp_path / "azure-bgp-oscillation-route-leak"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\nWORKDIR /app\n", encoding="utf-8")
    (task / "verifier").mkdir()
    (task / "verifier" / "test.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    return task


def test_docker_timeout_keeps_written_reward(tmp_path: Path, monkeypatch) -> None:
    task = _docker_task(tmp_path)
    logs = tmp_path / "logs"

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        reward = logs / "verifier" / "reward.txt"
        reward.parent.mkdir(parents=True, exist_ok=True)
        reward.write_text("0\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, timeout or 1, output="", stderr="slow apt")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=logs,
        runner=runner,
    )
    assert scores["hard"] == 0
    assert scores["reward"] == "0"
    assert scores["fail_reason"] == "verifier_failed"
    assert "slow apt" in scores["verifier_output"]


def test_docker_timeout_uses_passing_ctrf_when_reward_is_late(tmp_path: Path, monkeypatch) -> None:
    task = _docker_task(tmp_path)
    logs = tmp_path / "logs"

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        report = logs / "verifier" / "ctrf.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps({"results": {"summary": {"tests": 4, "passed": 4, "failed": 0}}}),
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=logs,
        timeout_sec=240,
        runner=runner,
    )
    assert scores["hard"] == 1
    assert scores["reward"] == "1"
    assert scores["fail_reason"] == ""


def test_docker_timeout_without_reward(tmp_path: Path, monkeypatch) -> None:
    task = _docker_task(tmp_path)

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=tmp_path / "logs",
        runner=runner,
    )
    assert scores["fail_reason"] == "verifier_timeout"
    assert scores["hard"] == 0


def test_docker_run_injects_verifier_env_and_tests_alias(tmp_path: Path, monkeypatch) -> None:
    task = _docker_task(tmp_path)
    (task / "task.md").write_text(
        "---\nverifier:\n  env:\n    REPO_ID: AgentOps-AI/agentops\n---\n",
        encoding="utf-8",
    )
    logs = tmp_path / "logs"
    runs: list[list[str]] = []

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        runs.append(list(cmd))
        (logs / "verifier").mkdir(parents=True, exist_ok=True)
        (logs / "verifier" / "reward.txt").write_text("1\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=logs,
        runner=runner,
    )
    assert scores["hard"] == 1
    (run_cmd,) = runs
    assert run_cmd[:3] == ["docker", "run", "--rm"]
    # verifier.env pairs ride in as -e flags right after --rm.
    assert run_cmd[3] == "-e"
    assert run_cmd[4] == "REPO_ID=AgentOps-AI/agentops"
    # Legacy SkillsBench verifiers resolve /tests paths against the verifier mount.
    inner = run_cmd[-1]
    assert "[ -e /tests ] || ln -s /verifier /tests" in inner
    assert "bash /verifier/test.sh" in inner


def test_process_one_records_conversation_when_scoring_fails(tmp_path: Path, monkeypatch) -> None:
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    item = {"id": "azure-bgp", "question": "detect leaks", "task_dir": str(task), "skills": []}
    cfg = SkillsBenchItemConfig(
        out_root=str(tmp_path / "out"),
        skill_content="---\nname: azure-bgp\n---\nbody\n",
    )
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.rollout.is_target_exec_backend",
        lambda: False,
    )

    def boom(**kwargs):
        raise RuntimeError("docker timed out after 240 seconds")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.rollout.run_task_verifier",
        boom,
    )
    row = process_one(item, cfg)
    assert "docker timed out" in row["fail_reason"]
    conversation = tmp_path / "out" / "predictions" / "azure-bgp" / "conversation.json"
    assert "docker timed out" in conversation.read_text(encoding="utf-8")


def test_skill_name_from_markdown() -> None:
    assert skill_name_from_markdown("---\nname: docx-fill\n---\nbody\n") == "docx-fill"
    assert skill_name_from_markdown("no frontmatter") == ""


def test_local_verifier_soft_is_passed_over_total(tmp_path: Path) -> None:
    task = tmp_path / "three-tests"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    (task / "verifier").mkdir(parents=True)
    work.mkdir()
    (task / "verifier" / "test_outputs.py").write_text(
        "def test_a():\n    assert True\n"
        "def test_b():\n    assert True\n"
        "def test_c():\n    assert False\n",
        encoding="utf-8",
    )
    scores = run_task_verifier_local(
        task_dir=task,
        agent_workdir=work,
        logs_dir=logs,
        timeout_sec=120,
    )
    assert scores["hard"] == 0
    assert scores["soft"] == round(2 / 3, 4)
    assert scores["tests_passed"] == 2
    assert scores["tests_total"] == 3
    assert scores["soft_source"] == "pytest"


def test_pytest_text_counts_rejects_partial_output() -> None:
    """A log that never reached pytest's closing summary line must not score."""
    partial = "test_outputs.py::test_a PASSED [ 50%]\ntest_outputs.py::test_b FAILED [100%]\n"
    assert _counts_from_pytest_text(partial) is None
    assert _counts_from_pytest_text("") is None


def test_pytest_text_counts_dedupes_parametrize_ids(tmp_path: Path) -> None:
    log = (
        "PASSED test_outputs.py::test_cell[F8-4639-FY2019 SpaceOps]\n"
        "FAILED test_outputs.py::test_cell[B8-7444.4-Science Avg Budget] - AssertionError\n"
        "PASSED test_outputs.py::test_cell[F8-4639-FY2019 SpaceOps]\n"
        "SKIPPED [2] test_outputs.py:7: needs openpyxl\n"
        "============ 1 failed, 1 passed in 0.49s ============\n"
    )
    assert _counts_from_pytest_text(log) == (1, 2)


def test_ctrf_summary_sets_soft_fraction(tmp_path: Path) -> None:
    report = tmp_path / "ctrf.json"
    report.write_text(
        json.dumps({"results": {"summary": {"tests": 8, "passed": 7, "failed": 1}}}),
        encoding="utf-8",
    )
    assert _counts_from_ctrf(report) == (7, 8)
    scores = _scores_from_ctrf(report)
    assert scores is not None
    assert scores["hard"] == 0
    assert scores["soft"] == 0.875
    assert scores["tests_total"] == 8


def test_docker_soft_from_ctrf_when_reward_fails(tmp_path: Path, monkeypatch) -> None:
    task = _docker_task(tmp_path)
    logs = tmp_path / "logs"

    def runner(cmd, timeout=None, cwd=None, env=None):
        if "build" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        (logs / "verifier").mkdir(parents=True, exist_ok=True)
        (logs / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")
        (logs / "verifier" / "ctrf.json").write_text(
            json.dumps({"results": {"summary": {"tests": 4, "passed": 3, "failed": 1}}}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator._docker_bin",
        lambda: "docker",
    )
    scores = run_task_verifier_docker(
        task_dir=task,
        agent_workdir=tmp_path / "work",
        logs_dir=logs,
        runner=runner,
    )
    assert scores["hard"] == 0
    assert scores["soft"] == 0.75
    assert scores["tests_passed"] == 3
    assert scores["tests_total"] == 4
    assert scores["soft_source"] == "ctrf"
