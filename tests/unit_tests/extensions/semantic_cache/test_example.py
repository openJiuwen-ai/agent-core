# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openjiuwen.extensions.semantic_cache import CacheScope, SemanticResponseCache


class TestReadOnlyQAExample(unittest.IsolatedAsyncioTestCase):
    async def test_model_invocation_and_storage_approval(self):
        root = Path(__file__).resolve().parents[4]
        spec = importlib.util.spec_from_file_location("qa_example", root / "examples/semantic_cache/read_only_qa.py")
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        response = SimpleNamespace(content="answer", finish_reason="stop", tool_calls=None)
        model = SimpleNamespace(invoke=AsyncMock(return_value=response))
        backend = SimpleNamespace(lookup=AsyncMock(return_value=None), store=AsyncMock())
        cache = SemanticResponseCache(backend, enabled=True)
        scope = CacheScope("t", "a", "app", "model", "template", "context", "generation", "embedding")
        for reason, approved in [("stop", True), ("length", True), ("stop", False)]:
            response.finish_reason = reason
            backend.store.reset_mock()
            result = await example.answer_question(
                model,
                cache,
                "question",
                system_prompt="policy",
                scope=scope,
                can_reuse=AsyncMock(return_value=False),
                may_store=lambda _, flag=approved: flag,
            )
            self.assertEqual(result.text, "answer")
            self.assertEqual(backend.store.await_count, int(reason == "stop" and approved))
        model.invoke.assert_called_with(
            [{"role": "system", "content": "policy"}, {"role": "user", "content": "question"}]
        )
