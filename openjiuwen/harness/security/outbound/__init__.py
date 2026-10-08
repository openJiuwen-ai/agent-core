# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host exit (P3): unified outbound HTTP for host-process tools.

Host-side outbound tools must send requests through this package instead of
calling ``requests`` / ``aiohttp`` directly, so ``permissions.net_guard`` is
enforced on every hop (initial URL and each redirect target)::

    from openjiuwen.harness.security.outbound import sync_client
    resp = sync_client.get(url, timeout=10)

    from openjiuwen.harness.security.outbound import async_client
    async with await async_client.request(session, "GET", url) as resp:
        ...
"""

from openjiuwen.harness.security.outbound.policy import (
    HOST_EXIT_EXEMPTIONS,
    HostExitState,
    OutboundBlockedError,
    check_mcp_server_url,
    check_outbound_url,
    describe_host_exit,
    get_host_exit_state,
    publish_host_exit_policy,
    reset_host_exit_policy,
)

__all__ = [
    "HOST_EXIT_EXEMPTIONS",
    "HostExitState",
    "OutboundBlockedError",
    "check_mcp_server_url",
    "check_outbound_url",
    "describe_host_exit",
    "get_host_exit_state",
    "publish_host_exit_policy",
    "reset_host_exit_policy",
]
