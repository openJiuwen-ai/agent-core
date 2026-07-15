#!/usr/bin/env python
# coding: utf-8
"""Tests for KeypointProposer._segmentation_overlay: pure numpy/PIL compositing, so
it (unlike the rest of the class) doesn't need the DINOv2/MobileSAM extras installed
to construct -- it's a @staticmethod, called directly on the class."""

from __future__ import annotations

import numpy as np

from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.keypoint_proposal import (
    KeypointProposer,
)


def test_segmentation_overlay_blends_color_only_inside_mask() -> None:
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True

    overlay = KeypointProposer._segmentation_overlay(rgb, [mask])

    assert overlay.shape == rgb.shape
    assert overlay.dtype == np.uint8
    assert overlay[3, 3].sum() > 0  # inside the mask: blended toward the palette color
    assert overlay[0, 0].sum() == 0  # outside the mask: untouched


def test_segmentation_overlay_cycles_palette_for_multiple_masks() -> None:
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    mask_a = np.zeros((4, 4), dtype=bool)
    mask_a[0, 0] = True
    mask_b = np.zeros((4, 4), dtype=bool)
    mask_b[3, 3] = True

    overlay = KeypointProposer._segmentation_overlay(rgb, [mask_a, mask_b])

    assert overlay[0, 0].sum() > 0
    assert overlay[3, 3].sum() > 0
    assert not np.array_equal(overlay[0, 0], overlay[3, 3])  # different masks -> different palette colors


def test_segmentation_overlay_no_masks_returns_original() -> None:
    rgb = np.random.default_rng(0).integers(0, 255, size=(5, 5, 3), dtype=np.uint8)

    overlay = KeypointProposer._segmentation_overlay(rgb, [])

    assert np.array_equal(overlay, rgb)
