# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for TiktokenCounter's native multimodal message counting.

``BaseMessage.content`` is ``Union[str, List[Union[str, dict]]]``, so block-list
content is a first-class message shape and the counter must price it the way a
provider does: text as text, images as a fixed per-image cost. Counting an
image's base64 payload as text overstates it by orders of magnitude, which
made context compression fire long before the window was actually full — the
bug that previously required a process-global monkey-patch to work around.
"""

import base64
import json

from openjiuwen.core.context_engine.token.tiktoken_counter import (
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,
    TiktokenCounter,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage

# Decodes to non-image bytes: exercises the unparseable-payload fallback.
_FAKE_DATA_URL = "data:image/jpeg;base64," + ("A" * 200_000)


def _counter() -> TiktokenCounter:
    return TiktokenCounter()


def _png_data_url(width: int, height: int) -> str:
    header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    return "data:image/png;base64," + base64.b64encode(header).decode()


def _jpeg_data_url(width: int, height: int) -> str:
    sof0 = (
        b"\xff\xd8"
        + b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + b"\x00" * 9
    )
    return "data:image/jpeg;base64," + base64.b64encode(sof0).decode()


def _image_message(url: str) -> list:
    return [UserMessage(content=[{"type": "image_url", "image_url": {"url": url}}])]


def test_single_text_block_counts_like_plain_string() -> None:
    """Wrapping the same text in a one-element block list must not change the
    count: downstream budgets would otherwise shift merely because a message
    was built via the multimodal path."""
    counter = _counter()
    as_string = counter.count_messages([UserMessage(content="hello desktop world")])
    as_block = counter.count_messages([UserMessage(content=[{"type": "text", "text": "hello desktop world"}])])
    assert as_block == as_string


def test_unparseable_image_costs_flat_fallback_not_payload() -> None:
    """A 200 KB data URL whose pixels can't be inspected must still be priced
    as one flat image, not ~50k tokens of 'text' — the entire point of
    multimodal-aware counting."""
    counter = _counter()
    without_image = counter.count_messages([UserMessage(content=[{"type": "text", "text": "look at this"}])])
    with_image = counter.count_messages(
        [
            UserMessage(
                content=[
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {"url": _FAKE_DATA_URL}},
                ]
            )
        ]
    )
    assert with_image - without_image == DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    # Sanity anchor: the payload itself would count vastly larger under either
    # the tiktoken or the len//4 fallback path.
    assert with_image < counter.count(_FAKE_DATA_URL)


def test_image_tokens_scale_with_dimensions() -> None:
    """A small image must cost a fraction of a full screenshot: pricing every
    image at the phone-screenshot worst case over-budgets sessions that attach
    icons or thumbnails. The estimate is the max of the OpenAI tile formula
    (85 + 170/tile of 512px after provider downscaling) and the Anthropic
    patch formula (one token per 28x28px patch, capped at 1568) — budgeting
    must err high across providers, not pick one billing scheme."""
    counter = _counter()
    # The non-image overhead (role wrapper + reserve) is identical across all
    # single-image messages, so it can be measured once via the fallback case.
    base = counter.count_messages(_image_message(_FAKE_DATA_URL)) - DEFAULT_IMAGE_PLACEHOLDER_TOKENS

    # 100x100: tiles 85+170=255 dominate the 4x4=16 patches.
    small = counter.count_messages(_image_message(_png_data_url(100, 100)))
    assert small - base == 255

    # 2560x1440 (a desktop screenshot): tiles say 1105, patches hit the 1568 cap.
    screenshot = counter.count_messages(_image_message(_png_data_url(2560, 1440)))
    assert screenshot - base == 1568

    # JPEG header parsing goes through the SOF walk, not the PNG fast path.
    # 512x512: 19x19=361 patches beat the single 255-token tile.
    jpeg = counter.count_messages(_image_message(_jpeg_data_url(512, 512)))
    assert jpeg - base == 361


def test_producer_stamped_dimensions_skip_payload_parsing() -> None:
    """A rail that resizes screenshots knows the final size; stamping it on
    the block must be believed without decoding anything — including blocks
    whose payload is unparseable or remote."""
    counter = _counter()
    base = counter.count_messages(_image_message(_FAKE_DATA_URL)) - DEFAULT_IMAGE_PLACEHOLDER_TOKENS

    stamped = counter.count_messages(
        [
            UserMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/screen.png"},
                        "width": 100,
                        "height": 100,
                    }
                ]
            )
        ]
    )
    assert stamped - base == 255

    dimensions_string = counter.count_messages(
        [UserMessage(content=[{"type": "image_url", "image_url": {"url": _FAKE_DATA_URL}, "dimensions": "100x100"}])]
    )
    assert dimensions_string - base == 255


def test_low_detail_requests_cost_flat_low_rate() -> None:
    """OpenAI bills detail:'low' images a flat 85 regardless of size; pricing
    them at full estimate would overstate deliberately-cheap perception."""
    counter = _counter()
    base = counter.count_messages(_image_message(_FAKE_DATA_URL)) - DEFAULT_IMAGE_PLACEHOLDER_TOKENS

    low = counter.count_messages(
        [UserMessage(content=[{"type": "image_url", "image_url": {"url": _png_data_url(2560, 1440), "detail": "low"}}])]
    )
    assert low - base == 85


def test_data_url_inside_plain_string_content_does_not_balloon() -> None:
    """The ballooning bug through a side door: a data URL pasted into ordinary
    string content (tool results, logs) must be counted as a placeholder, not
    as tens of thousands of base64 'text' tokens."""
    counter = _counter()
    embedded = counter.count_messages([UserMessage(content=f"see screenshot: {_FAKE_DATA_URL} end")])
    clean = counter.count_messages([UserMessage(content="see screenshot: [image:data-url-omitted] end")])
    assert embedded == clean


def test_data_url_inside_unknown_block_does_not_balloon() -> None:
    """Unknown block types are counted from their JSON — which must be
    sanitized first, or any novel block shape smuggles the payload back in."""
    counter = _counter()
    smuggled = counter.count_messages([UserMessage(content=[{"type": "video_frame", "data_url": _FAKE_DATA_URL}])])
    assert smuggled < counter.count(_FAKE_DATA_URL)


def test_remote_image_url_costs_flat_fallback() -> None:
    """No payload to inspect (http URL) -> the flat fallback, never zero:
    dropping remote images from the budget would hide real context cost."""
    counter = _counter()
    remote = counter.count_messages(_image_message("https://example.com/screen.png"))
    unparseable = counter.count_messages(_image_message(_FAKE_DATA_URL))
    assert remote == unparseable


def test_env_override_beats_dimension_estimate(monkeypatch) -> None:
    """An operator who calibrated the per-image cost must keep that exact
    number even for images the estimator could price itself."""
    counter = _counter()
    message = _image_message(_png_data_url(100, 100))
    estimated = counter.count_messages(message)

    monkeypatch.setenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS", "999")
    overridden = counter.count_messages(message)
    assert overridden - estimated == 999 - (85 + 170 * 1)


def test_image_placeholder_env_override(monkeypatch) -> None:
    """Deployments calibrate per-image cost to their provider via env, so the
    override must reach the counting path — and garbage must not."""
    counter = _counter()
    message = [UserMessage(content=[{"type": "image_url", "image_url": {"url": _FAKE_DATA_URL}}])]
    baseline = counter.count_messages(message)

    monkeypatch.setenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS", "500")
    assert counter.count_messages(message) == baseline - DEFAULT_IMAGE_PLACEHOLDER_TOKENS + 500

    monkeypatch.setenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS", "not-a-number")
    assert counter.count_messages(message) == baseline

    monkeypatch.setenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS", "-5")
    assert counter.count_messages(message) == baseline


def test_unknown_block_counted_from_compact_json() -> None:
    """Unknown block types must cost something proportional to their real
    payload rather than being silently dropped from the budget."""
    counter = _counter()
    block = {"type": "audio", "audio": {"id": "clip-1", "transcript": "hello " * 50}}
    without_block = counter.count_messages([UserMessage(content=[{"type": "text", "text": "note"}])])
    with_block = counter.count_messages([UserMessage(content=[{"type": "text", "text": "note"}, block])])
    expected = counter.count(json.dumps(block, ensure_ascii=False, separators=(",", ":")))
    assert with_block - without_block == expected


def test_plain_string_paths_unchanged_and_tool_calls_still_priced() -> None:
    """The multimodal branch must not disturb the existing contracts: empty
    input costs nothing, and assistant tool_calls keep contributing tokens."""
    counter = _counter()
    assert counter.count_messages([]) == 0

    bare = counter.count_messages([AssistantMessage(content="done")])
    with_calls = counter.count_messages(
        [
            AssistantMessage(
                content="done",
                tool_calls=[{"id": "c1", "type": "function", "name": "click", "arguments": '{"x": 1}'}],
            )
        ]
    )
    assert with_calls > bare
