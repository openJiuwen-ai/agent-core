# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from google.protobuf.json_format import MessageToDict

from openjiuwen.extensions.a2a.a2a_client import A2AClient
from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part


pytest.importorskip("a2a.types")
from a2a.types import AgentCard, Task as A2ATask, TaskState as A2ATaskState, TaskStatus as A2ATaskStatus  # noqa: E402

class TestA2AClient:
    def test_to_a2a_request_should_build_message_for_text_request(self):
        request = {
            "query": "hello",
            "sessionId": "conv-validate-1",
            "metadata": {"tenant": "demo"},
        }

        send_request = A2ATransformer.to_a2a_request(request)
        message = send_request.message

        assert message is not None
        dumped = MessageToDict(message, preserving_proto_field_name=True)
        assert dumped["role"] == "ROLE_USER"
        assert dumped["context_id"] == "conv-validate-1"
        assert dumped["message_id"]
        assert dumped["parts"]
        assert dumped["parts"][0]["text"] == "hello"

    def test_to_a2a_request_should_build_message_for_file_request(self):
        request = {
            "query": "please analyze this file",
            "sessionId": "context-file-1",
            "files": [
                {
                    "url": "https://example.com/data.csv",
                    "media_type": "text/csv",
                    "filename": "data.csv",
                    "metadata": {"file_size": 10245},
                }
            ],
        }

        send_request = A2ATransformer.to_a2a_request(request)
        message = send_request.message

        assert message is not None
        dumped = MessageToDict(message, preserving_proto_field_name=True)
        assert dumped["context_id"] == "context-file-1"
        assert dumped["parts"] == [{"text": "please analyze this file"}]
        assert dumped["metadata"]["files"][0]["url"] == "https://example.com/data.csv"
        assert dumped["metadata"]["files"][0]["media_type"] == "text/csv"
        assert dumped["metadata"]["files"][0]["filename"] == "data.csv"
        assert dumped["metadata"]["files"][0]["metadata"]["file_size"] == 10245

    @pytest.mark.asyncio
    async def test__send_message_should_delegate_to_official_sdk(self, monkeypatch):
        captured = {}

        class FakeSendMessageRequest:
            def __init__(self, message):
                self.message = message

        class FakeConnectedClient:
            def send_message(self, request):
                captured["sdk_request"] = MessageToDict(request.message, preserving_proto_field_name=True)
                async def stream():
                    yield (
                        type(
                            "FakeStreamResponse",
                            (),
                            {
                                "message": type(
                                    "FakeMessage",
                                    (),
                                    {
                                        "task_id": "sdk-task-1",
                                        "context_id": "sdk-context-1",
                                        "parts": [type("FakePart", (), {"text": "sdk ok", "url": "", "data": None})()],
                                        "metadata": {},
                                    },
                                )(),
                                "HasField": lambda self, name: name == "message",
                            },
                        )(),
                        None,
                    )
                return stream()

            async def close(self):
                captured["sdk_closed"] = True

        class FakeClientFactory:
            def __init__(self, config):
                captured["client_config"] = config

            def create(self, card):
                captured["connected_card_name"] = card.name
                return FakeConnectedClient()

        class FakeClientConfig:
            def __init__(self):
                captured["client_config_created"] = True

        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientConfig", FakeClientConfig)
        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", FakeClientFactory)

        card = AgentCard(name="fake-agent", description="fake")
        client = A2AClient(card=card)
        try:
            events = []
            request = A2ATransformer.to_a2a_request({
                "query": "hello sdk",
                "sessionId": "conv-sdk-1",
            })
            event_stream = client._send_message(request)
            async for event in event_stream:
                events.append(event)
        finally:
            await client.stop()

        assert captured["connected_card_name"] == "fake-agent"
        assert captured["client_config_created"] is True
        assert captured["sdk_request"]["context_id"] == "conv-sdk-1"
        assert captured["sdk_request"]["parts"] == [{"text": "hello sdk"}]
        assert len(events) == 1
        assert events[0][0].message.task_id == "sdk-task-1"
        assert captured["sdk_closed"] is True

    @pytest.mark.asyncio
    async def test_cancel_task_should_delegate_to_official_sdk(self, monkeypatch):
        captured = {}

        class FakeConnectedClient:
            async def cancel_task(self, request):
                captured["cancel_request"] = MessageToDict(request, preserving_proto_field_name=True)
                return A2ATask(
                    id="sdk-task-cancel-1",
                    context_id="sdk-context-cancel-1",
                    status=A2ATaskStatus(state=A2ATaskState.TASK_STATE_CANCELED),
                )

            async def close(self):
                captured["sdk_closed"] = True

        class FakeClientFactory:
            def __init__(self, config):
                return None

            def create(self, card):
                return FakeConnectedClient()

        class FakeClientConfig:
            def __init__(self):
                return None

        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientConfig", FakeClientConfig)
        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", FakeClientFactory)

        card = AgentCard(name="fake-agent", description="fake")
        client = A2AClient(card=card)
        try:
            result = await client.cancel_task("task-cancel-1", tenant="tenant-1")
        finally:
            await client.stop()

        assert captured["cancel_request"]["id"] == "task-cancel-1"
        assert captured["cancel_request"]["tenant"] == "tenant-1"
        assert result.task_id == "sdk-task-cancel-1"
        assert result.sessionId == "sdk-context-cancel-1"
        assert result.status == TaskStatus.CANCELED
        assert captured["sdk_closed"] is True

    @pytest.mark.asyncio
    async def test_invoke_should_return_first_event_when_polling_disabled(self, monkeypatch):
        # The fake stream yields two events to prove invoke() stops after the first one.
        class FakeConnectedClient:
            def send_message(self, request):
                async def stream():
                    yield (
                        type(
                            "FakeStreamResponse",
                            (),
                            {
                                "status_update": type(
                                    "FakeStatusUpdate",
                                    (),
                                    {
                                        "task_id": "sdk-task-2",
                                        "context_id": "sdk-context-2",
                                        "status": type(
                                            "FakeTaskStatus",
                                            (),
                                            {"state": 2},
                                        )(),
                                        "metadata": {},
                                    },
                                )(),
                                "HasField": lambda self, name: name == "status_update",
                            },
                        )(),
                        None,
                    )
                    yield (
                        type(
                            "FakeStreamResponse",
                            (),
                            {
                                "task": type(
                                    "FakeTask",
                                    (),
                                    {
                                        "id": "sdk-task-2",
                                        "context_id": "sdk-context-2",
                                        "status": type(
                                            "FakeTaskStatus",
                                            (),
                                            {"state": 3},
                                        )(),
                                        "artifacts": [],
                                        "metadata": {},
                                    },
                                )(),
                                "HasField": lambda self, name: name == "task",
                            },
                        )(),
                        None,
                    )
                return stream()

            async def close(self):
                return None

        class FakeClientFactory:
            def __init__(self, config):
                return None

            def create(self, card):
                return FakeConnectedClient()

        class FakeClientConfig:
            def __init__(self):
                return None

        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientConfig", FakeClientConfig)
        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", FakeClientFactory)

        card = AgentCard(name="fake-agent", description="fake")
        client = A2AClient(card=card)
        try:
            result = await client.invoke({"query": "hello invoke", "sessionId": "conv-invoke-1"})
        finally:
            await client.stop()

        assert isinstance(result, AgentResult)
        assert result.task_id == "sdk-task-2"
        assert result.sessionId == "conv-invoke-1"
        assert result.status == TaskStatus.WORKING
        assert not result.artifacts

    @pytest.mark.asyncio
    async def test_invoke_should_return_first_event_when_polling_enabled(self, monkeypatch):
        class FakeConnectedClient:
            def send_message(self, request):
                async def stream():
                    yield (
                        type(
                            "FakeStreamResponse",
                            (),
                            {
                                "status_update": type(
                                    "FakeStatusUpdate",
                                    (),
                                    {
                                        "task_id": "sdk-task-3",
                                        "context_id": "sdk-context-3",
                                        "status": type(
                                            "FakeTaskStatus",
                                            (),
                                            {"state": 2},
                                        )(),
                                        "metadata": {},
                                    },
                                )(),
                                "HasField": lambda self, name: name == "status_update",
                            },
                        )(),
                        None,
                    )
                    yield (
                        type(
                            "FakeStreamResponse",
                            (),
                            {
                                "task": type(
                                    "FakeTask",
                                    (),
                                    {
                                        "id": "sdk-task-3",
                                        "context_id": "sdk-context-3",
                                        "status": type(
                                            "FakeTaskStatus",
                                            (),
                                            {"state": 3},
                                        )(),
                                        "artifacts": [
                                            type(
                                                "FakeArtifact",
                                                (),
                                                {
                                                    "artifact_id": "artifact-3",
                                                    "name": "response",
                                                    "description": "",
                                                    "parts": [
                                                        type(
                                                            "FakePart",
                                                            (),
                                                            {
                                                                "text": "final result",
                                                                "raw": None,
                                                                "url": None,
                                                                "data": None,
                                                                "filename": None,
                                                                "media_type": None,
                                                                "metadata": {},
                                                            },
                                                        )()
                                                    ],
                                                    "metadata": {},
                                                },
                                            )()
                                        ],
                                        "metadata": {},
                                    },
                                )(),
                                "HasField": lambda self, name: name == "task",
                            },
                        )(),
                        None,
                    )
                return stream()

            async def close(self):
                return None

        class FakeClientFactory:
            def __init__(self, config):
                return None

            def create(self, card):
                return FakeConnectedClient()

        class FakeClientConfig:
            def __init__(self):
                return None

        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientConfig", FakeClientConfig)
        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", FakeClientFactory)

        card = AgentCard(name="fake-agent", description="fake")
        client = A2AClient(card=card, polling=True)
        try:
            result = await client.invoke({"query": "hello invoke", "sessionId": "conv-invoke-2"})
        finally:
            await client.stop()

        assert isinstance(result, AgentResult)
        assert result.task_id == "sdk-task-3"
        assert result.sessionId == "conv-invoke-2"
        assert result.status == TaskStatus.WORKING
        assert not result.artifacts
