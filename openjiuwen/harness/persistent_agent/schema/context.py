# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group-message and knowledge-material contracts; no storage or retrieval implementation."""

from datetime import timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .protocol import Digest, Identifier, InvocationRef, Nonnegative, ProtocolModel, Reason


def _is_session_output_path(path: str | None, session_id: str | None, file_name: str | None) -> bool:
    """Return whether a source file path points to this Session's final output file."""
    if path is None or session_id is None or file_name is None:
        return False

    parts = path.split("/")
    if len(parts) != 4:
        return False
    return all(
        (
            parts[0] == "sessions",
            parts[1] == session_id,
            parts[2] == "output",
            parts[3] == file_name,
            parts[3] not in ("", ".", ".."),
            not any(part in (".", "..") for part in parts),
            not any(char in path for char in ("\\", "\x00")),
        )
    )


def _is_safe_file_name(file_name: str | None) -> bool:
    """Return whether a value is a plain filename rather than a path or special name."""
    if file_name is None:
        return False
    return not any(char in file_name for char in ("/", "\\", "\x00", ":", "<", ">", '"', "|", "?", "*"))


def _is_current_session_import_path(path: str, session_id: str) -> bool:
    """Return whether an imported file path stays within the current Session."""
    parts = path.split("/")
    if len(parts) != 4:
        return False
    return all(
        (
            parts[0] == "sessions",
            parts[1] == session_id,
            parts[2] in ("output", "imports"),
            parts[3] not in ("", ".", ".."),
            not any(char in path for char in ("\\", "\x00", ":")),
            not any(char in parts[3] for char in ("<", ">", '"', "|", "?", "*")),
        )
    )


class GroupMessage(ProtocolModel):
    """One group message as authorized for this Session; recalled or unavailable messages carry no body."""

    message_id: Identifier
    sender_id: Identifier
    sender_name: Annotated[str, Field(min_length=1, max_length=512)]
    sender_kind: Literal["human", "agent"]
    received_at: AwareDatetime
    text: Annotated[str, Field(max_length=65536)] | None = None
    mention_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    reply_to: Identifier | None = None
    attachment_refs: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    # "unavailable": the message exists but its body cannot be read now; unlike "recalled", it may return.
    status: Literal["normal", "edited", "recalled", "unavailable"] = "normal"
    # Background only: a queued request must never become the current task's instruction.
    queued_request: bool = Field(default=False, strict=True)

    @field_validator("received_at")
    @classmethod
    def normalize_time(cls, value):
        """Normalize server receive time to UTC."""
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_recall(self) -> Self:
        """Never expose the body of a recalled message, nor invent one for an unavailable message."""
        if self.status in ("recalled", "unavailable") and self.text is not None:
            raise PydanticCustomError("persistent_context", f"{self.status} message cannot carry text")
        return self


