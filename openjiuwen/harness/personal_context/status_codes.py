"""Status values owned by the embedded PersonalContext package."""

from collections import namedtuple
from types import SimpleNamespace

from openjiuwen.core.common.exception.codes import StatusCode as FrameworkStatusCode

_PersonalContextStatus = namedtuple("_PersonalContextStatus", ("name", "code", "errmsg"))


def _personal_context_status(name: str, code: int, errmsg: str) -> _PersonalContextStatus:
    return _PersonalContextStatus(name, code, errmsg)


def build_error(status, *, msg=None, details=None, cause=None, **kwargs):
    """Build a framework error while resolving PersonalContext-owned statuses locally."""
    if isinstance(status, _PersonalContextStatus):
        from openjiuwen.core.common.exception.status_mapping import resolve_exception_class

        exception_type = resolve_exception_class(status)
        return exception_type(status, msg=msg, details=details, cause=cause, **kwargs)

    from openjiuwen.core.common.exception.errors import build_error as framework_build_error

    return framework_build_error(status, msg=msg, details=details, cause=cause, **kwargs)


# Keep the framework statuses available to existing PersonalContext helpers while keeping
# the seven PersonalContext-only values inside this newly added package directory.
StatusCode = SimpleNamespace(
    **{status.name: status for status in FrameworkStatusCode},
    CONTEXT_PROACTIVE_CONFIG_INVALID=_personal_context_status(
        "CONTEXT_PROACTIVE_CONFIG_INVALID", 154000, "context proactive_config is invalid, reason: {error_msg}"
    ),
    CONTEXT_PROACTIVE_STATE_INVALID=_personal_context_status(
        "CONTEXT_PROACTIVE_STATE_INVALID", 154001, "context proactive_state is invalid, reason: {error_msg}"
    ),
    CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR=_personal_context_status(
        "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR", 154002, "context proactive_file execution error, reason: {error_msg}"
    ),
    CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR=_personal_context_status(
        "CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR",
        154003,
        "context proactive_fetch execution error, reason: {error_msg}",
    ),
    CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR=_personal_context_status(
        "CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR",
        154004,
        "context proactive_pipeline execution error, reason: {error_msg}",
    ),
    CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR=_personal_context_status(
        "CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR",
        154005,
        "context proactive_publish execution error, reason: {error_msg}",
    ),
    CONTEXT_PROACTIVE_RUNTIME_TIMEOUT=_personal_context_status(
        "CONTEXT_PROACTIVE_RUNTIME_TIMEOUT",
        154006,
        "context proactive_runtime timeout ({timeout}s), reason: {error_msg}",
    ),
)


__all__ = ["StatusCode", "build_error"]
