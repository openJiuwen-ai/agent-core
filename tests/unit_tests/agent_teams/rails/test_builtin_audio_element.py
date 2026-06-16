# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.agent_teams.rails import builtin_elements


class _Context:
    language = "en"
    member_card_id = "agent-1"


def test_audio_element_keeps_metadata_without_complete_model_config() -> None:
    tools = builtin_elements._build_audio_tool_group(  # noqa: SLF001
        {"dedicated": True, "audio_model_config": {}},
        _Context(),
    )

    assert [tool.card.name for tool in tools] == ["audio_metadata"]


def test_audio_element_builds_all_tools_with_complete_model_config() -> None:
    tools = builtin_elements._build_audio_tool_group(  # noqa: SLF001
        {
            "dedicated": True,
            "audio_model_config": {
                "api_key": "key",
                "base_url": "https://audio.example/v1",
                "transcription_model": "audio-transcribe",
                "question_answering_model": "audio-qa",
            },
        },
        _Context(),
    )

    assert [tool.card.name for tool in tools] == [
        "audio_transcription",
        "audio_question_answering",
        "audio_metadata",
    ]
