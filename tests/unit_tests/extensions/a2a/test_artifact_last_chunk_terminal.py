# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A terminal streaming result must mark its artifact chunk as last."""

from types import SimpleNamespace

import pytest

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.extensions.a2a import a2a_agent_executor as module
from openjiuwen.extensions.a2a.a2a_agent_executor import A2AAgentExecutor


class _Updater:
    def __init__(self) -> None:
        self.artifacts: list[dict] = []

    async def add_artifact(self, **kwargs) -> None:
        self.artifacts.append(kwargs)

    async def start_work(self, **kwargs) -> None:
        return None

    async def complete(self) -> None:
        return None

    async def failed(self) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def requires_input(self) -> None:
        return None


@pytest.mark.asyncio
async def test_completed_streaming_result_marks_last_artifact_chunk(monkeypatch) -> None:
    monkeypatch.setattr(module.A2ATransformer, "to_a2a_part", staticmethod(lambda part: part))
    artifact = SimpleNamespace(
        parts=[object()],
        artifactId="a1",
        name="artifact",
        metadata=None,
    )
    result = SimpleNamespace(artifacts=[artifact], status=TaskStatus.COMPLETED)
    updater = _Updater()

    await A2AAgentExecutor()._publish_result(updater, result, final=False)

    assert updater.artifacts[0]["last_chunk"] is True
