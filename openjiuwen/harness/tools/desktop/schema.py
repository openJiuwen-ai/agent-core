"""Strict, platform-neutral desktop contracts. Coordinates are screenshot pixels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if __package__:
    from .session_contract import DesktopRect, ScreenshotGeometry
    from .shortcuts import normalize_keys, validate_keys, validate_modifiers
else:
    # The native worker imports sibling schemas without loading the SDK registry.
    from session_contract import DesktopRect, ScreenshotGeometry  # pylint: disable=import-error
    from shortcuts import normalize_keys, validate_keys, validate_modifiers  # pylint: disable=import-error


ACTION_FIELDS = {
    "launch": {"target"},
    "focus": {"target", "window_id", "element_id"},
    "move": {"x", "y", "modifiers"},
    "click": {"element_id", "x", "y", "button", "click_count", "modifiers"},
    "drag": {"path", "button", "duration", "modifiers"},
    "hold_mouse": {"x", "y", "button", "duration", "modifiers"},
    "type": {"text", "input_mode"},
    "shortcut": {"keys"},
    "hold_key": {"keys", "duration"},
    "scroll": {"direction", "amount", "x", "y", "modifiers"},
    "wait": {"seconds"},
}


class StrictModel(BaseModel):
    """Reject unknown fields and implicit input coercion."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Action(StrictModel):
    """One bounded desktop action or terminal model claim."""

    kind: Literal[
        "launch",
        "focus",
        "move",
        "click",
        "drag",
        "hold_mouse",
        "type",
        "shortcut",
        "hold_key",
        "scroll",
        "wait",
        "done",
        "fail",
    ]
    target: str = ""
    window_id: str = Field(default="", max_length=128)
    x: float | int | None = None
    y: float | int | None = None
    element_id: str = Field(default="", max_length=80)
    button: Literal["left", "right", "middle"] = "left"
    click_count: int = Field(default=1, ge=1, le=3)
    path: list[Annotated[list[float | int], Field(min_length=2, max_length=2)]] = Field(
        default_factory=list, max_length=64
    )
    duration: float = Field(default=0.3, ge=0.05, le=2)
    text: str = Field(default="", max_length=2000)
    input_mode: Literal["editable", "keyboard"] = "editable"
    keys: list[str] = Field(default_factory=list, max_length=5)
    modifiers: list[str] = Field(default_factory=list, max_length=4)
    direction: Literal["up", "down", "left", "right"] = "down"
    amount: int = Field(default=3, ge=1, le=20)
    seconds: float | int = Field(default=0.3, ge=0, le=2)
    reason: str = Field(default="", max_length=500)

    @field_validator("keys", "modifiers", mode="before")
    @classmethod
    def normalize_shortcut_spelling(cls, value):
        """Accept explicit macOS key synonyms without changing the selected combination."""
        return normalize_keys(value)

    @model_validator(mode="after")
    def check_action(self):
        """Validate action-specific requirements and reject non-text control characters."""
        # Full default-filled dumps from existing SDK callers remain readable;
        # nondefault fields from other operations must never be silently ignored.
        allowed = ACTION_FIELDS.get(self.kind, set()) | {"kind", "reason"}
        for name in self.model_fields_set - allowed:
            default = type(self).model_fields[name].get_default(call_default_factory=True)  # pylint: disable=unsubscriptable-object
            if getattr(self, name) != default:
                raise ValueError(f"unsupported field for {self.kind}: {name}")
        if self.kind == "click":
            if self.element_id:
                if self.x is not None or self.y is not None:
                    raise ValueError("click requires element_id or screenshot x/y, never both")
            elif self.x is None or self.y is None or not all(math.isfinite(v) for v in (self.x, self.y)):
                raise ValueError("click requires finite screenshot x and y")
        elif self.element_id and self.kind != "focus":
            raise ValueError("element_id is supported only for click or focus")
        if self.kind in {"move", "hold_mouse"} and (self.x is None or self.y is None):
            raise ValueError(f"{self.kind} requires screenshot x and y")
        if self.kind == "scroll" and (self.x is None) != (self.y is None):
            raise ValueError("scroll needs both x and y, or neither for the window center")
        if self.x is not None or self.y is not None:
            if not all(v is not None and math.isfinite(v) and v >= 0 for v in (self.x, self.y)):
                raise ValueError("coordinates must be finite nonnegative screenshot pixels")
        if self.kind == "drag":
            if len(self.path) < 2 or not all(math.isfinite(v) and v >= 0 for point in self.path for v in point):
                raise ValueError("drag requires 2-64 finite nonnegative screenshot-pixel pairs")
        if self.kind == "focus" and sum(bool(v) for v in (self.target, self.window_id, self.element_id)) != 1:
            raise ValueError("focus requires exactly one of target, window_id or element_id")
        if self.kind == "launch" and not self.target:
            raise ValueError("launch requires a configured target")
        if self.kind in {"shortcut", "hold_key"}:
            validate_keys(self.keys)
        validate_modifiers(self.modifiers)
        if any(ord(c) < 32 and c not in "\n\t" for c in self.text):
            raise ValueError("control characters are not text input")
        if self.input_mode == "keyboard" and any(c in "\n\t" for c in self.text):
            raise ValueError("keyboard text cannot include tab or newline; observe before navigation")
        return self


