# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The former runtime monkey-patch must now be a harmless no-op.

Core's TiktokenCounter counts multimodal content natively, so the patch's only
remaining job is backward compatibility: existing callers must keep working,
and calling it must NOT swap methods on the class — the process-global
first-caller-wins method swap was the hazard that motivated the core fix.
"""

from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.tools.mobile_gui.tiktoken_multimodal_patch import (
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,
    apply_tiktoken_counter_multimodal_patch,
)


def test_apply_is_a_no_op_and_multimodal_counting_needs_no_patch() -> None:
    original = TiktokenCounter.count_messages

    multimodal = [
        UserMessage(
            content=[
                {"type": "text", "text": "screen"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 50_000}},
            ]
        )
    ]
    counter = TiktokenCounter()
    before = counter.count_messages(multimodal)

    apply_tiktoken_counter_multimodal_patch()

    assert TiktokenCounter.count_messages is original, "patch must no longer swap methods on the class"
    assert counter.count_messages(multimodal) == before


def test_placeholder_constant_still_importable_from_legacy_path() -> None:
    assert DEFAULT_IMAGE_PLACEHOLDER_TOKENS > 0
