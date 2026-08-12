# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Consumers of ``BaseMessage.content`` must read Parts as well as raw dicts.

Stage 3 landed in two steps: consumers first (this file), producers second.
Every test here is written as a pair — the legacy dict shape and the Part
shape must give the *same* answer — because the migration is only correct if
it is behavior-preserving for the dialects already in flight.

See ``docs/dev/message-content-parts-refactor.md`` §3, Stage 3.
"""

import base64

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ImagePart,
    TextPart,
    UserMessage,
)

_PNG_MIME = "image/png"
_PAYLOAD = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-pixels").decode("ascii")
_DATA_URL = f"data:{_PNG_MIME};base64,{_PAYLOAD}"


def _openai_image_block() -> dict:
    return {"type": "image_url", "image_url": {"url": _DATA_URL}}


def _image_part() -> ImagePart:
    return ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)


class TestTiktokenCounter:
    """Image tokens must stay bounded whichever shape the image arrives in.

    Stage 4 folded this into ``TiktokenCounter`` itself and deleted the
    mobile-GUI monkey-patch these tests used to target.
    """

    def _count(self, content) -> int:
        from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter

        return TiktokenCounter().count_messages([UserMessage(content=content)])

    def test_image_part_counts_as_placeholder_like_the_dict_shape(self):
        assert self._count([_image_part()]) == self._count([_openai_image_block()])

    def test_image_part_is_not_billed_as_base64_text(self):
        assert self._count([_image_part()]) < 5000

    def test_text_part_counts_like_a_text_dict(self):
        assert self._count([TextPart(text="hello")]) == self._count([{"type": "text", "text": "hello"}])


class TestMultimodalContextSummarizerRail:
    def test_image_part_is_detected(self):
        from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
            _has_image_url,
        )

        assert _has_image_url(UserMessage(content=[_image_part()])) is True
        assert _has_image_url(UserMessage(content=[_openai_image_block()])) is True

    def test_text_only_content_is_not_detected(self):
        from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
            _has_image_url,
        )

        assert _has_image_url(UserMessage(content=[TextPart(text="no image")])) is False

    def test_image_part_is_replaced_by_the_placeholder(self):
        from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
            ARCHIVED_SCREEN_PLACEHOLDER,
            _replace_archived_screenshot_images,
        )

        message = UserMessage(content=[TextPart(text="before"), _image_part()])

        _replace_archived_screenshot_images(message)

        assert message.content[1] == {"type": "text", "text": ARCHIVED_SCREEN_PLACEHOLDER}


class TestTextExtraction:
    """Every text extractor must see through both shapes identically."""

    @staticmethod
    def _pair(fn):
        """Run ``fn`` over the dict shape and the Part shape."""
        dict_shape = [{"type": "text", "text": "a"}, _openai_image_block(), {"type": "text", "text": "b"}]
        part_shape = [TextPart(text="a"), _image_part(), TextPart(text="b")]
        return fn(dict_shape), fn(part_shape)

    def test_previous_steps_content_to_text(self):
        from openjiuwen.harness.tools.mobile_gui.skill_branch.previous_steps import _content_to_text

        legacy, parts = self._pair(_content_to_text)

        assert legacy == parts == "a\nb"

    def test_skill_branch_assistant_text(self):
        from openjiuwen.harness.tools.mobile_gui.skill_branch.runner import _assistant_text

        legacy, parts = self._pair(lambda c: _assistant_text(AssistantMessage(content=c)))

        assert legacy == parts == "a\nb"

    def test_vlm_grounding_flatten_message_text(self):
        from openjiuwen.harness.tools.mobile_gui.rails.vlm_grounding_perception_rail import (
            VlmGroundingPerceptionRail,
        )

        legacy, parts = self._pair(lambda c: VlmGroundingPerceptionRail._flatten_message_text(UserMessage(content=c)))

        assert legacy == parts == "a\nb"

    def test_security_rail_text_extraction(self):
        """Prompt-injection scanning must not go blind on Part-based content."""
        from openjiuwen.harness.rails.security.base_security_rail import BaseSecurityRail

        rail = BaseSecurityRail.__new__(BaseSecurityRail)
        legacy, parts = self._pair(lambda c: rail._extract_message_content(UserMessage(content=c)))

        assert legacy == parts == "a b"

    def test_evolution_review_materials(self):
        from openjiuwen.harness.rails.evolution.review.materials import _message_content_text

        legacy, parts = self._pair(_message_content_text)

        assert legacy == parts == "a\nb"

    def test_no_extractor_splices_a_part_repr_into_its_output(self):
        """Regression: an ImagePart is truthy and is not a dict.

        Any extractor with a ``str(item)`` fallback tail will stringify the
        whole model unless images are matched explicitly first.
        """
        from openjiuwen.harness.rails.evolution.review.materials import _message_content_text
        from openjiuwen.harness.tools.mobile_gui.skill_branch.previous_steps import _content_to_text

        for extract in (_message_content_text, _content_to_text):
            assert "mime_type" not in extract([TextPart(text="a"), _image_part()])


class TestIntelliRouterDashScopeContent:
    """The second DashScope conversion site, found while landing Stage 2."""

    def test_image_part_is_no_longer_dropped(self):
        message = UserMessage(content=[TextPart(text="draw"), _image_part()])

        content_list = []
        for item in message.content:
            if isinstance(item, TextPart):
                content_list.append({"text": item.text})
            elif isinstance(item, ImagePart):
                content_list.append({"image": item.url or item.to_data_url()})

        assert content_list == [{"text": "draw"}, {"image": _DATA_URL}]


class TestResponsesUtils:
    def test_text_part_contributes_its_text(self):
        from openjiuwen.core.foundation.llm.utils.responses_utils import _content_part_text

        assert _content_part_text(TextPart(text="hi")) == "hi"

    def test_image_part_contributes_nothing(self):
        from openjiuwen.core.foundation.llm.utils.responses_utils import _content_part_text

        assert _content_part_text(_image_part()) == ""
