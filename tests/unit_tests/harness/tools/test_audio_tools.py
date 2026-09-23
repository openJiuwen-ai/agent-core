# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import json
import wave
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.config import AudioModelConfig
from openjiuwen.harness.tools import (
    AudioMetadataTool,
    AudioQuestionAnsweringTool,
    AudioTranscriptionTool,
    create_audio_tools,
)


def _write_test_wav(path: Path, duration_seconds: int = 1) -> None:
    sample_rate = 16000
    num_frames = sample_rate * duration_seconds
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * num_frames)


def test_audio_transcription_tool_transcribes_local_audio(
    tmp_path: Path,
    monkeypatch,
):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        transcription_model="mock-transcribe",
        question_answering_model="mock-audio-qa",
    )

    def fake_invoke_audio_transcription(config, audio_path_arg):
        assert config is audio_model_config
        assert audio_path_arg == str(audio_path)
        return "hello from audio"

    monkeypatch.setattr(
        "openjiuwen.harness.tools.multimodal.audio._invoke_audio_transcription",
        fake_invoke_audio_transcription,
    )

    async def _run():
        await Runner.start()
        try:
            tool = AudioTranscriptionTool(
                audio_model_config=audio_model_config,
            )
            return await tool.invoke({"audio_path_or_url": str(audio_path)})
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["text"] == "hello from audio"
    assert result.data["model"] == "mock-transcribe"


def test_audio_transcription_tool_uses_chat_audio_for_non_endpoint_model(
    tmp_path: Path,
    monkeypatch,
):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        transcription_model="gemini-2.5-flash",
    )
    expected_audio_path = str(audio_path)

    def fake_invoke_audio_chat_completion(config, audio_path, question, model):
        assert config is audio_model_config
        assert audio_path == expected_audio_path
        assert "Transcribe all speech" in question
        assert model == "gemini-2.5-flash"
        return "chat audio transcript", 1.0

    monkeypatch.setattr(
        "openjiuwen.harness.tools.multimodal.audio._invoke_audio_chat_completion",
        fake_invoke_audio_chat_completion,
    )

    async def _run():
        await Runner.start()
        try:
            tool = AudioTranscriptionTool(
                audio_model_config=audio_model_config,
            )
            return await tool.invoke({"audio_path_or_url": str(audio_path)})
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["text"] == "chat audio transcript"
    assert result.data["model"] == "gemini-2.5-flash"


@pytest.mark.parametrize("audio_mime_type", ["audio/wav", "audio/x-wav", "audio/wave"])
@pytest.mark.parametrize(
    ("model_name", "endpoint"),
    [
        ("FunAudioLLM/SenseVoiceSmall", "/v1/audio/transcriptions"),
        ("funaudiollm/sensevoicesmall", "/v1/audio/transcriptions"),
        ("FUNAUDIOLLM/SENSEVOICESMALL", "/v1/audio/transcriptions"),
        ("gpt-4o-transcribe", "/v1/audio/transcriptions"),
        ("gpt-4o-mini-transcribe", "/v1/audio/transcriptions"),
        ("whisper-1", "/v1/audio/transcriptions"),
        ("xiaomi/mimo-v2.6-flash", "/v1/chat/completions"),
        ("gemini-2.5-flash", "/v1/chat/completions"),
        ("other/SenseVoiceSmall", "/v1/chat/completions"),
        ("FunAudioLLM/SenseVoiceSmall-custom", "/v1/chat/completions"),
    ],
)
def test_audio_transcription_routes_exact_model_with_correct_payload(
    tmp_path: Path,
    monkeypatch,
    model_name: str,
    endpoint: str,
    audio_mime_type: str,
):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_bytes = audio_path.read_bytes()
    monkeypatch.setattr(
        "openjiuwen.harness.tools.multimodal.audio.mimetypes.guess_type",
        lambda _path: (audio_mime_type, None),
    )
    config = AudioModelConfig(
        api_key="test-key",
        base_url="https://audio.example/v1",
        transcription_model=model_name,
        max_retries=1,
    )
    requests_seen = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if endpoint == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "test transcript"})

        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "test transcript"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as http_client:
        monkeypatch.setattr(
            "openjiuwen.harness.tools.multimodal.audio.OpenAI",
            lambda **kwargs: OpenAI(**kwargs, http_client=http_client, max_retries=0),
        )
        tool = AudioTranscriptionTool(audio_model_config=config)
        result = asyncio.run(tool.invoke({"audio_path_or_url": str(audio_path)}))

    assert [request.url.path for request in requests_seen] == [endpoint]
    request = requests_seen[0]
    assert request.method == "POST"
    if endpoint == "/v1/audio/transcriptions":
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data;")
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode("ascii") + request.read()
        )
        parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert set(parts) == {"model", "file"}
        assert parts["model"].get_payload(decode=True).decode("utf-8") == model_name
        assert parts["file"].get_filename() == "sample.wav"
        assert parts["file"].get_payload(decode=True) == audio_bytes
    else:
        assert request.headers["content-type"] == "application/json"
        payload = json.loads(request.read())
        assert payload["model"] == model_name
        content = payload["messages"][-1]["content"]
        assert "Transcribe all speech" in content[0]["text"]
        assert content[1]["type"] == "input_audio"
        assert content[1]["input_audio"]["format"] == "wav"
        assert base64.b64decode(content[1]["input_audio"]["data"]) == audio_bytes
    assert result.success is True, result.error
    assert result.data == {"text": "test transcript", "model": model_name}


