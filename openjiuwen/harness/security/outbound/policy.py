# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host exit (P3) policy registry.

P3 does not own a policy of its own: it enforces ``permissions.net_guard`` at
the moment the host process opens a connection. The policy is process-wide, so
only the host application publishes it (on config load and on config writes),
from the same permissions config its :class:`PermissionEngine` instances use.
Engines themselves never publish: any engine built from a stale or partial
config would otherwise overwrite the policy for the whole process.

States:

* ``unset``        no host has published a policy yet → pass through
* ``disabled``     ``net_guard`` missing or ``enabled: false`` → pass through
* ``not_enforced`` ``enforce_host_exit: false`` → pass through, warn
* ``active``       enforce net_guard rules
* ``error``        policy failed to build → fail-closed, every request denied
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from openjiuwen.harness.security.outbound.limits import (
    DEFAULT_MAX_REQUEST_MB,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_REQUEST_MB,
    MAX_TIMEOUT_SECONDS,
    MAX_URL_CHARS,
    parse_positive_int,
    request_limit_bytes,
)
from openjiuwen.harness.security.permission_engine.netguard.net_guard import (
    NetGuardChecker,
    url_hostname,
)

logger = logging.getLogger(__name__)

STATE_UNSET = "unset"
STATE_DISABLED = "disabled"
STATE_NOT_ENFORCED = "not_enforced"
STATE_ACTIVE = "active"
STATE_ERROR = "error"

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Code-side constant on purpose: making exemptions configurable would let a
# wildcard entry switch the whole layer off.
HOST_EXIT_EXEMPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "model_inference",
        "description": "模型推理端点（LLM / Embedding 客户端自带 HTTP 栈，由模型配置决定目标）",
    },
    {
        "id": "billing_report",
        "description": "计费上报（billing_client，trust_env=False 直连固定端点）",
    },
    {
        "id": "np_pipe",
        "description": "np:// 本地管道（llm_np_patch，不经过 TCP 出网）",
    },
    {
        "id": "mcp_loopback",
        "description": "host 为 localhost 或回环 IP 字面量的 http(s) MCP 服务端点（数据不离开本机；其余端点、非 http(s) 协议照常校验，策略构建失败时不豁免）",
    },
)


class OutboundBlockedError(PermissionError):
    """Raised when the host exit refuses a connection."""

    def __init__(self, url: str, reason: str, matched_rule: str | None = None):
        self.url = url
        self.reason = reason
        self.matched_rule = matched_rule
        rule = f" ({matched_rule})" if matched_rule else ""
        super().__init__(f"outbound blocked: {reason}{rule}: {url}")


@dataclass(frozen=True)
class OutboundRequestPolicy:
    """Immutable effective limits published together with net_guard rules."""

    max_request_mb: int = DEFAULT_MAX_REQUEST_MB
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def max_request_bytes(self) -> int:
        return request_limit_bytes(self.max_request_mb)


@dataclass(frozen=True)
class HostExitState:
    mode: str
    checker: NetGuardChecker | None = None
    policy: OutboundRequestPolicy | None = None
    error: str | None = None


_lock = threading.Lock()
_state = HostExitState(mode=STATE_UNSET)
_warned_not_enforced = False


def get_host_exit_state() -> HostExitState:
    return _state


def _set_state(state: HostExitState) -> None:
    global _state, _warned_not_enforced
    with _lock:
        _state = state
        if state.mode == STATE_NOT_ENFORCED:
            if not _warned_not_enforced:
                logger.warning(
                    "[HostExit] net_guard.enforce_host_exit=false: host outbound HTTP is NOT enforced"
                )
                _warned_not_enforced = True
        else:
            _warned_not_enforced = False
    logger.info("[HostExit] host_exit.state mode=%s error=%s", state.mode, state.error)


