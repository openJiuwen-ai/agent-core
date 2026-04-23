# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import mimetypes
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import urlparse

import requests
from openai import OpenAI

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.schema.config import AudioModelConfig
from openjiuwen.harness.tools.base_tool import ToolOutput

SANDBOX_PATH_MARKER = "home/user"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _get_audio_extension(url: str, content_type: str = "") -> str:
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    audio_extensions = [
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".wma",
    ]
    for ext in audio_extensions:
        if path.endswith(ext):
            return ext

    lowered_type = content_type.lower()
    if "mp3" in lowered_type or "mpeg" in lowered_type:
        return ".mp3"
    if "wav" in lowered_type:
        return ".wav"
    if "m4a" in lowered_type:
        return ".m4a"
    if "aac" in lowered_type:
        return ".aac"
    if "ogg" in lowered_type:
        return ".ogg"
    if "flac" in lowered_type:
        return ".flac"
    return ".mp3"


def _require_audio_model_config(
    audio_model_config: Optional[AudioModelConfig],
) -> AudioModelConfig:
    if audio_model_config is None:
        raise ValueError(
            "Audio model config is not set. Pass "
            "DeepAgentConfig.audio_model_config or construct "
            "the tool with AudioModelConfig."
        )
    if not audio_model_config.base_url:
        raise ValueError("Audio model config missing base_url.")
    return audio_model_config


def _build_openai_client(config: AudioModelConfig) -> OpenAI:
    if not config.api_key:
        raise ValueError("Audio model config missing api_key.")
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
    )


def _resolve_audio_path(
    audio_path_or_url: str,
    config: AudioModelConfig,
) -> tuple[str, bool]:
    if SANDBOX_PATH_MARKER in audio_path_or_url:
        raise ValueError(
            "Audio tools cannot access sandbox-only paths. "
            "Use a local path outside the sandbox or an https URL."
        )

    if _is_http_url(audio_path_or_url):
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        with requests.get(
            audio_path_or_url,
            headers=headers,
            timeout=config.http_timeout,
            stream=True,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            suffix = _get_audio_extension(audio_path_or_url, content_type)
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix,
            ) as temp_file:
                bytes_written = 0
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > config.max_audio_bytes:
                        raise ValueError(
                            "Audio file exceeds size limit."
                        )
                    temp_file.write(chunk)
                return temp_file.name, True

    audio_path = Path(audio_path_or_url).expanduser()
    if not audio_path.exists() or not audio_path.is_file():
        raise FileNotFoundError(
            f"Audio path does not exist or is not a file: {audio_path_or_url}"
        )
    return str(audio_path), False


def _get_audio_duration(audio_path: str) -> float:
    wave_error: Exception | None = None
    try:
        with contextlib.closing(wave.open(audio_path, "rb")) as audio_file:
            frames = audio_file.getnframes()
            rate = audio_file.getframerate()
            duration = frames / float(rate)
            if duration > 0:
                return duration
    except (OSError, EOFError, wave.Error) as exc:
        wave_error = exc

    mutagen_error: Exception | None = None
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(audio_path)
        if audio is not None and hasattr(audio, "info"):
            duration = getattr(audio.info, "length", 0)
            if duration:
                return float(duration)
    except (ImportError, OSError, TypeError, ValueError) as exc:
        mutagen_error = exc

    error_details = []
    if wave_error is not None:
        error_details.append(f"wave={wave_error}")
    if mutagen_error is not None:
        error_details.append(f"mutagen={mutagen_error}")
    detail_text = "; ".join(error_details) if error_details else "no parser succeeded"
    raise ValueError(
        f"Unable to determine audio duration: {detail_text}"
    )


def _encode_audio_file(audio_path: str) -> tuple[str, str]:
    with open(audio_path, "rb") as audio_file:
        audio_data = audio_file.read()

    encoded_string = base64.b64encode(audio_data).decode("utf-8")
    mime_type, _ = mimetypes.guess_type(audio_path)
    if mime_type and mime_type.startswith("audio/"):
        mime_format = mime_type.split("/")[-1]
        format_mapping = {
            "mpeg": "mp3",
            "wav": "wav",
            "wave": "wav",
        }
        file_format = format_mapping.get(mime_format, "mp3")
    else:
        file_format = "mp3"
    return encoded_string, file_format


