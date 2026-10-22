"""Read-only interactive-session prerequisite; native frameworks are injected."""

from typing import Any


def interactive_session_status(quartz: Any) -> tuple[bool, str]:
    """TCC permission and connected screens are insufficient while the OS is locked."""
    try:
        session = quartz.CGSessionCopyCurrentDictionary()
        if session is None:
            return False, "macOS login session state is unavailable"
        if session.get("CGSSessionScreenIsLocked", False):
            return False, "macOS desktop is locked; unlock it and start a new controlled session"
        if not session.get("kCGSSessionOnConsoleKey") or not session.get("kCGSessionLoginDoneKey"):
            return False, "an active, logged-in macOS console session is required"
    except (AttributeError, TypeError, RuntimeError):
        return False, "macOS login session state is unavailable"
    return True, "ready"


def require_interactive_session(quartz: Any) -> None:
    """Stop before capture or input; never use this guard for held-input release."""
    ready, reason = interactive_session_status(quartz)
    if not ready:
        raise PermissionError(reason)
