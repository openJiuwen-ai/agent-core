"""Tool-local rollback/measurement switch; never modifies host configuration."""

import os


def performance_settings(settings):
    """Apply the tool-local rollback profile without changing host settings."""
    profile = os.environ.get("DESKTOP_TOOL_PERFORMANCE", "optimized")
    if profile == "optimized":
        return settings
    if profile == "legacy":
        return settings.model_copy(
            update={
                "optimized_batches": False,
                "capture_backend": "screencapture",
                "compact_tool_results": False,
                "active_window_screenshots": False,
                "observation_settle_seconds": 0,
            }
        )
    raise ValueError("DESKTOP_TOOL_PERFORMANCE must be optimized or legacy")
