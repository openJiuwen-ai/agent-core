# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.config import AudioModelConfig
from openjiuwen.harness.tools import create_audio_tools


def _write_test_wav(path: Path, duration_seconds: int = 1) -> None:
    sample_rate = 16000
    num_frames = sample_rate * duration_seconds
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * num_frames)


def test_create_audio_tools_register_and_invoke(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    _write_test_wav(audio_path)
    audio_model_config = AudioModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        question_answering_model="mock-audio-qa",
    )

    def fake_invoke_audio_question_answering(config, audio_path_arg, question):
        assert config is audio_model_config
        assert audio_path_arg == str(audio_path)
        assert question == "What happens in the audio?"
        return "A person is speaking.", 1.0

    monkeypatch.setattr(
        "openjiuwen.harness.tools.audio._invoke_audio_question_answering",
        fake_invoke_audio_question_answering,
    )

    async def _run():
        await Runner.start()
        tools = create_audio_tools(audio_model_config=audio_model_config)
        try:
            Runner.resource_mgr.add_tool(tools)
            tool_id = ""
            for tool in tools:
                if "AudioQuestionAnsweringTool" in tool.card.id:
                    tool_id = tool.card.id
                    break
            registered_tool = Runner.resource_mgr.get_tool(tool_id)
            assert registered_tool is not None
            result = await registered_tool.invoke(
                {
                    "audio_path_or_url": str(audio_path),
                    "question": "What happens in the audio?",
                }
            )
            return result, [tool.card.name for tool in tools]
        finally:
            for tool in tools:
                Runner.resource_mgr.remove_tool(tool.card.id)
            await Runner.stop()

    result, tool_names = asyncio.run(_run())

    assert result.success is True
    assert result.data["answer"] == "A person is speaking."
    assert result.data["duration_seconds"] == 1.0
    assert result.data["model"] == "mock-audio-qa"
