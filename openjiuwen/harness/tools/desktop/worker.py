"""Private macOS executor. No model, shell actions, clipboard writes or Android code."""

# PyObjC exposes framework members dynamically. stdout is the private JSON RPC channel.
# pylint: disable=no-member,bad-builtin

from __future__ import annotations

import base64
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, replace

STOPPING = threading.Event()

if __package__:
    from .accessibility import AccessibilityTree
    from .capture import WindowCapture
    from .errors import InputNotStarted
    from .input_monitor import InputMonitor
    from .macos_input import KEY_CODES as KEYS
    from .macos_input import InputEvents
    from .macos_navigation import MacOSNavigation
    from .macos_session import interactive_session_status, require_interactive_session
    from .native_batch import run_batch
    from .schema import Action, DesktopSettings, Observation
    from .shortcuts import validate_keys
else:
    # Direct execution avoids importing the SDK's eager tool registry into the
    # native worker (and keeps its stdout exclusively for the JSON protocol).
    from accessibility import AccessibilityTree  # pylint: disable=import-error
    from capture import WindowCapture  # pylint: disable=import-error
    from errors import InputNotStarted  # pylint: disable=import-error
    from input_monitor import InputMonitor  # pylint: disable=import-error
    from macos_input import KEY_CODES as KEYS  # pylint: disable=import-error
    from macos_input import InputEvents  # pylint: disable=import-error
    from macos_navigation import MacOSNavigation  # pylint: disable=import-error
    from macos_session import interactive_session_status, require_interactive_session  # pylint: disable=import-error
    from native_batch import run_batch  # pylint: disable=import-error
    from schema import Action, DesktopSettings, Observation  # pylint: disable=import-error
    from shortcuts import validate_keys  # pylint: disable=import-error


def capability(display_id=None):
    """Report native dependency, display and TCC availability without prompting."""
    if sys.platform != "darwin":
        return {"available": False, "reason": "macOS is required"}
    try:
        import AppKit  # noqa: F401  # pylint: disable=unused-import
        import ApplicationServices as AX
        import Quartz as Q
        from PIL import Image  # noqa: F401  # pylint: disable=unused-import

        _, displays, count = Q.CGGetActiveDisplayList(16, None, None)
        session_ready, session_reason = interactive_session_status(Q)
        result = {
            "screen_recording": bool(Q.CGPreflightScreenCaptureAccess()),
            "accessibility": bool(AX.AXIsProcessTrusted()),
            "display_count": count,
            "display_ids": list(displays),
            "selected_display": display_id,
            "interactive_session": session_ready,
        }
        display_ok = (display_id in displays) if display_id is not None else count == 1
        result["available"] = all((result["screen_recording"], result["accessibility"], display_ok, session_ready))
        result["reason"] = (
            "ready"
            if result["available"]
            else (
                "Requires one active or explicitly selected display and macOS Privacy & Security permissions: "
                "Accessibility and Screen & System Audio Recording for the launching host"
            )
        )
        if not session_ready:
            result["reason"] = session_reason
        return result
    except ImportError as exc:
        return {"available": False, "reason": f"optional desktop dependency missing: {exc.name}"}


