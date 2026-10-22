"""Bounded AX projection and private, observation-scoped target references.

Framework objects are injected by the worker; importing this module is headless-safe.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Sequence

CLICK_ROLES = frozenset(
    {"AXButton", "AXCheckBox", "AXRadioButton", "AXTextField", "AXTextArea", "AXComboBox", "AXPopUpButton", "AXLink"}
)
TEXT_ROLES = frozenset({"AXStaticText", "AXHeading"})
EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox"})
FOCUS_CONTAINER_ROLES = frozenset({"AXWindow", "AXGroup", "AXScrollArea", "AXSplitGroup", "AXLayoutArea", "AXWebArea"})
TOGGLE_ROLES = frozenset({"AXCheckBox", "AXRadioButton"})
MAX_NODES = 256
MAX_ELEMENTS = 80
MAX_DEPTH = 12
MAX_SECONDS = 1.0
_ABSENT = object()


# Keep sequential native guards and cleanup visible in one operation.
# pylint: disable-next=too-many-public-methods
class AccessibilityTree:
    """Keep native references out of RPC/model data and revalidate before input."""

    def __init__(self, ax, *, equal=None):
        self.ax = ax
        self.equal = equal or (lambda first, second: first == second)
        self.clear()

    def clear(self):
        """Invalidate all previously issued element IDs."""
        self.references = {}
        self.root = None
        self.root_bounds = None
        self.pid = None
        self.generation = None
        self.read_failed = False
        self.last_failure = None
        self.application = None
        self.typing_reference = None
        self.typing_uses_element_focus = False
        self.typing_container = None
        self.typing_failure = None
        self.keyboard_reference = _ABSENT
        self.keyboard_blocked = False
        self.selection_probes = {}

    def attr(self, element, name, default=None, *, optional_description=False):
        """An unsupported AX attribute never invents a value."""
        try:
            if self.ax.AXUIElementSetMessagingTimeout(element, 0.1) != 0:
                self.read_failed = True
                return default
            error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
            # Some native text controls fail an optional AXDescription lookup.
            # This exception is only for that display field at the label call site;
            # the same generic failure on required state remains fail-closed.
            missing_description = optional_description and name == "AXDescription" and error == -25200
            if error not in (0, -25205, -25212) and not missing_description:
                self.read_failed = True
                self.last_failure = (name, error)
            return value if error == 0 else default
        except (TypeError, ValueError, RuntimeError):
            self.read_failed = True
            return default

    def secure(self, element):
        """Do not read values or descendants of a secure/protected element."""
        role = self.attr(element, "AXRole")
        subrole = self.attr(element, "AXSubrole")
        protected = self.attr(element, "AXProtectedContent")
        return self.read_failed or role is None or "AXSecureTextField" in (role, subrole) or protected is True

    def bounds(self, element):
        """Read AX position/size in global desktop points, never screenshot pixels."""
        position, size = self.attr(element, "AXPosition"), self.attr(element, "AXSize")
        if position is None or size is None:
            return None
        try:
            ok_position, point = self.ax.AXValueGetValue(position, self.ax.kAXValueCGPointType, None)
            ok_size, extent = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
            if not ok_position or not ok_size:
                return None
            values = (float(point.x), float(point.y), float(extent.width), float(extent.height))
            return values if all(math.isfinite(value) for value in values) and min(values[2:]) > 0 else None
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def inside(bounds, window):
        """Only expose elements wholly inside the observed window."""
        if bounds is None:
            return False
        x, y, width, height = bounds
        left, top, window_width, window_height = window
        return left <= x and top <= y and x + width <= left + window_width and y + height <= top + window_height

    @staticmethod
    def text(value, limit):
        """Expose only scalar display content, bounded independently of IDs."""
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return str(value)[:limit]
        return ""

    def label(self, element):
        """Read a bounded display label without exposing native identifiers."""
        return self.text(
            self.attr(element, "AXTitle") or self.attr(element, "AXDescription", optional_description=True), 160
        )

    def editable(self, element, role):
        """Query editability without reading or changing the field's value."""
        if role not in EDITABLE_ROLES:
            return False
        if self.ax.AXUIElementSetMessagingTimeout(element, 0.1) != 0:
            self.read_failed = True
            return False
        error, writable = self.ax.AXUIElementIsAttributeSettable(element, "AXValue", None)
        if error not in (0, -25205, -25212):
            self.read_failed = True
        return error == 0 and writable is True

    def enabled(self, element, role):
        """Require explicit enabled state, or independent text editability when absent."""
        value = self.attr(element, "AXEnabled", _ABSENT)
        if self.read_failed:
            return False
        if value is True:
            return True
        if value is _ABSENT and role in EDITABLE_ROLES:
            return self.editable(element, role)
        return False

    def focusable(self, element):
        """An optional native capability, not permission to click or type."""
        try:
            if self.ax.AXUIElementSetMessagingTimeout(element, 0.1) != 0:
                return False
            error, writable = self.ax.AXUIElementIsAttributeSettable(element, "AXFocused", None)
            return error == 0 and writable is True
        except (TypeError, ValueError, RuntimeError):
            return False

    def refresh_text_state(self, element):
        """Read an optional caret/selection attribute; never infer focus from it."""
        try:
            error = self.ax.AXUIElementSetMessagingTimeout(element, 0.1)
            if error != 0:
                return {"error": int(error), "available": False}
            error, value = self.ax.AXUIElementCopyAttributeValue(element, "AXSelectedTextRange", None)
            # Some providers refresh lazy focus bookkeeping while answering this
            # read. Discard the range; subsequent focus/ownership checks decide.
            return {"error": int(error), "available": error == 0 and value is not None}
        except (TypeError, ValueError, RuntimeError) as exc:
            return {"error": type(exc).__name__, "available": False}

    def belongs(self, element):
        """Prove exact-window ownership, including controls that omit AXWindow."""
        error, pid = self.ax.AXUIElementGetPid(element, None)
        owner = self.attr(element, "AXWindow")
        if self.read_failed or error != 0 or pid != self.pid:
            return False
        if owner is not None:
            return self.equal(owner, self.root)
        return self.safe_ancestry(element)

    def safe_ancestry(self, element, *, visible=False):
        """Recheck secure ancestors after asynchronous UI/model changes."""
        current = element
        for _ in range(MAX_DEPTH + 2):
            if current is None or self.secure(current) or (visible and self.attr(current, "AXHidden", False)):
                return False
            error, pid = self.ax.AXUIElementGetPid(current, None)
            if error != 0 or pid != self.pid:
                return False
            if self.equal(current, self.root):
                return True
            current = self.attr(current, "AXParent")
        return False

    def focus_container_contains(self, container, element, role):
        """Prove a structural focus is an ancestor in this exact shared window."""
        if role not in FOCUS_CONTAINER_ROLES or self.attr(container, "AXRole") != role:
            return False
        if not self.belongs(container) or not self.safe_ancestry(element):
            return False
        current = element
        found = False
        for _ in range(MAX_DEPTH + 2):
            if current is None or self.secure(current) or self.attr(current, "AXHidden", False):
                return False
            found = found or self.equal(current, container)
            if self.equal(current, self.root):
                return found and not self.read_failed
            current = self.attr(current, "AXParent")
        return False

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-branches
    def snapshot(self, app, observation, *, window_title="", window_bounds=None):  # pylint: disable=too-many-locals,too-many-statements
        """Project one exact native window with hard traversal and payload bounds."""
        self.clear()
        self.pid = int(app.processIdentifier())
        self.generation = observation.generation
        application = self.ax.AXUIElementCreateApplication(self.pid)
        self.application = application
        windows = self.attr(application, "AXWindows", [])
        if not isinstance(windows, Sequence) or isinstance(windows, (str, bytes)) or len(windows) > 32:
            return [], "unavailable"
        matches = [
            item
            for item in windows
            if self.bounds(item) == (window_bounds or observation.bounds)
            and (not window_title or self.attr(item, "AXTitle") == window_title)
        ]
        if len(matches) != 1:
            return [], "unavailable"
        self.root = matches[0]
        self.root_bounds = window_bounds or observation.bounds
        prefix = f"{observation.generation}-{uuid.uuid4().hex[:12]}"
        elements, stack, visited = [], [(self.root, 0)], []
        deadline = time.monotonic() + MAX_SECONDS
        status = "available"

        def blocked(element):
            nonlocal status
            previous_failure = self.read_failed
            protected = self.secure(element)
            if (
                self.read_failed
                and not previous_failure
                and self.last_failure == ("AXSubrole", -25200)
                and self.attr(element, "AXRole") == "AXButton"
                and not self.equal(element, self.root)
            ):
                # Quarantine unreadable security metadata without exposing this
                # node or its descendants. Unrelated validated siblings survive.
                self.read_failed = False
                status = "truncated"
            return protected

        while stack:
            if len(visited) >= MAX_NODES or len(elements) >= MAX_ELEMENTS or time.monotonic() >= deadline:
                status = "truncated"
                break
            current, depth = stack.pop()
            if any(self.equal(current, previous) for previous in visited):
                continue
            visited.append(current)
            if blocked(current):
                continue
            role = self.attr(current, "AXRole", "")
            self.keyboard_blocked = self.keyboard_blocked or role in EDITABLE_ROLES
            bounds = self.bounds(current)
            hidden = self.attr(current, "AXHidden", False)
            if hidden:
                continue
            if role in CLICK_ROLES | TEXT_ROLES and self.inside(bounds, observation.bounds) and self.belongs(current):
                enabled = self.enabled(current, role)
                label = self.label(current)
                value = self.attr(current, "AXValue")
                identifier = f"{prefix}-{len(elements)}"
                editable = enabled and self.editable(current, role)
                focused = self.attr(current, "AXFocused") is True
                if editable and not focused and not self.read_failed and self.safe_ancestry(current, visible=True):
                    self.selection_probes[identifier] = self.refresh_text_state(current)
                    focused = self.attr(current, "AXFocused") is True
                left, top, window_width, window_height = observation.bounds
                x, y, width, height = bounds
                projected = {
                    "id": identifier,
                    "role": role,
                    "label": label,
                    "value": self.text(value, 512),
                    "bounds": [
                        (x - left) * observation.width / window_width,
                        (y - top) * observation.height / window_height,
                        width * observation.width / window_width,
                        height * observation.height / window_height,
                    ],
                    "enabled": enabled,
                    "focused": focused,
                    "actionable": enabled and role in CLICK_ROLES,
                    "editable": editable,
                }
                if projected["editable"]:
                    projected["typing_ready"] = False
                    projected["focusable"] = self.focusable(current)
                elements.append(projected)
                if projected["actionable"]:
                    self.references[identifier] = (
                        current,
                        bounds,
                        role,
                        label,
                        value if role in TOGGLE_ROLES else None,
                    )
                    projected["actionable"] = self.center_hits(current, bounds)
            children = self.attr(current, "AXChildren", [])
            if isinstance(children, Sequence) and not isinstance(children, (str, bytes)) and children:
                if depth >= MAX_DEPTH:
                    status = "truncated"
                else:
                    remaining = MAX_NODES - len(visited) - len(stack)
                    if len(children) > remaining:
                        status = "truncated"
                    stack.extend((child, depth + 1) for child in reversed(children[: max(0, remaining)]))
        focused = self.attr(application, "AXFocusedUIElement")
        current = focused
        for _ in range(MAX_DEPTH + 2):
            if current is None or blocked(current) or self.equal(current, self.root) or not self.belongs(current):
                break
            candidate = next(
                (item for item in elements if item["editable"] and self.equal(self.references[item["id"]][0], current)),
                None,
            )
            if candidate:
                self.typing_reference = (focused, candidate["id"])
                candidate["focused"] = True
                break
            current = self.attr(current, "AXParent")
        if self.typing_reference is None and not self.read_failed:
            candidates = [item for item in elements if item["editable"] and item["focused"]]
            if len(candidates) == 1:
                candidate = candidates[0]
                element = self.references[candidate["id"]][0]
                container_role = self.attr(focused, "AXRole") if focused is not None else None
                container_matches = (
                    focused is not None
                    and status == "available"
                    and self.focus_container_contains(focused, element, container_role)
                )
                if (focused is None or container_matches) and self.safe_ancestry(element):
                    self.typing_reference = (element, candidate["id"])
                    self.typing_uses_element_focus = True
                    if container_matches:
                        self.typing_container = (focused, container_role)
        if self.typing_reference is not None and not self.read_failed:
            try:
                self.validate_typing(observation)
            except RuntimeError as exc:
                self.typing_reference = None
                self.typing_failure = str(exc)
            else:
                identifier = self.typing_reference[1]
                next(item for item in elements if item["id"] == identifier)["typing_ready"] = True
        if self.read_failed:
            self.clear()
            return [], "unavailable"
        if not self.keyboard_blocked:
            self.keyboard_reference = focused
        return elements, status

    def validate_keyboard(self, observation):
        """Fence printable window commands without treating an editor as a fallback."""
        self.read_failed = False
        if (
            self.keyboard_reference is _ABSENT
            or observation.generation != self.generation
            or self.root is None
            or self.application is None
        ):
            raise RuntimeError("keyboard input requires an observed window without editable controls")
        current = self.attr(self.application, "AXFocusedUIElement")
        window = self.attr(self.application, "AXFocusedWindow")
        expected = self.keyboard_reference
        unchanged = current is None if expected is None else current is not None and self.equal(current, expected)
        if (
            window is None
            or not self.equal(window, self.root)
            or not unchanged
            or self.bounds(self.root) != self.root_bounds
            or not self.safe_ancestry(self.root, visible=True)
        ):
            raise RuntimeError("keyboard window or focus changed; observe again")
        if current is not None:
            if not self.belongs(current) or not self.safe_ancestry(current, visible=True):
                raise RuntimeError("keyboard focus is outside the shared window or protected")
            owner = current
            for _ in range(MAX_DEPTH + 2):
                if self.attr(owner, "AXRole") in EDITABLE_ROLES:
                    raise RuntimeError("keyboard focus became an editor; use editable typing")
                if self.equal(owner, self.root):
                    break
                owner = self.attr(owner, "AXParent")
                if owner is None:
                    raise RuntimeError("keyboard focus ancestry cannot be verified")
        if self.read_failed:
            raise RuntimeError("keyboard focus could not be verified")

    def validate_reference(self, identifier, observation, *, expected_toggle_state=None):
        """Verify a listed target's identity and state without any input."""
        if observation.generation != self.generation or identifier not in self.references:
            raise RuntimeError("stale or unknown accessibility element ID; observe again")
        element, bounds, role, label, state = self.references[identifier]
        if self.bounds(self.root) != self.root_bounds or not self.belongs(element) or not self.safe_ancestry(element):
            raise RuntimeError("accessibility element belongs to another window or secure subtree")
        if not self.enabled(element, role):
            raise RuntimeError("accessibility element is disabled")
        if self.attr(element, "AXRole") != role or self.label(element) != label:
            raise RuntimeError("accessibility element changed; observe again")
        if role in TOGGLE_ROLES:
            actual = self.attr(element, "AXValue")
            matches = (
                actual == state if expected_toggle_state is None else self.text(actual, 8) == expected_toggle_state
            )
            if not matches:
                if expected_toggle_state is not None:
                    raise RuntimeError("accessibility toggle did not reach its expected state; not retried")
                raise RuntimeError("accessibility toggle value changed; observe again")
        if self.attr(element, "AXHidden", False) or self.bounds(element) != bounds:
            raise RuntimeError("accessibility element moved or hidden; observe again")
        if self.read_failed:
            raise RuntimeError("accessibility state could not be verified")
        return element, bounds, role

    def validate_typing(self, observation):
        """Fence each key pair against a changed, disabled or protected text focus."""
        self.read_failed = False
        if self.typing_reference is None:
            raise RuntimeError("no observed editable accessibility focus for typing")
        focused, identifier = self.typing_reference
        current = self.attr(self.application, "AXFocusedUIElement")
        if self.typing_uses_element_focus:
            if self.attr(focused, "AXFocused") is not True or self.read_failed:
                raise RuntimeError("editable accessibility focus changed; observe again")
            if self.typing_container is not None:
                container, role = self.typing_container
                if (
                    current is None
                    or not self.equal(current, container)
                    or not self.focus_container_contains(container, focused, role)
                ):
                    raise RuntimeError("editable accessibility focus container changed; observe again")
                current = focused
            elif current is None:
                current = focused
        if current is None or not self.equal(current, focused) or not self.safe_ancestry(current):
            raise RuntimeError("editable accessibility focus changed or became secure; observe again")
        element, _, role = self.validate_reference(identifier, observation)
        owner = current
        for _ in range(MAX_DEPTH + 2):
            if owner is None or self.equal(owner, self.root):
                raise RuntimeError("editable accessibility focus moved to another control")
            if self.equal(owner, element):
                break
            owner = self.attr(owner, "AXParent")
        else:
            raise RuntimeError("editable accessibility focus ancestry cannot be verified")
        if not self.belongs(current) or not self.editable(element, role) or self.read_failed:
            raise RuntimeError("accessibility focus is no longer editable")

    def toggle_at_point(self, observation, x, y):
        """Bind only an exact observed/native hit, never a nearby toggle."""
        candidates = [
            item
            for item in observation.elements
            if item.get("enabled")
            and item.get("actionable")
            and len(item.get("bounds", ())) == 4
            and item["bounds"][0] <= x < item["bounds"][0] + item["bounds"][2]
            and item["bounds"][1] <= y < item["bounds"][1] + item["bounds"][3]
        ]
        if len(candidates) != 1 or candidates[0].get("role") not in TOGGLE_ROLES:
            return None
        identifier = candidates[0]["id"]
        self.read_failed = False
        element, _, _ = self.validate_reference(identifier, observation)
        return identifier if self.point_hits(element, observation.point(x, y)) else None

    def resolve(self, identifier, observation, *, point=None):
        """Return the verified requested point or center before native input."""
        self.read_failed = False
        element, bounds, _ = self.validate_reference(identifier, observation)
        if point is None:
            point = (bounds[0] + bounds[2] / 2, bounds[1] + bounds[3] / 2)
        inside = bounds[0] <= point[0] < bounds[0] + bounds[2] and bounds[1] <= point[1] < bounds[1] + bounds[3]
        if inside and self.point_hits(element, point) and not self.read_failed:
            return point
        raise RuntimeError("accessibility element point is occluded or replaced")

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals,too-many-return-statements,too-many-statements
    def press_toggle(self, identifier, observation, *, guard_point, record=None, deadline=None, point=None):
        """Activate an explicitly selected toggle once, only with native capability."""
        reference = self.references.get(identifier)
        if reference is not None and reference[2] not in TOGGLE_ROLES:
            return False  # Other controls keep the existing single-resolution path.
        self.read_failed = False
        element, _, role = self.validate_reference(identifier, observation)
        query = getattr(self.ax, "AXUIElementCopyActionNames", None)
        perform = getattr(self.ax, "AXUIElementPerformAction", None)
        if not callable(query) or not callable(perform):
            return False
        point = self.resolve(identifier, observation, point=point)
        guard_point(point)
        error, actions = query(element, None)
        if error in (-25205, -25212):
            return False
        if error != 0 or not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            raise RuntimeError("native accessibility actions could not be verified")
        if "AXPress" not in actions:
            return False
        # Capability lookup may block while UI state changes. Resolve again, then
        # use the physical recipient/Stop guard even though no mouse pair is sent.
        point = self.resolve(identifier, observation, point=point)
        if not self.safe_ancestry(element, visible=True) or self.read_failed:
            raise RuntimeError("accessibility toggle is no longer visible")
        guard_point(point)
        started = time.monotonic()
        deadline = deadline if deadline is not None else started + 2.0
        timeout = min(1.0, deadline - started)
        if timeout <= 0:
            raise TimeoutError("native accessibility action deadline expired before press")
        if self.ax.AXUIElementSetMessagingTimeout(element, timeout) != 0:
            raise RuntimeError("native accessibility action timeout could not be set")
        try:
            native_status = perform(element, "AXPress")
        except Exception as exc:
            if record:
                record("accessibility_toggle_press", role=role, native_status=None, error=type(exc).__name__)
            raise
        finally:
            restored = self.ax.AXUIElementSetMessagingTimeout(element, 0.1)
        if record:
            record(
                "accessibility_toggle_press",
                role=role,
                native_status=native_status,
                acknowledgement_seconds=time.monotonic() - started,
                acknowledgement_timeout=timeout,
            )
        if restored != 0:
            raise RuntimeError("native accessibility metadata timeout could not be restored")
        if time.monotonic() >= deadline:
            raise TimeoutError("native accessibility action deadline expired after press; not retried")
        if native_status == -25204:
            verified = False
            try:
                # CannotComplete can mean the app acted but its reply was late.
                # Read only; never repeat the press or fall back to a mouse click.
                previous = self.text(reference[4], 8)
                expected = (str(1 - int(previous)) if role == "AXCheckBox" else "1") if previous in ("0", "1") else None
                if expected is None or (role == "AXRadioButton" and previous == "1"):
                    raise RuntimeError("native toggle timeout has no verifiable binary transition")
                guard_point(point)
                self.read_failed = False
                self.validate_reference(identifier, observation, expected_toggle_state=expected)
                if (
                    not self.safe_ancestry(element, visible=True)
                    or not self.point_hits(element, point)
                    or self.read_failed
                ):
                    raise RuntimeError("native toggle timeout target could not be verified")
                guard_point(point)
                if time.monotonic() >= deadline:
                    raise TimeoutError("native toggle verification deadline expired")
                verified = True
            finally:
                if record:
                    record("accessibility_toggle_timeout_verified", role=role, verified=verified)
            return True
        if native_status != 0:
            # A failed IPC reply does not prove the action was unapplied.
            raise RuntimeError("native accessibility toggle press failed; not retried")
        return True

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals
    def focus(self, identifier, observation, *, app, check_scope, pause, record=None):
        """Request only keyboard focus on the explicitly selected editable control."""
        self.read_failed = False
        check_scope()
        element, bounds, role = self.validate_reference(identifier, observation)
        root, root_bounds = self.root, self.root_bounds
        label = self.references[identifier][3]
        if not self.editable(element, role) or self.read_failed or not self.focusable(element):
            raise RuntimeError("observed editable control does not support accessibility focus")
        # The capability query may have blocked while focus, scope or the target
        # changed. Recheck all guards before the single mutation; never retry it.
        check_scope()
        self.validate_reference(identifier, observation)
        if not self.editable(element, role) or not self.safe_ancestry(element, visible=True) or self.read_failed:
            raise RuntimeError("accessibility focus target is no longer editable")
        check_scope()
        native_status = self.ax.AXUIElementSetAttributeValue(element, "AXFocused", True)
        if record:
            record("editor_focus_requested", role=role, native_status=native_status)
        if native_status != 0:
            raise RuntimeError("native accessibility focus request failed")
        deadline = time.monotonic() + 1.0
        for attempt in range(4):
            if time.monotonic() >= deadline:
                break
            check_scope()
            # AX-only refresh: no capture, text/value write or hidden input. Use
            # the normal exact-focus/container binding rules for the postcondition.
            elements, status = self.snapshot(app, observation, window_bounds=root_bounds)
            check_scope()
            selected = [
                item
                for item in elements
                if item["id"] in self.references and self.equal(self.references[item["id"]][0], element)
            ]
            if record:
                observed = selected[0] if len(selected) == 1 else {}
                record(
                    "editor_focus_poll",
                    attempt=attempt + 1,
                    projection=status,
                    selected_count=len(selected),
                    focused=observed.get("focused"),
                    typing_ready=observed.get("typing_ready"),
                    selection_probe=self.selection_probes.get(observed.get("id")),
                    typing_validation_error=self.typing_failure,
                    binding=(
                        "container"
                        if self.typing_container
                        else "element"
                        if self.typing_uses_element_focus
                        else "direct"
                        if self.typing_reference
                        else "unbound"
                    ),
                )
            if self.root is None or not self.equal(self.root, root):
                raise RuntimeError("accessibility focus target window changed")
            if status == "unavailable" or len(selected) != 1:
                raise RuntimeError("accessibility focus target disappeared or could not be verified")
            item = selected[0]
            if (
                self.references[item["id"]][1] != bounds
                or self.references[item["id"]][3] != label
                or item["role"] != role
                or not item.get("editable")
            ):
                raise RuntimeError("accessibility focus target changed during focus request")
            if time.monotonic() >= deadline:
                break
            if item.get("typing_ready"):
                self.validate_typing(observation)
                if not self.safe_ancestry(element, visible=True) or self.read_failed:
                    raise RuntimeError("accessibility focus target is no longer visible")
                check_scope()
                if time.monotonic() >= deadline:
                    break
                return
            if attempt < 3:
                pause(min(0.1, max(0, deadline - time.monotonic())))
        raise RuntimeError("requested editor did not acquire verified keyboard focus")

    def center_hits(self, element, bounds):
        """Check native hit ownership without correcting questionable AX coordinates."""
        return self.point_hits(element, (bounds[0] + bounds[2] / 2, bounds[1] + bounds[3] / 2))

    def point_hits(self, element, point):
        """Prove native ownership at the exact point, including safe label children."""
        system = self.ax.AXUIElementCreateSystemWide()
        if self.ax.AXUIElementSetMessagingTimeout(system, 0.1) != 0:
            return False
        error, hit = self.ax.AXUIElementCopyElementAtPosition(system, *point, None)
        if error != 0 or hit is None:
            return False
        # Hit testing may return a child such as a button's text, never a sibling.
        current = hit
        for _ in range(MAX_DEPTH + 2):
            if current is None or self.secure(current) or not self.belongs(current):
                break
            if self.equal(current, element):
                return not self.read_failed
            if self.attr(current, "AXRole") in CLICK_ROLES:
                break  # A nested action is a different recipient, not a label.
            if self.equal(current, self.root):
                break
            current = self.attr(current, "AXParent")
        return False