def publish_host_exit_policy(permissions: Mapping[str, Any] | None) -> HostExitState:
    """Publish ``permissions.net_guard`` as the host exit policy.

    Configs without a ``net_guard`` key leave the current state untouched so
    a partial config cannot clear the active policy.
    """
    if not isinstance(permissions, Mapping) or "net_guard" not in permissions:
        return _state
    section = permissions.get("net_guard")
    try:
        if not isinstance(section, Mapping) or not section.get("enabled"):
            state = HostExitState(mode=STATE_DISABLED)
        else:
            checker = NetGuardChecker(section)
            mode = STATE_ACTIVE if checker.enforce_host_exit else STATE_NOT_ENFORCED
            policy = OutboundRequestPolicy(
                max_request_mb=parse_positive_int(
                    section.get("max_request_mb"),
                    name="net_guard.max_request_mb",
                    default=DEFAULT_MAX_REQUEST_MB,
                    maximum=MAX_REQUEST_MB,
                ),
                timeout_seconds=parse_positive_int(
                    section.get("timeout_seconds"),
                    name="net_guard.timeout_seconds",
                    default=DEFAULT_TIMEOUT_SECONDS,
                    maximum=MAX_TIMEOUT_SECONDS,
                ),
            )
            state = HostExitState(mode=mode, checker=checker, policy=policy)
    except Exception as exc:
        logger.exception("[HostExit] host_exit.policy_build_failed")
        state = HostExitState(mode=STATE_ERROR, error=str(exc) or type(exc).__name__)
    _set_state(state)
    return state


def reset_host_exit_policy() -> None:
    """Back to ``unset`` (tests / host shutdown)."""
    _set_state(HostExitState(mode=STATE_UNSET))


def check_outbound_url(url: str, *, is_redirect: bool = False) -> None:
    """Validate one hop before connecting; raise :class:`OutboundBlockedError` on deny."""
    state = _state
    if state.mode == STATE_ERROR:
        raise OutboundBlockedError(url, f"host exit policy unavailable: {state.error}", "host_exit:error")
    if state.mode != STATE_ACTIVE or state.checker is None:
        return
    if len(url) > MAX_URL_CHARS:
        raise OutboundBlockedError(url, "URL exceeds internal length limit", "host_exit:url_size")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise OutboundBlockedError(url, f"scheme not allowed: {scheme or '<none>'}", "host_exit:scheme")
    if parsed.username is not None or parsed.password is not None:
        raise OutboundBlockedError(url, "URL userinfo is not allowed", "host_exit:userinfo")
    host = url_hostname(url)
    if not host:
        raise OutboundBlockedError(url, "missing host", "host_exit:host")
    result = state.checker.check_url(url)
    if result is not None and result.is_denied:
        logger.warning("[HostExit] host_exit.deny url=%s matched_rule=%s", url, result.matched_rule)
        raise OutboundBlockedError(url, result.reason or "net_guard denied", result.matched_rule)
    if result is not None and result.needs_approval and is_redirect:
        # The tool approval covers its input URL, not an arbitrary server-chosen
        # destination. The synchronous exit cannot open a permission interrupt.
        raise OutboundBlockedError(
            url, "Redirect target requires approval; request this URL in a separate tool call",
            result.matched_rule,
        )


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_mcp_server_url(url: str) -> None:
    """Host exit check for remote MCP endpoints.

    The ``mcp_loopback`` exemption only covers http(s) endpoints while the policy
    is ``active``: other schemes and the fail-closed ``error`` state are still
    decided by :func:`check_outbound_url`.
    """
    mode = _state.mode
    if mode not in (STATE_ACTIVE, STATE_ERROR):
        return
    scheme = (urlparse(url).scheme or "").lower()
    if mode == STATE_ACTIVE and scheme in _ALLOWED_SCHEMES and _is_loopback_host(url_hostname(url)):
        return
    check_outbound_url(url)


def describe_host_exit() -> dict[str, Any]:
    """Read-only snapshot for RPC / UI."""
    state = _state
    return {
        "mode": state.mode,
        "enforced": state.mode in (STATE_ACTIVE, STATE_ERROR),
        "error": state.error,
        "limits_active": state.mode == STATE_ACTIVE and state.policy is not None,
        "limits": (
            {
                "max_request_mb": state.policy.max_request_mb,
                "timeout_seconds": state.policy.timeout_seconds,
            }
            if state.policy is not None
            else None
        ),
        "exemptions": [dict(e) for e in HOST_EXIT_EXEMPTIONS],
    }


__all__ = [
    "HOST_EXIT_EXEMPTIONS",
    "STATE_ACTIVE",
    "STATE_DISABLED",
    "STATE_ERROR",
    "STATE_NOT_ENFORCED",
    "STATE_UNSET",
    "HostExitState",
    "OutboundBlockedError",
    "OutboundRequestPolicy",
    "check_mcp_server_url",
    "check_outbound_url",
    "describe_host_exit",
    "get_host_exit_state",
    "publish_host_exit_policy",
    "reset_host_exit_policy",
]
