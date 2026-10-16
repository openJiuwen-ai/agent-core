# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mobile GUI shim over the shared multimodal context summarizer rail.

The implementation moved to ``openjiuwen.harness.rails``; this module keeps
the historical import path and the mobile-specific protection default.
"""

from __future__ import annotations

from openjiuwen.harness.rails.multimodal_context_summarizer_rail import (
    ARCHIVED_SCREEN_PLACEHOLDER,
)
from openjiuwen.harness.rails.multimodal_context_summarizer_rail import (
    MultimodalContextSummarizerRail as _SharedMultimodalContextSummarizerRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE,
)

__all__ = [
    "ARCHIVED_SCREEN_PLACEHOLDER",
    "MultimodalContextSummarizerRail",
]


class MultimodalContextSummarizerRail(_SharedMultimodalContextSummarizerRail):
    """Shared rail preconfigured to protect mobile skill user messages."""

    def __init__(self, screenshots_to_keep: int = 3) -> None:
        super().__init__(
            screenshots_to_keep,
            protected_user_message_names=USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE,
        )
