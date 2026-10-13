# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable host-supplied invocation references for one Execution."""

import re
from dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


@dataclass(frozen=True, kw_only=True)
class ExecutionContext:
    """Immutable host-supplied identity and permission snapshots for one call."""

    namespace_id: str
    agent_id: str
    session_id: str
    request_id: str
    execution_id: str
    source_ref: str
    config_version: str
    config_fingerprint: str
    principal_ref: str
    startup_scope: frozenset[str]
    source_scope: frozenset[str]
    policy_revision: str
    capability_snapshot: frozenset[str]
    # Earlier Execution of the same Request that this one continues; None for a new Request.
    recovery_ref: str | None = None

    def __post_init__(self):
        """Validate identity and normalize immutable scope snapshots."""
        for name in (
            "namespace_id",
            "agent_id",
            "session_id",
            "request_id",
            "execution_id",
            "source_ref",
            "config_version",
            "principal_ref",
            "policy_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"missing {name}")
        if not isinstance(self.config_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", self.config_fingerprint):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid config_fingerprint")
        for name in ("startup_scope", "source_scope", "capability_snapshot"):
            values = getattr(self, name)
            if not isinstance(values, (set, frozenset)) or any(not isinstance(x, str) or not x.strip() for x in values):
                raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg=f"invalid {name}")
            object.__setattr__(self, name, frozenset(values))
        if not self.startup_scope or not self.source_scope:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="permission/source scope is required")
        if self.recovery_ref is not None and (not isinstance(self.recovery_ref, str) or not self.recovery_ref.strip()):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid recovery_ref")
        if self.recovery_ref == self.execution_id:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="recovery_ref must name an earlier Execution"
            )
