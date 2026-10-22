"""Shared-window macOS navigation; frameworks and native operations are injected."""

# Worker collaborators intentionally share private input guards.
# pylint: disable=protected-access

from __future__ import annotations

import base64
import hashlib
import io
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, replace

if __package__:
    from .accessibility import AccessibilityTree
    from .errors import InputNotStarted
    from .macos_launch import launch_application
    from .macos_session import require_interactive_session
    from .modal_sharing import OpenedDialogs
    from .navigation import Surface, WindowCatalog, contains
    from .schema import Observation
    from .session_contract import DesktopObservationContext, DesktopRect, DesktopSessionScope, ScreenshotGeometry
    from .window_sharing import SharedWindows
else:
    from accessibility import AccessibilityTree  # pylint: disable=import-error
    from errors import InputNotStarted  # pylint: disable=import-error
    from macos_launch import launch_application  # pylint: disable=import-error
    from macos_session import require_interactive_session  # pylint: disable=import-error
    from modal_sharing import OpenedDialogs  # pylint: disable=import-error
    from navigation import Surface, WindowCatalog, contains  # pylint: disable=import-error
    from schema import Observation  # pylint: disable=import-error
    from session_contract import DesktopObservationContext, DesktopRect, DesktopSessionScope, ScreenshotGeometry  # pylint: disable=import-error
    from window_sharing import SharedWindows  # pylint: disable=import-error


class ObservationChanged(RuntimeError):
    """Discard an unstable capture; only observation may be retried, never input."""


class FocusRequired(RuntimeError):
    """No shared window owns input; recoverable only before an action starts."""


