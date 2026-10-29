# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session state record: one JSON document per Session, decoded only into whitelisted native types.

Encoding failures happen while saving and are runtime errors. Decoding failures mean the stored
record is invalid; they raise a framework validation error whose message never carries record text.
"""

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import interrupt_kind_of
from openjiuwen.core.single_agent.interrupt.state import InterruptAnswer, ToolInterruptEntry, ToolInterruptionState

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
    spec_fingerprint: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reply_target: StrictStr | None = None
    output_refs: list[StrictStr] = Field(default_factory=list)


class RecordCheckpoint(BaseModel):
    """Safe active boundary; request_start has no completed ReAct iteration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: StrictStr
    execution_id: StrictStr
    iteration_seq: StrictInt = Field(ge=0)
    kind: Literal["react_iteration", "request_start", "rollover"]

    @model_validator(mode="after")
    def _validate_boundary(self):
        if self.kind == "request_start" and self.iteration_seq != 0:
            raise ValueError("request start has no completed iteration")
        if self.kind == "react_iteration" and self.iteration_seq < 1:
            raise ValueError("ReAct checkpoint requires a completed iteration")
        return self


class RecordRollover(BaseModel):
    """The latest committed transition from one archived window to the current one."""

    model_config = ConfigDict(extra="forbid", strict=True)

    old_window_id: StrictStr = Field(min_length=1)
    old_window_seq: StrictInt = Field(ge=1)
    new_window_id: StrictStr = Field(min_length=1)
    new_window_seq: StrictInt = Field(ge=2)
    archive_ref: StrictStr = Field(min_length=1)
    trigger_kind: Literal["agent", "capacity_fallback", "request_entry"]
    token_usage: dict[StrictStr, StrictInt | StrictFloat | StrictStr | StrictBool | None]

    @model_validator(mode="after")
    def _validate_transition(self):
        if self.old_window_id == self.new_window_id or self.new_window_seq != self.old_window_seq + 1:
            raise ValueError("rollover must identify one consecutive successor window")
        return self


class WindowArchivePayload(BaseModel):
    """Transition content supplied by the window manager; storage identity and reference are store-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    old_window_id: StrictStr = Field(min_length=1)
    old_window_seq: StrictInt = Field(ge=1)
    new_window_id: StrictStr = Field(min_length=1)
    new_window_seq: StrictInt = Field(ge=2)
    trigger_kind: Literal["agent", "capacity_fallback", "request_entry"]
    request_id: StrictStr
    execution_id: StrictStr
    spec_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    seed: dict[StrictStr, Any]
    retained_item_refs: list[StrictStr]
    token_usage: dict[StrictStr, StrictInt | StrictFloat | StrictStr | StrictBool | None]
    messages: list[dict[StrictStr, Any]]
    candidate_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    # Missing on existing archives: reconstruct their original temporary-seed format exactly.
    candidate_version: StrictInt = Field(default=1, ge=1, le=2)
    iteration_seq: StrictInt = Field(ge=0)
    last_group_message_id: StrictStr | None

    @model_validator(mode="after")
    def _validate_transition(self):
        if self.old_window_id == self.new_window_id or self.new_window_seq != self.old_window_seq + 1:
            raise ValueError("archive must identify one consecutive successor window")
        if (self.iteration_seq == 0) != (self.trigger_kind == "request_entry"):
            raise ValueError("only request-entry archives have zero completed iterations")
        return self


class RecordWindowArchive(WindowArchivePayload):
    """Session-scoped full old window plus the metadata needed to verify its transition."""

    agent_id: StrictStr
    session_id: StrictStr
    archive_ref: StrictStr = Field(min_length=1)
    created_at: AwareDatetime


class RecordArchiveIndexEntry(BaseModel):
    """Navigation-only summary for one stored archive."""

    model_config = ConfigDict(extra="forbid", strict=True)

    archive_ref: StrictStr
    old_window_seq: StrictInt = Field(ge=1)
    request_id: StrictStr
    execution_id: StrictStr
    created_at: AwareDatetime


class RecordArchiveIndex(BaseModel):
    """Rebuildable archive navigation index, never the source of archive contents."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    entries: list[RecordArchiveIndexEntry] = Field(default_factory=list)


