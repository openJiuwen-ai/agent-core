from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from video_memory.config import LLMConfig
from video_memory.data.frame_index import read_frame_text
from video_memory.llm.base import load_prompt
from video_memory.schemas import FrameRecord, FrameWindow, MemoryNode, QAItem, QAParseResult


class OpenAIResponsesClient:
    def __init__(
        self,
        config: LLMConfig,
        prompts_dir: str | Path = "prompts",
    ) -> None:
        from openai import OpenAI

        self.client = OpenAI()
        self.config = config
        self.prompts_dir = Path(prompts_dir)

    def generate_memory(self, window: FrameWindow, frames: list[FrameRecord]) -> list[dict]:
        prompt = load_prompt(self.prompts_dir / "memory_generation.md")
        payload = {
            "window": window.to_dict(),
            "frames": [frame.to_dict() for frame in frames],
        }
        result = self._json_response(prompt, payload, frames=frames)
        return list(result.get("nodes", []))

    def generate_memory_from_ocr(
        self,
        window: FrameWindow,
        frames: list[FrameRecord],
        ocr_observations: list[dict],
    ) -> list[dict]:
        prompt = load_prompt(self.prompts_dir / "memory_generation_ocr.md")
        payload = _ocr_memory_payload(window, frames, ocr_observations)
        result = self._json_response(prompt, payload)
        return list(result.get("nodes", []))

    def parse_qa(self, qa: QAItem, video_time_range: tuple[int, int]) -> QAParseResult:
        prompt = load_prompt(self.prompts_dir / "qa_parsing.md")
        result = self._json_response(
            prompt,
            {
                "qa": qa.to_dict(),
                "video_time_range": list(video_time_range),
            },
        )
        time_range_raw = result.get("time_range")
        time_range = tuple(time_range_raw) if time_range_raw and len(time_range_raw) == 2 else None
        qa_types = [value for value in result.get("qa_types", []) if value in {"detail", "summary", "preference"}]
        return QAParseResult(
            qa_types=qa_types,
            entities=list(result.get("entities", [])),
            time_range=time_range,  # type: ignore[arg-type]
            temporal_hint=str(result.get("temporal_hint", "none")),
            time_order=_parse_time_order(result.get("time_order")),
            intent=str(result.get("intent", "")),
        )

    def rank_nodes(
        self,
        question: str,
        qa_entities: list[str],
        candidate_nodes: list[MemoryNode],
    ) -> dict[str, float]:
        prompt = load_prompt(self.prompts_dir / "node_ranking.md")
        result = self._json_response(
            prompt,
            {
                "question": question,
                "qa_entities": qa_entities,
                "candidate_nodes": [node.to_dict() for node in candidate_nodes],
            },
        )
        scores: dict[str, float] = {}
        for item in result.get("scores", []):
            node_id = item.get("node_id")
            if not node_id:
                continue
            scores[str(node_id)] = float(item.get("score", 0.0))
        return scores

    def answer(
        self,
        question: str,
        selected_nodes: list[MemoryNode],
        frames: list[FrameRecord],
    ) -> str:
        prompt = load_prompt(self.prompts_dir / "answer_generation.md")
        payload = {
            "question": question,
            "selected_nodes": [node.to_dict() for node in selected_nodes],
            "frames": [frame.to_dict() for frame in frames],
        }
        text = self._text_response(prompt, payload, frames=frames)
        return text.strip()

    def _json_response(
        self,
        prompt: str,
        payload: dict[str, Any],
        frames: list[FrameRecord] | None = None,
    ) -> dict[str, Any]:
        text = self._text_response(prompt + "\nReturn JSON only.", payload, frames=frames)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model did not return valid JSON: {text[:500]}") from exc

    def _text_response(
        self,
        prompt: str,
        payload: dict[str, Any],
        frames: list[FrameRecord] | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [
            {"type": "input_text", "text": prompt},
            {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
        ]

        for frame in frames or []:
            if frame.modality == "txt":
                content.append(
                    {
                        "type": "input_text",
                        "text": f"Frame {frame.frame_key} text:\n{read_frame_text(frame)}",
                    }
                )
            elif frame.modality == "png":
                content.append(
                    {
                        "type": "input_image",
                        "image_url": _image_data_url(frame.path),
                        "detail": self.config.image_detail,
                    }
                )

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.client.responses.create(
                    model=self.config.model,
                    input=[
                        {
                            "role": "user",
                            "content": content,
                        }
                    ],
                )
                return response.output_text
            except Exception as exc:  # pragma: no cover - API errors are environment-specific.
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("OpenAI Responses API call failed") from last_error


def _image_data_url(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


class OpenRouterChatClient:
    def __init__(
        self,
        config: LLMConfig,
        prompts_dir: str | Path = "prompts",
    ) -> None:
        from openai import OpenAI

        api_key_env = config.api_key_env or "OPENROUTER_API_KEY"
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing OpenRouter API key. Set {api_key_env} first.")

        default_headers = {}
        if config.http_referer:
            default_headers["HTTP-Referer"] = config.http_referer
        if config.app_title:
            default_headers["X-OpenRouter-Title"] = config.app_title

        self.client = OpenAI(
            base_url=config.base_url or "https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers=default_headers or None,
        )
        self.config = config
        self.prompts_dir = Path(prompts_dir)

    def generate_memory(self, window: FrameWindow, frames: list[FrameRecord]) -> list[dict]:
        prompt = load_prompt(self.prompts_dir / "memory_generation.md")
        payload = {
            "window": window.to_dict(),
            "frames": [frame.to_dict() for frame in frames],
        }
        result = self._json_response(prompt, payload, frames=frames)
        return list(result.get("nodes", []))

    def generate_memory_from_ocr(
        self,
        window: FrameWindow,
        frames: list[FrameRecord],
        ocr_observations: list[dict],
    ) -> list[dict]:
        prompt = load_prompt(self.prompts_dir / "memory_generation_ocr.md")
        payload = _ocr_memory_payload(window, frames, ocr_observations)
        result = self._json_response(prompt, payload)
        return list(result.get("nodes", []))

    def parse_qa(self, qa: QAItem, video_time_range: tuple[int, int]) -> QAParseResult:
        prompt = load_prompt(self.prompts_dir / "qa_parsing.md")
        result = self._json_response(
            prompt,
            {
                "qa": qa.to_dict(),
                "video_time_range": list(video_time_range),
            },
        )
        time_range_raw = result.get("time_range")
        time_range = tuple(time_range_raw) if time_range_raw and len(time_range_raw) == 2 else None
        qa_types = [value for value in result.get("qa_types", []) if value in {"detail", "summary", "preference"}]
        return QAParseResult(
            qa_types=qa_types,
            entities=list(result.get("entities", [])),
            time_range=time_range,  # type: ignore[arg-type]
            temporal_hint=str(result.get("temporal_hint", "none")),
            time_order=_parse_time_order(result.get("time_order")),
            intent=str(result.get("intent", "")),
        )

    def rank_nodes(
        self,
        question: str,
        qa_entities: list[str],
        candidate_nodes: list[MemoryNode],
    ) -> dict[str, float]:
        prompt = load_prompt(self.prompts_dir / "node_ranking.md")
        result = self._json_response(
            prompt,
            {
                "question": question,
                "qa_entities": qa_entities,
                "candidate_nodes": [node.to_dict() for node in candidate_nodes],
            },
        )
        scores: dict[str, float] = {}
        for item in result.get("scores", []):
            node_id = item.get("node_id")
            if not node_id:
                continue
            scores[str(node_id)] = float(item.get("score", 0.0))
        return scores

    def answer(
        self,
        question: str,
        selected_nodes: list[MemoryNode],
        frames: list[FrameRecord],
    ) -> str:
        prompt = load_prompt(self.prompts_dir / "answer_generation.md")
        ordered_frames = sorted(frames, key=lambda frame: (frame.time_id, frame.frame_key))
        payload = {
            "question": question,
            "selected_nodes": [node.to_dict() for node in selected_nodes],
            "frames": [frame.to_dict() for frame in ordered_frames],
        }
        return self._text_response(
            prompt,
            payload,
            frames=ordered_frames,
        ).strip()

    def _json_response(
        self,
        prompt: str,
        payload: dict[str, Any],
        frames: list[FrameRecord] | None = None,
    ) -> dict[str, Any]:
        text = self._text_response(prompt + "\nReturn JSON only.", payload, frames=frames)
        text = _strip_json_fences(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model did not return valid JSON: {text[:500]}") from exc

    def _text_response(
        self,
        prompt: str,
        payload: dict[str, Any],
        frames: list[FrameRecord] | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
        ]

        for frame in frames or []:
            if frame.modality == "txt":
                content.append(
                    {
                        "type": "text",
                        "text": f"Frame {frame.frame_key} text:\n{read_frame_text(frame)}",
                    }
                )
            elif frame.modality == "png":
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(frame.path),
                            "detail": self.config.image_detail,
                        },
                    }
                )

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                completion = self.client.chat.completions.create(
                    model=self.config.model,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "user",
                            "content": content,
                        }
                    ],
                )
                message = completion.choices[0].message.content
                if isinstance(message, list):
                    return "".join(str(part) for part in message)
                return str(message or "")
            except Exception as exc:  # pragma: no cover - API errors are environment-specific.
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("OpenRouter API call failed") from last_error


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped.removeprefix("```json").strip()
    elif stripped.startswith("```"):
        stripped = stripped.removeprefix("```").strip()
    if stripped.endswith("```"):
        stripped = stripped.removesuffix("```").strip()
    return stripped


def _ocr_memory_payload(
    window: FrameWindow,
    frames: list[FrameRecord],
    ocr_observations: list[dict],
) -> dict[str, Any]:
    return {
        "window": window.to_dict(),
        "frames": [
            {
                "frame_key": frame.frame_key,
                "time_id": frame.time_id,
                "modality": frame.modality,
            }
            for frame in frames
        ],
        "ocr_frames": ocr_observations,
    }


def _parse_time_order(value: Any) -> str:
    value = str(value or "none")
    return value if value in {"none", "recent", "earliest", "latest"} else "none"


def make_model_client(config: LLMConfig):
    if config.provider == "openai":
        return OpenAIResponsesClient(config)
    if config.provider == "openrouter":
        return OpenRouterChatClient(config)
    raise ValueError(f"Unsupported llm.provider: {config.provider!r}. Use 'openai' or 'openrouter'.")
