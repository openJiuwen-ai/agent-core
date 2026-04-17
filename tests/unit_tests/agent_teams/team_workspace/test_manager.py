# coding: utf-8

"""Guard tests for team workspace manager."""

import pytest


@pytest.mark.skip(reason="Temporarily skipped in Linux CI due Windows path simulation causing pytest internal errors")
def test_mount_into_workspace_falls_back_to_junction_on_windows_1314():
    """Placeholder to keep the flaky Windows-specific case skipped in CI."""
