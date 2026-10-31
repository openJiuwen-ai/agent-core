"""Characterization tests for the two API clients.

OpenAIResponsesClient and OpenRouterChatClient duplicate all five business
methods and differ only in how a request is shaped. They have also drifted:
fence stripping, temperature=0 and frame ordering exist on the OpenRouter side
only. These tests pin both shapes and all three differences so that folding
them onto a shared base cannot change a request silently.

No network is used: the SDK object is constructed with a dummy key and its
create() call is replaced with a capturing stub.
"""

import json
from pathlib import Path

import pytest

from video_memory.config import LLMConfig
from video_memory.llm.api_client import (
    OpenAIResponsesClient,
    OpenRouterChatClient,
    _image_data_url,
    _ocr_memory_payload,
    _parse_time_order,
    _strip_json_fences,
    make_model_client,
)
from video_memory.schemas import FrameRecord, FrameWindow, MemoryNode

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class _Captured:
    """Records the kwargs of the last create() call and returns a canned reply."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict = {}

    def responses_create(self, **kwargs):
        self.kwargs = kwargs
        return type("Response", (), {"output_text": self.text})()

    def chat_create(self, **kwargs):
        self.kwargs = kwargs
        message = type("Message", (), {"content": self.text})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


@pytest.fixture()
def frames(tmp_path: Path) -> list[FrameRecord]:
    png = tmp_path / "000002_evt_1.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    txt = tmp_path / "000001_evt_0.txt"
    txt.write_text("visible text", encoding="utf-8")
    return [
        FrameRecord("000002", "evt_1", 2, "evt", 1, 2, "png", png),
        FrameRecord("000001", "evt_0", 1, "evt", 0, 1, "txt", txt),
    ]


def _openai_client(monkeypatch, reply: str) -> tuple[OpenAIResponsesClient, _Captured]:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = OpenAIResponsesClient(LLMConfig(provider="openai"), prompts_dir=PROMPTS)
    captured = _Captured(reply)
    monkeypatch.setattr(client.client.responses, "create", captured.responses_create)
    return client, captured


def _openrouter_client(monkeypatch, reply: str) -> tuple[OpenRouterChatClient, _Captured]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterChatClient(LLMConfig(provider="openrouter"), prompts_dir=PROMPTS)
    captured = _Captured(reply)
    monkeypatch.setattr(client.client.chat.completions, "create", captured.chat_create)
    return client, captured


# --------------------------------------------------------------------------
# request shapes
# --------------------------------------------------------------------------


def test_openai_builds_input_text_and_input_image_parts(monkeypatch, frames) -> None:
    client, captured = _openai_client(monkeypatch, "answer")
    client.answer("Q?", [MemoryNode("n", "detail", "text", [1])], frames)

    content = captured.kwargs["input"][0]["content"]
    # Prompt, payload, then one part per frame in the caller's order.
    assert [part["type"] for part in content] == ["input_text", "input_text", "input_image", "input_text"]

    image = content[2]
    assert image["image_url"].startswith("data:image/png;base64,")
    assert image["detail"] == "low"
    assert "visible text" in content[3]["text"]
    assert content[3]["text"].startswith("Frame evt_0 text:")


def test_openrouter_builds_text_and_nested_image_url_parts(monkeypatch, frames) -> None:
    client, captured = _openrouter_client(monkeypatch, "answer")
    client.answer("Q?", [MemoryNode("n", "detail", "text", [1])], frames)

    content = captured.kwargs["messages"][0]["content"]
    # Same two leading parts, but answer() sorts the frames by time_id first,
    # so the text frame (time_id 1) precedes the image (time_id 2).
    assert [part["type"] for part in content] == ["text", "text", "text", "image_url"]

    assert content[2]["text"].startswith("Frame evt_0 text:")
    image = content[3]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert image["image_url"]["detail"] == "low"


# --------------------------------------------------------------------------
# the three drifts between the two clients
# --------------------------------------------------------------------------


def test_only_openrouter_pins_temperature(monkeypatch, frames) -> None:
    openrouter, captured_router = _openrouter_client(monkeypatch, "answer")
    openrouter.answer("Q?", [], [])
    assert captured_router.kwargs["temperature"] == 0.0

    openai, captured_openai = _openai_client(monkeypatch, "answer")
    openai.answer("Q?", [], [])
    assert "temperature" not in captured_openai.kwargs


def test_only_openrouter_strips_markdown_fences(monkeypatch) -> None:
    fenced = '```json\n{"nodes": [{"node_type": "detail"}]}\n```'

    openrouter, _ = _openrouter_client(monkeypatch, fenced)
    assert openrouter.generate_memory(FrameWindow("w", [], 0, 0), []) == [{"node_type": "detail"}]

    openai, _ = _openai_client(monkeypatch, fenced)
    with pytest.raises(ValueError, match="did not return valid JSON"):
        openai.generate_memory(FrameWindow("w", [], 0, 0), [])


def _payload_frame_keys(part: dict) -> list[str]:
    return [frame["frame_key"] for frame in json.loads(part["text"])["frames"]]


def test_only_openrouter_orders_answer_frames_by_time(monkeypatch, frames) -> None:
    """frames arrive as [time_id 2, time_id 1]; only OpenRouter reorders them."""
    openrouter, captured_router = _openrouter_client(monkeypatch, "answer")
    openrouter.answer("Q?", [], frames)
    assert _payload_frame_keys(captured_router.kwargs["messages"][0]["content"][1]) == ["evt_0", "evt_1"]

    openai, captured_openai = _openai_client(monkeypatch, "answer")
    openai.answer("Q?", [], frames)
    assert _payload_frame_keys(captured_openai.kwargs["input"][0]["content"][1]) == ["evt_1", "evt_0"]


# --------------------------------------------------------------------------
# shared parsing helpers
# --------------------------------------------------------------------------


def test_parse_qa_filters_unknown_types_and_normalises_time_order(monkeypatch) -> None:
    reply = (
        '{"qa_types": ["detail", "nonsense"], "entities": ["cnn"], "time_range": [0, 60], '
        '"temporal_hint": "recently", "time_order": "sideways", "intent": "find it"}'
    )
    client, _ = _openrouter_client(monkeypatch, reply)
    from video_memory.schemas import QAItem

    parsed = client.parse_qa(QAItem("q", "Q?", "A", None, 60, [["f"]]), (0, 100))

    assert parsed.qa_types == ["detail"]
    assert parsed.time_range == (0, 60)
    assert parsed.temporal_hint == "recently"
    assert parsed.time_order == "none"  # "sideways" is not a valid value


def test_rank_nodes_skips_entries_without_a_node_id(monkeypatch) -> None:
    reply = '{"scores": [{"node_id": "a", "score": 0.9}, {"score": 0.5}, {"node_id": "b", "score": "0.1"}]}'
    client, _ = _openrouter_client(monkeypatch, reply)

    assert client.rank_nodes("Q?", [], []) == {"a": 0.9, "b": 0.1}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('{"a": 1}', '{"a": 1}'),
    ],
)
def test_strip_json_fences(raw: str, expected: str) -> None:
    assert _strip_json_fences(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("recent", "recent"), ("earliest", "earliest"), ("latest", "latest"), ("bogus", "none"), (None, "none")],
)
def test_parse_time_order(raw, expected: str) -> None:
    assert _parse_time_order(raw) == expected


def test_image_data_url_is_base64_png(tmp_path: Path) -> None:
    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG")
    assert _image_data_url(png) == "data:image/png;base64,iVBORw=="


def test_ocr_memory_payload_sends_no_pixels(frames) -> None:
    payload = _ocr_memory_payload(FrameWindow("w", ["evt_1"], 1, 2), frames, [{"frame_key": "evt_1"}])

    assert set(payload) == {"window", "frames", "ocr_frames"}
    assert set(payload["frames"][0]) == {"frame_key", "time_id", "modality"}
    assert "path" not in payload["frames"][0]


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------


def test_make_model_client_rejects_unknown_providers() -> None:
    with pytest.raises(ValueError, match="openai.*openrouter"):
        make_model_client(LLMConfig(provider="mock"))
