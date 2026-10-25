# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-supplied authorization scope for PA-14 workspace reads."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from ..schema.execution import ExecutionContext

WorkspaceReadStatus = Literal["allowed", "forbidden", "unknown"]
WorkspaceWriteStatus = Literal["allowed", "forbidden", "unknown"]
PermissionProfile = Literal["default", "automatic", "full_access"]


@dataclass(frozen=True, kw_only=True)
class WorkspaceReadScope:
    """Immutable host decision for one Agent/Session workspace read scope."""

    status: WorkspaceReadStatus
    agent_id: str
    session_id: str
    workspace_root: Path
    allowed_roots: tuple[Path, ...]
    private_sessions_root: Path | None
    current_session_root: Path | None
    source_ref: str
    policy_revision: str
    host_wide: bool = False
    principal_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate shape only; do not contact the host or filesystem."""
        if self.status not in {"allowed", "forbidden", "unknown"}:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace access status")
        for name in ("agent_id", "session_id", "source_ref", "policy_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"missing workspace {name}")
        if self.principal_ref is not None and (
            not isinstance(self.principal_ref, str) or not self.principal_ref.strip()
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace principal_ref")
        if self.reason is not None and not isinstance(self.reason, str):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace reason")
        if not isinstance(self.host_wide, bool):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid host-wide workspace flag")
        if not isinstance(self.workspace_root, (str, Path)):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace root")
        if not isinstance(self.allowed_roots, (tuple, list)) or any(
            not isinstance(root, (str, Path)) for root in self.allowed_roots
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace allowed roots")
        for name in ("private_sessions_root", "current_session_root"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, (str, Path)):
                raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"invalid {name}")

        object.__setattr__(self, "workspace_root", Path(self.workspace_root))
        object.__setattr__(self, "allowed_roots", tuple(Path(root) for root in self.allowed_roots))
        if self.private_sessions_root is not None:
            object.__setattr__(self, "private_sessions_root", Path(self.private_sessions_root))
        if self.current_session_root is not None:
            object.__setattr__(self, "current_session_root", Path(self.current_session_root))


@runtime_checkable
class WorkspaceAccessPort(Protocol):
    """Host contract that resolves the current call's workspace read scope."""

    async def resolve_read_scope(self, execution: ExecutionContext) -> WorkspaceReadScope:
        """Resolve current Agent/Session authorization without performing a file read."""
        ...


def validate_read_scope(
    scope: WorkspaceReadScope,
    execution: ExecutionContext,
    *,
    permission_profile: PermissionProfile = "default",
) -> WorkspaceReadScope:
    """Reject a scope that is not bound to the current immutable Execution identity."""
    if not isinstance(scope, WorkspaceReadScope):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace access scope")
    if (
        scope.agent_id != execution.agent_id
        or scope.session_id != execution.session_id
        or (scope.principal_ref is not None and scope.principal_ref != execution.principal_ref)
        or scope.policy_revision != execution.policy_revision
    ):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="workspace access scope mismatch")
    if permission_profile not in {"default", "automatic", "full_access"} or scope.host_wide != (
        permission_profile == "full_access"
    ):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="workspace access profile mismatch")
    return scope


@dataclass(frozen=True, kw_only=True)
class WorkspaceWriteScope:
    """Immutable host decision for one Agent/Session workspace write scope."""

    status: WorkspaceWriteStatus
    agent_id: str
    session_id: str
    workspace_root: Path
    allowed_write_roots: tuple[Path, ...]
    private_sessions_root: Path | None
    current_session_root: Path | None
    source_ref: str
    policy_revision: str
    host_wide: bool = False
    principal_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate shape only; do not contact the host or filesystem."""
        if self.status not in {"allowed", "forbidden", "unknown"}:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="invalid workspace write status",
            )
        for name in ("agent_id", "session_id", "source_ref", "policy_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg=f"missing workspace {name}",
                )
        if self.principal_ref is not None and (
            not isinstance(self.principal_ref, str) or not self.principal_ref.strip()
        ):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="invalid workspace principal_ref",
            )
        if self.reason is not None and not isinstance(self.reason, str):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace reason")
        if not isinstance(self.host_wide, bool):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="invalid host-wide workspace write flag",
            )
        if not isinstance(self.workspace_root, (str, Path)):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace root")
        if not isinstance(self.allowed_write_roots, (tuple, list)) or any(
            not isinstance(root, (str, Path)) for root in self.allowed_write_roots
        ):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="invalid workspace write roots",
            )
        for name in ("private_sessions_root", "current_session_root"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, (str, Path)):
                raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"invalid {name}")

        object.__setattr__(self, "workspace_root", Path(self.workspace_root))
        object.__setattr__(self, "allowed_write_roots", tuple(Path(root) for root in self.allowed_write_roots))
        if self.private_sessions_root is not None:
            object.__setattr__(self, "private_sessions_root", Path(self.private_sessions_root))
        if self.current_session_root is not None:
            object.__setattr__(self, "current_session_root", Path(self.current_session_root))


@runtime_checkable
class WorkspaceWriteAccessPort(Protocol):
    """Host contract that resolves the current call's workspace write scope."""

    async def resolve_write_scope(self, execution: ExecutionContext) -> WorkspaceWriteScope:
        """Resolve current Agent/Session authorization without performing a file write."""
        ...


def validate_write_scope(
    scope: WorkspaceWriteScope,
    execution: ExecutionContext,
    *,
    permission_profile: PermissionProfile = "default",
) -> WorkspaceWriteScope:
    """Reject a scope that is not bound to the current immutable Execution identity."""
    if not isinstance(scope, WorkspaceWriteScope):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid workspace write scope")
    if (
        scope.agent_id != execution.agent_id
        or scope.session_id != execution.session_id
        or (scope.principal_ref is not None and scope.principal_ref != execution.principal_ref)
        or scope.policy_revision != execution.policy_revision
    ):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="workspace write scope mismatch")
    if permission_profile not in {"default", "automatic", "full_access"} or scope.host_wide != (
        permission_profile == "full_access"
    ):
        raise build_error(
            StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
            error_msg="workspace write profile mismatch",
        )
    return scope


__all__ = [
    "WorkspaceAccessPort",
    "WorkspaceReadScope",
    "WorkspaceWriteAccessPort",
    "WorkspaceWriteScope",
    "validate_read_scope",
    "validate_write_scope",
]
