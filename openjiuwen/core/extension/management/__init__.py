# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Core Handler orchestration for the nine package management events.

``events.builtins.event_types`` registers the nine event types and forwards
each call to :func:`dispatch_management_event`. That import happens inside the
handler body, so the events package does not import extension while it loads.
"""

from collections.abc import Awaitable, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.management.context import (
    ManagementContext,
    bind_context,
    context_for,
    replace_context,
    unbind_context,
)
from openjiuwen.core.extension.management.handlers import (
    handle_extension_apply,
    handle_extension_plan,
    handle_extension_rollback,
    handle_package_install,
    handle_package_parse,
    handle_package_query,
    handle_package_reload,
    handle_package_resolve,
    handle_package_uninstall,
)
from openjiuwen.core.extension.management.registration import ensure_management_events
from openjiuwen.core.extension.management.types import (
    EXTENSION_APPLY_EVENT,
    EXTENSION_PLAN_EVENT,
    EXTENSION_ROLLBACK_EVENT,
    MANAGEMENT_EVENTS,
    PACKAGE_INSTALL_EVENT,
    PACKAGE_PARSE_EVENT,
    PACKAGE_QUERY_EVENT,
    PACKAGE_RELOAD_EVENT,
    PACKAGE_RESOLVE_EVENT,
    PACKAGE_UNINSTALL_EVENT,
    ApplyRequest,
    InstallRequest,
    PackageOperationResult,
    ParsedPackage,
    ParseRequest,
    ParseResult,
    PlanRequest,
    PlanResult,
    QueryRequest,
    QueryResult,
    ReloadRequest,
    ResolveRequest,
    ResolveResult,
    RollbackRequest,
    RollbackResult,
    UninstallRequest,
)

_ROUTES: dict[str, tuple[Callable[..., Awaitable[object]], type]] = {
    PACKAGE_PARSE_EVENT: (handle_package_parse, ParseRequest),
    PACKAGE_RESOLVE_EVENT: (handle_package_resolve, ResolveRequest),
    PACKAGE_QUERY_EVENT: (handle_package_query, QueryRequest),
    PACKAGE_INSTALL_EVENT: (handle_package_install, InstallRequest),
    PACKAGE_UNINSTALL_EVENT: (handle_package_uninstall, UninstallRequest),
    PACKAGE_RELOAD_EVENT: (handle_package_reload, ReloadRequest),
    EXTENSION_PLAN_EVENT: (handle_extension_plan, PlanRequest),
    EXTENSION_APPLY_EVENT: (handle_extension_apply, ApplyRequest),
    EXTENSION_ROLLBACK_EVENT: (handle_extension_rollback, RollbackRequest),
}


async def dispatch_management_event(event_type: str, event_input: object) -> object:
    """Run the Core Handler of one management event.

    Raises:
        ExtensionFailure: The event type is not a management event, or the
            input is not the request object that event accepts.
    """
    route = _ROUTES.get(event_type)
    if route is None:
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"not a package management event: {event_type}",
        )
    handler, request_type = route
    if not isinstance(event_input, request_type):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"{event_type} needs a {request_type.__name__} input",
        )
    return await handler(event_input)


__all__ = [
    "EXTENSION_APPLY_EVENT",
    "EXTENSION_PLAN_EVENT",
    "EXTENSION_ROLLBACK_EVENT",
    "MANAGEMENT_EVENTS",
    "PACKAGE_INSTALL_EVENT",
    "PACKAGE_PARSE_EVENT",
    "PACKAGE_QUERY_EVENT",
    "PACKAGE_RELOAD_EVENT",
    "PACKAGE_RESOLVE_EVENT",
    "PACKAGE_UNINSTALL_EVENT",
    "ApplyRequest",
    "InstallRequest",
    "ManagementContext",
    "PackageOperationResult",
    "ParseRequest",
    "ParseResult",
    "ParsedPackage",
    "PlanRequest",
    "PlanResult",
    "QueryRequest",
    "QueryResult",
    "ReloadRequest",
    "ResolveRequest",
    "ResolveResult",
    "RollbackRequest",
    "RollbackResult",
    "UninstallRequest",
    "bind_context",
    "context_for",
    "replace_context",
    "dispatch_management_event",
    "ensure_management_events",
    "handle_extension_apply",
    "handle_extension_plan",
    "handle_extension_rollback",
    "handle_package_install",
    "handle_package_parse",
    "handle_package_query",
    "handle_package_reload",
    "handle_package_resolve",
    "handle_package_uninstall",
    "unbind_context",
]
