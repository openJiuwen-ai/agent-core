# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lossless long-answer evidence and scoring-policy cache identity."""

import copy
import json
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_evidence
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_evidence import prepare_judge_workspace
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import JudgeBudgetRail
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import _evaluation_input_fingerprint
from tests.unit_tests.rsi.test_evaluator_agent import _config


@pytest.mark.parametrize("text", [
    "Proof step\n" * 7000,
    "x" * 12001,
    "\n" * 15000 + "proof at the end",
    "first\r\n" + "\u8bc1\u660e\U0001f4c4" * 5000 + "\r\nlast",
    "\U0001f4c4" * 7000,
], ids=["multiline", "long-line", "blank-lines", "unicode-crlf", "byte-limit"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_long_response_pages_preserve_every_character_and_original_metadata(tmp_path, text, wrapped):
    response = {"output": text, "result_type": "answer", "extra": "keep"} if wrapped else text
    before = copy.deepcopy(response)
    workspace = tmp_path / "judge"
    prepare_judge_workspace(
        case={"input": "Produce an implementation and proof."}, response=response,
        case_dir=tmp_path, workspace=workspace, behaviors=[], forbidden=[],
    )
    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    manifest = request["response"]
    pages = manifest["pages"]
    decoded = []
    cursor = 0
    for page in pages:
        content = (workspace / page["path"]).read_bytes().decode("utf-8")
        assert 0 < len(content) <= judge_evidence._RESPONSE_PAGE_CHARS
        assert len(content.splitlines()) <= judge_evidence._RESPONSE_PAGE_LINES
        assert len(content.encode("utf-8")) <= judge_evidence._RESPONSE_PAGE_BYTES
        assert page["start_char"] == cursor
        assert page["end_char"] == cursor + len(content)
        cursor = page["end_char"]
        assert page["path"] in request["evidence_files"]
        decoded.append(content)
    assert "".join(decoded) == text
    assert manifest["characters"] == len(text)
    original = json.loads((workspace / manifest["original_json"]).read_text(encoding="utf-8"))
    assert original["response"] == before == response
    assert text not in (workspace / "request.json").read_text(encoding="utf-8")
    assert request["judge_protocol"] == judge_evidence.judge_protocol_identity()


@pytest.mark.parametrize("response", ["", "short answer", {"output": "42", "result_type": "answer"}])
def test_small_or_empty_response_stays_inline(tmp_path, response):
    workspace = tmp_path / "judge"
    prepare_judge_workspace(
        case={"input": "Question"}, response=response, case_dir=tmp_path,
        workspace=workspace, behaviors=[], forbidden=[],
    )
    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    assert request["response"] == response
    assert not (workspace / "response").exists()


def test_nonstandard_response_object_is_not_dropped(tmp_path):
    response = {"parts": [{"body": "long" * 4000}], "verdict": "draft"}
    workspace = tmp_path / "judge"
    prepare_judge_workspace(
        case={"input": "Question"}, response=response, case_dir=tmp_path,
        workspace=workspace, behaviors=[], forbidden=[],
    )
    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    combined = "".join((workspace / p["path"]).read_text(encoding="utf-8") for p in request["response"]["pages"])
    assert json.loads(combined) == response


def test_judge_policy_change_invalidates_case_cache_but_not_non_llm_evals(monkeypatch):
    kwargs = {"cases": [{"case_id": "a"}], "team_skill_ref_path": "", "harness_refs_path": ""}
    monkeypatch.setattr(judge_evidence, "judge_protocol_identity", lambda: {"policy": "old"})
    old = _evaluation_input_fingerprint(**kwargs, evaluator_config=_config())
    non_llm = _evaluation_input_fingerprint(**kwargs)
    monkeypatch.setattr(judge_evidence, "judge_protocol_identity", lambda: {"policy": "new"})
    assert old != _evaluation_input_fingerprint(**kwargs, evaluator_config=_config())
    assert non_llm == _evaluation_input_fingerprint(**kwargs)


@pytest.mark.asyncio
async def test_read_log_distinguishes_read_limit_error_from_empty_answer(tmp_path):
    log = tmp_path / "tools.jsonl"
    rail = JudgeBudgetRail(8, log)
    for result in [
        {"success": False, "error": "Read exceeds token limit"},
        SimpleNamespace(success=True, error=None, data={"content": "proof"}),
    ]:
        ctx = SimpleNamespace(inputs=SimpleNamespace(
            tool_result=result, tool_name="read_file", tool_args={"file_path": "response/part_001.txt"},
        ))
        await rail.after_tool_call(ctx)
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["error"] == "Read exceeds token limit"
    assert records[0]["success"] is False
    assert records[1]["returned_chars"] == 5
    assert records[1]["success"] is True