class GroupMessageQuery(ProtocolModel):
    """Bounded search over authorized group messages and final Session artifacts."""

    query: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    keywords: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S")], ...],
        Field(max_length=16),
    ] = ()
    content_types: Annotated[
        tuple[Literal["messages", "files", "online_docs"], ...], Field(min_length=1, max_length=3)
    ] = ("messages", "files", "online_docs")
    platforms: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")], ...],
        Field(max_length=16),
    ] = ()
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    sender_id: Identifier | None = None
    cursor: Identifier | None = None
    limit: Annotated[int, Field(strict=True, gt=0, le=500)] = 50
    group_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    group_names: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")], ...], Field(max_length=128)
    ] = ()

    @field_validator("keywords")
    @classmethod
    def deduplicate_keywords(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Treat the keyword list as an unordered OR set, independent of casing and duplicates."""
        unique: dict[str, str] = {}
        for keyword in value:
            unique.setdefault(keyword.casefold(), keyword)
        return tuple(keyword for _, keyword in sorted(unique.items()))

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Legacy ``query`` and the batch ``keywords`` field share OR semantics."""
        terms = (() if self.query is None else (self.query,)) + self.keywords
        unique: dict[str, str] = {}
        for term in terms:
            unique.setdefault(term.casefold(), term)
        return tuple(unique.values())

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        """Require at least one criterion and an ordered time range."""
        has_query_criterion = self.query is not None or bool(self.keywords)
        has_time_criterion = self.since is not None or self.until is not None
        has_sender_criterion = self.sender_id is not None
        if not any(
            (
                has_query_criterion,
                has_time_criterion,
                has_sender_criterion,
                self.group_ids,
                self.group_names,
                self.platforms,
            )
        ):
            raise PydanticCustomError(
                "persistent_context",
                "group search requires a group, query, keywords, a time range, or sender",
            )
        if self.since is not None and self.until is not None and self.since > self.until:
            raise PydanticCustomError("persistent_context", "group search time range is reversed")
        if len(set(self.content_types)) != len(self.content_types):
            raise PydanticCustomError("persistent_context", "content_types must not contain duplicates")
        if self.sender_id is not None and "messages" not in self.content_types:
            raise PydanticCustomError("persistent_context", "sender_id only applies to message searches")
        return self


class GroupMessagesResult(InvocationRef):
    """Found, confirmed absent, or incomplete; incomplete never means "no messages"."""

    status: Literal["found", "empty", "incomplete"]
    messages: Annotated[tuple[GroupMessage, ...], Field(max_length=500)] = ()
    # Paging position for the next read; not a statement about how far the host scanned.
    next_cursor: Identifier | None = None
    # Message position the host confirms it scanned through, including messages whose body is unavailable.
    # None means no trusted progress: a reader keeps its previous cursor.
    scanned_through: Identifier | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_messages(self) -> Self:
        """Keep confirmed absence distinct from an unfinished or failed lookup."""
        if self.status == "found" and not self.messages:
            raise PydanticCustomError("persistent_context", "found group result requires messages")
        if self.status == "empty" and (self.messages or self.next_cursor is not None):
            raise PydanticCustomError("persistent_context", "empty group result must be a complete absence")
        if self.status == "incomplete" and self.reason is None:
            raise PydanticCustomError("persistent_context", "incomplete group result requires a reason")
        return self


class GroupMessageSearchHit(ProtocolModel):
    """An authorized historical message with an unambiguous source."""

    group_id: Identifier
    group_name: Annotated[str, Field(min_length=1, max_length=512)]
    platform: Identifier | None = None
    platform_chat_id: Identifier | None = None
    message_ref: Identifier
    message: GroupMessage


class GroupArtifactSearchHit(ProtocolModel):
    """A final file or online-document link from one authorized group Session.

    ``source_file_path`` is a host-only locator retained in the private approval snapshot.
    ``imported_path`` is populated only after the exact artifact is approved and staged in
    the current Session; the tool must never render the source locator to the model.
    """

    group_id: Identifier
    group_name: Annotated[str, Field(min_length=1, max_length=512)]
    platform: Identifier | None = None
    platform_chat_id: Identifier | None = None
    artifact_id: Identifier
    artifact_kind: Literal["file", "online_doc"]
    title: Annotated[str, Field(min_length=1, max_length=1024)]
    file_name: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    file_size: Nonnegative | None = None
    file_digest: Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")] | None = None
    source_session_id: Identifier | None = None
    source_file_path: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    url: Annotated[str, Field(max_length=4096)] | None = None
    snippet: Annotated[str, Field(max_length=4096)] | None = None
    created_at: AwareDatetime
    imported_path: Annotated[str, Field(min_length=1, max_length=4096)] | None = None

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        """Require a complete file reference or a validated online document link."""
        if self.artifact_kind == "file":
            file_metadata = (
                self.file_name,
                self.file_size,
                self.file_digest,
                self.source_session_id,
                self.source_file_path,
            )
            if any(value is None for value in file_metadata) or self.url is not None:
                raise PydanticCustomError("persistent_context", "file artifact reference is incomplete")
            if not _is_session_output_path(self.source_file_path, self.source_session_id, self.file_name):
                raise PydanticCustomError("persistent_context", "file artifact path is outside Session output")
            if not _is_safe_file_name(self.file_name):
                raise PydanticCustomError("persistent_context", "file artifact name is not a filename")
        else:
            if self.url is None:
                raise PydanticCustomError("persistent_context", "online document reference is incomplete")
            file_metadata = (
                self.file_name,
                self.source_file_path,
                self.file_size,
                self.file_digest,
                self.source_session_id,
            )
            if any(value is not None for value in file_metadata):
                raise PydanticCustomError("persistent_context", "online document reference is incomplete")
        return self


class GroupMessageSearchResult(InvocationRef):
    """Group-content search result; messages and artifacts keep separate typed collections."""

    status: Literal["found", "empty", "incomplete", "forbidden", "unavailable", "failed"]
    hits: Annotated[tuple[GroupMessageSearchHit, ...], Field(max_length=500)] = ()
    artifacts: Annotated[tuple[GroupArtifactSearchHit, ...], Field(max_length=500)] = ()
    next_cursor: Identifier | None = None
    query_complete: bool = Field(strict=True)
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        """Never present partial scans or failed authorization as confirmed absence."""
        complete = self.status in ("found", "empty")
        if self.query_complete != complete or (complete and self.next_cursor is not None):
            raise PydanticCustomError("persistent_context", "search status and completeness disagree")
        if self.status == "found" and not (self.hits or self.artifacts):
            raise PydanticCustomError("persistent_context", "found search requires hits")
        if self.status == "empty" and (self.hits or self.artifacts):
            raise PydanticCustomError("persistent_context", "empty search cannot contain hits")
        if not complete and self.reason is None:
            raise PydanticCustomError("persistent_context", "unfinished search requires a reason")
        has_search_payload = bool(self.hits or self.artifacts or self.next_cursor is not None)
        if self.status in ("forbidden", "unavailable", "failed") and has_search_payload:
            raise PydanticCustomError("persistent_context", "failed search cannot expose hits or cursor")
        for artifact in self.artifacts:
            if artifact.imported_path is None:
                continue
            if artifact.artifact_kind != "file" or not _is_current_session_import_path(
                artifact.imported_path, self.session_id
            ):
                raise PydanticCustomError("persistent_context", "imported file must stay in the current Session")
            parts = artifact.imported_path.split("/")
            if parts[2] == "output" and (
                artifact.source_session_id != self.session_id or artifact.source_file_path != artifact.imported_path
            ):
                raise PydanticCustomError("persistent_context", "same-Session output path does not match its source")
        return self


# Keep the descriptive content name available while preserving the PA-23 public class.
GroupContentSearchResult = GroupMessageSearchResult


class MaterialQuery(ProtocolModel):
    """Bounded query for authorized knowledge material."""

    query: Annotated[str, Field(min_length=1, max_length=4096)]
    source_ref: Identifier | None = None
    scope_ref: Identifier | None = None
    cursor: Identifier | None = None
    limit: Annotated[int, Field(strict=True, gt=0, le=500)] = 50


class MaterialSnapshot(ProtocolModel):
    """Versioned source evidence; it is a reference, not an authorization grant."""

    material_ref: Identifier
    source_ref: Identifier
    scope_ref: Identifier
    version_ref: Identifier
    content_ref: Identifier
    content_digest: Digest
    policy_revision: Identifier
    completeness: Literal["complete", "partial"]
    source_cursor: Identifier | None = None


class MaterialReadResult(InvocationRef):
    """Search/read outcome with explicit source versions and completeness."""

    status: Literal["found", "empty", "forbidden", "partial", "unavailable", "unknown"]
    items: Annotated[tuple[MaterialSnapshot, ...], Field(max_length=500)] = ()
    next_cursor: Identifier | None = None
    query_complete: bool = Field(default=False, strict=True)
    watermark: Nonnegative | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_material_result(self) -> Self:
        """Prevent incomplete or unavailable results from appearing empty."""
        if self.status == "found" and not self.items:
            raise PydanticCustomError("persistent_context", "found material result requires items")
        incomplete_absence = self.items or not self.query_complete or self.watermark is None
        if self.status == "empty" and incomplete_absence:
            raise PydanticCustomError("persistent_context", "empty material result requires complete absence")
        if (
            self.status == "partial"
            and self.next_cursor is None
            and not any(item.completeness == "partial" for item in self.items)
        ):
            raise PydanticCustomError("persistent_context", "partial material result needs an incomplete reference")
        if self.status in ("forbidden", "unavailable", "unknown") and (self.items or self.reason is None):
            raise PydanticCustomError("persistent_context", "failed material result requires a reason and no items")
        if self.query_complete != (self.watermark is not None):
            raise PydanticCustomError("persistent_context", "material completeness and watermark must be paired")
        return self