class NativeDesktop:
    """Execute foreground input within configured application and display scope."""

    def __init__(self, settings):
        import AppKit
        import ApplicationServices
        import CoreFoundation
        import Quartz

        self.q, self.appkit, self.ax = Quartz, AppKit, ApplicationServices
        self.ax_equal = CoreFoundation.CFEqual
        self._inputs()
        self.settings = settings
        self.targets = {t.name: t for t in settings.targets}
        self.target = next(iter(self.targets))
        self.generation = 0
        self.last = None
        self.accessibility = (
            AccessibilityTree(self.ax, equal=CoreFoundation.CFEqual) if settings.accessibility_enabled else None
        )
        cap = capability(settings.display_id)
        if not cap["available"]:
            raise PermissionError(cap["reason"])
        self.display_id = settings.display_id or cap["display_ids"][0]
        self.navigation = MacOSNavigation(self) if settings.window_navigation else None
        if self.navigation is None:
            self.window()
        self.initial_focus = True
        self.monitor = None
        if settings.stop_on_external_input:
            if not settings.evidence_dir:
                raise ValueError("controlled input monitoring requires an evidence directory")
            # Geometry logging is not app discovery. Resolve once on this thread;
            # native input guards independently revalidate identities before input.
            self.monitor_pids = (
                frozenset(self.navigation.names)
                if self.navigation is not None
                else frozenset(int(self.application(name).processIdentifier()) for name in self.targets)
            )
            self.monitor = InputMonitor(self.q, settings.evidence_dir, observer=self.monitor_snapshot)
            self.monitor.start()

    def monitor_snapshot(self):
        """Read foreground identity and owned window geometry without user content."""
        front = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        windows = (
            self.q.CGWindowListCopyWindowInfo(
                self.q.kCGWindowListOptionOnScreenOnly | self.q.kCGWindowListExcludeDesktopElements,
                self.q.kCGNullWindowID,
            )
            or []
        )
        owned = [
            dict(pid=int(w.get("kCGWindowOwnerPID", 0)), window_id=int(w["kCGWindowNumber"]), bounds=self.bounds(w))
            for w in windows
            if int(w.get("kCGWindowOwnerPID", 0)) in self.monitor_pids and int(w.get("kCGWindowLayer", -1)) == 0
        ]
        return {"foreground_pid": front.processIdentifier() if front else None, "owned_windows": owned}

    def post_event(self, event):
        """Post an event with experiment ownership when monitoring is enabled."""
        if getattr(self, "monitor", None):
            self.monitor.post(event)
        else:
            self.q.CGEventPost(self.q.kCGHIDEventTap, event)

    def application(self, target, *, allow_missing=False):
        """Resolve exactly one live configured application."""
        spec = self.targets[target]
        if spec.pid:
            app = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(spec.pid)
            for _ in range(2):
                if app is not None:
                    break
                # AppKit may briefly lose the registration during activation.
                # Refresh only this read; pinned launch identity is checked by
                # navigation before any capture/input. Never revive terminated apps.
                self.appkit.NSRunLoop.currentRunLoop().runUntilDate_(
                    self.appkit.NSDate.dateWithTimeIntervalSinceNow_(0.025)
                )
                app = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(spec.pid)
            if app is None or app.isTerminated():
                if getattr(self, "monitor", None):
                    self.monitor.record("application_unavailable", pid=spec.pid, missing=app is None)
                raise RuntimeError("configured application is no longer running")
            if spec.bundle_id and app.bundleIdentifier() != spec.bundle_id:
                raise RuntimeError("target PID no longer matches configured bundle")
            return app
        apps = self.appkit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(spec.bundle_id)
        if not apps and allow_missing and spec.allow_launch:
            return None
        if len(apps) != 1:
            raise RuntimeError("application target must resolve to exactly one running process")
        return apps[0]

    def window(self):
        """Resolve one on-screen window wholly inside the selected display."""
        require_interactive_session(self.q)
        app = self.application(self.target)
        windows = (
            self.q.CGWindowListCopyWindowInfo(
                self.q.kCGWindowListOptionOnScreenOnly | self.q.kCGWindowListExcludeDesktopElements,
                self.q.kCGNullWindowID,
            )
            or []
        )
        spec = self.targets[self.target]
        if not spec.window_title and not spec.window_ids:
            raise PermissionError("share explicit window_ids or one exact window_title; app-wide access is disabled")
        if self.target in getattr(self, "_retired_window_targets", set()):
            raise PermissionError("shared window disappeared; select it again in the host")
        matches = [
            w
            for w in windows
            if int(w.get("kCGWindowOwnerPID", 0)) == app.processIdentifier()
            and int(w.get("kCGWindowLayer", -1)) == 0
            and (not spec.window_title or w.get("kCGWindowName") == spec.window_title)
            and (not spec.window_ids or int(w["kCGWindowNumber"]) in spec.window_ids)
            and w["kCGWindowBounds"]["Width"] > 1
            and w["kCGWindowBounds"]["Height"] > 1
        ]
        if len(matches) != 1:
            if self.target in getattr(self, "_window_bindings", {}):
                if not hasattr(self, "_retired_window_targets"):
                    self._retired_window_targets = set()
                self._retired_window_targets.add(self.target)
            raise RuntimeError("target must have exactly one matching on-screen window")
        identity = (*MacOSNavigation.identity(app), int(matches[0]["kCGWindowNumber"]))
        if not hasattr(self, "_window_bindings"):
            self._window_bindings = {}
        previous = self._window_bindings.setdefault(self.target, identity)
        if previous != identity:
            raise PermissionError("shared window or application instance changed; select it again in the host")
        _, active, _ = self.q.CGGetActiveDisplayList(16, None, None)
        if self.display_id not in active:
            raise RuntimeError("selected display disconnected")
        display = self.q.CGDisplayBounds(self.display_id)
        x, y, width, height = self.bounds(matches[0])
        if not (
            display.origin.x <= x
            and display.origin.y <= y
            and x + width <= display.origin.x + display.size.width
            and y + height <= display.origin.y + display.size.height
        ):
            raise RuntimeError("target window is outside the selected display")
        return app, matches[0]

    def focus(self, target):
        """Validate scope, activate and raise the configured target window."""
        if target not in self.targets:
            raise PermissionError("application is outside the host allowlist")
        self.target = target
        _, target_window = self.window()  # validate scope before activation
        app = self.application(target)
        app.activateWithOptions_(self.appkit.NSApplicationActivateIgnoringOtherApps)
        element = self.ax.AXUIElementCreateApplication(app.processIdentifier())
        error, windows = self.ax.AXUIElementCopyAttributeValue(element, "AXWindows", None)
        if error == 0:
            for window in windows or []:
                _, title = self.ax.AXUIElementCopyAttributeValue(window, "AXTitle", None)
                if not self.targets[target].ax_window_title or title == self.targets[target].ax_window_title:
                    self.ax.AXUIElementPerformAction(window, "AXRaise")
        # Activation is asynchronous, especially between two unbundled Cocoa apps.
        for _ in range(40):
            front = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is not None and front.processIdentifier() == app.processIdentifier():
                self.check_focus()
                _, window = self.window()
                x, y, width, height = self.bounds(window)
                if self.point_is_target((x + width / 2, y + height / 2)):
                    time.sleep(0.05)
                    return
            self.appkit.NSRunLoop.currentRunLoop().runUntilDate_(
                self.appkit.NSDate.dateWithTimeIntervalSinceNow_(0.025)
            )
        raise RuntimeError(
            f"application activation failed for target {target}; "
            f"target_window={target_window['kCGWindowNumber']}, "
            f"foreground_pid={front.processIdentifier() if front else None}"
        )

    def point_is_target(self, point):
        # AX hit testing respects click-through overlays; rectangle stacking does not.
        """Use AX hit testing to reject an input point belonging to another window."""
        if getattr(self, "navigation", None):
            return self.navigation.point_is_target(point)
        error, element = self.ax.AXUIElementCopyElementAtPosition(
            self.ax.AXUIElementCreateSystemWide(), float(point[0]), float(point[1]), None
        )
        if error != 0 or element is None:
            return False
        error, pid = self.ax.AXUIElementGetPid(element, None)
        if error != 0 or pid != self.application(self.target).processIdentifier():
            return False
        title = self.targets[self.target].ax_window_title
        if title:
            error, window = self.ax.AXUIElementCopyAttributeValue(element, "AXWindow", None)
            if error != 0:
                window = element
            error, actual = self.ax.AXUIElementCopyAttributeValue(window, "AXTitle", None)
            return error == 0 and actual == title
        return True

    def check_focus(self):
        """Stop on lost foreground, cancellation or a focused secure text field."""
        require_interactive_session(self.q)
        if getattr(self, "navigation", None):
            return self.navigation.check()
        if STOPPING.is_set():
            raise RuntimeError("native executor is stopping")
        if getattr(self, "monitor", None):
            self.monitor.check()
        app = self.application(self.target)
        front = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or front.processIdentifier() != app.processIdentifier():
            if getattr(self, "monitor", None):
                self.monitor.record(
                    "focus_lost",
                    foreground_pid=front.processIdentifier() if front else None,
                    target_pid=app.processIdentifier(),
                    target=self.target,
                )
            raise RuntimeError("target lost foreground; input stopped")
        _, window = self.window()  # recheck display scope before every key pair
        if self.last is not None and self.last.target == self.target:
            if int(window["kCGWindowNumber"]) != self.last.window_id or self.bounds(window) != self.last.bounds:
                raise RuntimeError("window moved or changed; observe again before input")
        element = self.ax.AXUIElementCreateApplication(app.processIdentifier())
        title = self.targets[self.target].ax_window_title
        if title:
            error, focused_window = self.ax.AXUIElementCopyAttributeValue(element, "AXFocusedWindow", None)
            if error != 0 or focused_window is None:
                raise RuntimeError("cannot verify focused target window")
            error, actual_title = self.ax.AXUIElementCopyAttributeValue(focused_window, "AXTitle", None)
            if error != 0 or actual_title != title:
                raise RuntimeError("another window in the target application has focus")
        err, focused = self.ax.AXUIElementCopyAttributeValue(element, "AXFocusedUIElement", None)
        if err == 0 and focused:
            for attr in ("AXRole", "AXSubrole"):
                error, role = self.ax.AXUIElementCopyAttributeValue(focused, attr, None)
                if error == 0 and role == "AXSecureTextField":
                    raise PermissionError("secure input requires manual takeover")
        return app

    @staticmethod
    def bounds(window):
        """Return window bounds as desktop points in top-left coordinates."""
        b = window["kCGWindowBounds"]
        return tuple(float(b[k]) for k in ("X", "Y", "Width", "Height"))

    def window_capture(self):
        """Create the optional capture backend only inside the native worker."""
        if getattr(self, "_window_capture", None) is None:
            # Capture bindings are loaded only at the first native observation.
            self._window_capture = WindowCapture(  # pylint: disable=attribute-defined-outside-init
                self.q,
                backend=self.settings.capture_backend,
                check_stop=self._inputs().check_stop,
            )
        return self._window_capture

    def observe(self, *, image_mode="full"):
        """Capture only the target window and generate a fresh action token."""
        if getattr(self, "navigation", None):
            if image_mode == "full":
                self.navigation.observe()
                self.last = replace(self.last, cursor=self.cursor_position(self.last))
            else:
                self.navigation.observe(image_mode=image_mode)
            return asdict(self.last)
        from PIL import Image

        if self.initial_focus:
            self.focus(self.target)
            self.initial_focus = False
        self.check_focus()
        _, window = self.window()
        with tempfile.TemporaryDirectory(prefix="jiuwen-desktop-capture-") as directory:
            path = os.path.join(directory, "window.png")
            subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window["kCGWindowNumber"]), path],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with Image.open(path) as im:
                im.thumbnail((self.settings.screenshot_max_side,) * 2)
                buffer = io.BytesIO()
                im.convert("RGB").save(buffer, format="PNG")
                width, height = im.size
        self.generation += 1
        self.last = Observation(
            base64.b64encode(buffer.getvalue()).decode(),
            width,
            height,
            self.bounds(window),
            self.target,
            int(window["kCGWindowNumber"]),
            self.generation,
        )
        if self.accessibility:
            elements, status = self.accessibility.snapshot(
                self.application(self.target), self.last, window_title=self.targets[self.target].ax_window_title
            )
            self.last = replace(self.last, elements=elements, accessibility_status=status)
        self.last = replace(self.last, cursor=self.cursor_position(self.last))
        self.check_focus()  # Capture/metadata must not hide a lock or foreground/window change.
        return asdict(self.last)

    def cursor_position(self, observation: Observation) -> dict[str, float] | None:
        """Read the pointer in screenshot pixels only when over an authorized recipient."""
        try:
            event = self.q.CGEventCreate(None)
            if event is None:
                return None
            point = self.q.CGEventGetLocation(event)
            left, top, width, height = observation.bounds
            if not (left <= point.x < left + width and top <= point.y < top + height):
                return None
            if not self.point_is_target((point.x, point.y)):
                return None
            return {
                "x": (point.x - left) * observation.width / width,
                "y": (point.y - top) * observation.height / height,
            }
        except Exception:  # Optional metadata must not turn an unavailable pointer into a guessed position.
            return None

    def _inputs(self):
        """Keep one event stream so Stop can release an in-progress press."""
        if not hasattr(self, "input_events"):
            self.input_events = InputEvents(self.q, self.post_event, self.check_focus, self.point_is_target, STOPPING)
        return self.input_events

    def release_inputs(self):
        """Release held events without requiring the target to retain focus."""
        if hasattr(self, "input_events"):
            self.input_events.release()

    def post_key(
        self, keycode, flags=0, text=None, *, require_editable=False, require_keyboard=False, duration=0, modifiers=()
    ):
        """Post a paired key event after checking foreground and cancellation."""
        self.check_focus()
        if require_keyboard:
            if (
                not getattr(self, "navigation", None)
                or not self.settings.accessibility_enabled
                or self.last.accessibility_status not in {"available", "truncated"}
            ):
                raise RuntimeError("keyboard text requires shared-window navigation and accessibility")
            self.accessibility.validate_keyboard(self.last)
            self.check_focus()
        if (
            require_editable
            and self.settings.accessibility_enabled
            and self.last.accessibility_status in {"available", "truncated"}
        ):
            self.accessibility.validate_typing(self.last)
            self.check_focus()
        if duration:
            with self._inputs().modified(modifiers) as held_flags:
                self.check_focus()
                self._inputs().key(keycode, held_flags, text, duration)
        else:
            self._inputs().key(keycode, flags, text)

    def act(self, action, observation):  # pylint: disable=too-many-statements
        """Validate observation freshness and execute exactly one allowed action."""
        if getattr(self, "navigation", None):
            self.navigation.act(action, observation)
            return
        if action.kind == "launch":
            raise ValueError("launch requires shared-window navigation")
        if action.window_id:
            raise ValueError("window_id requires shared-window navigation")
        if self.last is None or observation["generation"] != self.last.generation:
            raise RuntimeError("stale desktop observation")
        self.check_focus()
        _, window = self.window()
        if int(window["kCGWindowNumber"]) != self.last.window_id or self.bounds(window) != self.last.bounds:
            raise RuntimeError("window moved or changed; observe again before input")
        try:
            self._perform_action(action)
        finally:
            # Failed/partial input cannot be replayed against the old snapshot.
            self.last = None
            if getattr(self, "accessibility", None):
                self.accessibility.clear()
        time.sleep(0.08)

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-statements
    def _perform_action(self, action):
        """Execute an already scoped action; the caller consumes its snapshot even on error."""
        if action.kind == "focus":
            if action.element_id:
                if not self.settings.accessibility_enabled or not self.accessibility:
                    raise PermissionError("accessibility target selection is disabled")
                app = self.check_focus()
                monitor = getattr(self, "monitor", None)
                self.accessibility.focus(
                    action.element_id,
                    self.last,
                    app=app,
                    check_scope=self.check_focus,
                    pause=self._inputs().pause,
                    record=monitor.record if monitor else None,
                )
            else:
                self.focus(action.target)
        elif action.kind == "move":
            self._inputs().move(self.last.point(action.x, action.y), action.modifiers)
        elif action.kind == "hold_mouse":
            self._inputs().click(
                self.last.point(action.x, action.y), action.button, modifiers=action.modifiers, duration=action.duration
            )
        elif action.kind == "click":
            identifier = action.element_id
            if identifier and (not self.settings.accessibility_enabled or not self.accessibility):
                raise PermissionError("accessibility target selection is disabled")
            point = None if identifier else self.last.point(action.x, action.y)
            if action.button == "left" and action.click_count == 1 and not action.modifiers:
                if not identifier and self.settings.accessibility_enabled and self.accessibility:
                    identifier = self.accessibility.toggle_at_point(self.last, action.x, action.y)
                if identifier:
                    monitor = getattr(self, "monitor", None)
                    if self.accessibility.press_toggle(
                        identifier,
                        self.last,
                        guard_point=self._inputs().guard_point,
                        record=monitor.record if monitor else None,
                        point=point,
                        deadline=(
                            getattr(self, "phase_deadline", None)
                            or time.monotonic() + self.settings.budgets.action_seconds
                        ),
                    ):
                        return
            if identifier:
                # An unsupported query can still have outlived this observation.
                point = self.accessibility.resolve(identifier, self.last, point=point)
            self._inputs().click(point, action.button, action.click_count, action.modifiers)
        elif action.kind == "drag":
            # Validate every requested point before posting the first down event.
            points = [self.last.point(*point) for point in action.path]
            self._inputs().drag(points, action.button, action.duration, action.modifiers)
        elif action.kind == "type":
            keyboard = action.input_mode == "keyboard"
            for char in action.text:
                if char in "\n\t":
                    self.post_key(KEYS["return" if char == "\n" else "tab"], require_editable=True)
                else:
                    self.post_key(0, text=char, require_editable=not keyboard, require_keyboard=keyboard)
                if getattr(self, "navigation", None):
                    self.navigation.typed_keys += 1
        elif action.kind in {"shortcut", "hold_key"}:
            validate_keys(action.keys)
            keys = tuple(action.keys)
            flags = 0
            flag_names = {"cmd": "Command", "ctrl": "Control", "alt": "Alternate", "shift": "Shift"}
            for modifier in keys[:-1]:
                flags |= getattr(self.q, "kCGEventFlagMask" + flag_names[modifier])
            # Preserve text-editability checks for editing chords, including word
            # deletion and cut/paste, while allowing navigation/selection elsewhere.
            editable = keys[-1] in {"backspace", "delete"} or ("cmd" in keys[:-1] and keys[-1] in {"x", "v"})
            self.post_key(
                KEYS[keys[-1]],
                flags,
                require_editable=editable,
                duration=action.duration if action.kind == "hold_key" else 0,
                modifiers=keys[:-1],
            )
        elif action.kind == "scroll":
            point = self.last.point(
                self.last.width / 2 if action.x is None else action.x,
                self.last.height / 2 if action.y is None else action.y,
            )
            self._inputs().scroll(point, action.direction, action.amount, action.modifiers)
        elif action.kind == "wait":
            self._inputs().pause(action.seconds)
        else:
            raise ValueError("terminal decisions must not reach native executor")


