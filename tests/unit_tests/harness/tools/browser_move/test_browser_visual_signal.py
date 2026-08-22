#!/usr/bin/env python
# coding: utf-8
"""Tests for the visual-only discovery signal emitted by the compact probes."""

from __future__ import annotations

from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    VISUAL_CONTENT_HINT,
    build_card_probe_js,
    build_interactive_probe_js,
)


def test_both_probes_carry_the_visual_scan() -> None:
    for js in (build_interactive_probe_js(), build_card_probe_js()):
        assert "__visualRegions" in js
        assert "visual_content: __visualContent()" in js
        # The template placeholders must be fully substituted.
        assert "__VISUAL_SCAN_JS__" not in js
        assert "__VISUAL_CONTENT_HINT__" not in js


def test_visual_scan_names_browser_vision_as_the_resolution() -> None:
    js = build_interactive_probe_js()

    assert "browser_vision" in VISUAL_CONTENT_HINT
    assert "browser_vision" in js


def test_visual_scan_covers_every_dom_blind_spot() -> None:
    js = build_card_probe_js()

    for kind in ("canvas", "svg_graphic", "image_without_alt", "visual_table"):
        assert f"'{kind}'" in js


def test_card_probe_marks_visual_only_cards() -> None:
    js = build_card_probe_js()

    assert "visualOnlyKind" in js
    assert "visual_only: visualOnly || null" in js
    # has_image stays: visual_only is an addition, not a replacement.
    assert "has_image: imagePresent" in js
