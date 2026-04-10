# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from google.protobuf.json_format import MessageToDict

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer
from openjiuwen.core.controller.schema.task import TaskStatus as OJWTaskStatus


class TestA2ATransformer:
    def test_to_a2a_request_should_raise_clear_error_for_non_dict_input(self):
        try:
            A2ATransformer.to_a2a_request("hello")
            assert False, "expected TypeError"
        except TypeError as exc:
            assert "must be a dict" in str(exc)
            assert "str" in str(exc)

    def test_to_a2a_request_should_treat_all_other_fields_as_metadata(self):
        request = {
            "query": "hello",
            "sessionId": "conv-2",
            "metadata": [],
            "region": "shenzhen",
        }

        result = A2ATransformer.to_a2a_request(request)
        dumped = MessageToDict(result, preserving_proto_field_name=True)

        assert dumped["metadata"]["metadata"] == []
        assert dumped["metadata"]["region"] == "shenzhen"

    def test_to_a2a_request_should_convert_openjiuwen_request(self):
        request = {
            "query": "hello",
            "sessionId": "conv-1",
            "metadata": {"tenant": "demo"},
            "city": "shenzhen",
        }

        result = A2ATransformer.to_a2a_request(request)
        dumped = MessageToDict(result.message, preserving_proto_field_name=True)
        request_dumped = MessageToDict(result, preserving_proto_field_name=True)

        assert dumped["role"] == "ROLE_USER"
        assert dumped["message_id"]
        assert dumped["context_id"] == "conv-1"
        assert dumped["parts"] == [{"text": "hello"}]
        assert request_dumped["metadata"]["metadata"]["tenant"] == "demo"
        assert request_dumped["metadata"]["city"] == "shenzhen"

    def test_to_a2a_request_should_put_all_other_fields_into_metadata(self):
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
            "reference_task_ids": ["task-reference-1"],
            "extensions": ["https://example.com/extensions/typing-indicator"],
        }

        result = A2ATransformer.to_a2a_request(request)
        dumped = MessageToDict(result, preserving_proto_field_name=True)

        assert dumped["message"]["context_id"] == "context-file-1"
        assert dumped["message"]["parts"] == [{"text": "please analyze this file"}]
        assert dumped["metadata"]["files"] == [
            {
                "url": "https://example.com/data.csv",
                "media_type": "text/csv",
                "filename": "data.csv",
                "metadata": {"file_size": 10245},
            }
        ]
        assert dumped["metadata"]["reference_task_ids"] == ["task-reference-1"]
        assert dumped["metadata"]["extensions"] == ["https://example.com/extensions/typing-indicator"]

    def test_to_a2a_request_should_ignore_none_values_in_top_level_metadata_merge(self):
        request = {
            "query": "hello",
            "metadata": {"tenant": "demo"},
            "city": None,
            "region": "sz",
        }

        result = A2ATransformer.to_a2a_request(request)
        dumped = MessageToDict(result, preserving_proto_field_name=True)

        assert dumped["metadata"]["metadata"]["tenant"] == "demo"
        assert dumped["metadata"]["region"] == "sz"
        assert "city" not in dumped["metadata"]

    def test_from_a2a_message_should_return_agent_result(self):
        message = Message(
            message_id="msg-1",
            context_id="conv-1",
            task_id="task-1",
            parts=[Part(text="hello from agent")],
        )
        message.metadata.update({"source": "a2a"})

        result = A2ATransformer.from_a2a_response(message)

        assert result.task_id == "task-1"
        assert result.sessionId == "conv-1"
        assert result.status == OJWTaskStatus.COMPLETED
        assert result.artifacts[0].artifactId == "message"
        assert result.artifacts[0].parts[0].text == "hello from agent"
        assert result.metadata["source"] == "a2a"

    def test_from_a2a_message_should_preserve_rich_part_fields(self):
        file_part = Part(
            url="https://example.com/report.pdf",
            filename="report.pdf",
            media_type="application/pdf",
        )
        file_part.metadata.update({"source": "upload"})

        data_part = Part(raw=b"abc")
        data_part.data.struct_value.fields["title"].string_value = "Quarterly Report"

        message = Message(
            context_id="conv-rich-1",
            task_id="task-rich-1",
            parts=[file_part, data_part],
        )

        result = A2ATransformer.from_a2a_response(message)

        assert result.task_id == "task-rich-1"
        assert result.sessionId == "conv-rich-1"
        assert result.artifacts[0].parts[0].url == "https://example.com/report.pdf"
        assert result.artifacts[0].parts[0].filename == "report.pdf"
        assert result.artifacts[0].parts[0].media_type == "application/pdf"
        assert result.artifacts[0].parts[0].metadata == {"source": "upload"}
        assert result.artifacts[0].parts[1].data == 'struct_value {\n  fields {\n    key: "title"\n    value {\n      string_value: "Quarterly Report"\n    }\n  }\n}\n'

    def test_from_a2a_task_should_return_agent_result(self):
        task = Task(
            id="task-2",
            context_id="context-2",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[
                Artifact(
                    artifact_id="result",
                    name="summary",
                    description="task result",
                    parts=[Part(text="task result body")],
                )
            ],
        )
        task.metadata.update({"priority": "high"})

        result = A2ATransformer.from_a2a_response(task)

        assert result.task_id == "task-2"
        assert result.sessionId == "context-2"
        assert result.status == OJWTaskStatus.COMPLETED
        assert result.artifacts[0].artifactId == "result"
        assert result.artifacts[0].parts[0].text == "task result body"
        assert result.metadata["priority"] == "high"

    def test_from_a2a_status_update_should_return_agent_result(self):
        event = TaskStatusUpdateEvent(
            task_id="task-3",
            context_id="context-3",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        event.metadata.update({"agent_id": "agent-1"})

        result = A2ATransformer.from_a2a_response(event)

        assert result.task_id == "task-3"
        assert result.sessionId == "context-3"
        assert result.status == OJWTaskStatus.WORKING
        assert result.artifacts == []
        assert result.metadata["agent_id"] == "agent-1"

    def test_from_a2a_status_update_should_return_completed_agent_result_from_protobuf_enum(self):
        event = TaskStatusUpdateEvent(
            task_id="task-3",
            context_id="context-3",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        result = A2ATransformer.from_a2a_response(event)

        assert result.status == OJWTaskStatus.COMPLETED

    def test_from_a2a_status_update_should_map_all_task_states_explicitly(self):
        cases = [
            (TaskState.TASK_STATE_UNSPECIFIED, "unknown"),
            (TaskState.TASK_STATE_SUBMITTED, "submitted"),
            (TaskState.TASK_STATE_WORKING, "working"),
            (TaskState.TASK_STATE_COMPLETED, "completed"),
            (TaskState.TASK_STATE_FAILED, "failed"),
            (TaskState.TASK_STATE_CANCELED, "canceled"),
            (TaskState.TASK_STATE_INPUT_REQUIRED, "input-required"),
            (TaskState.TASK_STATE_REJECTED, "failed"),
            (TaskState.TASK_STATE_AUTH_REQUIRED, "input-required"),
        ]

        for state, expected in cases:
            event = TaskStatusUpdateEvent(
                task_id="task-state-map",
                context_id="context-state-map",
                status=TaskStatus(state=state),
            )

            result = A2ATransformer.from_a2a_response(event)

            assert result.status == expected, f"state {state} should map to {expected}"

    def test_from_a2a_artifact_update_should_return_agent_result(self):
        event = TaskArtifactUpdateEvent(
            task_id="task-4",
            context_id="context-4",
            artifact=Artifact(
                artifact_id="artifact-4",
                name="Technical_Specification.md",
                description="Generated technical specification document",
                parts=[Part(text="Technical Specification")],
            ),
        )
        event.metadata.update({"format": "markdown"})

        result = A2ATransformer.from_a2a_response(event)

        assert result.task_id == "task-4"
        assert result.sessionId == "context-4"
        assert result.status == OJWTaskStatus.WORKING
        assert result.artifacts[0].artifactId == "artifact-4"
        assert result.artifacts[0].parts[0].text == "Technical Specification"
        assert result.metadata["format"] == "markdown"

    def test_from_client_event_should_return_agent_result(self):
        task = Task(
            id="task-event-1",
            context_id="context-event-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        stream_response = type("FakeStreamResponse", (), {"task": task, "HasField": lambda self, name: name == "task"})()

        result = A2ATransformer.from_a2a_response((stream_response, task))

        assert result.task_id == "task-event-1"
        assert result.sessionId == "context-event-1"
        assert result.status == OJWTaskStatus.COMPLETED

    def test_from_client_event_should_fallback_to_task_when_stream_response_has_no_payload(self):
        task = Task(
            id="task-fallback-1",
            context_id="context-fallback-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        stream_response = type("FakeStreamResponse", (), {"HasField": lambda self, name: False})()

        result = A2ATransformer.from_a2a_response((stream_response, task))

        assert result.task_id == "task-fallback-1"
        assert result.sessionId == "context-fallback-1"
        assert result.status == OJWTaskStatus.WORKING

    def test_from_unknown_response_should_return_minimal_completed_agent_result(self):
        result = A2ATransformer.from_a2a_response(object())

        assert result.task_id is None
        assert result.sessionId is None
        assert result.status == OJWTaskStatus.COMPLETED
        assert result.artifacts == []
        assert result.metadata == {}
