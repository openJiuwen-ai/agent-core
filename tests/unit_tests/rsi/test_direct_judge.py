# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Routing and recovery without dropping evidence or changing scores."""

import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_runtime
from openjiuwen.rsi.harness_rsi.evaluator.judger.direct_evidence import (
    MAX_CLOSEOUT_BYTES,
    MAX_INLINE_BYTES,
    inline_evidence,
)


@pytest.mark.parametrize("kind", ["text", "binary", "large", "outside", "missing", "invalid_utf8"])
def test_lossless_route(tmp_path, kind):
    name = "answer.txt"
    data = b"complete evidence"
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
    if kind == "text":
        assert json.loads(result)["evidence_files"][name] == data.decode()
    else:
        assert result is None


def test_closeout_includes_large_evidence_without_clipping(tmp_path):
    content = "\u8bc1\u636e" * 15000
    (tmp_path / "answer.txt").write_text(content, encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["answer.txt"]}', encoding="utf-8")
    assert inline_evidence(tmp_path) is None
    payload = inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES)
    assert json.loads(payload)["evidence_files"]["answer.txt"] == content
    assert len(payload.encode("utf-8")) <= MAX_CLOSEOUT_BYTES


def test_serialized_payload_limit_includes_json_escaping(tmp_path):
    (tmp_path / "answer.txt").write_text("\\" * (MAX_CLOSEOUT_BYTES // 2), encoding="utf-8")
    (tmp_path / "request.json").write_text('{"evidence_files":["answer.txt"]}', encoding="utf-8")
    assert inline_evidence(tmp_path, max_bytes=MAX_CLOSEOUT_BYTES) is None


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