class SessionHistorySearchQuery(BaseModel):
    """Bounded keyword search over one Session's archived windows."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: StrictStr = Field(min_length=1, max_length=4096)
    limit: StrictInt = Field(default=50, ge=1, le=500)

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class SessionHistorySearchHit(BaseModel):
    """One original text field matching a keyword in an archived message."""

    model_config = ConfigDict(extra="forbid", strict=True)

    archive_ref: StrictStr = Field(min_length=1)
    old_window_id: StrictStr = Field(min_length=1)
    old_window_seq: StrictInt = Field(ge=1)
    message_index: StrictInt = Field(ge=0)
    role: Literal["user", "assistant", "tool"]
    matched_field: StrictStr = Field(min_length=1)
    tool_name: StrictStr | None = None
    excerpt: StrictStr = Field(max_length=2048)
    excerpt_truncated: StrictBool


class SessionHistorySearchResult(BaseModel):
    """Search outcome; scan completeness is independent of display truncation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["found", "empty", "incomplete", "failed"]
    query_complete: StrictBool
    hits: list[SessionHistorySearchHit] = Field(default_factory=list)
    results_truncated: StrictBool = False
    reason: StrictStr | None = None

    @model_validator(mode="after")
    def _validate_outcome(self):
        if self.status in {"found", "empty"} and not self.query_complete:
            raise ValueError("found and empty require a complete query")
        if self.status == "found" and not self.hits:
            raise ValueError("found requires at least one hit")
        if self.status == "empty" and (self.hits or self.results_truncated):
            raise ValueError("empty cannot contain hits or truncated results")
        if self.status in {"incomplete", "failed"}:
            if self.query_complete or not self.reason:
                raise ValueError("incomplete and failed require an incomplete query and a reason")
        if self.status == "failed" and self.hits:
            raise ValueError("failed cannot contain hits")
        if self.status in {"found", "empty"} and self.reason is not None:
            raise ValueError("a complete query cannot contain a failure reason")
        return self


