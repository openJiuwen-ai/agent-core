# coding: utf-8
"""Tests for mobile_gui coordinate_utils (VLM metadata and pixel mapping)."""

from __future__ import annotations

import pytest

from openjiuwen.harness.tools.mobile_gui.coordinate_utils import (
    get_vlm_screen_metadata,
    normalized_to_pixel,
    resolve_vlm_pixel,
    unwrap_xy_coords,
)


@pytest.mark.parametrize(
    "x,y,expected",
    [
        ([100, 200], None, (100, 200)),
        (None, [50, 60], (50, 60)),
        (10, 20, (10, 20)),
    ],
)
def test_unwrap_xy_coords_handles_list_and_scalar_pairs(x, y, expected):
    assert unwrap_xy_coords(x, y) == expected


def test_get_vlm_screen_metadata_missing_fields_returns_error():
    meta, err = get_vlm_screen_metadata({})
    assert meta == {}
    assert err is not None
    assert "VlmScreenMetadataMissing" in err


def test_get_vlm_screen_metadata_invalid_dimensions_returns_error():
    meta, err = get_vlm_screen_metadata(
        {
            "vlm_screen_width": 0,
            "vlm_screen_height": 1080,
            "vlm_coordinate_scale": 1000,
        }
    )
    assert meta == {}
    assert err is not None
    assert "VlmScreenMetadataInvalid" in err


def test_get_vlm_screen_metadata_success_with_split_scales():
    meta, err = get_vlm_screen_metadata(
        {
            "vlm_screen_width": 1080,
            "vlm_screen_height": 1920,
            "vlm_coordinate_scale_x": 800,
            "vlm_coordinate_scale_y": 600,
        }
    )
    assert err is None
    assert meta == {"width": 1080, "height": 1920, "scale_x": 800, "scale_y": 600}


def test_normalized_to_pixel_maps_center_of_normalized_range():
    point, err = normalized_to_pixel(500, 500, width=100, height=200, scale=1000)
    assert err is None
    assert point == (50, 100)


def test_normalized_to_pixel_clamps_to_screen_edges():
    point, err = normalized_to_pixel(1000, 1000, width=10, height=10, scale=1000)
    assert err is None
    assert point == (9, 9)


def test_normalized_to_pixel_rejects_out_of_range_coordinates():
    point, err = normalized_to_pixel(1001, 0, width=100, height=100, scale=1000)
    assert point is None
    assert err is not None
    assert "CoordinateOutOfRange" in err


def test_normalized_to_pixel_rejects_non_numeric_coordinates():
    point, err = normalized_to_pixel("left", 0, width=100, height=100, scale=1000)
    assert point is None
    assert "InvalidCoordinate" in (err or "")


def test_resolve_vlm_pixel_end_to_end():
    extra = {
        "vlm_screen_width": 200,
        "vlm_screen_height": 400,
        "vlm_coordinate_scale": 1000,
    }
    point, err = resolve_vlm_pixel(extra, 250, 750)
    assert err is None
    assert point == (50, 300)


def test_resolve_vlm_pixel_propagates_missing_metadata():
    point, err = resolve_vlm_pixel({}, 1, 1)
    assert point is None
    assert "VlmScreenMetadataMissing" in (err or "")
