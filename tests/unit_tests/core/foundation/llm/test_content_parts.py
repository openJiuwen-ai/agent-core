# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the provider-neutral content part schema.

Covers the part models themselves. Their interaction with ``BaseMessage`` is
covered in ``test_message_content_normalization.py``.
"""

import base64

import pytest
from pydantic import TypeAdapter, ValidationError

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import ContentPart, ImagePart, TextPart
from openjiuwen.core.foundation.llm.schema.content_part import DEFAULT_IMAGE_TOKENS

_PNG_MIME = "image/png"
_PAYLOAD = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-pixels").decode("ascii")
_DATA_URL = f"data:{_PNG_MIME};base64,{_PAYLOAD}"


class TestConstruction:
    def test_text_part_requires_text(self):
        with pytest.raises(ValidationError):
            TextPart()

    def test_image_part_requires_mime_type(self):
        with pytest.raises(ValidationError):
            ImagePart(data=_PAYLOAD)

    def test_image_part_rejects_both_data_and_url(self):
        with pytest.raises(ValidationError, match="exactly one"):
            ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, url="https://example.invalid/a.png")

    def test_image_part_rejects_neither(self):
        with pytest.raises(ValidationError, match="exactly one"):
            ImagePart(mime_type=_PNG_MIME)

    def test_type_discriminators_are_defaulted(self):
        assert TextPart(text="x").type == "text"
        assert ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD).type == "image"


class TestDiscriminator:
    def test_unknown_type_reports_against_matching_branch_only(self):
        """The discriminator must not report a failure from every union member."""
        adapter = TypeAdapter(ContentPart)

        with pytest.raises(ValidationError) as excinfo:
            adapter.validate_python({"type": "image", "text": "not an image"})

        errors = excinfo.value.errors()
        assert all(error["loc"][0] == "image" for error in errors), errors
        assert any("mime_type" in error["loc"] for error in errors), errors

    def test_unknown_tag_names_the_allowed_tags(self):
        adapter = TypeAdapter(ContentPart)

        with pytest.raises(ValidationError) as excinfo:
            adapter.validate_python({"type": "audio", "url": "https://example.invalid/a.mp3"})

        assert "union_tag_invalid" in str(excinfo.value)


class TestDataUrl:
    def test_from_data_url_roundtrip(self):
        part = ImagePart.from_data_url(_DATA_URL)

        assert part.mime_type == _PNG_MIME
        assert part.data == _PAYLOAD
        assert part.url is None
        assert part.to_data_url() == _DATA_URL

    def test_from_data_url_accepts_field_overrides(self):
        part = ImagePart.from_data_url(_DATA_URL, detail="low", width=64, height=32)

        assert (part.detail, part.width, part.height) == ("low", 64, 32)

    def test_from_data_url_rejects_non_data_scheme(self):
        with pytest.raises(BaseError, match="data:"):
            ImagePart.from_data_url("https://example.invalid/a.png")

    def test_from_data_url_rejects_missing_base64_marker(self):
        with pytest.raises(BaseError, match="base64"):
            ImagePart.from_data_url("data:image/png,raw-bytes")

    def test_from_data_url_rejects_malformed_base64(self):
        with pytest.raises(BaseError, match="valid base64"):
            ImagePart.from_data_url("data:image/png;base64,!!!not-base64!!!")

    def test_from_data_url_rejects_missing_media_type(self):
        with pytest.raises(BaseError, match="media type"):
            ImagePart.from_data_url(f"data:;base64,{_PAYLOAD}")

    def test_to_data_url_rejects_url_backed_part(self):
        part = ImagePart(mime_type=_PNG_MIME, url="https://example.invalid/a.png")

        with pytest.raises(BaseError, match="url-backed"):
            part.to_data_url()


class TestDataUrlUnchecked:
    """The lenient counterpart, for pipelines that forwarded bytes unvalidated.

    Every case here is one that :meth:`from_data_url` rejects. Dropping such a
    payload would silently lose an image the model is meant to see, so these
    must degrade rather than raise.
    """

    def test_well_formed_url_matches_the_strict_parser(self):
        assert ImagePart.from_data_url_unchecked(_DATA_URL) == ImagePart.from_data_url(_DATA_URL)

    def test_accepts_field_overrides(self):
        part = ImagePart.from_data_url_unchecked(_DATA_URL, detail="low", width=8, height=4)

        assert (part.detail, part.width, part.height) == ("low", 8, 4)

    def test_malformed_base64_is_kept_as_data(self):
        part = ImagePart.from_data_url_unchecked("data:image/png;base64,!!!not-base64!!!")

        assert part.data == "!!!not-base64!!!"
        assert part.mime_type == _PNG_MIME

    def test_unpadded_base64_is_kept_as_data(self):
        assert ImagePart.from_data_url_unchecked("data:image/png;base64,abc").data == "abc"

    def test_missing_media_type_falls_back_to_png(self):
        assert ImagePart.from_data_url_unchecked(f"data:;base64,{_PAYLOAD}").mime_type == _PNG_MIME

    def test_non_base64_payload_becomes_a_url(self):
        """``ImagePart`` demands exactly one of data/url, so an empty ``data``
        would raise rather than degrade."""
        part = ImagePart.from_data_url_unchecked("data:image/svg+xml,%3Csvg%2F%3E")

        assert part.data is None
        assert part.url == "data:image/svg+xml,%3Csvg%2F%3E"

    def test_non_base64_payload_reads_only_the_media_type(self):
        """The payload must not be swallowed into ``mime_type`` by the split."""
        part = ImagePart.from_data_url_unchecked("data:image/svg+xml,%3Csvg%2F%3E")

        assert part.mime_type == "image/svg+xml"

    def test_media_type_parameters_are_stripped(self):
        part = ImagePart.from_data_url_unchecked("data:image/svg+xml;charset=utf-8,%3Csvg%2F%3E")

        assert part.mime_type == "image/svg+xml"

    def test_never_raises_on_any_of_the_strict_parser_rejections(self):
        for url in (
            "data:image/png,raw-bytes",
            "data:image/png;base64,!!!",
            f"data:;base64,{_PAYLOAD}",
            "data:",
        ):
            assert isinstance(ImagePart.from_data_url_unchecked(url), ImagePart)


class TestEstimatedTokens:
    def test_estimated_tokens_scales_with_dimensions(self):
        small = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=256, height=256)
        large = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=2048, height=2048)

        assert small.estimated_tokens("openai") < large.estimated_tokens("openai")

    def test_estimated_tokens_differs_by_provider(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=1024, height=1024)

        assert part.estimated_tokens("openai") != part.estimated_tokens("anthropic")

    def test_estimated_tokens_falls_back_without_dimensions(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD)

        assert part.estimated_tokens("openai") == DEFAULT_IMAGE_TOKENS

    def test_estimated_tokens_falls_back_for_unknown_provider(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=1024, height=1024)

        assert part.estimated_tokens("some-local-vllm") == DEFAULT_IMAGE_TOKENS

    def test_provider_matching_is_case_insensitive(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=1024, height=1024)

        assert part.estimated_tokens("OpenAI") == part.estimated_tokens("openai")

    def test_low_detail_is_charged_a_flat_rate_on_openai(self):
        """OpenAI bills low-detail images at the base rate regardless of size."""
        small = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=256, height=256, detail="low")
        large = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=4096, height=4096, detail="low")

        assert small.estimated_tokens("openai") == large.estimated_tokens("openai") == 85

    def test_oversized_image_is_capped_on_anthropic(self):
        part = ImagePart(mime_type=_PNG_MIME, data=_PAYLOAD, width=8000, height=8000)

        assert part.estimated_tokens("anthropic") == 1600
