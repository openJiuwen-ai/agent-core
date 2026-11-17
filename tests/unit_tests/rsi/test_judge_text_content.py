# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Text evidence is identified by content, not producer naming conventions."""
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, judge_runtime
from openjiuwen.rsi.harness_rsi.evaluator.judger.direct_evidence import inline_evidence


@pytest.mark.parametrize("name", ["_t1.out", "code.cpp", "run.sh", "answer", "output.custom", "looks.bin"])
@pytest.mark.parametrize("content", ["", "result=4\n\tcomplete\n", "\u7ed3\u679c\uff1a4\n"])
def test_arbitrary_text_filename_is_lossless(tmp_path, name, content):
    (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "request.json").write_text(json.dumps({"evidence_files": [name]}), encoding="utf-8")
    payload = inline_evidence(tmp_path, required=True)
    assert json.loads(payload)["evidence_files"][name] == content


@pytest.mark.parametrize("data", [b"\x00", b"\xff\xfe", b"valid\n" * 1000 + b"\x00"])
def test_binary_disguised_as_text_is_not_silently_sent(tmp_path, data):
    (tmp_path / "answer.txt").write_bytes(data)
    (tmp_path / "request.json").write_text('{"evidence_files":["answer.txt"]}', encoding="utf-8")
    payload = json.loads(inline_evidence(tmp_path, required=True))
    assert payload["evidence_files"] == {}
    assert payload["unavailable_evidence_files"][0]["path"] == "answer.txt"


def test_sparse_control_characters_are_visible_and_auditable(tmp_path):
    (tmp_path / "REPORT.md").write_text("before\x18after", encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["REPORT.md"]}', encoding="utf-8")
    payload = json.loads(inline_evidence(tmp_path, required=True))
    assert payload["evidence_files"]["REPORT.md"] == "before<CONTROL-U+0018>after"
    assert payload["evidence_transformations"] == [{
        "path": "REPORT.md",
        "operation": "rendered control characters as visible markers",
        "count": 1,
    }]


def test_bad_auxiliary_file_does_not_hide_readable_evidence(tmp_path):
    (tmp_path / "answer.txt").write_text("supported answer", encoding="utf-8")
    (tmp_path / "broken.dat").write_bytes(b"\xff")
    (tmp_path / "request.json").write_text(
        '{"evidence_files":["answer.txt","broken.dat"]}', encoding="utf-8",
    )
    payload = json.loads(inline_evidence(tmp_path, required=True))
    assert payload["evidence_files"] == {"answer.txt": "supported answer"}
    assert payload["unavailable_evidence_files"] == [
        {"path": "broken.dat", "reason": "not valid UTF-8"},
    ]


@pytest.mark.asyncio
async def test_out_artifact_reaches_judge_and_strict_scoring(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "_t1.out").write_text("result=4\n", encoding="utf-8")
    model = AsyncMock()
    model.invoke.return_value = AssistantMessage(content=json.dumps({
        "status": "completed", "overall_reason": "Evidence inspected", "behaviors": [
            {"id": "rubric_001", "score": 1, "reason": "Correct result", "evidence": "artifacts/_t1.out: result=4"}
        ], "forbidden_hits": [],
    }))
    monkeypatch.setattr(judge_runtime, "_judge_model", lambda _: model)
    runner = LlmAsJudgeJudger(EvaluatorConfig(judge_model_config_ref="unused"))
    result = await runner.judge(
        case={"case_id": "text", "input": "Compute 2+2", "reference": {"rubric": ["Result is 4"]}},
        execution_result=CaseExecutionResult("See output", "passed"), output_dir=str(tmp_path),
    )
    assert result.passed
    payload = json.loads(model.invoke.call_args.kwargs["messages"][1].content)
    assert payload["evidence_files"]["artifacts/_t1.out"] == "result=4\n"
