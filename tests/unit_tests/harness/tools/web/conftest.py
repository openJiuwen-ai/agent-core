# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scripted model replies for routing tests, never a semantic quality oracle."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.harness.tools.web import free_search


@pytest.fixture
def scripted_search_model(monkeypatch):
    class ScriptedModel:
        def __init__(self):
            self.labels = {}
            self.calls = []

        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            payload = json.loads(kwargs["messages"][1]["content"])
            return SimpleNamespace(content=json.dumps({"results": [
                {"id": row["id"], "relevance": self.labels.get(row["title"], "relevant"), "reason": "scripted judgment"}
                for row in payload["candidates"]
            ]}))

    model = ScriptedModel()
    monkeypatch.setattr(free_search, "resolve_search_model", lambda context, fallback=None: model)
    return model
