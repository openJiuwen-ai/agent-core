# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any


def unwrap_xy_coords(x: Any, y: Any) -> tuple[Any, Any]:
    """Map routinely malformed LLM tool JSON ([x,y] under one key) onto scalar x/y.

    Preferred shape remains two JSON numbers (`x` and `y`). Models still often emit a
    single two-element list for one coordinate pair; unwrap so the gesture still runs.
    """
    if isinstance(x, (list, tuple)) and len(x) == 2 and y is None:
        return x[0], x[1]
    if isinstance(y, (list, tuple)) and len(y) == 2 and x is None:
        return y[0], y[1]
    return x, y


def _metadata_dimensions_positive(
    width_i: int,
    height_i: int,
    scale_x_i: int,
    scale_y_i: int,
) -> bool:
    return width_i > 0 and height_i > 0 and scale_x_i > 0 and scale_y_i > 0


def _normalized_coords_in_range(
    x_f: float,
    y_f: float,
    scale_x: int,
    scale_y: int,
) -> bool:
    return 0 <= x_f <= scale_x and 0 <= y_f <= scale_y


def get_vlm_screen_metadata(extra: dict) -> tuple[dict[str, int], str | None]:
    width = extra.get("vlm_screen_width")
    height = extra.get("vlm_screen_height")
    scale_x = extra.get(
        "vlm_coordinate_scale_x",
        extra.get("vlm_coordinate_scale", 1000),
    )
    scale_y = extra.get(
        "vlm_coordinate_scale_y",
        extra.get("vlm_coordinate_scale", 1000),
    )

    try:
        width_i = int(width)
        height_i = int(height)
        scale_x_i = int(scale_x)
        scale_y_i = int(scale_y)
    except (TypeError, ValueError):
        return {}, (
            "Error: VlmScreenMetadataMissing: latest VLM screenshot metadata is "
            "not available. Ensure VlmGroundingPerceptionRail ran before coordinate tools."
        )

    if not _metadata_dimensions_positive(width_i, height_i, scale_x_i, scale_y_i):
        return {}, (
            "Error: VlmScreenMetadataInvalid: screen width, height, and coordinate "
            "scales must be positive."
        )

    return {
        "width": width_i,
        "height": height_i,
        "scale_x": scale_x_i,
        "scale_y": scale_y_i,
    }, None


def normalized_to_pixel(
    x: Any,
    y: Any,
    *,
    width: int,
    height: int,
    scale: int | None = None,
    scale_x: int | None = None,
    scale_y: int | None = None,
) -> tuple[tuple[int, int] | None, str | None]:
    x, y = unwrap_xy_coords(x, y)
    try:
        x_f = float(x)
        y_f = float(y)
    except (TypeError, ValueError):
        return None, f"Error: InvalidCoordinate: coordinates must be numeric, got ({x}, {y})."

    if scale is not None:
        scale_x = scale
        scale_y = scale
    if scale_x is None:
        scale_x = 1000
    if scale_y is None:
        scale_y = 1000

    if not _normalized_coords_in_range(x_f, y_f, scale_x, scale_y):
        return None, (
            f"Error: CoordinateOutOfRange: coordinates ({x_f:g}, {y_f:g}) must be "
            f"inside x=[0, {scale_x}], y=[0, {scale_y}]."
        )

    px = int(round(x_f * width / scale_x))
    py = int(round(y_f * height / scale_y))
    px = max(0, min(width - 1, px))
    py = max(0, min(height - 1, py))
    return (px, py), None


def resolve_vlm_pixel(
    extra: dict,
    x: Any,
    y: Any,
) -> tuple[tuple[int, int] | None, str | None]:
    metadata, error = get_vlm_screen_metadata(extra)
    if error:
        return None, error
    return normalized_to_pixel(
        x,
        y,
        width=metadata["width"],
        height=metadata["height"],
        scale_x=metadata["scale_x"],
        scale_y=metadata["scale_y"],
    )
