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


# ---------------------------------------------------------------------------
# Regression nails for the E-1125 family: a vocab download that hangs forever
# (silent network black hole) must never hang session initialization. Before
# the bounded-lock / preflight-download / negative-cache fix, a stuck loader
# held ``_ENCODING_LOAD_LOCK`` indefinitely and every ``TiktokenCounter()``
# blocked on it until the relay watchdog killed the request (2h, zero model
# calls, OA.05000030).
# ---------------------------------------------------------------------------

_VOCAB_MODULE = "openjiuwen.core.context_engine.token.tiktoken_counter"


class _StuckLockGuard:
    """Hold the load lock in a background thread without releasing it."""

    def __enter__(self):
        import threading

        import openjiuwen.core.context_engine.token.tiktoken_counter as mod

        self._holder = threading.Event()
        self._release_evt = threading.Event()
        self._mod = mod

        def _hold() -> None:
            with mod._ENCODING_LOAD_LOCK:
                self._holder.set()
                self._release_evt.wait(timeout=30)

        self._thread = threading.Thread(target=_hold, name="stuck-encoding-lock", daemon=True)
        self._thread.start()
        assert self._holder.wait(timeout=5), "test lock holder failed to start"
        return self

    def __exit__(self, *exc) -> None:
        self._release_evt.set()
        self._thread.join(timeout=5)


def _reset_vocab_state(monkeypatch) -> None:
    import time as _time

    import openjiuwen.core.context_engine.token.tiktoken_counter as mod

    monkeypatch.setattr(mod, "_vocab_unavailable_until", 0.0)
    monkeypatch.setattr(mod, "_ENCODING_LOAD_TIMEOUT_S", 1.0)
    assert mod._vocab_unavailable_until <= _time.monotonic()


def test_init_falls_back_when_load_lock_is_stuck(monkeypatch) -> None:
    """A permanently stuck loader (the E-1125 shape) must degrade init to
    character estimation within the bounded lock timeout instead of hanging."""
    import time

    _reset_vocab_state(monkeypatch)
    with _StuckLockGuard():
        started = time.monotonic()
        counter = TiktokenCounter()
        elapsed = time.monotonic() - started
    assert counter._enc is None
    assert counter.measurement_source == "string_length_fallback"
    assert elapsed < 5.0, f"init waited {elapsed:.1f}s on a stuck lock; bounded acquire regressed"
    assert counter.count("你好世界") > 0


def test_cooldown_makes_subsequent_inits_instant(monkeypatch) -> None:
    """After a failure the negative cache must skip the lock and the download
    entirely: every new session must not re-pay the timeout."""
    import time

    import openjiuwen.core.context_engine.token.tiktoken_counter as mod

    _reset_vocab_state(monkeypatch)
    with _StuckLockGuard():
        first = TiktokenCounter()  # pays the lock timeout once, arms the cooldown
        assert first.measurement_source == "string_length_fallback"
        started = time.monotonic()
        second = TiktokenCounter()  # cooldown active: no lock wait at all
        elapsed = time.monotonic() - started
    assert second.measurement_source == "string_length_fallback"
    assert elapsed < 0.5, f"cooldown did not skip the lock wait ({elapsed:.2f}s)"


def test_preflight_download_times_out_on_blackhole(monkeypatch, tmp_path) -> None:
    """``_ensure_vocab_cached`` must fail within its own timeout against a
    TCP server that accepts the connection but never answers (the field
    shape of E-1125: handshake OK, data black-holed)."""
    import socket
    import threading
    import time

    import pytest

    import openjiuwen.core.context_engine.token.tiktoken_counter as mod

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    conns: list = []

    def _swallow() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conns.append(conn)  # hold; never respond

    t = threading.Thread(target=_swallow, name="blackhole", daemon=True)
    t.start()

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_VOCAB_URLS", {"cl100k_base": f"http://127.0.0.1:{port}/vocab.tiktoken"})
    monkeypatch.setattr(mod, "_VOCAB_DOWNLOAD_TIMEOUT_S", (0.5, 1.0))
    monkeypatch.setattr(mod, "_vocab_unavailable_until", 0.0)

    try:
        started = time.monotonic()
        with pytest.raises(Exception):
            mod._ensure_vocab_cached("cl100k_base")
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"preflight download took {elapsed:.1f}s against a black hole"
    finally:
        srv.close()
        for conn in conns:
            conn.close()


def test_vocab_cache_dir_mirrors_tiktoken_resolution(monkeypatch, tmp_path) -> None:
    """Review-2 nail: the prefill dir must equal ``get_encoding``'s lookup
    dir under every tiktoken-supported env combo, including the empty-string
    "caching disabled" sentinel."""
    import os
    import tempfile

    import openjiuwen.core.context_engine.token.tiktoken_counter as mod

    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.delenv("DATA_GYM_CACHE_DIR", raising=False)
    assert mod._vocab_cache_dir() == os.path.join(tempfile.gettempdir(), "data-gym-cache")

    monkeypatch.setenv("DATA_GYM_CACHE_DIR", str(tmp_path))
    assert mod._vocab_cache_dir() == str(tmp_path)

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "C:/prefers-this")
    assert mod._vocab_cache_dir() == "C:/prefers-this"  # TIKTOKEN wins over DATA_GYM

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "")
    assert mod._vocab_cache_dir() is None  # empty == caching disabled in tiktoken


def test_preflight_covers_every_encoding_name_branch(monkeypatch) -> None:
    """Review-1 nail: models resolved via ``encoding_name_for_model`` (known
    to tiktoken but absent from ``_MODEL2ENC``, e.g. gpt-4.1 -> o200k_base)
    must go through the preflight too, so no branch can run tiktoken's
    unbounded internal download on the lock-holding thread."""
    import tiktoken

    import openjiuwen.core.context_engine.token.tiktoken_counter as mod

    class _StubEncoding:
        name = "stub"

        def encode(self, text, disallowed_special=()):
            return [0] * (len(text) // 4 + 1)

    prefilled: list = []
    monkeypatch.setattr(mod, "_ensure_vocab_cached", lambda name: prefilled.append(name))
    monkeypatch.setattr(mod, "_vocab_unavailable_until", 0.0)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name="cl100k_base": _StubEncoding())

    assert tiktoken.encoding_name_for_model("gpt-4.1") == "o200k_base"  # assumption guard

    TiktokenCounter(model="gpt-4.1")
    assert prefilled[-1] == "o200k_base", "tiktoken-known model bypassed the preflight"

    unknown = TiktokenCounter(model="totally-unknown-model")
    assert prefilled[-1] == "cl100k_base", "unregistered model must prefill the default encoding"
    assert unknown.measurement_source == "tiktoken_fallback"
    assert unknown.measurement_fallback_reason == "model_not_registered"
