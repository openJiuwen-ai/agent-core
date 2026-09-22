# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exec-backend branches of the SearchQA / DocVQA / OfficeQA rollouts.

``run_jiuwenswarm_cli_exec`` is stubbed so no CLI or Gateway is needed; the
tests assert workspace preparation, prompt content, scoring and artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.envs.docvqa import rollout as docvqa_rollout
from openjiuwen.agent_evolving.skill_train.envs.officeqa import rollout as officeqa_rollout
from openjiuwen.agent_evolving.skill_train.envs.searchqa import rollout as searchqa_rollout
from openjiuwen.agent_evolving.skill_train.jiuwenswarm_exec import GatewayUnreachableError
from openjiuwen.agent_evolving.skill_train.model_compat import set_target_backend


def _list_files(root: Path) -> list[str]:
    """Workspace-relative file list following directory symlinks (linked corpora)."""
    found: list[str] = []
    for dirpath, _dirs, names in os.walk(root, followlinks=True):
        for name in names:
            rel = Path(dirpath, name).relative_to(root)
            found.append(str(rel).replace("\\", "/"))
    return sorted(found)


class _FakeExec:
    """Stand-in for ``run_jiuwenswarm_cli_exec`` capturing the prepared workspace."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *, work_dir, prompt, timeout, **kwargs):
        ws = Path(work_dir)
        snapshot = {
            "work_dir": work_dir,
            "prompt": prompt,
            "timeout": timeout,
            "task": (ws / "task.md").read_text(encoding="utf-8"),
            "skill": (ws / ".agents" / "skills" / "reflact-target" / "SKILL.md").read_text(encoding="utf-8"),
            "files": _list_files(ws),
        }
        self.calls.append(snapshot)
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        response = self.responses[idx]
        if isinstance(response, Exception):
            raise response
        return response, f"raw:{response}"


@pytest.fixture
def exec_backend():
    set_target_backend("jiuwenswarm_cli_exec")
    yield
    set_target_backend("openai_chat")


@pytest.fixture
def chat_backend():
    set_target_backend("openai_chat")
    yield


# ── SearchQA ─────────────────────────────────────────────────────────────────


class TestSearchQAExec:
    def test_exec_branch_scores_and_writes_artifacts(self, tmp_path, monkeypatch, exec_backend):
        fake = _FakeExec(["I think <answer>Paris</answer>"])
        monkeypatch.setattr(searchqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = searchqa_rollout.SearchQAItemConfig(
            out_root=str(tmp_path), skill_content="# Skill\nBe brief.", exec_timeout=33
        )
        item = {
            "id": "q:1",
            "question": "Capital of France?",
            "context": "[DOC] Paris is the capital.",
            "answers": ["Paris"],
        }

        row = searchqa_rollout.process_one(item, cfg)

        assert row["agent_ok"] is True
        assert row["hard"] == 1 and row["em"] == 1.0
        assert row["n_turns"] == 1
        call = fake.calls[0]
        assert call["timeout"] == 33
        assert "## Question\nCapital of France?" in call["task"]
        assert "[DOC] Paris is the capital." in call["task"]
        assert "Be brief." in call["skill"]
        assert "answer the SearchQA question" in call["prompt"]
        assert call["work_dir"].endswith(str(Path("predictions") / "q_1" / "jiuwenswarm_exec"))
        pred_dir = tmp_path / "predictions" / "q_1"
        conversation = json.loads((pred_dir / "conversation.json").read_text(encoding="utf-8"))
        assert conversation[0]["content"] == "I think <answer>Paris</answer>"
        assert conversation[-1]["role"] == "system"
        assert (pred_dir / "target_system_prompt.txt").read_text(encoding="utf-8").startswith("---")

    def test_exec_retries_until_answer_tag(self, tmp_path, monkeypatch, exec_backend):
        fake = _FakeExec(["not sure", "<answer>Paris</answer>"])
        monkeypatch.setattr(searchqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = searchqa_rollout.SearchQAItemConfig(out_root=str(tmp_path), skill_content="", max_turns=2)
        item = {"id": "q2", "question": "Q?", "context": "ctx", "answers": ["Paris"]}

        row = searchqa_rollout.process_one(item, cfg)

        assert len(fake.calls) == 2
        assert "## Previous Attempt\nnot sure" in fake.calls[1]["task"]
        assert row["n_turns"] == 2 and row["hard"] == 1

    def test_exec_failure_becomes_fail_reason(self, tmp_path, monkeypatch, exec_backend):
        fake = _FakeExec([RuntimeError("cli exploded")])
        monkeypatch.setattr(searchqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = searchqa_rollout.SearchQAItemConfig(out_root=str(tmp_path), skill_content="")
        row = searchqa_rollout.process_one({"id": "q3", "question": "Q?", "context": "", "answers": ["x"]}, cfg)
        assert row["agent_ok"] is False
        assert "cli exploded" in row["fail_reason"]

    def test_gateway_error_propagates(self, tmp_path, monkeypatch, exec_backend):
        fake = _FakeExec([GatewayUnreachableError("down")])
        monkeypatch.setattr(searchqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = searchqa_rollout.SearchQAItemConfig(out_root=str(tmp_path), skill_content="")
        with pytest.raises(GatewayUnreachableError):
            searchqa_rollout.process_one({"id": "q4", "question": "Q?", "context": "", "answers": ["x"]}, cfg)

    def test_chat_backend_does_not_touch_exec(self, tmp_path, monkeypatch, chat_backend):
        fake = _FakeExec(["<answer>x</answer>"])
        monkeypatch.setattr(searchqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        monkeypatch.setattr(searchqa_rollout, "chat_target", lambda **kw: ("<answer>Paris</answer>", {}))
        cfg = searchqa_rollout.SearchQAItemConfig(out_root=str(tmp_path), skill_content="")
        row = searchqa_rollout.process_one({"id": "q5", "question": "Q?", "context": "", "answers": ["Paris"]}, cfg)
        assert fake.calls == []
        assert row["hard"] == 1


# ── DocVQA ───────────────────────────────────────────────────────────────────


class TestDocVQAExec:
    def test_exec_branch_copies_image(self, tmp_path, monkeypatch, exec_backend):
        image = tmp_path / "doc_page.png"
        image.write_bytes(b"\x89PNGfake")
        fake = _FakeExec(["<answer>1999</answer>"])
        monkeypatch.setattr(docvqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = docvqa_rollout.DocVQAItemConfig(out_root=str(tmp_path / "out"), skill_content="look carefully")
        item = {"id": "d1", "question": "Which year?", "image_path": str(image), "answers": ["1999"]}

        row = docvqa_rollout.process_one(item, cfg)

        assert row["agent_ok"] is True and row["hard"] == 1
        call = fake.calls[0]
        assert "attachments/01_doc_page.png" in call["files"]
        assert "ATTACHMENTS.md" in call["files"]
        assert "Which year?" in call["task"]
        assert "attached document image" in call["prompt"]
        assert "look carefully" in call["skill"]

    def test_exec_missing_image_is_soft_failure(self, tmp_path, monkeypatch, exec_backend):
        fake = _FakeExec(["<answer>x</answer>"])
        monkeypatch.setattr(docvqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        cfg = docvqa_rollout.DocVQAItemConfig(out_root=str(tmp_path / "out"), skill_content="")
        item = {"id": "d2", "question": "Q?", "image_path": str(tmp_path / "missing.png"), "answers": ["x"]}
        row = docvqa_rollout.process_one(item, cfg)
        assert row["agent_ok"] is False
        assert fake.calls == []
        assert "missing.png" in row["fail_reason"]


# ── OfficeQA ─────────────────────────────────────────────────────────────────


class TestOfficeQAExec:
    @pytest.fixture
    def corpus(self, tmp_path, monkeypatch):
        root = tmp_path / "treasury_docs"
        root.mkdir()
        (root / "bulletin_2020.txt").write_text("Total receipts: 3.4 trillion", encoding="utf-8")
        (root / "unrelated_noise.txt").write_text("ignore me", encoding="utf-8")
        monkeypatch.setattr(officeqa_rollout, "resolve_docs_roots", lambda data_dirs: [str(root)])
        monkeypatch.setattr(officeqa_rollout, "build_oracle_parsed_pages_context", lambda *a, **k: "")
        return root

    def test_exec_branch_links_only_source_files_and_scores(self, tmp_path, monkeypatch, exec_backend, corpus):
        fake = _FakeExec(["From the bulletin: <answer>3.4 trillion</answer>"])
        monkeypatch.setattr(officeqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        item = {
            "id": "o1",
            "question": "What were total receipts?",
            "ground_truth": "3.4 trillion",
            "source_files": ["bulletin_2020.txt"],
        }

        row = officeqa_rollout.process_one(item, str(tmp_path / "out"), "# Skill\ncheck tables", search_mode="offline")

        assert row["hard"] == 1 and row["agent_ok"] is True
        call = fake.calls[0]
        assert "docs/treasury_docs/bulletin_2020.txt" in call["files"]
        assert "unrelated_noise.txt" not in "\n".join(call["files"])
        assert "## Local Documents" in call["task"]
        assert "docs/treasury_docs/bulletin_2020.txt" in call["task"]
        assert "Only these candidate document files" in call["task"]
        assert str(corpus) not in call["task"].split("## Local Documents")[0]
        assert "documents under `docs/`" in call["prompt"]
        assert row["resolved_source_paths"] == [str(corpus / "bulletin_2020.txt")]

    def test_exec_missing_answer_tag_is_flagged(self, tmp_path, monkeypatch, exec_backend, corpus):
        fake = _FakeExec(["I could not find it."])
        monkeypatch.setattr(officeqa_rollout, "run_jiuwenswarm_cli_exec", fake)
        item = {"id": "o2", "question": "Q?", "ground_truth": "x", "source_files": []}
        row = officeqa_rollout.process_one(item, str(tmp_path / "out"), "", search_mode="offline")
        assert row["hard"] == 0 and row["agent_ok"] is False
        assert "lacked a final <answer> tag" in row["fail_reason"]
        # Empty source_files must not mount the full corpus into the workspace.
        assert "bulletin_2020.txt" not in "\n".join(fake.calls[0]["files"])
        assert "unrelated_noise.txt" not in "\n".join(fake.calls[0]["files"])
        assert "## Local Documents" not in fake.calls[0]["task"]

    def test_exec_rejects_non_offline_mode(self, tmp_path, monkeypatch, exec_backend, corpus):
        monkeypatch.setattr(officeqa_rollout, "run_jiuwenswarm_cli_exec", _FakeExec(["x"]))
        item = {"id": "o3", "question": "Q?", "ground_truth": "x", "source_files": []}
        with pytest.raises(ValueError, match="only supports offline mode"):
            officeqa_rollout.process_one(item, str(tmp_path / "out"), "", search_mode="custom")
