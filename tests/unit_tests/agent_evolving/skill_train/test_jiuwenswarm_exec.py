# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the jiuwenswarm CLI exec harness (no real CLI / Gateway)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.skill_train import jiuwenswarm_exec as jx
from openjiuwen.agent_evolving.skill_train.model_compat import (
    get_target_backend,
    is_target_exec_backend,
    set_target_backend,
)


def _frame(event: str, **payload) -> str:
    return json.dumps({"type": "event", "event": event, "payload": payload})


def _stream(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    set_target_backend("openai_chat")


@pytest.fixture
def clean_env(monkeypatch):
    for key in (
        "JIUWENSWARM_CLI_PATH",
        "JIUWENSWARM_GATEWAY_URL",
        "JIUWENSWARM_CHAT_MODE",
        "JIUWENSWARM_INSTANCE_NAME",
        "EXEC_EMPTY_RESPONSE_RETRIES",
        "TARGET_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)


# ── model_compat ─────────────────────────────────────────────────────────────


class TestTargetBackend:
    def test_default_is_chat(self, clean_env):
        set_target_backend(None)
        assert get_target_backend() == "openai_chat"
        assert not is_target_exec_backend()

    def test_exec_backend_roundtrip_and_env_mirror(self, clean_env, monkeypatch):
        name = set_target_backend("JiuwenSwarm_CLI_Exec")
        assert name == "jiuwenswarm_cli_exec"
        assert is_target_exec_backend()
        import os

        assert os.environ["TARGET_BACKEND"] == "jiuwenswarm_cli_exec"

    def test_unknown_backend_rejected(self, clean_env):
        with pytest.raises(ValueError, match="unsupported target backend"):
            set_target_backend("claude_code_exec")


# ── config ───────────────────────────────────────────────────────────────────


class TestExecConfig:
    def test_defaults(self, clean_env):
        cfg = jx.get_jiuwenswarm_exec_config()
        assert cfg.cli_path == "jiuwenswarm"
        assert cfg.chat_mode == "code.normal"
        assert cfg.gateway_url == ""
        assert cfg.empty_response_retries == 1

    def test_configure_persists_to_env(self, clean_env):
        cfg = jx.configure_jiuwenswarm_exec(
            cli_path="C:/x/jiuwenswarm.exe",
            gateway_url="ws://127.0.0.1:19001/tui",
            chat_mode="code.plan",
            empty_response_retries=0,
        )
        assert cfg.cli_path == "C:/x/jiuwenswarm.exe"
        assert cfg.gateway_url == "ws://127.0.0.1:19001/tui"
        assert cfg.chat_mode == "code.plan"
        assert cfg.empty_response_retries == 0
        # empty string clears
        cfg2 = jx.configure_jiuwenswarm_exec(gateway_url="")
        assert cfg2.gateway_url == ""
        assert cfg2.cli_path == "C:/x/jiuwenswarm.exe"


# ── workspace ────────────────────────────────────────────────────────────────


class TestWorkspace:
    def test_render_skill_md_has_frontmatter(self):
        text = jx.render_skill_md("# Body\nrule 1")
        assert text.startswith('---\nname: "reflact-target"')
        assert "# Body\nrule 1" in text
        assert jx.render_skill_md("").rstrip().endswith("for this task.")

    def test_prepare_workspace_layout(self, tmp_path):
        img = tmp_path / "page.png"
        img.write_bytes(b"\x89PNG")
        corpus = tmp_path / "corpus"
        (corpus / "sub").mkdir(parents=True)
        (corpus / "sub" / "a.txt").write_text("hello", encoding="utf-8")
        work = tmp_path / "ws"
        (work / "stale").mkdir(parents=True)  # must be wiped

        skill_path, task_path = jx.prepare_workspace(
            work_dir=str(work),
            skill_md="---\nname: x\n---\nbody",
            task_text="# Task\nQ?",
            images=[str(img)],
            extra_files={"notes/hint.md": "hint"},
            link_dirs=[(str(corpus), "docs/corpus")],
        )
        assert not (work / "stale").exists()
        assert Path(skill_path) == work / ".agents" / "skills" / "reflact-target" / "SKILL.md"
        assert Path(task_path).read_text(encoding="utf-8") == "# Task\nQ?"
        assert (work / "attachments" / "01_page.png").read_bytes() == b"\x89PNG"
        attachments = (work / "ATTACHMENTS.md").read_text(encoding="utf-8")
        assert "attachments/01_page.png" in attachments
        assert (work / "notes" / "hint.md").read_text(encoding="utf-8") == "hint"
        assert (work / "docs" / "corpus" / "sub" / "a.txt").read_text(encoding="utf-8") == "hello"

    def test_prepare_workspace_missing_image(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            jx.prepare_workspace(work_dir=str(tmp_path / "ws"), skill_md="s", images=[str(tmp_path / "nope.png")])

    def test_exec_work_dir_sanitizes_id(self, tmp_path):
        path = jx.exec_work_dir_for(str(tmp_path), "q:1/2")
        assert path.endswith(str(Path("predictions") / "q_1_2" / "jiuwenswarm_exec"))


# ── event parsing ────────────────────────────────────────────────────────────


_RAW = _stream(
    "info: connecting to gateway",  # non-JSON noise
    _frame("chat.reasoning", content="Let me read the skill first."),
    _frame("chat.tool_call", tool_call={"name": "read_file", "arguments": {"path": "task.md"}}),
    _frame("chat.tool_result", tool_result={"name": "read_file", "result": "# Task\nQ?", "success": True}),
    _frame("chat.tool_call", tool_name="bash", arguments=json.dumps({"command": "ls"})),
    _frame("chat.tool_result", tool_name="bash", result="x" * 400, status="error"),
    _frame("chat.delta", content="The answer is "),
    _frame("chat.delta", content="<answer>Paris</answer>"),
    _frame("chat.final", content="The answer is <answer>Paris</answer>", event_type="chat.final"),
)


class TestEventParsing:
    def test_iter_events_skips_noise(self):
        events = list(jx.iter_jsonl_events(_RAW))
        assert len(events) == 8
        assert all(frame["type"] == "event" for frame in events)

    def test_extract_final_prefers_final_frame(self):
        text, err = jx.extract_final_response(jx.iter_jsonl_events(_RAW))
        assert text == "The answer is <answer>Paris</answer>"
        assert err == ""

    def test_extract_final_falls_back_to_deltas(self):
        raw = _stream(_frame("chat.delta", content="a"), _frame("chat.delta", content="b"))
        text, _ = jx.extract_final_response(jx.iter_jsonl_events(raw))
        assert text == "ab"

    def test_extract_final_ignores_non_content_finals_and_reports_error(self):
        raw = _stream(
            _frame("chat.final", event_type="team.member_done", content="ignored"),
            _frame("chat.final", event_type="team.error", error="boom"),
        )
        text, err = jx.extract_final_response(jx.iter_jsonl_events(raw))
        assert text == ""
        assert err == "boom"

    def test_parse_steps_are_compact_and_ordered(self):
        steps = jx.parse_jiuwenswarm_trace_steps(_RAW)
        kinds = [s["type"] for s in steps]
        # chat.reasoning is omitted from compact steps (token-stream noise).
        assert kinds == ["tool_call", "tool_result", "tool_call", "tool_result", "text"]
        assert steps[0]["summary"] == "read_file task.md"
        assert steps[2]["summary"] == "bash ls"
        assert steps[3]["summary"].startswith("bash: [error] " + "x" * 200)
        assert "[+200 chars]" in steps[3]["summary"]
        # deltas + identical final are folded into a single text step
        assert steps[4]["summary"] == "The answer is <answer>Paris</answer>"
        assert [s["index"] for s in steps] == [1, 2, 3, 4, 5]

    def test_format_steps_and_summary(self):
        text = jx.format_jiuwenswarm_trace_steps(_RAW)
        assert text.splitlines()[0] == "[1] tool_call: read_file task.md"
        assert "reasoning:" not in text
        assert jx.format_jiuwenswarm_trace_steps("") == ""
        summary = jx.build_trace_summary(_RAW, "<answer>Paris</answer>")
        assert "- tool calls: 2" in summary
        assert "- final answer format: tagged" in summary
        # summary still counts reasoning events from the raw stream
        assert "- reasoning events: 1" in summary

    def test_parse_steps_across_attempts(self):
        raw = (
            "===== JIUWENSWARM ATTEMPT 1 =====\n"
            + _stream(_frame("chat.delta", content="first"))
            + "\n===== JIUWENSWARM ATTEMPT 2 =====\n"
            + _stream(_frame("chat.error", error="socket closed"))
        )
        steps = jx.parse_jiuwenswarm_trace_steps(raw)
        assert [(s["type"], s["summary"]) for s in steps] == [("text", "first"), ("error", "socket closed")]


# ── command + subprocess ─────────────────────────────────────────────────────


class TestCommand:
    def test_build_cli_command(self, tmp_path):
        cfg = jx.JiuwenswarmExecConfig(
            cli_path="jw", gateway_url="ws://gw", chat_mode="code.normal", instance_name="i1"
        )
        cmd = jx.build_cli_command(work_dir=str(tmp_path), prompt="do it", cfg=cfg)
        ws = str(tmp_path.resolve())
        assert cmd[:2] == ["jw", "chat"]
        assert cmd[cmd.index("--cwd") + 1] == ws
        assert cmd[cmd.index("--project-dir") + 1] == ws
        assert "--jsonl" in cmd
        assert cmd[cmd.index("--gateway-url") + 1] == "ws://gw"
        assert cmd[cmd.index("--name") + 1] == "i1"
        assert "--trusted-dir" not in cmd
        assert cmd[-2:] == ["--", "do it"]


class _FakeRun:
    """Records subprocess.run calls and returns scripted stdout per call."""

    def __init__(self, outputs, returncodes=None):
        self.outputs = list(outputs)
        self.returncodes = list(returncodes or [0] * len(outputs))
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        idx = min(len(self.calls) - 1, len(self.outputs) - 1)
        return SimpleNamespace(stdout=self.outputs[idx], stderr="", returncode=self.returncodes[idx])


class TestRunExec:
    def test_success_persists_artifacts(self, tmp_path, monkeypatch, clean_env):
        work = tmp_path / "predictions" / "q1" / "jiuwenswarm_exec"
        work.mkdir(parents=True)
        fake = _FakeRun([_RAW])
        monkeypatch.setattr(jx.subprocess, "run", fake)

        response, raw = jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Answer.", timeout=30)
        assert response == "The answer is <answer>Paris</answer>"
        assert "[exit_code] 0" in raw
        assert len(fake.calls) == 1
        prompt = fake.calls[0][-1]
        assert "Read task.md and the skill at .agents/skills/reflact-target/SKILL.md" in prompt
        assert "Do not modify files." in prompt
        assert prompt.endswith("Answer.")
        pred_dir = work.parent
        assert (pred_dir / "jiuwenswarm_raw.txt").exists()
        steps = (pred_dir / "jiuwenswarm_trace_steps.txt").read_text(encoding="utf-8")
        assert steps.startswith("[1] tool_call:")
        assert "reasoning:" not in steps
        assert "tool calls: 2" in (pred_dir / "jiuwenswarm_trace_summary.txt").read_text(encoding="utf-8")

    def test_empty_response_retries_once(self, tmp_path, monkeypatch, clean_env):
        work = tmp_path / "p" / "q2" / "jiuwenswarm_exec"
        work.mkdir(parents=True)
        fake = _FakeRun(["", _stream(_frame("chat.final", content="<answer>42</answer>"))])
        monkeypatch.setattr(jx.subprocess, "run", fake)

        response, raw = jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Q", timeout=5)
        assert response == "<answer>42</answer>"
        assert len(fake.calls) == 2
        assert "Previous execution returned an empty final response" in fake.calls[1][-1]
        assert "ATTEMPT 1" in raw and "ATTEMPT 2" in raw

    def test_retries_disabled(self, tmp_path, monkeypatch, clean_env):
        monkeypatch.setenv("EXEC_EMPTY_RESPONSE_RETRIES", "0")
        work = tmp_path / "p" / "q3" / "jiuwenswarm_exec"
        work.mkdir(parents=True)
        fake = _FakeRun([""])
        monkeypatch.setattr(jx.subprocess, "run", fake)
        response, _ = jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Q", timeout=5)
        assert response == ""
        assert len(fake.calls) == 1

    def test_gateway_unreachable_raises(self, tmp_path, monkeypatch, clean_env):
        work = tmp_path / "p" / "q4" / "jiuwenswarm_exec"
        work.mkdir(parents=True)
        monkeypatch.setattr(jx.subprocess, "run", _FakeRun(["gateway down"], returncodes=[3]))
        with pytest.raises(jx.GatewayUnreachableError, match="jiuwenswarm-start app"):
            jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Q", timeout=5)

    def test_timeout_returns_empty_without_raising(self, tmp_path, monkeypatch, clean_env):
        monkeypatch.setenv("EXEC_EMPTY_RESPONSE_RETRIES", "0")
        work = tmp_path / "p" / "q5" / "jiuwenswarm_exec"
        work.mkdir(parents=True)

        def _boom(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output="partial", stderr="")

        monkeypatch.setattr(jx.subprocess, "run", _boom)
        response, raw = jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Q", timeout=7)
        assert response == ""
        assert "[timeout] jiuwenswarm chat exceeded 7s" in raw
        assert "partial" in raw

    def test_missing_cli_raises_runtime_error(self, tmp_path, monkeypatch, clean_env):
        work = tmp_path / "p" / "q6" / "jiuwenswarm_exec"
        work.mkdir(parents=True)

        def _missing(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(jx.subprocess, "run", _missing)
        with pytest.raises(RuntimeError, match="could not be executed"):
            jx.run_jiuwenswarm_cli_exec(work_dir=str(work), prompt="Q", timeout=5)
