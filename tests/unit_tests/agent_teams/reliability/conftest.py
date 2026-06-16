# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pytest config for reliability tests.

Defaults every reliability test to ``level0`` (smoke / core) unless it already
carries an explicit level marker (e.g. ``test_integration`` is ``level1``).
Keeps the suite's "every test is level-tagged" invariant without a per-file
``pytestmark`` on each module.
"""

import pytest


def pytest_collection_modifyitems(items: list) -> None:
    """Tag un-marked reliability tests as level0."""
    for item in items:
        if "reliability" not in str(item.fspath):
            continue
        if item.get_closest_marker("level0") or item.get_closest_marker("level1"):
            continue
        item.add_marker(pytest.mark.level0)