# Keep sequential native guards and cleanup visible in one operation.
# pylint: disable-next=too-many-statements
def main():
    """Serve the private worker protocol or a side-effect-free capability probe."""
    if "--probe" in sys.argv:
        display_id = int(sys.argv[sys.argv.index("--display") + 1]) if "--display" in sys.argv else None
        print(json.dumps(capability(display_id)))
        return
    parent = os.getppid()
    desktop = None
    # Set the fence in the signal handler; release from ordinary Python code.
    signal.signal(signal.SIGTERM, lambda *_: STOPPING.set())

    def parent_watchdog():
        while True:
            time.sleep(0.01)
            parent_lost = os.getppid() != parent
            if parent_lost:
                STOPPING.set()
            current = desktop
            phase_deadline = getattr(current, "phase_deadline", None)
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                STOPPING.set()
            monitor = getattr(current, "monitor", None)
            if monitor is not None and monitor.contaminated.is_set():
                STOPPING.set()
            if STOPPING.is_set() and current is not None:
                try:
                    current.release_inputs()
                except Exception:
                    pass  # Main-thread cleanup also retries; a failed OS post is not a release guarantee.
            if parent_lost:
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return

    threading.Thread(target=parent_watchdog, daemon=True).start()
    try:
        for line in sys.stdin:
            if STOPPING.is_set():
                break
            try:
                request = json.loads(line)
                method = request["method"]
                if method == "start" and desktop is None:
                    desktop = NativeDesktop(DesktopSettings.model_validate(request["settings"]))
                    result = None
                elif desktop is None:
                    raise RuntimeError("desktop not initialized")
                elif method == "observe":
                    result = desktop.observe()
                elif method == "act":
                    result = desktop.act(Action.model_validate(request["action"]), request["observation"])
                elif method == "batch":
                    result = run_batch(
                        desktop,
                        [Action.model_validate(a) for a in request["actions"]],
                        request["observation"],
                        max_steps=request["max_steps"],
                        max_observations=request["max_observations"],
                        ineffective_clicks=request.get("ineffective_clicks", 0),
                        wait_for=request.get("wait_for"),
                    )
                else:
                    raise ValueError("unknown desktop RPC")
                print(json.dumps({"ok": True, "result": result}), flush=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                input_not_started = isinstance(exc, InputNotStarted)
                if desktop is not None:
                    try:
                        desktop.release_inputs()
                    except Exception as release_error:
                        input_not_started = False
                        error += f"; input release failed: {release_error}"
                    monitor = getattr(desktop, "monitor", None)
                    if monitor is not None and monitor.contaminated.is_set():
                        input_not_started = False
                        error = f"CONTAMINATED: {monitor.reason}; {error}"
                print(json.dumps({"ok": False, "error": error, "input_not_started": input_not_started}), flush=True)
    finally:
        STOPPING.set()
        if desktop is not None:
            try:
                desktop.release_inputs()
            finally:
                if desktop.monitor is not None:
                    desktop.monitor.close()


if __name__ == "__main__":
    main()
