"""Opt-in scope for new modal windows; never infer ownership from a PID alone."""

import time
from collections.abc import Sequence
from dataclasses import dataclass

if __package__:
    from .accessibility import AccessibilityTree
else:
    from accessibility import AccessibilityTree  # pylint: disable=import-error


@dataclass(frozen=True)
class Window:
    """One AX window bound to native geometry and modal state."""

    element: object
    bounds: tuple
    modal: bool


@dataclass(frozen=True)
class Grant:
    """Identity chain from a shared root to its admitted dialog."""

    root: tuple
    parent: tuple
    root_element: object
    parent_element: object
    element: object


class OpenedDialogs:
    """One completed input may admit one new modal; grants retain root/parent identity."""

    def __init__(self, navigation):
        self.nav = navigation
        self.pending = None
        self.grants = {}  # (pid, native_id) -> Grant
        self.scan_failure = None
        self.waiting_for_metadata = False

    def record(self, event, **values):
        """Record grant decisions when host evidence logging is enabled."""
        if getattr(self.nav.desktop, "monitor", None):
            self.nav.desktop.monitor.record(event, **values)

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-return-statements
    def scan(self, pid, surfaces):
        """Match every AX window to native surfaces before granting a dialog."""
        reader = AccessibilityTree(self.nav.desktop.ax, equal=self.nav.reader.equal)
        self.scan_failure = None

        def reject(reason, **details):
            self.scan_failure = {"reason": reason, "ax_failure": reader.last_failure, **details}

        app = reader.ax.AXUIElementCreateApplication(pid)
        elements = reader.attr(app, "AXWindows")
        if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)) or not 1 <= len(elements) <= 64:
            return reject("window enumeration unavailable")
        result = {}
        for element in elements:
            if reader.ax.AXUIElementGetPid(element, None) != (0, pid):
                return reject("window PID unavailable or different")
            bounds = reader.bounds(element)
            matches = [s for s in surfaces if s.pid == pid and tuple(s.rect.model_dump().values()) == bounds]
            role = reader.attr(element, "AXRole")
            if bounds and not matches and role == "AXWindow":
                return reject("AX/native window geometry not yet matched", bounds=bounds)
            if len(matches) != 1 or role != "AXWindow":
                return reject(
                    "window geometry or role unavailable or ambiguous",
                    bounds=bounds,
                    native_matches=len(matches),
                    role=role,
                )
            subrole = reader.attr(element, "AXSubrole")
            modal = reader.attr(element, "AXModal")
            if not ((subrole == "AXStandardWindow" and modal is False) or (subrole == "AXDialog" and modal is True)):
                return reject("window subrole/modal state unavailable or unsupported")
            # Unknown protected state errors and attached subwindows cannot be
            # silently ignored when deciding whether the process has other UI.
            if reader.attr(element, "AXProtectedContent") is True or reader.attr(element, "AXSheets", []):
                return reject("protected window or attached subwindow")
            identity = (pid, matches[0].native_id)
            if identity in result:
                return reject("duplicate AX window")
            result[identity] = Window(element, bounds, modal is True and subrole == "AXDialog")
        if any(s.pid == pid and s.layer == 0 and (pid, s.native_id) not in result for s in surfaces):
            return reject("normal native window missing from AX")
        return reject("AX attribute read failed") if reader.read_failed else result

    def same(self, first, second):
        """Compare stable AX identity and native geometry."""
        return self.nav.reader.equal(first.element, second.element) and first.bounds == second.bounds

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-return-statements
    def before_action(self, action):
        """Arm dialog sharing only from an unambiguous shared parent."""
        if action.kind not in {"click", "shortcut"}:
            return None
        nav, pid = self.nav, self.nav.front_pid
        name = nav.names.get(pid)
        if name is None or not nav.desktop.targets[name].share_opened_dialogs:
            return None
        roots = nav.sharing.roots.get(name, set())
        if len(roots) != 1:
            return None
        root = next(iter(roots))
        current = self.scan(pid, nav.catalog.surfaces)
        allowed = roots | {key for key, grant in self.grants.items() if grant.root == root}
        if not current or set(current) != allowed or current[root].modal:
            self.record(
                "dialog_permission_not_armed",
                pid=pid,
                scan_failure=self.scan_failure,
                reason="AX windows do not match the shared root and granted modals",
            )
            return None
        focused = [key for key, window in current.items() if window.bounds == nav.focused_rect]
        if len(focused) != 1:
            return None
        return dict(
            root=root, parent=focused[0], windows=current, previous={(s.pid, s.native_id) for s in nav.catalog.surfaces}
        )

    def after_action(self, start):
        """Retain a bounded grant opportunity after completed input."""
        if start is not None:
            self.pending = {**start, "deadline": time.monotonic() + 2}
            self.record("dialog_permission_armed", pid=start["root"][0], parent_window_id=start["parent"][1])

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-boolean-expressions,too-many-locals
    def update(self, surfaces, roots):
        """Expire invalid grants and admit only newly observed owned dialogs."""
        nav = self.nav
        self.waiting_for_metadata = False
        for name, (pid, _) in nav.instances.items():
            existing = {key: grant for key, grant in self.grants.items() if key[0] == pid}
            pending = self.pending if self.pending and self.pending["root"][0] == pid else None
            if not existing and pending is None:
                continue
            allowed_roots = roots & nav.sharing.roots.get(name, set())
            current = self.scan(pid, surfaces) if nav.desktop.targets[name].share_opened_dialogs else None
            transient = bool(
                pending
                and self.scan_failure
                and (
                    self.scan_failure.get("ax_failure") == ("AXWindows", -25204)
                    or self.scan_failure.get("reason") == "AX/native window geometry not yet matched"
                )
                and time.monotonic() <= pending["deadline"]
                and nav.front_pid == pid
                and pending["root"] in allowed_roots
                and not any(
                    s.pid == pid and s.layer == 0 and (pid, s.native_id) not in pending["previous"] for s in surfaces
                )
            )
            valid = {}
            if current and len(allowed_roots) == 1 and allowed_roots <= current.keys():
                for key, grant in existing.items():
                    root, parent = grant.root, grant.parent
                    if (
                        # pylint: disable-next=too-many-boolean-expressions
                        root in allowed_roots
                        and not current[root].modal
                        and key in current
                        and current[key].modal
                        and nav.reader.equal(grant.element, current[key].element)
                        and nav.reader.equal(grant.root_element, current[root].element)
                        and parent in (allowed_roots | valid.keys())
                        and parent in current
                        and nav.reader.equal(grant.parent_element, current[parent].element)
                    ):
                        valid[key] = grant
            allowed = allowed_roots | valid.keys()
            new = set(current or {}) - allowed
            admit = None
            if pending and current and len(new) == 1 and pending["root"] in allowed_roots:
                key = next(iter(new))
                old = pending["windows"]
                if (
                    # pylint: disable-next=too-many-boolean-expressions
                    time.monotonic() <= pending["deadline"]
                    and nav.front_pid == pid
                    and current[key].bounds == nav.focused_rect
                    and current[key].modal
                    and key not in pending["previous"]
                    and set(old) == allowed
                    and all(k in current and self.same(w, current[k]) for k, w in old.items())
                ):
                    admit = key
            if new and admit is None:
                valid = {}  # A private or unknown AX window invalidates this policy.
            self.grants = {key: value for key, value in self.grants.items() if key[0] != pid} | valid
            if admit is not None:
                root, parent = pending["root"], pending["parent"]
                self.grants[admit] = Grant(
                    root, parent, current[root].element, current[parent].element, current[admit].element
                )
                if getattr(nav.desktop, "monitor", None):
                    nav.desktop.monitor.record(
                        "shared_dialog_granted",
                        pid=pid,
                        window_id=admit[1],
                        root_window_id=root[1],
                        parent_window_id=parent[1],
                        policy="share_opened_dialogs",
                    )
                self.pending = None
            elif transient:
                # AXWindows can be briefly busy while a dialog opens. Retain
                # only the pending request, never a capture/input grant.
                self.waiting_for_metadata = True
            # pylint: disable-next=too-many-boolean-expressions
            elif pending and (
                not current
                or new
                or nav.front_pid != pid
                or time.monotonic() > pending["deadline"]
                or pending["root"] not in allowed_roots
            ):
                self.record(
                    "dialog_permission_rejected",
                    pid=pid,
                    scan_failure=self.scan_failure,
                    expired=time.monotonic() > pending["deadline"],
                    foreground_matches=nav.front_pid == pid,
                    new_window_count=len(new),
                    old_windows_unchanged=bool(current)
                    and all(
                        key in current and self.same(value, current[key]) for key, value in pending["windows"].items()
                    ),
                )
                self.pending = None
        return set(self.grants)