class MacOSNavigation:
    """Pinned application instances, live windows and one selected-display coordinate space."""

    def __init__(self, desktop):
        self.desktop = desktop
        self.reader = AccessibilityTree(desktop.ax, equal=getattr(desktop, "ax_equal", None))
        self.instances = {}
        self.names = {}
        self.sharing = SharedWindows()
        self.dialogs = OpenedDialogs(self)
        self.scope = DesktopSessionScope(
            session_id=uuid.uuid4().hex,
            display_id=str(desktop.display_id),
            application_ids=(),
        )
        self.catalog = WindowCatalog(self.scope.display_id, {}, self.names)
        for name, target in desktop.targets.items():
            app = desktop.application(name, allow_missing=True) if target.allow_launch else desktop.application(name)
            if app is not None:
                if target.allow_launch:
                    raise PermissionError("application is already running; explicitly share its window before use")
                self.bind(name, app)
        self.expected = None
        self.display = None
        self.front_pid = None
        self.focused_rect = None
        self.focus_source = ""
        self.last_active_window_id = None
        self._capture_prepared = False
        self.typing = False
        self.typed_keys = 0

    def bind(self, name, app):
        """Pin an approved process once; never replace its identity after restart."""
        identity = self.identity(app)
        if name in self.instances:
            if self.instances[name] != identity:
                raise RuntimeError("approved application instance changed; start a new session")
            return
        if identity[0] in self.names:
            raise ValueError("navigation targets must identify distinct application instances")
        application_id = uuid.uuid4().hex
        self.instances[name] = identity
        self.names[identity[0]] = name
        self.catalog.names = self.names
        self.catalog.applications[identity[0]] = application_id
        self.scope = self.scope.model_copy(update={"application_ids": (*self.scope.application_ids, application_id)})
        # The monitor only reads this immutable PID snapshot, never app registrations.
        self.desktop.monitor_pids = frozenset(self.names)

    @staticmethod
    def identity(app):
        """Resolve a live PID and process creation time."""
        date = app.launchDate()
        if app.isTerminated():
            raise RuntimeError("cannot bind live application launch identity")
        pid = int(app.processIdentifier())
        if date is not None:
            return pid, float(date.timeIntervalSince1970())
        # Unbundled Cocoa apps may have no NSRunningApplication launchDate.
        # Create a fresh Process on every lookup: create_time is instance-cached.
        import psutil

        return pid, float(psutil.Process(pid).create_time())

    def applications(self):
        """Revalidate every pinned application before navigation."""
        apps = {}
        for name, identity in self.instances.items():
            app = self.desktop.application(name)
            if self.identity(app) != identity:
                raise RuntimeError("approved application instance changed; start a new session")
            apps[identity[0]] = app
        return apps

    def attr(self, element, name, default=None):
        # AX is an optional metadata source. Required identity/geometry comes from Quartz.
        """Read optional accessibility metadata through the bounded reader."""
        return self.reader.attr(element, name, default)

    def ax_windows(self, pid):
        """Enumerate bounded accessibility windows for a pinned process."""
        root = self.desktop.ax.AXUIElementCreateApplication(pid)
        windows = self.attr(root, "AXWindows", [])
        if not isinstance(windows, Sequence) or isinstance(windows, (str, bytes)) or len(windows) > 64:
            return []
        result = list(windows)
        for window in windows:
            sheets = self.attr(window, "AXSheets", [])
            if isinstance(sheets, Sequence) and not isinstance(sheets, (str, bytes)):
                result.extend(sheets[:64])
        return result[:128]

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals
    def refresh(self, *, describe=False):
        """Re-read native identity, geometry and stacking; never activate during discovery."""
        d, q = self.desktop, self.desktop.q
        require_interactive_session(q)
        self.applications()
        _, active, _ = q.CGGetActiveDisplayList(16, None, None)
        if d.display_id not in active:
            raise RuntimeError("selected display disconnected")
        rect = q.CGDisplayBounds(d.display_id)
        self.display = DesktopRect(x=rect.origin.x, y=rect.origin.y, width=rect.size.width, height=rect.size.height)
        rows = q.CGWindowListCopyWindowInfo(
            q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements, q.kCGNullWindowID
        )
        if rows is None or len(rows) > 512:
            raise RuntimeError("native window catalog unavailable or too large")
        descriptions = {}
        if describe:
            for pid in self.names:
                for element in self.ax_windows(pid):
                    bounds = self.reader.bounds(element)
                    role, subrole = self.attr(element, "AXRole"), self.attr(element, "AXSubrole")
                    kind = (
                        "menu"
                        if role in {"AXMenu", "AXMenuBar"}
                        else "dialog"
                        if role == "AXSheet" or subrole in {"AXDialog", "AXSystemDialog"}
                        else "window"
                    )
                    if bounds:
                        descriptions[(pid, bounds)] = kind
        previous_kinds = {(s.pid, s.native_id): s.kind for s in self.catalog.surfaces}
        surfaces = []
        cursor_level = q.CGWindowLevelForKey(q.kCGCursorWindowLevelKey)
        for row in rows:
            if float(row.get("kCGWindowAlpha", 1)) <= 0:
                continue
            if int(row.get("kCGWindowLayer", 0)) == cursor_level:
                continue  # The pointer is not a window occluder or an input recipient.
            bounds = d.bounds(row)
            if min(bounds[2:]) <= 0:
                continue
            pid, native_id = int(row["kCGWindowOwnerPID"]), int(row["kCGWindowNumber"])
            kind = descriptions.get((pid, bounds), previous_kinds.get((pid, native_id), "window"))
            surfaces.append(
                Surface(
                    native_id,
                    pid,
                    DesktopRect(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3]),
                    str(row.get("kCGWindowName", "")),
                    int(row.get("kCGWindowLayer", 0)),
                    kind,
                )
            )
        shared = self.sharing.update(d.targets, self.instances, surfaces)
        self.front_pid = self.keyboard_recipient()
        self.focused_rect = None
        if self.front_pid in self.names:
            app_element = d.ax.AXUIElementCreateApplication(self.front_pid)
            focused = self.attr(app_element, "AXFocusedWindow")
            self.focused_rect = self.reader.bounds(focused) if focused is not None else None
        opened = self.dialogs.update(surfaces, shared)
        self.catalog.shared = frozenset(shared | opened | self.attached_sheets(surfaces, shared))
        self.catalog.update(surfaces, self.display)
        return (
            self.display,
            self.catalog.signature(),
            self.front_pid,
            self.focused_rect,
            frozenset(self.catalog.handles),
        )

    def attached_sheets(self, surfaces, roots):
        """Only AXSheets attached to a selected root; sibling windows never inherit access."""
        result = set()
        for name, identities in self.sharing.roots.items():
            if not self.desktop.targets[name].share_attached_sheets:
                continue
            pid = self.instances[name][0]
            if not any(s.pid == pid and (pid, s.native_id) not in roots for s in surfaces):
                continue
            windows = self.ax_windows(pid)
            for surface in surfaces:
                if (surface.pid, surface.native_id) not in identities & roots:
                    continue
                bounds = tuple(surface.rect.model_dump().values())
                # Both native and AX identity must be unambiguous.
                if sum(s.pid == pid and s.rect == surface.rect for s in surfaces) != 1:
                    continue
                matches = [w for w in windows if self.reader.bounds(w) == bounds]
                if len(matches) != 1:
                    continue
                sheets = self.attr(matches[0], "AXSheets", [])
                if not isinstance(sheets, Sequence) or isinstance(sheets, (str, bytes)) or len(sheets) > 64:
                    continue
                for sheet in sheets:
                    if self.attr(sheet, "AXRole") != "AXSheet":
                        continue
                    child_bounds = self.reader.bounds(sheet)
                    children = [
                        s for s in surfaces if s.pid == pid and tuple(s.rect.model_dump().values()) == child_bounds
                    ]
                    if len(children) == 1:
                        result.add((pid, children[0].native_id))
        return result

    def keyboard_recipient(self):
        """Read current keyboard ownership, including while AppKit's cache is idle."""
        d = self.desktop
        focused_app = self.attr(d.ax.AXUIElementCreateSystemWide(), "AXFocusedApplication")
        if focused_app is not None:
            status, pid = d.ax.AXUIElementGetPid(focused_app, None)
            if status != 0 or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise RuntimeError("cannot verify the current keyboard recipient")
            self.focus_source = "AXFocusedApplication"
            return pid
        # NSWorkspace/NSRunningApplication state changes are delivered through
        # the main run loop, which is otherwise idle while this worker reads stdin.
        d.appkit.NSRunLoop.currentRunLoop().runUntilDate_(d.appkit.NSDate.dateWithTimeIntervalSinceNow_(0.001))
        front = d.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        self.focus_source = "NSWorkspace"
        return int(front.processIdentifier()) if front else None

    def check(self, *, require_app=True):
        """One fresh observation covers one action, including every key/drag sample."""
        self.desktop._inputs().check_stop()
        if getattr(self.desktop, "monitor", None):
            self.desktop.monitor.check()
        previous_surfaces = self.catalog.surfaces
        current = self.refresh()
        if (
            self.expected is not None
            and current != self.expected
            and self._typing_popup_dismissed(current, previous_surfaces)
        ):
            self.expected = current
        if self.expected is not None and current != self.expected:
            if getattr(self.desktop, "monitor", None):

                def geometry(state):
                    display, surfaces, front, focused, shared = state
                    return {
                        "display": display.model_dump(),
                        "surfaces": [(pid, wid, rect.model_dump(), layer) for pid, wid, rect, layer in surfaces],
                        "front_pid": front,
                        "focused_rect": focused,
                        "shared_windows": sorted(shared),
                    }

                self.desktop.monitor.record(
                    "stale_observation", expected=geometry(self.expected), actual=geometry(current)
                )
            raise ObservationChanged(
                "desktop windows or focus changed; inspect the fresh returned observation; "
                "observe again only if none was returned"
            )
        if require_app and self.front_pid not in self.names:
            raise FocusRequired("focus a permitted window before input; use focus with a listed window_id")
        if require_app:
            owned = [s for s in self.catalog.surfaces if s.pid == self.front_pid]
            if self.focused_rect is not None:
                x, y, width, height = self.focused_rect
                focused = DesktopRect(x=x, y=y, width=width, height=height)
                matches = [s for s in owned if s.rect == focused]
                scoped = (
                    contains(self.display, focused)
                    and len(matches) == 1
                    and (matches[0].pid, matches[0].native_id) in self.catalog.handles
                )
            else:
                scoped = False  # App foreground ownership alone is not a window grant.
            if not scoped:
                raise FocusRequired(
                    "focused window is not shared or is outside the selected display; focus a shared window"
                )
        self.check_secure()
        if self.front_pid in self.names:
            return self.applications()[self.front_pid]
        return None

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-boolean-expressions,too-many-return-statements
    def _typing_popup_dismissed(self, current, previous_surfaces):
        """Allow only a removed nonfocused popup after typing into the same AX owner."""
        d = self.desktop
        if (
            # pylint: disable-next=too-many-boolean-expressions
            not self.typing
            or not self.typed_keys
            or not d.settings.accessibility_enabled
            or d.accessibility is None
            or d.last is None
            or d.last.accessibility_status != "available"
        ):
            return False
        display, surfaces, front, focused, shared = self.expected
        if (
            focused is None
            or front not in self.names
            or (display, front, focused) != (current[0], current[2], current[3])
        ):
            return False
        focused_windows = [s for s in surfaces if s[0] == front and tuple(s[2].model_dump().values()) == focused]
        if len(focused_windows) != 1 or focused_windows[0] not in current[1]:
            return False
        removed = [s for s in surfaces if s not in current[1]]
        # This also rejects additions, movement, changed layers and survivor reordering.
        if not removed or tuple(s for s in surfaces if s not in removed) != current[1]:
            return False
        # Project native surface rows to their two-part sharing identities.
        if current[4] != shared - {(pid, wid) for pid, wid, *_ in removed}:  # pylint: disable=unnecessary-comprehension
            return False  # A changed sharing relationship is not popup dismissal.
        kinds = {(s.pid, s.native_id): s.kind for s in previous_surfaces}
        if any(
            pid != front or not contains(display, rect) or kinds.get((pid, wid)) not in {"dialog", "menu"}
            for pid, wid, rect, _ in removed
        ):
            return False
        d.accessibility.validate_typing(d.last)
        if self.refresh() != current:
            return False  # AX lookup must not hide another native transition.
        if getattr(d, "monitor", None):
            d.monitor.record(
                "typing_popup_dismissed",
                typed_keys=self.typed_keys,
                windows=[{"pid": s[0], "window_id": s[1]} for s in removed],
            )
        return True

    def check_secure(self):
        """Reject password focus before any native input."""
        if self.front_pid in self.names:
            app = self.desktop.ax.AXUIElementCreateApplication(self.front_pid)
            focused = self.attr(app, "AXFocusedUIElement")
            if focused is not None and any(
                self.attr(focused, attr) == "AXSecureTextField" for attr in ("AXRole", "AXSubrole")
            ):
                raise PermissionError("secure input requires manual takeover")

    def point_is_target(self, point):
        """Resolve native hit-test evidence to the approved foreground window."""
        # The observation and all native geometry are rechecked by InputEvents guards.
        handle = self.catalog.at(point, approved_only=True)
        if handle is None:
            return False
        surface = self.catalog.resolve(handle)
        if surface.pid != self.front_pid:
            return False  # Switch applications explicitly with focus.
        ax = self.desktop.ax
        error, element = ax.AXUIElementCopyElementAtPosition(ax.AXUIElementCreateSystemWide(), *point, None)
        if error == 0 and element is not None:
            error, pid = ax.AXUIElementGetPid(element, None)
            if error != 0 or pid != surface.pid:
                return False
            owner = self.attr(element, "AXWindow")
            if owner is None and self.attr(element, "AXRole") in {"AXWindow", "AXSheet"}:
                owner = element
            return owner is not None and self.reader.bounds(owner) == tuple(surface.rect.model_dump().values())
        # No hit evidence: a foreign or excluded rectangle must still block input.
        # Never use the approved-window composition as evidence of click-through.
        return self.catalog.at(point) == handle

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals,too-many-statements
    def focus(self, *, target="", window_id=""):
        """Bring only a granted window to the foreground and validate focus."""
        self.check(require_app=False)
        if target:
            if target not in self.instances:
                raise PermissionError("application is outside the host allowlist")
            pid = self.instances[target][0]
            matches = [
                handle
                for handle in self.catalog.windows
                if self.catalog.resolve(handle).pid == pid and self.catalog.resolve(handle).layer == 0
            ]
            if len(matches) != 1:
                raise ValueError("target has multiple or no windows; use a listed window_id")
            window_id = matches[0]
        last_windows = self.desktop.last.context["windows"]
        if window_id not in {w["window_id"] for w in last_windows}:
            raise ValueError("window_id was not in the latest observation")
        surface = self.catalog.resolve(window_id)
        bounds = tuple(surface.rect.model_dump().values())
        matches = [w for w in self.ax_windows(surface.pid) if self.reader.bounds(w) == bounds]
        if len(matches) != 1:
            raise RuntimeError("cannot unambiguously raise this window; use a visible same-app coordinate")
        self.check(require_app=False)  # AX lookup must not hide an identity/focus change.
        d = self.desktop
        started = time.monotonic()
        deadline = min(started + 2.0, getattr(d, "phase_deadline", None) or float("inf"))
        attempts = []
        frontmost_checked = False
        d.target = self.names[surface.pid]

        def record(event):
            details = dict(
                target_pid=surface.pid,
                window_id=surface.native_id,
                expected_bounds=bounds,
                front_pid=self.front_pid,
                focused_rect=self.focused_rect,
                focus_source=self.focus_source,
                elapsed=time.monotonic() - started,
                attempts=list(attempts),
            )
            if getattr(d, "monitor", None):
                d.monitor.record(event, **details)

        def selected_window():
            # Every native mutation needs a live recipient, including after an
            # AX capability query or activation call that may itself take time.
            d._inputs().check_stop()
            if getattr(d, "monitor", None):
                d.monitor.check()
            self.refresh()
            if self.catalog.resolve(window_id).rect != surface.rect:
                raise RuntimeError("selected window moved during activation")
            windows = [w for w in self.ax_windows(surface.pid) if self.reader.bounds(w) == bounds]
            if len(windows) != 1:
                raise RuntimeError("selected AX window changed during activation")
            self.refresh()
            if self.catalog.resolve(window_id).rect != surface.rect:
                raise RuntimeError("selected window moved during activation")
            d._inputs().check_stop()
            if getattr(d, "monitor", None):
                d.monitor.check()
            return windows[0] if time.monotonic() < deadline else None

        # Native activation is asynchronous. A zero AXRaise acknowledgement does
        # not prove foreground focus, nor does a nonzero acknowledgement disprove it.
        for _ in range(128):
            d._inputs().check_stop()
            if getattr(d, "monitor", None):
                d.monitor.check()
            self.refresh()
            current = self.catalog.resolve(window_id)
            if current.rect != surface.rect:
                raise RuntimeError("selected window moved during activation")
            now = time.monotonic()
            if now >= deadline:
                break
            if attempts and self.front_pid == surface.pid and self.focused_rect == bounds:
                record("focus_verified")
                return
            if len(attempts) < 3 and (not attempts or now - attempts[-1]["at"] >= 0.35):
                if selected_window() is None:
                    break
                app = self.applications()[surface.pid]
                accepted = app.activateWithOptions_(d.appkit.NSApplicationActivateIgnoringOtherApps)
                attempt = dict(at=now, activation_accepted=bool(accepted), raise_status=None)
                attempts.append(attempt)
                record("focus_requested")
                if selected_window() is None:
                    break
                if not accepted and not frontmost_checked and self.front_pid != surface.pid:
                    frontmost_checked = True
                    attempt["activation_policy"] = int(app.activationPolicy())
                    root = d.ax.AXUIElementCreateApplication(surface.pid)
                    d.ax.AXUIElementSetMessagingTimeout(root, 0.2)
                    status, writable = d.ax.AXUIElementIsAttributeSettable(root, "AXFrontmost", None)
                    attempt.update(ax_frontmost_query_status=status, ax_frontmost_settable=bool(writable))
                    # Do not write after a slow query hides cancellation, a
                    # replaced process/window, or an exhausted phase budget.
                    if selected_window() is None:
                        break
                    if status == 0 and writable and self.front_pid != surface.pid:
                        attempt["ax_frontmost_status"] = d.ax.AXUIElementSetAttributeValue(root, "AXFrontmost", True)
                    record("focus_ax_frontmost")
                element = selected_window()
                if element is None:
                    break
                attempt["raise_status"] = d.ax.AXUIElementPerformAction(element, "AXRaise")
                record("focus_raise_requested")
                continue  # Verify immediately before waiting.
            d.appkit.NSRunLoop.currentRunLoop().runUntilDate_(
                d.appkit.NSDate.dateWithTimeIntervalSinceNow_(min(0.025, max(0, deadline - now)))
            )
        record("focus_failed")
        raise RuntimeError(
            "selected window did not acquire focus "
            f"(target_pid={surface.pid}, front_pid={self.front_pid}, expected_bounds={bounds}, "
            f"focused_rect={self.focused_rect}, attempts={len(attempts)}, "
            f"AXRaise status={attempts[-1]['raise_status'] if attempts else 'not requested'})"
        )

    def capture_window(self, native_id):
        """Capture one approved surface; no full-display pixels enter the worker."""
        capture = self.desktop.window_capture()
        if not self._capture_prepared:
            capture.refresh(signature=self.catalog.signature())
            self._capture_prepared = True
        surface = next(s for s in self.catalog.surfaces if s.native_id == native_id)
        if (surface.pid, surface.native_id) not in self.catalog.handles:
            raise PermissionError("capture window is not shared")
        return capture.capture(native_id, expected_pid=surface.pid)

    # Every retry returns or re-raises the last capture failure.
    # pylint: disable-next=inconsistent-return-statements,too-many-locals
    def observe(self, *, image_mode="full"):
        """Allow bounded UI animation to settle, restarting only discarded captures."""
        attempts = 0
        settle = self.desktop.settings.observation_settle_seconds if image_mode == "full" else 0
        started = time.monotonic()
        deadline = started + settle
        previous = None
        samples = captures = 0
        timings = {}
        while True:
            try:
                current = self._observe_once(image_mode=image_mode)
                if not settle:
                    return current
                samples += 1
                captures += current["performance"]["window_captures"]
                for key, value in current["performance"].items():
                    if key.endswith("_seconds"):
                        timings[key] = timings.get(key, 0) + value
                signature = (
                    current["image_digest"],
                    current["bounds"],
                    current["context"]["active_window_id"],
                    current["context"]["windows"],
                )
                if signature == previous or time.monotonic() >= deadline:
                    current["performance"].update(timings)
                    current["performance"].update(
                        window_captures=captures,
                        settle_samples=samples,
                        settle_seconds=time.monotonic() - started,
                        total_seconds=time.monotonic() - started,
                        settled=signature == previous,
                    )
                    self.desktop.last = replace(self.desktop.last, performance=current["performance"])
                    return current
                previous = signature
                self.desktop._inputs().pause(min(0.05, max(0, deadline - time.monotonic())))
            except ObservationChanged:
                previous = None
                attempts += 1
                pending = self.dialogs.pending if self.dialogs.waiting_for_metadata else None
                remaining = max(0, pending["deadline"] - time.monotonic()) if pending else 0
                if attempts >= 3 and not remaining:
                    raise
                self.desktop._inputs().pause(min(0.15, remaining) if remaining else 0.15)

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals,too-many-statements
    def _observe_once(self, *, image_mode="full"):
        """Discover and capture without requiring or changing the current foreground app."""
        if image_mode not in {"full", "pixels", "metadata"}:
            raise ValueError("invalid internal image mode")
        started = time.monotonic()
        d = self.desktop
        d.last = None
        self.expected = None
        if d.accessibility:
            d.accessibility.clear()
        d._inputs().check_stop()
        before = self.refresh(describe=True)
        if self.dialogs.waiting_for_metadata:
            raise ObservationChanged("opening modal window metadata is not ready; no pixels captured")
        self.check_secure()
        active = [
            handle
            for handle in self.catalog.windows
            if self.catalog.resolve(handle).pid == self.front_pid
            and tuple(self.catalog.resolve(handle).rect.model_dump().values()) == self.focused_rect
            and sum(
                s.pid == self.front_pid and tuple(s.rect.model_dump().values()) == self.focused_rect
                for s in self.catalog.surfaces
            )
            == 1
        ]
        active_window_id = active[0] if len(active) == 1 else None
        if self.last_active_window_id not in self.catalog.windows:
            self.last_active_window_id = None
        # Losing foreground ownership must not also change the model's pixel scale.
        crop_candidate = active_window_id or self.last_active_window_id
        view_window_id = (
            self.catalog.crop_window(crop_candidate)
            if crop_candidate and d.settings.active_window_screenshots
            else None
        )
        view = self.catalog.windows[view_window_id].desktop_rect if view_window_id else self.display
        metadata_seconds = time.monotonic() - started
        pixel_started = time.monotonic()
        self._capture_prepared = False
        captures = {}
        capture_handles = (view_window_id,) if view_window_id else self.catalog.windows
        for handle in capture_handles if image_mode != "metadata" else ():
            d._inputs().check_stop()
            surface = self.catalog.resolve(handle)
            try:
                captures[handle] = self.capture_window(surface.native_id)
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                if self.refresh() != before:
                    raise ObservationChanged("window disappeared during capture; pixels discarded") from exc
                raise
            if self.refresh() != before:
                raise ObservationChanged("desktop changed during capture; pixels discarded")
        bitmap = (
            self.catalog.compose(view, captures, d.settings.screenshot_max_side, view_window_id=view_window_id)
            if image_mode != "metadata"
            else None
        )
        width, height = self.catalog.image_size(view, d.settings.screenshot_max_side)
        digest = hashlib.sha256(bitmap.tobytes()).hexdigest() if bitmap is not None else ""
        pixel_seconds = time.monotonic() - pixel_started
        d.generation += 1
        context = DesktopObservationContext(
            session_id=self.scope.session_id,
            observation_id=uuid.uuid4().hex,
            display_id=self.scope.display_id,
            geometry=ScreenshotGeometry(pixel_width=width, pixel_height=height, desktop_rect=view),
            windows=tuple(self.catalog.windows.values()),
            active_window_id=active_window_id,
            view_window_id=view_window_id,
        )
        self.scope.validate_observation(context)
        target = self.names.get(self.front_pid, "")
        if target:
            d.target = target
        buffer = io.BytesIO()
        encode_started = time.monotonic()
        if image_mode == "full":
            bitmap.save(buffer, format="PNG")
        encode_seconds = time.monotonic() - encode_started
        observation = Observation(
            base64.b64encode(buffer.getvalue()).decode(),
            width,
            height,
            tuple(view.model_dump().values()),
            target,
            0,
            d.generation,
            context=context.model_dump(mode="json"),
            image_digest=digest,
        )
        ax_started = time.monotonic()
        if d.accessibility and context.active_window_id:
            elements, status = d.accessibility.snapshot(
                self.applications()[self.front_pid], observation, window_bounds=self.focused_rect
            )
            observation = Observation(**{**asdict(observation), "elements": elements, "accessibility_status": status})
        elif d.accessibility:
            d.accessibility.clear()
            observation = Observation(**{**asdict(observation), "accessibility_status": "unavailable"})
        if self.refresh() != before:
            raise ObservationChanged("desktop changed during observation; pixels discarded")
        self.check_secure()
        # Complete AX metadata is required before omitting a raster observation.
        if image_mode == "metadata" and observation.accessibility_status != "available":
            return self._observe_once(image_mode="pixels")
        performance = {
            "image_mode": image_mode,
            "metadata_seconds": metadata_seconds,
            "capture_compose_seconds": pixel_seconds,
            "encode_seconds": encode_seconds,
            "accessibility_seconds": time.monotonic() - ax_started,
            "total_seconds": time.monotonic() - started,
            "window_captures": len(captures),
        }
        capture = getattr(d, "_window_capture", None)
        if capture is not None:
            performance["capture_backend"] = capture.backend
        observation = Observation(**{**asdict(observation), "performance": performance})
        self.expected = before
        d.last = observation
        if active_window_id:
            self.last_active_window_id = active_window_id
        if getattr(d, "monitor", None):
            d.monitor.record(
                "observation_geometry",
                display=self.display.model_dump(),
                front_pid=self.front_pid,
                focused_rect=self.focused_rect,
                focus_source=self.focus_source,
                surfaces=[
                    {
                        "pid": s.pid,
                        "window_id": s.native_id,
                        "bounds": s.rect.model_dump(),
                        "layer": s.layer,
                        "approved": (s.pid, s.native_id) in self.catalog.handles,
                    }
                    for s in self.catalog.surfaces
                ],
            )
        return asdict(observation)

    def act(self, action, observation):
        """Apply one action against the current scoped observation."""
        d = self.desktop
        if (
            d.last is None
            or observation.get("generation") != d.last.generation
            or (observation.get("context") or {}).get("observation_id") != d.last.context["observation_id"]
        ):
            raise RuntimeError("stale desktop observation")
        try:
            self.dialogs.pending = None
            try:
                self.check(require_app=action.kind not in {"focus", "launch"} or bool(action.element_id))
            except (ObservationChanged, FocusRequired) as exc:
                raise InputNotStarted(str(exc)) from exc
            dialog_start = self.dialogs.before_action(action)
            self.typing = action.kind == "type" and action.input_mode == "editable"
            self.typed_keys = 0
            if action.kind == "launch":
                launch_application(self, action.target)
            elif action.kind == "focus" and not action.element_id:
                self.focus(target=action.target, window_id=action.window_id)
            else:
                d._perform_action(action)
            self.dialogs.after_action(dialog_start)
        finally:
            self.typing = False
            self.typed_keys = 0
            d.last = None
            self.expected = None
            if d.accessibility:
                d.accessibility.clear()
        d._inputs().pause(0.08)  # Match the legacy settling interval, while remaining interruptible.
