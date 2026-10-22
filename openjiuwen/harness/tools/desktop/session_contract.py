"""Internal desktop metadata contracts; no native calls, permissions or model execution.

The navigation adapter binds handles to live instances and enforces scope before
exposing pixels. Legacy window capture reuses the screenshot geometry contract.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

InstanceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]


class _Contract(BaseModel):
    """Immutable, finite, explicitly typed metadata with no ignored fields."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class DesktopRect(_Contract):
    """Top-left rectangle in adapter desktop units (macOS points, not image pixels)."""

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    @model_validator(mode="after")
    def finite_edges(self) -> DesktopRect:
        """Finite components must also produce finite rectangle edges."""
        if not math.isfinite(self.x + self.width) or not math.isfinite(self.y + self.height):
            raise ValueError("desktop rectangle edges must be finite")
        return self


class ScreenshotGeometry(_Contract):
    """Geometry of the exact bitmap returned to the model, including any resizing."""

    pixel_width: int = Field(gt=0)
    pixel_height: int = Field(gt=0)
    desktop_rect: DesktopRect

    def to_desktop(self, x: float | int, y: float | int) -> tuple[float, float]:
        """Map a point inside this image; this conversion alone does not authorize input."""
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y)):
            raise ValueError("screenshot coordinates must be numbers")
        if not (0 <= x < self.pixel_width and 0 <= y < self.pixel_height):
            raise ValueError(
                f"point is outside the screenshot: use pixel x in [0, {self.pixel_width}) "
                f"and y in [0, {self.pixel_height}); not desktop points or normalized coordinates"
            )
        rect = self.desktop_rect
        # Divide first so a valid large coordinate does not overflow during scaling.
        return rect.x + (x / self.pixel_width) * rect.width, rect.y + (y / self.pixel_height) * rect.height


class DesktopWindow(_Contract):
    """Adapter-issued live-instance handles; title is descriptive, untrusted UI text."""

    window_id: InstanceId
    application_id: InstanceId
    display_id: InstanceId
    title: str = ""
    application_name: str = ""
    kind: Literal["window", "dialog", "menu"] = "window"
    desktop_rect: DesktopRect


class DesktopObservationContext(_Contract):
    """Metadata accompanying one screenshot, with no required accessibility tree.

    A token is an identity, not proof of freshness: the session must retain and
    compare its latest issued token before executing input.
    """

    session_id: InstanceId
    observation_id: InstanceId
    display_id: InstanceId
    geometry: ScreenshotGeometry
    windows: tuple[DesktopWindow, ...] = ()
    active_window_id: InstanceId | None = None
    # None denotes a scoped display view; otherwise the bitmap is one window crop.
    view_window_id: InstanceId | None = None

    @model_validator(mode="after")
    def consistent_windows(self) -> DesktopObservationContext:
        """Reject ambiguous handles, foreign displays and dangling/cross-crop references."""
        windows = {window.window_id: window for window in self.windows}
        if len(windows) != len(self.windows):
            raise ValueError("window handles must be unique within an observation")
        if any(window.display_id != self.display_id for window in self.windows):
            raise ValueError("all observed windows must belong to the selected display")
        for name in (self.active_window_id, self.view_window_id):
            if name is not None and name not in windows:
                raise ValueError("active and crop window handles must reference an observed window")
        if self.view_window_id is not None:
            if windows[self.view_window_id].desktop_rect != self.geometry.desktop_rect:
                raise ValueError("window crop geometry must match the referenced window")
        return self


class DesktopSessionScope(_Contract):
    """Host-approved metadata scope, not a grant of native access or capture permission.

    Application handles identify process instances, not display names or bare PIDs.
    They are assigned and resolved by the adapter, never by model-generated text.
    """

    session_id: InstanceId
    display_id: InstanceId
    # Before an explicit launch there may be no bound instances and no permitted pixels.
    application_ids: tuple[InstanceId, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_applications(self) -> DesktopSessionScope:
        """Keep the host's explicit application-instance grant unambiguous."""
        if len(set(self.application_ids)) != len(self.application_ids):
            raise ValueError("application handles must be unique")
        return self

    def permits(self, window: DesktopWindow) -> bool:
        """Check descriptor membership only; native identity and visibility need revalidation."""
        return window.display_id == self.display_id and window.application_id in self.application_ids

    def validate_observation(self, observation: DesktopObservationContext) -> None:
        """Reject metadata outside this session; does not inspect or authorize screenshot pixels."""
        if observation.session_id != self.session_id:
            raise PermissionError("observation belongs to another desktop session")
        if observation.display_id != self.display_id:
            raise PermissionError("observation belongs to another display")
        if any(not self.permits(window) for window in observation.windows):
            raise PermissionError("observation contains an application outside the session scope")
