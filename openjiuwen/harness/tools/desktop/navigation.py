"""Native-free window lifetime, scope and compositing rules for desktop navigation."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

if __package__:
    from .session_contract import DesktopRect, DesktopWindow
else:
    from session_contract import DesktopRect, DesktopWindow  # pylint: disable=import-error


@dataclass(frozen=True)
class Surface:
    """A window-server surface in front-to-back order, including foreign occluders."""

    native_id: int
    pid: int
    rect: DesktopRect
    title: str = ""
    layer: int = 0
    kind: str = "window"


def contains(outer, inner):
    """Check whether a rectangle lies completely inside another."""
    return (
        outer.x <= inner.x
        and outer.y <= inner.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def covers(rect, point):
    """Check whether a point lies inside a rectangle."""
    return rect.x <= point[0] < rect.x + rect.width and rect.y <= point[1] < rect.y + rect.height


def intersects(a, b):
    """Check whether two rectangles overlap."""
    return a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.height and b.y < a.y + a.height


class WindowCatalog:
    """Opaque handles survive title/geometry changes, but never an observed disappearance."""

    def __init__(self, display_id, applications, names=None):
        self.display_id = display_id
        self.applications = applications  # pinned PID -> opaque application instance
        self.names = names or {}
        self.handles = {}
        self.windows = {}
        self.surfaces = ()
        self.display = None
        self.shared = frozenset()

    def update(self, surfaces, display):
        """Rebuild scoped handles and retire disappeared windows."""
        if len(surfaces) > 512:
            raise RuntimeError("native window catalog exceeds its bound")
        identities = [(s.pid, s.native_id) for s in surfaces]
        if len(set(identities)) != len(identities):
            raise RuntimeError("ambiguous native window identity")
        permitted = [
            s
            for s in surfaces
            if (s.pid, s.native_id) in self.shared and s.pid in self.applications and contains(display, s.rect)
        ]
        if len(permitted) > 64:
            raise RuntimeError("permitted window catalog exceeds its bound")
        previous = self.handles
        self.handles = {(s.pid, s.native_id): previous.get((s.pid, s.native_id), uuid.uuid4().hex) for s in permitted}
        self.windows = {
            self.handles[(s.pid, s.native_id)]: DesktopWindow(
                window_id=self.handles[(s.pid, s.native_id)],
                application_id=self.applications[s.pid],
                display_id=self.display_id,
                title=s.title[:256],
                application_name=self.names.get(s.pid, ""),
                kind=s.kind,
                desktop_rect=s.rect,
            )
            for s in permitted
        }
        self.surfaces = tuple(surfaces)
        self.display = display

    def resolve(self, handle):
        """Resolve only a handle from the current approved catalog."""
        if handle not in self.windows:
            raise ValueError("unknown or expired window_id; observe again")
        return next(s for s in self.surfaces if self.handles.get((s.pid, s.native_id)) == handle)

    def at(self, point, *, approved_only=False):
        """Find a scoped candidate; only native hit testing can authorize skipping foreign surfaces."""
        for surface in self.surfaces:
            if approved_only and surface.pid not in self.applications:
                continue
            if covers(surface.rect, point):
                # An excluded approved-app surface must still block underlying windows.
                return self.handles.get((surface.pid, surface.native_id))
        return None

    def signature(self):
        """Track approved geometry and possible occluders, not unrelated desktop activity."""
        approved = [window.desktop_rect for window in self.windows.values()]
        return tuple(
            (s.pid, s.native_id, s.rect, s.layer)
            for s in self.surfaces
            if (s.pid, s.native_id) in self.shared or any(intersects(window, s.rect) for window in approved)
        )

    @staticmethod
    def image_size(display, max_side):
        """The same geometry for pixel and metadata observations."""
        scale = min(1, max_side / max(display.width, display.height))
        return max(1, round(display.width * scale)), max(1, round(display.height * scale))

    def crop_window(self, handle: str) -> str | None:
        """Keep the overview when a shared sheet/window overlaps the active parent."""
        window = self.windows[handle]
        for surface in self.surfaces:
            current = self.handles.get((surface.pid, surface.native_id))
            if current == handle:
                return handle
            if current and intersects(surface.rect, window.desktop_rect):
                return None
        return None

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals
    def compose(self, display, captures, max_side, *, view_window_id=None):
        """Compose approved captures, not desktop visibility; input ownership is checked separately."""
        from PIL import Image

        width, height = self.image_size(display, max_side)
        result = Image.new("RGB", (width, height), "black")
        sx, sy = width / display.width, height / display.height
        for surface in reversed(self.surfaces):
            if surface.pid not in self.applications:
                # A foreign rectangle says nothing about opacity or click-through.
                # Its pixels are never captured, so omitting it cannot expose them.
                continue
            rect = surface.rect
            handle = self.handles.get((surface.pid, surface.native_id))
            # Round authorized extents inward; excluded app surfaces mask outward.
            low, high = (math.ceil, math.floor) if handle else (math.floor, math.ceil)
            box = (
                low((rect.x - display.x) * sx),
                low((rect.y - display.y) * sy),
                high((rect.x + rect.width - display.x) * sx),
                high((rect.y + rect.height - display.y) * sy),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            if handle and (view_window_id is None or handle == view_window_id):
                im = captures[handle].convert("RGBA")
                if abs(im.width / im.height - rect.width / rect.height) > 0.02:
                    raise RuntimeError("captured window aspect ratio does not match its geometry")
                im = im.resize((box[2] - box[0], box[3] - box[1]))
                # Transparency must not reveal another application's pixels.
                result.paste("black", box)
                result.paste(im, box, im)
            else:
                result.paste("black", box)
        return result
