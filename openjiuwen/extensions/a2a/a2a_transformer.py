# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid
from typing import Any, Dict

from a2a.server.agent_execution.context import RequestContext
from a2a.types.a2a_pb2 import Message, Part as A2APart, Role, SendMessageRequest, Task
from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent, TaskState as A2ATaskStatus, TaskStatusUpdateEvent
from a2a.types import StreamResponse
from google.protobuf.struct_pb2 import Struct
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part


class A2ATransformer:
    """Minimal transformer between openjiuwen payloads and A2A payloads."""

    _A2A_STATUS_TO_OJW_STATUS = {
        "TASK_STATE_UNSPECIFIED": TaskStatus.UNKNOWN,
        "TASK_STATE_SUBMITTED": TaskStatus.SUBMITTED,
        "TASK_STATE_WORKING": TaskStatus.WORKING,
        "TASK_STATE_COMPLETED": TaskStatus.COMPLETED,
        "TASK_STATE_FAILED": TaskStatus.FAILED,
        "TASK_STATE_CANCELED": TaskStatus.CANCELED,
        "TASK_STATE_INPUT_REQUIRED": TaskStatus.INPUT_REQUIRED,
        "TASK_STATE_REJECTED": TaskStatus.FAILED,
        "TASK_STATE_AUTH_REQUIRED": TaskStatus.INPUT_REQUIRED,
    }

    @classmethod
    def to_a2a_request(cls, request: Dict[str, Any]) -> SendMessageRequest:
        if not isinstance(request, dict):
            raise TypeError(f"request must be a dict, got {type(request).__name__}")

        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
        )

        session_id = request.get("conversation_id") or request.get("sessionId")
        if session_id:
            message.context_id = str(session_id)

        text = request.get("query")
        if text is not None:
            message.parts.append(A2APart(text=str(text)))

        metadata = {
            key: value
            for key, value in request.items()
            if key not in {"query", "sessionId", "conversation_id"} and value is not None
        }
        if metadata:
            message.metadata.CopyFrom(cls._to_struct(metadata))
        send_request = SendMessageRequest(message=message)
        return send_request

    @classmethod
    def from_a2a_request(
        cls,
        request: RequestContext | SendMessageRequest | Message | Dict[str, Any],
    ) -> Dict[str, Any]:
        if isinstance(request, dict):
            return cls._dict_request_to_payload(request)

        if isinstance(request, RequestContext):
            return cls._request_context_to_payload(request)

        if isinstance(request, SendMessageRequest):
            return cls._send_message_request_to_payload(request)

        if isinstance(request, Message):
            return cls._message_to_payload(request)

        return {}

    @classmethod
    def from_a2a_response(cls, response: StreamResponse | Any) -> AgentResult:
        if isinstance(response, tuple) and len(response) == 2:
            stream_response, task = response

            if cls._has_field(stream_response, "artifact_update"):
                return cls._a2a_artifact_update_to_result(stream_response.artifact_update)
            if cls._has_field(stream_response, "status_update"):
                return cls._a2a_status_update_to_result(stream_response.status_update)
            if cls._has_field(stream_response, "message"):
                return cls._a2a_message_to_result(stream_response.message)
            if cls._has_field(stream_response, "task"):
                return cls._a2a_task_to_result(stream_response.task)
            if task is not None:
                return cls._a2a_task_to_result(task)

            return cls._build_agent_result(status=TaskStatus.COMPLETED)

        if isinstance(response, TaskArtifactUpdateEvent):
            return cls._a2a_artifact_update_to_result(response)
        if isinstance(response, TaskStatusUpdateEvent):
            return cls._a2a_status_update_to_result(response)
        if isinstance(response, Message):
            return cls._a2a_message_to_result(response)
        if isinstance(response, Task):
            return cls._a2a_task_to_result(response)

        if cls._has_field(response, "artifact_update"):
            return cls._a2a_artifact_update_to_result(response.artifact_update)
        if cls._has_field(response, "status_update"):
            return cls._a2a_status_update_to_result(response.status_update)
        if cls._has_field(response, "message"):
            return cls._a2a_message_to_result(response.message)
        if cls._has_field(response, "task"):
            return cls._a2a_task_to_result(response.task)

        return cls._build_agent_result(status=TaskStatus.COMPLETED)

    @classmethod
    def _a2a_message_to_result(cls, message: Message) -> AgentResult:
        return cls._build_agent_result(
            task_id=message.task_id or None,
            session_id=message.context_id or None,
            status=TaskStatus.COMPLETED,
            artifacts=[
                Artifact(
                    artifactId="message",
                    parts=[cls._a2a_part_to_part(part) for part in message.parts],
                    metadata={},
                )
            ],
            metadata=cls._from_struct(message.metadata),
        )

    @classmethod
    def _a2a_task_to_result(cls, task: Task) -> AgentResult:
        return cls._build_agent_result(
            task_id=task.id or None,
            session_id=task.context_id or None,
            status=cls._to_ojw_status(task.status.state if cls._has_field(task, "status") else None),
            artifacts=[cls._a2a_artifact_to_artifact(artifact) for artifact in task.artifacts],
            metadata=cls._from_struct(task.metadata),
        )

    @classmethod
    def _a2a_status_update_to_result(cls, event: TaskStatusUpdateEvent) -> AgentResult:
        return cls._build_agent_result(
            task_id=event.task_id or None,
            session_id=event.context_id or None,
            status=cls._to_ojw_status(event.status.state if cls._has_field(event, "status") else None),
            metadata=cls._from_struct(event.metadata),
        )

    @classmethod
    def _a2a_artifact_update_to_result(cls, event: TaskArtifactUpdateEvent) -> AgentResult:
        return cls._build_agent_result(
            task_id=event.task_id or None,
            session_id=event.context_id or None,
            status=TaskStatus.WORKING,
            artifacts=[cls._a2a_artifact_to_artifact(event.artifact)],
            metadata=cls._from_struct(event.metadata),
        )

    @classmethod
    def to_a2a_part(cls, part: Part) -> A2APart:
        a2a_part = A2APart()
        if part.text is not None:
            a2a_part.text = part.text
        if part.raw is not None:
            a2a_part.raw = part.raw
        if part.url is not None:
            a2a_part.url = part.url
        if part.data is not None:
            if isinstance(part.data, dict):
                a2a_part.data.struct_value.CopyFrom(cls._to_struct(part.data))
            else:
                a2a_part.data.string_value = str(part.data)
        if part.filename is not None:
            a2a_part.filename = part.filename
        if part.media_type is not None:
            a2a_part.media_type = part.media_type
        if part.metadata:
            a2a_part.metadata.CopyFrom(cls._to_struct(part.metadata))
        return a2a_part

    @staticmethod
    def _a2a_artifact_to_artifact(artifact: Any) -> Artifact:
        return Artifact(
            artifactId=getattr(artifact, "artifact_id", None) or None,
            name=getattr(artifact, "name", None) or None,
            description=getattr(artifact, "description", None) or None,
            parts=[A2ATransformer._a2a_part_to_part(part) for part in getattr(artifact, "parts", [])],
            metadata=A2ATransformer._from_struct(getattr(artifact, "metadata", None)),
        )

    @staticmethod
    def _a2a_part_to_part(part: A2APart) -> Part:
        return Part(
            text=part.text or None,
            raw=part.raw or None,
            url=part.url or None,
            data=str(part.data) if A2ATransformer._has_field(part, "data") else None,
            filename=part.filename or None,
            media_type=part.media_type or None,
            metadata=A2ATransformer._from_struct(getattr(part, "metadata", None)),
        )

    @classmethod
    def _dict_request_to_payload(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(request)
        metadata = payload.pop("metadata", None)
        return cls._merge_metadata(payload, metadata if isinstance(metadata, dict) else None)

    @classmethod
    def _request_context_to_payload(cls, request: RequestContext) -> Dict[str, Any]:
        if request.message is not None:
            payload = cls._message_to_payload(request.message)
        elif request.task is not None:
            payload = cls.from_a2a_response(request.task).model_dump(exclude_none=True)
        else:
            payload = {}

        if request.task_id and "task_id" not in payload:
            payload["task_id"] = request.task_id
        if request.context_id and "sessionId" not in payload:
            payload["sessionId"] = request.context_id
        return payload

    @classmethod
    def _send_message_request_to_payload(cls, request: SendMessageRequest) -> Dict[str, Any]:
        payload = cls._message_to_payload(request.message)
        return cls._merge_metadata(payload, cls._from_struct(getattr(request, "metadata", None)))

    @classmethod
    def _message_to_payload(cls, message: Message) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for part in message.parts or []:
            text = getattr(part, "text", None)
            if text is not None:
                payload["query"] = str(text)
                break
        if message.task_id:
            payload["task_id"] = message.task_id
        if message.context_id:
            payload["sessionId"] = message.context_id
        return cls._merge_metadata(payload, cls._from_struct(getattr(message, "metadata", None)))

    @staticmethod
    def _merge_metadata(payload: Dict[str, Any], metadata: Dict[str, Any] | None) -> Dict[str, Any]:
        if not metadata:
            return payload

        for key, value in metadata.items():
            if value is not None and key not in payload:
                payload[key] = value
        return payload

    @staticmethod
    def _to_ojw_status(status: Any) -> TaskStatus:
        if status is None:
            return TaskStatus.UNKNOWN

        if isinstance(status, int):
            try:
                status_name = A2ATaskStatus.Name(status)
            except ValueError:
                return TaskStatus.UNKNOWN
        else:
            status_name = str(status)

        return A2ATransformer._A2A_STATUS_TO_OJW_STATUS.get(status_name, TaskStatus.UNKNOWN)

    @staticmethod
    def _to_struct(data: Dict[str, Any]) -> Struct:
        struct = Struct()
        struct.update(data)
        return struct

    @staticmethod
    def _from_struct(struct: Struct) -> Dict[str, Any]:
        return {key: value for key, value in struct.items()} if struct else {}

    @staticmethod
    def _has_field(message: Any, field_name: str) -> bool:
        if message is None:
            return False

        has_field = getattr(message, "HasField", None)
        if callable(has_field):
            try:
                return has_field(field_name)
            except ValueError:
                pass

        return getattr(message, field_name, None) is not None

    @staticmethod
    def _build_agent_result(
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        status: TaskStatus | None = None,
        artifacts: list[Artifact] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task_id,
            sessionId=session_id,
            status=status,
            artifacts=artifacts or [],
            metadata=metadata or {},
        )