class Target(StrictModel):
    """Host-selected windows in one application; the model cannot extend this grant."""

    name: str = Field(min_length=1, max_length=80)
    pid: int | None = Field(default=None, gt=1)
    bundle_id: str = ""
    allow_launch: bool = False
    launch_arguments: list[str] = Field(default_factory=list, max_length=16)
    window_title: str = Field(default="", max_length=256)
    accessibility_window_title: str = Field(default="", max_length=256)
    window_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list, max_length=64)
    share_attached_sheets: bool = True
    share_opened_dialogs: bool = False

    @property
    def ax_window_title(self):
        """Exact AX identity; preserve existing targets when both APIs agree."""
        return self.accessibility_window_title or self.window_title

    @model_validator(mode="after")
    def identity_required(self):
        """Require an explicit host-selected process or application bundle."""
        if self.pid is None and not self.bundle_id:
            raise ValueError("target requires pid or bundle_id")
        if self.accessibility_window_title and not self.window_title:
            raise ValueError("a separate accessibility title requires a Quartz window title")
        if len(self.window_ids) != len(set(self.window_ids)) or (self.window_ids and self.window_title):
            raise ValueError("select unique window_ids or one exact window_title, not both")
        if self.launch_arguments and (
            not self.allow_launch or any(len(a) > 4096 or "\x00" in a for a in self.launch_arguments)
        ):
            raise ValueError("launch_arguments require allow_launch and bounded strings without NUL")
        if self.allow_launch and (not self.bundle_id or self.pid is not None or self.window_title or self.window_ids):
            raise ValueError("allow_launch requires a bundle_id without PID or window-title restrictions")
        return self


class Budgets(StrictModel):
    """Hard ceilings for one task and each native operation."""

    max_steps: int = Field(default=24, ge=1, le=100)
    wall_seconds: float | int = Field(default=180, gt=0, le=1800)
    action_seconds: float | int = Field(default=8, gt=0, le=30)


class DesktopSettings(StrictModel):
    """Immutable host configuration; disabled by default for headless startup."""

    enabled: bool = False
    execution_mode: Literal["direct"] = "direct"
    targets: list[Target] = Field(default_factory=list, max_length=8)
    budgets: Budgets = Field(default_factory=Budgets)
    screenshot_max_side: int = Field(default=1280, ge=320, le=1600)
    evidence_dir: str | None = None
    display_id: int | None = Field(default=None, gt=0)
    max_batch_actions: int = Field(default=8, ge=1, le=8)
    stall_click_limit: int = Field(default=3, ge=2, le=10)
    stop_on_external_input: bool = False
    accessibility_enabled: bool = False
    window_navigation: bool = False
    capture_backend: Literal["auto", "screencapture", "screencapturekit"] = "auto"
    optimized_batches: bool = True
    compact_tool_results: bool = True
    active_window_screenshots: bool = False
    observation_settle_seconds: float = Field(default=0, ge=0, le=0.5)

    @model_validator(mode="after")
    def unique_targets(self):
        """Reject ambiguous names and incomplete enabled configurations."""
        names = [t.name for t in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        if not self.window_navigation and any(t.window_ids for t in self.targets):
            raise ValueError("shared window_ids require window_navigation")
        if any(t.allow_launch for t in self.targets) and (
            not self.window_navigation or self.execution_mode != "direct"
        ):
            raise ValueError("allow_launch requires direct execution and window_navigation")
        if self.enabled and not self.targets:
            raise ValueError("enabled desktop requires targets")
        return self


@dataclass(frozen=True)
class Observation:
    """One window or scoped-display bitmap paired with its desktop-point geometry."""

    png_b64: str
    width: int
    height: int
    # Captured region in desktop points (not native backing pixels).
    bounds: tuple[float, float, float, float]
    target: str
    window_id: int
    generation: int
    elements: list[dict] = field(default_factory=list)
    accessibility_status: str = "disabled"
    context: dict | None = None
    cursor: dict[str, float] | None = None
    image_digest: str = ""
    performance: dict = field(default_factory=dict)

    def point(self, x: float, y: float) -> tuple[float, float]:
        """Map a finite screenshot pixel into the captured region's bounds."""
        left, top, width, height = self.bounds
        geometry = ScreenshotGeometry(
            pixel_width=self.width,
            pixel_height=self.height,
            desktop_rect=DesktopRect(x=left, y=top, width=width, height=height),
        )
        return geometry.to_desktop(x, y)


class DesktopDriver(Protocol):
    """Platform-neutral asynchronous screenshot and input contract."""

    async def start(self) -> None:
        """Initialize the executor without posting input."""

    async def observe(self) -> Observation:
        """Capture the configured window or scoped display and coordinate transform."""

    async def act(self, action: Action, observation: Observation) -> None:
        """Apply one action derived from the fresh observation."""

    async def close(self) -> None:
        """Fence new input and reap only owned resources."""
