# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host argument/result adaptation; all decisions use an already published key."""

from agent_shield.tool_permission import PermissionLevel, PermissionResult, get_permission_manager


def fail_closed_permission_callback(callback):
    """Keep permission failures terminal at the callback framework boundary."""
    from functools import wraps
    from openjiuwen.core.runner.callback.errors import AbortError

    @wraps(callback)
    async def guarded(*args, **kwargs):
        try:
            return await callback(*args, **kwargs)
        except AbortError:
            raise
        except Exception as exc:
            # Do not set AbortError.cause: trigger() would rethrow that ordinary
            # exception, which an outer callback chain could then swallow.
            raise AbortError("Permission validation failed; tool execution stopped") from exc

    return guarded


_RANK = {PermissionLevel.ALLOW: 0, PermissionLevel.UNDETERMINED: 1, PermissionLevel.ASK: 2, PermissionLevel.DENY: 3}
_FETCH_TOOLS = {"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"}


def merge_permission_results(results):
    """Keep the strongest decision and all SDK warnings/external paths."""
    strongest = max(results, key=lambda result: _RANK[result.permission])
    return PermissionResult(
        strongest.permission,
        strongest.matched_rule,
        strongest.reason,
        warnings=sorted({warning for result in results for warning in result.warnings or []}),
        external_paths=sorted({path for result in results for path in result.external_paths or []}),
        ask_accesses=sorted({
            item for result in results for item in (result.ask_accesses or [])
        }),
    )


async def check_tool_permission(tool_name, tool_args, key):
    """Expand a host fetch URL list into the SDK's scalar URL contract."""
    manager = get_permission_manager()
    if tool_name in _FETCH_TOOLS:
        urls = tool_args.get("url")
        if isinstance(urls, list):
            if not urls or any(not isinstance(url, str) or not url.strip() for url in urls):
                return PermissionResult(PermissionLevel.DENY, "host:invalid_urls", "Invalid fetch URL list")
            results = [await manager.check_permission(tool_name, {**tool_args, "url": url}, key) for url in urls]
            return merge_permission_results(results)
    return await manager.check_permission(tool_name, tool_args, key)