def _extract_message_text(response: Any) -> str:
    message = response.choices[0].message
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(item.get("text", ""))
                continue
            text = getattr(item, "text", None)
            if text:
                chunks.append(text)
        return "\n".join(chunk.strip() for chunk in chunks if chunk).strip()
    return str(content).strip()


def _invoke_audio_transcription(
    config: AudioModelConfig,
    audio_path: str,
) -> str:
    client = _build_openai_client(config)
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model=config.transcription_model,
            file=audio_file,
        )
    text = getattr(transcription, "text", "")
    if not text:
        raise ValueError("Audio transcription returned empty content.")
    return text


def _invoke_audio_question_answering(
    config: AudioModelConfig,
    audio_path: str,
    question: str,
) -> tuple[str, float]:
    client = _build_openai_client(config)
    encoded_string, file_format = _encode_audio_file(audio_path)
    duration = _get_audio_duration(audio_path)

    response = client.chat.completions.create(
        model=config.question_answering_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant specializing "
                    "in audio analysis."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Answer the following question based on "
                            f"the given audio information:\n\n{question}"
                        ),
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": encoded_string,
                            "format": file_format,
                        },
                    },
                ],
            },
        ],
    )
    answer = _extract_message_text(response)
    if not answer:
        raise ValueError("Audio question answering returned empty content.")
    return answer, duration


def _invoke_audio_metadata(
    config: AudioModelConfig,
    audio_path: str,
) -> dict[str, Any]:
    duration = _get_audio_duration(audio_path)
    result = {
        "duration_seconds": round(duration, 2),
        "title": None,
        "artist": None,
        "release_date": None,
        "score": None,
        "identified": False,
        "note": None,
    }

    if not config.acr_access_key or not config.acr_access_secret:
        result["note"] = (
            "Title and artist identification is disabled because "
            "ACR credentials are not configured."
        )
        return result

    if duration > 15:
        result["note"] = (
            "Audio metadata identification works best for clips "
            "shorter than 15 seconds."
        )
        return result

    timestamp = str(time.time())
    string_to_sign = "\n".join(
        [
            "POST",
            "/v1/identify",
            config.acr_access_key,
            "audio",
            "1",
            timestamp,
        ]
    )
    signature = base64.b64encode(
        hmac.new(
            config.acr_access_secret.encode("ascii"),
            string_to_sign.encode("ascii"),
            digestmod=hashlib.sha1,
        ).digest()
    ).decode("ascii")

    mime_type, _ = mimetypes.guess_type(audio_path)
    file_format = "mp3"
    if mime_type and mime_type.startswith("audio/"):
        file_format = {
            "mpeg": "mp3",
            "wav": "wav",
            "wave": "wav",
        }.get(mime_type.split("/")[-1], "mp3")

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            config.acr_base_url,
            files=[
                (
                    "sample",
                    (
                        os.path.basename(audio_path),
                        audio_file,
                        file_format,
                    ),
                )
            ],
            data={
                "access_key": config.acr_access_key,
                "sample_bytes": os.path.getsize(audio_path),
                "timestamp": timestamp,
                "signature": signature,
                "data_type": "audio",
                "signature_version": "1",
            },
            timeout=config.http_timeout,
        )

    payload = json.loads(response.text)
    metadata = payload.get("metadata", {})
    if "humming" in metadata and metadata["humming"]:
        best = sorted(
            metadata["humming"],
            key=lambda item: item.get("duration_ms") or 0,
            reverse=True,
        )[0]
        result.update(
            {
                "title": best.get("title"),
                "artist": best.get("artists", [{}])[0].get("name"),
                "release_date": best.get("release_date"),
                "score": best.get("score"),
                "identified": True,
            }
        )
        return result

    if "music" in metadata and metadata["music"]:
        best = metadata["music"][0]
        result.update(
            {
                "title": best.get("title"),
                "artist": best.get("artists", [{}])[0].get("name"),
                "release_date": best.get("release_date"),
                "identified": True,
            }
        )
        return result

    result["note"] = "No metadata found for the given audio file."
    return result