def test_audio_question_answering_tool_returns_answer_and_duration(
    tmp_path: Path,
    monkeypatch,
):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        transcription_model="mock-transcribe",
        question_answering_model="mock-audio-qa",
    )

    def fake_invoke_audio_question_answering(config, audio_path_arg, question):
        assert config is audio_model_config
        assert audio_path_arg == str(audio_path)
        assert question == "What is being said?"
        return "A person says hello.", 1.0

    monkeypatch.setattr(
        "openjiuwen.harness.tools.multimodal.audio._invoke_audio_question_answering",
        fake_invoke_audio_question_answering,
    )

    async def _run():
        await Runner.start()
        try:
            tool = AudioQuestionAnsweringTool(
                audio_model_config=audio_model_config,
            )
            return await tool.invoke(
                {
                    "audio_path_or_url": str(audio_path),
                    "question": "What is being said?",
                }
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["answer"] == "A person says hello."
    assert result.data["duration_seconds"] == 1.0
    assert result.data["model"] == "mock-audio-qa"


def test_audio_question_answering_reports_non_ascii_api_key(tmp_path: Path):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_model_config = AudioModelConfig(
        api_key="sk-test中文",
        base_url="https://example.com/v1",
        question_answering_model="gemini-2.5-flash",
    )

    async def _run():
        await Runner.start()
        try:
            tool = AudioQuestionAnsweringTool(
                audio_model_config=audio_model_config,
            )
            return await tool.invoke(
                {
                    "audio_path_or_url": str(audio_path),
                    "question": "请转写这段音频",
                }
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is False
    assert "api_key contains non-ASCII characters" in result.error


def test_audio_metadata_tool_returns_duration_when_acr_missing(tmp_path: Path):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path, duration_seconds=2)
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        acr_access_key="",
        acr_access_secret="",
    )

    async def _run():
        await Runner.start()
        try:
            tool = AudioMetadataTool(audio_model_config=audio_model_config)
            return await tool.invoke({"audio_path_or_url": str(audio_path)})
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["duration_seconds"] == 2.0
    assert result.data["identified"] is False
    assert "ACR credentials" in result.data["note"]


def test_create_audio_tools_supports_language():
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
    )
    tools = create_audio_tools(
        language="en",
        audio_model_config=audio_model_config,
    )

    assert tools[0].audio_model_config is audio_model_config
    assert tools[1].audio_model_config is audio_model_config
    assert tools[2].audio_model_config is audio_model_config


def test_audio_transcription_tool_returns_clear_error_without_config():
    async def _run():
        await Runner.start()
        try:
            tool = AudioTranscriptionTool()
            return await tool.invoke(
                {"audio_path_or_url": "https://example.com/audio.wav"}
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is False
    assert "Audio model config is not set" in result.error


def test_audio_model_config_from_env(monkeypatch):
    monkeypatch.setenv("AUDIO_API_KEY", "audio-key")
    monkeypatch.setenv("AUDIO_BASE_URL", "https://audio.example.com/v1")
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_MODEL", "mock-transcribe")
    monkeypatch.setenv("AUDIO_QUESTION_ANSWERING_MODEL", "mock-qa")
    monkeypatch.setenv("AUDIO_MAX_RETRIES", "5")
    monkeypatch.setenv("ACR_ACCESS_KEY", "acr-key")
    monkeypatch.setenv("ACR_ACCESS_SECRET", "acr-secret")

    config = AudioModelConfig.from_env()

    assert config.api_key == "audio-key"
    assert config.base_url == "https://audio.example.com/v1"
    assert config.transcription_model == "mock-transcribe"
    assert config.question_answering_model == "mock-qa"
    assert config.max_retries == 5
    assert config.acr_access_key == "acr-key"
    assert config.acr_access_secret == "acr-secret"


def test_audio_model_config_from_env_uses_shared_audio_model_name(monkeypatch):
    monkeypatch.setenv("AUDIO_API_KEY", "audio-key")
    monkeypatch.setenv("AUDIO_BASE_URL", "https://audio.example.com/v1")
    monkeypatch.setenv("AUDIO_MODEL_NAME", "gemini-2.5-flash")
    monkeypatch.delenv("AUDIO_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.delenv("AUDIO_QUESTION_ANSWERING_MODEL", raising=False)

    config = AudioModelConfig.from_env()

    assert config.transcription_model == "gemini-2.5-flash"
    assert config.question_answering_model == "gemini-2.5-flash"
