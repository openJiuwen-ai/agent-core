# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

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
