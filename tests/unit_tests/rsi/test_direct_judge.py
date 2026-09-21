# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Routing and recovery without dropping evidence or changing scores."""

import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_runtime
from openjiuwen.rsi.harness_rsi.evaluator.judger.direct_evidence import (
    MAX_CLOSEOUT_BYTES,
    MAX_INLINE_BYTES,
    inline_evidence,
)


@pytest.mark.parametrize("kind", ["text", "jsonl", "binary", "large", "outside", "missing", "invalid_utf8"])
def test_lossless_route(tmp_path, kind):
    name = "answer.txt"
    data = b"complete evidence"
    if kind == "jsonl":
        name = "evidence.jsonl"
        data = b'{"step":1}\n{"step":2}\n'
    if kind == "binary":
        name = "answer.pdf"
    elif kind == "large":
        data = b"a" * MAX_INLINE_BYTES
    elif kind == "outside":
        name = "../outside.txt"
    elif kind == "invalid_utf8":
        data = b"\xff"
    if kind not in {"outside", "missing"}:
        (tmp_path / name).write_bytes(data)
    (tmp_path / "request.json").write_text(json.dumps({"evidence_files": [name]}), encoding="utf-8")
    result = inline_evidence(tmp_path)
    if kind in {"text", "jsonl"}:
        assert json.loads(result)["evidence_files"][name] == data.decode()
    else:
        assert result is None


@pytest.mark.parametrize("name", ["answer.txt", "evidence.jsonl"])
def test_closeout_includes_large_evidence_without_clipping(tmp_path, name):
    content = "\u8bc1\u636e" * 15000
    (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "request.json").write_text(json.dumps({"evidence_files": [name]}), encoding="utf-8")
    assert inline_evidence(tmp_path) is None
    payload = inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES)
    assert json.loads(payload)["evidence_files"][name] == content
    assert len(payload.encode("utf-8")) <= MAX_CLOSEOUT_BYTES


def test_serialized_payload_limit_includes_json_escaping(tmp_path):
    (tmp_path / "answer.txt").write_text("\\" * (MAX_CLOSEOUT_BYTES // 2), encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["answer.txt"]}', encoding="utf-8")
    assert inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES) is None




@pytest.mark.parametrize("kind, expected", [
    ("large", "exceeds 262144 bytes at evidence.jsonl"),
    ("binary", "unsupported text evidence format: evidence.pdf"),
    ("missing", "evidence file missing or not a regular file: evidence.jsonl"),
    ("utf8", "evidence is not valid UTF-8: evidence.jsonl"),
    ("outside", "evidence path escapes snapshot"),
    ("request", "cannot read request.json"),
    ("request_utf8", "cannot read request.json \\(UnicodeDecodeError\\)"),
    ("escaped", "serialized evidence exceeds"),
])
def test_required_evidence_reports_specific_failure(tmp_path, kind, expected):
    name = "evidence.pdf" if kind == "binary" else "evidence.jsonl"
    if kind == "outside":
        name = "../outside.jsonl"
    data = b'{"ok": true}\n'
    if kind == "large":
        data = b"x" * MAX_CLOSEOUT_BYTES
    elif kind == "utf8":
        data = b"\xff"
    elif kind == "escaped":
        data = b"\\" * (MAX_CLOSEOUT_BYTES // 2)
    if kind not in {"missing", "outside"}:
        (tmp_path / name).write_bytes(data)
    request = "invalid" if kind == "request" else json.dumps({"evidence_files": [name]})
    (tmp_path / "request.json").write_text(request, encoding="utf-8")
    if kind == "request_utf8":
        (tmp_path / "request.json").write_bytes(b"\xff")
    assert inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES) is None
    with pytest.raises(EvaluationInfrastructureError, match=expected):
        inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES, required=True)


def test_unreadable_file_names_the_file_without_exposing_exception_details(tmp_path, monkeypatch):
    target = tmp_path / "evidence.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["evidence.jsonl"]}', encoding="utf-8")
    original = type(target).read_text

    def read_text(path, *args, **kwargs):
        if path.name == target.name:
            raise PermissionError("private detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(target), "read_text", read_text)
    assert inline_evidence(tmp_path) is None
    with pytest.raises(EvaluationInfrastructureError, match="evidence.jsonl") as error:
        inline_evidence(tmp_path, required=True)
    assert "PermissionError" in str(error.value)
    assert "private detail" not in str(error.value)


@pytest.mark.asyncio
async def test_missing_closeout_payload_never_calls_model(tmp_path, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(judge_runtime, "_judge_model", lambda _: model)
    monkeypatch.setattr(judge_runtime, "inline_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(judge_runtime, "create_deep_agent", lambda **kwargs: object())
    budget = judge_runtime.JudgeBudgetRail(20, tmp_path / "tools.jsonl")
    judge_runtime.build_judge_agent(EvaluatorConfig(), tmp_path, tmp_path / "tools.jsonl", budget=budget)
    with pytest.raises(EvaluationInfrastructureError, match="evidence is unavailable"):
        await budget.closeout("")
    model.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_agent_closeout_sends_large_complete_evidence_without_capacity_estimate(tmp_path, monkeypatch):
    content = "\\" * 300000
    (tmp_path / "answer.txt").write_text(content, encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["answer.txt"]}', encoding="utf-8")
    model = AsyncMock()
    model.context_budget.side_effect = AssertionError("capacity must not be used as a byte limit")
    verdict = AsyncMock(return_value='{"status":"completed"}')
    monkeypatch.setattr(judge_runtime, "_judge_model", lambda _: model)
    monkeypatch.setattr(judge_runtime, "_invoke_complete_evidence", verdict)
    monkeypatch.setattr(judge_runtime, "create_deep_agent", lambda **kwargs: object())
    budget = judge_runtime.JudgeBudgetRail(20, tmp_path / "tools.jsonl")
    judge_runtime.build_judge_agent(EvaluatorConfig(), tmp_path, tmp_path / "tools.jsonl", budget=budget)
    assert await budget.closeout("") == '{"status":"completed"}'
    payload = verdict.call_args.args[1]
    assert json.loads(payload)["evidence_files"]["answer.txt"] == content


@pytest.mark.asyncio
async def test_direct_call_and_one_recovery(tmp_path, monkeypatch):
    (tmp_path / "request.json").write_text('{"response":"42","evidence_files":[]}', encoding="utf-8")
    model = AsyncMock()
    model.invoke.side_effect = [
        AssistantMessage(content="malformed"), AssistantMessage(content='{"status":"completed"}'),
    ]
    monkeypatch.setattr(judge_runtime, "_judge_model", lambda _: model)
    monkeypatch.setattr(judge_runtime, "build_judge_agent", lambda *a, **k: pytest.fail("should not build an agent"))
    budget = judge_runtime.JudgeBudgetRail(20, tmp_path / "tools.jsonl")
    result = await judge_runtime.run_judge_agent(
        EvaluatorConfig(), tmp_path, "", tmp_path / "tools.jsonl", budget=budget,
    )
    assert result == "malformed"
    assert await budget.closeout(result) == '{"status":"completed"}'
    assert model.invoke.await_count == 2
    assert all(c.kwargs['tools'] is None for c in model.invoke.call_args_list)
    assert '42' in model.invoke.call_args_list[1].kwargs['messages'][1].content
