# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session state record: one JSON document per Session, decoded only into whitelisted native types.

Encoding failures happen while saving and are runtime errors. Decoding failures mean the stored
record is invalid; they raise a framework validation error whose message never carries record text.
"""

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry, ToolInterruptionState

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

RECORD_VERSION: Literal[1] = 1

# Window messages are restored only into these classes, chosen by role.
_MESSAGE_TYPES: dict[str, type[BaseMessage]] = {
    "user": UserMessage,
    "assistant": AssistantMessage,
    "tool": ToolMessage,
    "system": SystemMessage,
}


class RecordEnding(BaseModel):
    """How the Execution that saved the record ended; the end marker and the next load read it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: StrictStr
    request_id: StrictStr
    execution_id: StrictStr
    reply_target: StrictStr | None = None
    output_refs: list[StrictStr] = Field(default_factory=list)
    # Kept as handed over under the current contract; PA-08 decides how it is carried further.
    pending_answer: StrictStr | None = None
    last_group_message_id: StrictStr | None = None


class SessionStateRecord(BaseModel):
    """The whitelisted Session state of the latest saved Execution, written in one set."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = RECORD_VERSION
    # Raw identities; compared exactly, never normalised.
    agent_id: StrictStr
    session_id: StrictStr
    messages: list[dict[StrictStr, Any]]
    interruption: dict[StrictStr, Any] | None = None
    auto_confirm: dict[StrictStr, StrictBool] = Field(default_factory=dict)
    deepagent_state: dict[StrictStr, Any] | None = None
    ending: RecordEnding


def invalid_record(reason: str) -> BaseError:
    """The stored record cannot be used; the reason names a category, never record content."""
    return build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"session state record is invalid: {reason}")


def unsavable(reason: str, cause: BaseException | None = None) -> BaseError:
    """The current Session state cannot be saved as a record; a runtime error, nothing is written."""
    return build_error(
        StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=f"session state cannot be saved: {reason}", cause=cause
    )


def _request_types() -> dict[str, type["InterruptRequest"]]:
    """Interrupt request classes a record may hold, by their stable tag; matched by exact type."""
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
    from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRequest

    return {"interrupt_request": InterruptRequest, "ask_user_request": AskUserRequest}


def encode_messages(messages: list) -> list[dict]:
    """JSON form of the window; only the exact message class of each role is accepted."""
    encoded = []
    for message in messages:
        expected = _MESSAGE_TYPES.get(getattr(message, "role", None))
        # A subclass may carry fields its role class would drop, so only the exact class is saved.
        if expected is None or type(message) is not expected:  # pylint: disable=unidiomatic-typecheck
            raise unsavable(f"window message of type {type(message).__name__} is not supported")
        encoded.append(message.model_dump(mode="json"))
    return encoded


def decode_messages(items: list[dict]) -> list:
    """Restore the window; an unknown role or an unrestorable message makes the record invalid."""
    messages = []
    for item in items:
        role = item.get("role")
        message_type = _MESSAGE_TYPES.get(role) if isinstance(role, str) else None
        if message_type is None:
            raise invalid_record("window message has an unknown role")
        try:
            messages.append(message_type.model_validate(item))
        except (PydanticValidationError, TypeError, ValueError):
            # Validators may raise TypeError, which pydantic passes through; the record is still at fault.
            raise invalid_record("window message cannot be restored") from None
    return messages


def encode_interruption(state: Any) -> dict:
    """Encode each request with its type tag so subclass fields such as AskUserRequest.questions survive."""
    # Exact type: another interruption state would be stored with fields this record cannot carry.
    if type(state) is not ToolInterruptionState:  # pylint: disable=unidiomatic-typecheck
        raise unsavable(f"interruption state of type {type(state).__name__} is not supported")
    tags = {request_type: tag for tag, request_type in _request_types().items()}
    tools = {}
    for call_id, entry in state.interrupted_tools.items():
        if entry.is_sub_agent:
            raise unsavable("sub-agent interruptions are not supported")
        requests = {}
        for inner_id, request in entry.interrupt_requests.items():
            tag = tags.get(type(request))
            if tag is None:
                raise unsavable(f"interrupt request of type {type(request).__name__} is not supported")
            requests[inner_id] = {"type": tag, "data": request.model_dump(mode="json")}
        tools[call_id] = {"tool_call": entry.tool_call.model_dump(mode="json"), "interrupt_requests": requests}
    return {
        "ai_message": state.ai_message.model_dump(mode="json"),
        "iteration": state.iteration,
        "original_query": state.original_query,
        "interrupted_tools": tools,
        "auto_confirm_mapping": dict(state.auto_confirm_mapping),
    }


class _EncodedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: StrictStr
    data: dict[StrictStr, Any]


class _EncodedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tool_call: dict[StrictStr, Any]
    interrupt_requests: dict[StrictStr, _EncodedRequest]


class _EncodedInterruption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ai_message: dict[StrictStr, Any]
    iteration: StrictInt
    original_query: StrictStr
    interrupted_tools: dict[StrictStr, _EncodedEntry]
    auto_confirm_mapping: dict[StrictStr, StrictStr]


def decode_interruption(data: dict) -> ToolInterruptionState:
    """Restore the native state; an unknown request tag is invalid, never downgraded or imported."""
    types = _request_types()
    try:
        encoded = _EncodedInterruption.model_validate(data)
        tools = {}
        for call_id, entry in encoded.interrupted_tools.items():
            requests = {}
            for inner_id, request in entry.interrupt_requests.items():
                request_type = types.get(request.type)
                if request_type is None:
                    raise invalid_record("interrupt request has an unknown type")
                requests[inner_id] = request_type.model_validate(request.data)
            tools[call_id] = ToolInterruptEntry(
                tool_call=ToolCall.model_validate(entry.tool_call), interrupt_requests=requests
            )
        return ToolInterruptionState(
            ai_message=AssistantMessage.model_validate(encoded.ai_message),
            iteration=encoded.iteration,
            original_query=encoded.original_query,
            interrupted_tools=tools,
            auto_confirm_mapping=encoded.auto_confirm_mapping,
        )
    except (PydanticValidationError, TypeError, ValueError):
        raise invalid_record("interruption state cannot be restored") from None


def dump_record(record: SessionStateRecord) -> str:
    """Serialized record for one backend set."""
    return record.model_dump_json()


def parse_record(raw: str | bytes) -> SessionStateRecord:
    """Parse what the backend returned; pydantic errors stop here and never reach the entry."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise invalid_record("not valid JSON") from None
    if not isinstance(data, dict):
        raise invalid_record("not a JSON object")
    if data.get("schema_version") != RECORD_VERSION:
        raise invalid_record("unsupported schema version")
    try:
        return SessionStateRecord.model_validate(data)
    except PydanticValidationError:
        raise invalid_record("record structure is not valid") from None
