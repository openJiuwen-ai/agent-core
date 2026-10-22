"""One desktop owner per OS user across tasks, agents and processes."""

import os
from pathlib import Path


class DesktopBusy(RuntimeError):
    """Another cooperating process already owns this user desktop."""


def invocation_owners(session, owner_session_id=None):
    """Read explicit SDK/host lineage for cancellation; opaque IDs are never parsed."""
    from openjiuwen.core.single_agent.rail.base import current_usage_attribution, current_usage_delegation

    identities = []
    for name in ("get_session_id", "get_parent_session_id"):
        getter = getattr(session, name, None)
        if callable(getter):
            identities.append(getter())
    for lineage in (current_usage_attribution(), current_usage_delegation()):
        identities.extend(lineage.get(key) for key in ("product_session_id", "parent_session_id"))
    if owner_session_id is not None:
        identities.append(owner_session_id())
    return frozenset(value for value in identities if isinstance(value, str) and value.strip())


class DesktopLease:
    """Nonblocking per-user file lock, held until every native executor stops."""

    def __init__(self, path: Path | None = None):
        # Production callers always use the same path; override is for isolated tests.
        # Fixed host path: launchd and shells may have different TMPDIR values.
        self.path = path or Path("/tmp") / f"openjiuwen-desktop-{os.getuid()}.lock"
        self.fd = None

    def acquire(self):
        """Acquire the existing lock inode or fail without queuing."""
        import fcntl

        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise PermissionError("desktop lease is not owned by this user")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise DesktopBusy("desktop is owned by another request") from exc
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def release(self):
        """Close ownership without unlinking the shared lock inode."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        # Do not unlink: another process may already be waiting on this inode.
