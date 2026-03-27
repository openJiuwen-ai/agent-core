# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for audio tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

AUDIO_TRANSCRIPTION_DESCRIPTION: Dict[str, str] = {
    "cn": "转写本地音频文件或公网音频 URL，提取音频中的语音文本内容。",
    "en": "Transcribe a local audio file or public audio URL into text.",
}

AUDIO_QUESTION_ANSWERING_DESCRIPTION: Dict[str, str] = {
    "cn": "理解音频内容并回答问题，适合语音、访谈、播客和普通音频内容分析。",
    "en": "Understand audio content and answer questions about speech or general audio.",
}

AUDIO_METADATA_DESCRIPTION: Dict[str, str] = {
    "cn": "识别音频时长，并在配置了 ACR 信息时尝试识别歌曲标题、歌手和发布时间。",
    "en": "Inspect audio duration and optionally identify song metadata when ACR credentials are configured.",
}

AUDIO_TRANSCRIPTION_PARAMS: Dict[str, Dict[str, str]] = {
    "audio_path_or_url": {
        "cn": "本地音频路径或公网 http(s) 音频 URL，不支持 sandbox-only 路径",
        "en": "Local audio path or public http(s) audio URL; sandbox-only paths are not supported",
    },
}

AUDIO_QUESTION_ANSWERING_PARAMS: Dict[str, Dict[str, str]] = {
    "audio_path_or_url": {
        "cn": "本地音频路径或公网 http(s) 音频 URL，不支持 sandbox-only 路径",
        "en": "Local audio path or public http(s) audio URL; sandbox-only paths are not supported",
    },
    "question": {
        "cn": "要基于音频内容回答的问题",
        "en": "Question to answer based on the audio content",
    },
}

AUDIO_METADATA_PARAMS: Dict[str, Dict[str, str]] = {
    "audio_path_or_url": {
        "cn": "本地音频路径或公网 http(s) 音频 URL，不支持 sandbox-only 路径",
        "en": "Local audio path or public http(s) audio URL; sandbox-only paths are not supported",
    },
}


def get_audio_transcription_input_params(
    language: str = "cn",
) -> Dict[str, Any]:
    """Return JSON Schema for audio_transcription input_params."""
    p = AUDIO_TRANSCRIPTION_PARAMS
    return {
        "type": "object",
        "properties": {
            "audio_path_or_url": {
                "type": "string",
                "description": p["audio_path_or_url"].get(
                    language,
                    p["audio_path_or_url"]["cn"],
                ),
            },
        },
        "required": ["audio_path_or_url"],
    }


def get_audio_question_answering_input_params(
    language: str = "cn",
) -> Dict[str, Any]:
    """Return JSON Schema for audio_question_answering input_params."""
    p = AUDIO_QUESTION_ANSWERING_PARAMS
    return {
        "type": "object",
        "properties": {
            "audio_path_or_url": {
                "type": "string",
                "description": p["audio_path_or_url"].get(
                    language,
                    p["audio_path_or_url"]["cn"],
                ),
            },
            "question": {
                "type": "string",
                "description": p["question"].get(
                    language,
                    p["question"]["cn"],
                ),
            },
        },
        "required": ["audio_path_or_url", "question"],
    }


def get_audio_metadata_input_params(
    language: str = "cn",
) -> Dict[str, Any]:
    """Return JSON Schema for audio_metadata input_params."""
    p = AUDIO_METADATA_PARAMS
    return {
        "type": "object",
        "properties": {
            "audio_path_or_url": {
                "type": "string",
                "description": p["audio_path_or_url"].get(
                    language,
                    p["audio_path_or_url"]["cn"],
                ),
            },
        },
        "required": ["audio_path_or_url"],
    }


class AudioTranscriptionMetadataProvider(ToolMetadataProvider):
    """Metadata provider for audio_transcription."""

    def get_name(self) -> str:
        return "audio_transcription"

    def get_description(self, language: str = "cn") -> str:
        return AUDIO_TRANSCRIPTION_DESCRIPTION.get(
            language,
            AUDIO_TRANSCRIPTION_DESCRIPTION["cn"],
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_audio_transcription_input_params(language)


class AudioQuestionAnsweringMetadataProvider(ToolMetadataProvider):
    """Metadata provider for audio_question_answering."""

    def get_name(self) -> str:
        return "audio_question_answering"

    def get_description(self, language: str = "cn") -> str:
        return AUDIO_QUESTION_ANSWERING_DESCRIPTION.get(
            language,
            AUDIO_QUESTION_ANSWERING_DESCRIPTION["cn"],
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_audio_question_answering_input_params(language)


class AudioMetadataMetadataProvider(ToolMetadataProvider):
    """Metadata provider for audio_metadata."""

    def get_name(self) -> str:
        return "audio_metadata"

    def get_description(self, language: str = "cn") -> str:
        return AUDIO_METADATA_DESCRIPTION.get(
            language,
            AUDIO_METADATA_DESCRIPTION["cn"],
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_audio_metadata_input_params(language)