class SessionStateRecord(BaseModel):
    """The final V1 Session state: either one active checkpoint or one terminal ending."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = RECORD_VERSION
    # Raw identities; compared exactly, never normalised.
    agent_id: StrictStr
    session_id: StrictStr
    messages: list[dict[StrictStr, Any]]
    interruption: dict[StrictStr, Any] | None = None
    auto_confirm: dict[StrictStr, StrictBool] = Field(default_factory=dict)
    deepagent_state: dict[StrictStr, Any] | None = None
    checkpoint: RecordCheckpoint | None
    last_group_message_id: StrictStr | None
    current_window_id: StrictStr | None
    window_seq: StrictInt | None
    last_rollover: RecordRollover | None
    ending: RecordEnding | None

    @model_validator(mode="after")
    def _validate_lifecycle(self):
        if (self.checkpoint is None) == (self.ending is None):
            raise ValueError("exactly one of checkpoint and ending must be present")
        if (self.current_window_id is None) != (self.window_seq is None):
            raise ValueError("current window identity and sequence must be present together")
        if self.window_seq is not None and self.window_seq < 1:
            raise ValueError("window sequence must be positive")
        if self.last_rollover is not None:
            if (
                self.current_window_id != self.last_rollover.new_window_id
                or self.window_seq != self.last_rollover.new_window_seq
            ):
                raise ValueError("last rollover must point at the current window")
        if (
            self.ending is not None
            and self.ending.outcome == "awaiting_confirmation"
            and self.ending.spec_fingerprint is None
        ):
            raise ValueError("a waiting confirmation must identify its published Spec")
        if self.checkpoint is not None and self.checkpoint.kind == "rollover" and self.last_rollover is None:
            raise ValueError("a rollover checkpoint requires rollover metadata")
        if self.checkpoint is not None and self.checkpoint.kind == "rollover":
            if (self.checkpoint.iteration_seq == 0) != (self.last_rollover.trigger_kind == "request_entry"):
                raise ValueError("rollover boundary does not match its trigger")
        return self


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


def _encode_request(request, tags: dict) -> dict:
    tag = tags.get(type(request))
    if tag is None:
        raise unsavable(f"interrupt request of type {type(request).__name__} is not supported")
    return {"type": tag, "data": request.model_dump(mode="json")}


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
            requests[inner_id] = _encode_request(request, tags)
        carried = {}
        for inner_id, answers in entry.carried_answers.items():
            carried[inner_id] = {}
            for owner, answer in answers.items():
                if owner != answer.owner or answer.request.interrupt_owner != owner:
                    raise unsavable("carried interrupt answer owner does not match")
                carried[inner_id][owner] = {
                    **answer.model_dump(mode="json", exclude={"request"}),
                    "request": _encode_request(answer.request, tags),
                }
        tools[call_id] = {
            "tool_call": entry.tool_call.model_dump(mode="json"), "interrupt_requests": requests,
            "carried_answers": carried,
        }
    return {
        "ai_message": state.ai_message.model_dump(mode="json"),
        "iteration": state.iteration,
        "original_query": state.original_query,
        "interrupted_tools": tools,
    }


class _EncodedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: StrictStr
    data: dict[StrictStr, Any]


class _EncodedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    owner: StrictStr
    kind: StrictStr
    value: Any
    request: _EncodedRequest
    carried: StrictBool = False


class _EncodedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tool_call: dict[StrictStr, Any]
    interrupt_requests: dict[StrictStr, _EncodedRequest]
    carried_answers: dict[StrictStr, dict[StrictStr, _EncodedAnswer]] = Field(default_factory=dict)


class _EncodedInterruption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ai_message: dict[StrictStr, Any]
    iteration: StrictInt
    original_query: StrictStr
    interrupted_tools: dict[StrictStr, _EncodedEntry]
    # Legacy field from before ToolInterruptionState dropped auto_confirm_mapping;
    # session-scoped remembers live on SessionStateRecord.auto_confirm instead.
    auto_confirm_mapping: dict[StrictStr, StrictStr] = Field(default_factory=dict)


def _decode_request(encoded: _EncodedRequest, types: dict, call_id: str):
    request_type = types.get(encoded.type)
    if request_type is None:
        raise invalid_record("interrupt request has an unknown type")
    request = request_type.model_validate(encoded.data)
    # JSON loaders do not run native's pickle-only legacy upgrade hook.
    if "interrupt_id" not in encoded.data:
        request.interrupt_id = f"intr_{call_id}"
    if not request.interrupt_owner and request.metadata.get("kind") == "group_share":
        from ..tools.group_history import GROUP_SHARE_INTERRUPT_OWNER

        request.interrupt_owner = GROUP_SHARE_INTERRUPT_OWNER
        request.interrupt_kind = "confirm"
    elif "interrupt_owner" not in encoded.data:
        # Restore only recognizable built-in legacy requests. Unknown custom
        # questions must be asked again, rather than lending an answer to a rail.
        owner = kind = ""
        if encoded.type == "ask_user_request":
            owner = "openjiuwen.harness.rails.interrupt.ask_user_rail.AskUserRail"
            kind = "ask_user"
        elif {"ask_category", "ask_title", "ask_summary", "matched_rule"} <= request.metadata.keys():
            owner = "openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail"
            kind = "permission"
        elif request.auto_confirm_key:
            from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload

            if request.payload_schema == ConfirmPayload.to_schema():
                owner = "openjiuwen.harness.rails.interrupt.confirm_rail.ConfirmInterruptRail"
                kind = "confirm"
        if owner:
            request.interrupt_owner = owner
            request.interrupt_kind = request.interrupt_kind or kind
    return request


def decode_interruption(data: dict) -> ToolInterruptionState:
    """Restore the native state; an unknown request tag is invalid, never downgraded or imported."""
    types = _request_types()
    try:
        encoded = _EncodedInterruption.model_validate(data)
        tools = {}
        for call_id, entry in encoded.interrupted_tools.items():
            requests = {}
            for inner_id, request in entry.interrupt_requests.items():
                requests[inner_id] = _decode_request(request, types, inner_id)
            carried = {}
            for inner_id, answers in entry.carried_answers.items():
                carried[inner_id] = {}
                for owner, answer in answers.items():
                    request = _decode_request(answer.request, types, inner_id)
                    if (
                        owner != answer.owner or request.interrupt_owner != owner
                        or interrupt_kind_of(request) != answer.kind
                    ):
                        raise invalid_record("carried interrupt answer ownership does not match")
                    carried[inner_id][owner] = InterruptAnswer(
                        owner=owner, kind=answer.kind, value=answer.value, request=request, carried=answer.carried,
                    )
            tools[call_id] = ToolInterruptEntry(
                tool_call=ToolCall.model_validate(entry.tool_call),
                interrupt_requests=requests, carried_answers=carried,
            )
        return ToolInterruptionState(
            ai_message=AssistantMessage.model_validate(encoded.ai_message),
            iteration=encoded.iteration,
            original_query=encoded.original_query,
            interrupted_tools=tools,
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
