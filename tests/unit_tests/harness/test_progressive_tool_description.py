# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for deferred-tool directory summaries."""

import pytest

from openjiuwen.harness.prompts.sections.progressive_tool_rail import (
    _compact_tool_description,
    render_deferred_tool_catalog_snapshot,
)


@pytest.mark.parametrize(
    "description",
    [
        "Insert slides from a .pptx file the user supplied into the open presentation, "
        "keeping or dropping the source formatting as requested by the user.",
        "Uses the v1.2 schema to validate the document and returns a list of problems found, "
        "one per line, with their location in the file.",
    ],
)
def test_embedded_period_does_not_end_summary(description):
    assert _compact_tool_description(description) == description[:119].rstrip() + "…"


def test_real_sentence_ending_after_filename():
    description = "Read a .pptx file. " + "Additional details " * 10
    assert _compact_tool_description(description) == "Read a .pptx file."


def test_chinese_sentence_ending_needs_no_space():
    description = "读取页面文字。" + "返回页面上的文字内容" * 20
    assert _compact_tool_description(description) == "读取页面文字。"


def test_short_description_is_unchanged_after_whitespace_normalization():
    assert _compact_tool_description("  Read\n a .pptx file.  ") == "Read a .pptx file."


def test_abbreviation_followed_by_space_is_still_a_sentence_boundary():
    description = "Read files e.g. slides or documents, " + "with extra details " * 10
    assert _compact_tool_description(description) == "Read files e.g."


def test_catalog_renders_description_without_cutting_at_file_extension():
    description = "Import a .pptx file. " + "More information " * 10
    result = render_deferred_tool_catalog_snapshot({"import_slides": description}, language="en")
    assert "- **import_slides**: Import a .pptx file." in result