async def _call_with_retries(
    config: AudioModelConfig,
    func,
    *args,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            return await asyncio.to_thread(func, config, *args)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            error_text = str(exc)
            is_retryable = any(
                code in error_text for code in ("429", "500", "502", "503", "504")
            )
            if attempt == config.max_retries or not is_retryable:
                break
            await asyncio.sleep(2 ** (attempt - 1))
    if last_error is None:
        raise RuntimeError(
            "Audio model call failed without a captured exception."
        )
    raise last_error


class AudioTranscriptionTool(Tool):
    """Transcribe speech audio into text."""

    def __init__(
        self,
        language: str = "cn",
        audio_model_config: Optional[AudioModelConfig] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(
                "audio_transcription",
                "AudioTranscriptionTool",
                language,
                agent_id=agent_id,
            )
        )
        self.audio_model_config = audio_model_config

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        _ = kwargs
        audio_path_or_url = inputs.get("audio_path_or_url")
        temp_path = None
        try:
            config = _require_audio_model_config(self.audio_model_config)
            audio_path, should_delete = await asyncio.to_thread(
                _resolve_audio_path,
                audio_path_or_url,
                config,
            )
            if should_delete:
                temp_path = audio_path
            text = await _call_with_retries(
                config,
                _invoke_audio_transcription,
                audio_path,
            )
            return ToolOutput(
                success=True,
                data={
                    "text": text,
                    "model": config.transcription_model,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        _ = inputs, kwargs
        if False:
            yield None


class AudioQuestionAnsweringTool(Tool):
    """Answer questions based on audio content."""

    def __init__(
        self,
        language: str = "cn",
        audio_model_config: Optional[AudioModelConfig] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(
                "audio_question_answering",
                "AudioQuestionAnsweringTool",
                language,
                agent_id=agent_id,
            )
        )
        self.audio_model_config = audio_model_config

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        _ = kwargs
        audio_path_or_url = inputs.get("audio_path_or_url")
        question = inputs.get("question")
        temp_path = None
        try:
            config = _require_audio_model_config(self.audio_model_config)
            audio_path, should_delete = await asyncio.to_thread(
                _resolve_audio_path,
                audio_path_or_url,
                config,
            )
            if should_delete:
                temp_path = audio_path
            answer, duration = await _call_with_retries(
                config,
                _invoke_audio_question_answering,
                audio_path,
                question,
            )
            return ToolOutput(
                success=True,
                data={
                    "answer": answer,
                    "duration_seconds": duration,
                    "model": config.question_answering_model,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        _ = inputs, kwargs
        if False:
            yield None


class AudioMetadataTool(Tool):
    """Inspect duration and optionally identify audio metadata."""

    def __init__(
        self,
        language: str = "cn",
        audio_model_config: Optional[AudioModelConfig] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(
                "audio_metadata",
                "AudioMetadataTool",
                language,
                agent_id=agent_id,
            )
        )
        self.audio_model_config = audio_model_config

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        _ = kwargs
        audio_path_or_url = inputs.get("audio_path_or_url")
        temp_path = None
        try:
            config = _require_audio_model_config(self.audio_model_config)
            audio_path, should_delete = await asyncio.to_thread(
                _resolve_audio_path,
                audio_path_or_url,
                config,
            )
            if should_delete:
                temp_path = audio_path
            metadata = await _call_with_retries(
                config,
                _invoke_audio_metadata,
                audio_path,
            )
            return ToolOutput(success=True, data=metadata)
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        _ = inputs, kwargs
        if False:
            yield None


def create_audio_tools(
    language: str = "cn",
    audio_model_config: Optional[AudioModelConfig] = None,
    agent_id: Optional[str] = None,
) -> list[Tool]:
    return [
        AudioTranscriptionTool(
            language=language,
            audio_model_config=audio_model_config,
            agent_id=agent_id,
        ),
        AudioQuestionAnsweringTool(
            language=language,
            audio_model_config=audio_model_config,
            agent_id=agent_id,
        ),
        AudioMetadataTool(
            language=language,
            audio_model_config=audio_model_config,
            agent_id=agent_id,
        ),
    ]


__all__ = [
    "AudioMetadataTool",
    "AudioQuestionAnsweringTool",
    "AudioTranscriptionTool",
    "create_audio_tools",
]
