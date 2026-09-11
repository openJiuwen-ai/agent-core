# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SFT data formatting helpers for the v1-compatible veRL parquet schema."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
    assistant_has_trainable_output,
    normalize_assistant_message,
    normalize_messages,
)

try:
    from jinja2.exceptions import TemplateError as _JinjaTemplateError
except ImportError:
    _TOKENIZER_TEMPLATE_ERRORS = (TypeError, ValueError, RuntimeError, KeyError, AttributeError)
else:
    _TOKENIZER_TEMPLATE_ERRORS = (
        TypeError,
        ValueError,
        RuntimeError,
        KeyError,
        AttributeError,
        _JinjaTemplateError,
    )


@dataclass(frozen=True)
class SFTDatasetStats:
    """Summary for one generated pre-tokenized SFT parquet."""

    path: str
    rows: int
    skipped: int
    filtered_multimodal: int
    filtered_no_assistant: int
    total_tokens: int
    loss_norm: str
    supervise: str


def format_tool_call_qwen_xml(name: str, arguments: dict[str, Any]) -> str:
    lines = ["<tool_call>", f"<function={name}>"]
    for param_name, param_value in arguments.items():
        value = (
            json.dumps(param_value, ensure_ascii=False, indent=2)
            if isinstance(param_value, (dict, list))
            else str(param_value)
        )
        lines.extend([f"<parameter={param_name}>", value, "</parameter>"])
    lines.extend(["</function>", "</tool_call>"])
    return "\n".join(lines)


def _parse_tool_call_args(args: Any) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_THINK_RE = re.compile(r"^\s*<think>\s*(.*?)\s*</think>\s*", re.DOTALL)


def extract_think_from_content(content: str) -> tuple[str, str]:
    match = _THINK_RE.match(content)
    if not match:
        return "", content
    return match.group(1).strip(), content[slice(match.end(), None)]


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item.get("value", ""))))
            else:
                parts.append(str(item))
        return "".join(parts)
    return "" if content is None else str(content)


def convert_message_openai(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role", ""))
    content = message.get("content", "") or ""
    if not isinstance(content, str):
        content = "".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
        )

    if role == "assistant":
        reasoning = str(message.get("reasoning_content") or "").strip()
        text = content
        if not reasoning and "<think>" in text:
            reasoning, text = extract_think_from_content(text)
        text = text.strip()

        xml_parts: list[str] = []
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function", tool_call) if isinstance(tool_call, dict) else {}
            name = str(fn.get("name", "")) if isinstance(fn, dict) else ""
            args = _parse_tool_call_args(fn.get("arguments", {}) if isinstance(fn, dict) else {})
            xml_parts.append(format_tool_call_qwen_xml(name, args))
        if xml_parts:
            text = "\n".join(part for part in [text, *xml_parts] if part)

        converted = {"role": "assistant", "content": text}
        if reasoning:
            converted["reasoning_content"] = reasoning
        return converted

    if role == "tool":
        return {"role": "user", "content": f"<tool_response>\n{content}\n</tool_response>"}

    return {"role": role, "content": content}


def normalize_token_ids(tokenized_output: Any) -> list[int]:
    if isinstance(tokenized_output, dict):
        candidate = tokenized_output.get("input_ids", tokenized_output)
    else:
        candidate = getattr(tokenized_output, "input_ids", tokenized_output)

    to_list = getattr(candidate, "tolist", None)
    if callable(to_list):
        candidate = to_list()
    if isinstance(candidate, tuple):
        candidate = [*candidate]

    if isinstance(candidate, list) and len(candidate) == 1:
        only_item = candidate[0]
        if isinstance(only_item, (list, tuple)):
            candidate = list(only_item)

    if not isinstance(candidate, list):
        raise TypeError(f"token_ids must be list-like, got {type(candidate).__name__}")
    return [int(token_id) for token_id in candidate]


def tokenize_single_message(tokenizer: Any, message: dict[str, Any]) -> list[int]:
    try:
        return normalize_token_ids(tokenizer.apply_chat_template([message], add_generation_prompt=False, tokenize=True))
    except _TOKENIZER_TEMPLATE_ERRORS:
        if message["role"] == "system":
            text = f"<|im_start|>system\n{_flatten_content(message.get('content', ''))}<|im_end|>\n"
            return normalize_token_ids(tokenizer.encode(text, add_special_tokens=False))

        dummy = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
        try:
            dummy_prefix = normalize_token_ids(
                tokenizer.apply_chat_template(dummy, add_generation_prompt=False, tokenize=True)
            )
            combined = normalize_token_ids(
                tokenizer.apply_chat_template(dummy + [message], add_generation_prompt=False, tokenize=True)
            )
            return combined[slice(len(dummy_prefix), None)]
        except _TOKENIZER_TEMPLATE_ERRORS:
            text = f"<|im_start|>{message['role']}\n{_flatten_content(message.get('content', ''))}<|im_end|>\n"
            return normalize_token_ids(tokenizer.encode(text, add_special_tokens=False))


def _mask_for_mode(turn_len: int, mode: str) -> float:
    if turn_len <= 0:
        return 0.0
    if mode == "turn":
        return 1.0 / turn_len
    if mode == "sqrt":
        return 1.0 / math.sqrt(turn_len)
    return 1.0


def _turn_segments(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        current.append(message)
        if message.get("role") == "assistant":
            segments.append(current)
            current = []
    return segments


def build_sft_tokenized_sample(
    tokenizer: Any,
    messages: list[dict[str, Any]],
    *,
    loss_norm: str = "sqrt",
    supervise: str = "last",
) -> dict[str, list[Any]]:
    segments = _turn_segments(messages)
    last_segment_index = len(segments) - 1
    input_ids: list[int] = []
    loss_mask: list[float] = []
    turn_lengths: list[int] = []
    turn_offsets: list[list[int]] = []
    assistant_prompt_len: int | None = None

    def get_assistant_prompt_len() -> int:
        nonlocal assistant_prompt_len
        if assistant_prompt_len is None:
            assistant_prompt_len = len(tokenize_single_message(tokenizer, {"role": "assistant", "content": ""}))
        return assistant_prompt_len

    for segment_index, segment in enumerate(segments):
        segment_start = len(input_ids)
        for message in segment:
            tokens = tokenize_single_message(tokenizer, message)
            input_ids.extend(tokens)
            if message.get("role") != "assistant":
                loss_mask.extend([0.0] * len(tokens))
                continue

            prompt_len = get_assistant_prompt_len()
            turn_len = max(0, len(tokens) - prompt_len)
            turn_lengths.append(turn_len)
            if supervise == "last" and segment_index != last_segment_index:
                loss_mask.extend([0.0] * len(tokens))
            else:
                weight = _mask_for_mode(turn_len, loss_norm)
                loss_mask.extend([0.0] * prompt_len + [weight] * (len(tokens) - prompt_len))
        turn_offsets.append([segment_start, len(input_ids)])

    length = len(input_ids)
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * length,
        "position_ids": list(range(length)),
        "loss_mask": loss_mask,
        "turn_lengths": turn_lengths,
        "turn_offsets": turn_offsets,
    }


def sample_to_openai_messages(sample: dict[str, Any]) -> list[dict[str, Any]]:
    messages = normalize_messages(sample.get("messages") or [])
    if "assistant_message" not in sample:
        return messages
    assistant = normalize_assistant_message(sample.get("assistant_message") or {})
    if assistant_has_trainable_output(assistant):
        messages.append(assistant)
    return messages


def _is_multimodal(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message.get("content"), list) for message in messages)


def _ends_with_assistant(messages: list[dict[str, Any]]) -> bool:
    return bool(messages) and messages[-1].get("role") == "assistant"


def write_sft_parquet(
    *,
    samples: list[dict[str, Any]],
    output_path: str | os.PathLike[str],
    model_path: str,
    tokenizer: Any | None = None,
    loss_norm: str = "sqrt",
    supervise: str = "last",
    max_samples: int = -1,
    no_filter: bool = False,
) -> SFTDatasetStats:
    """Write agent-core SFT samples as v1-compatible pre-tokenized parquet."""

    import pandas as pd
    from transformers import AutoTokenizer

    if loss_norm not in {"token", "turn", "sqrt"}:
        raise ValueError("loss_norm must be one of: token, turn, sqrt")
    if supervise not in {"all", "last"}:
        raise ValueError("supervise must be one of: all, last")

    selected_samples = samples[:max_samples] if max_samples > 0 else samples
    active_tokenizer = tokenizer or AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    rows: list[dict[str, Any]] = []
    skipped = 0
    filtered_multimodal = 0
    filtered_no_assistant = 0
    total_tokens = 0
    for sample in selected_samples:
        raw_messages = sample_to_openai_messages(sample)
        if not raw_messages:
            skipped += 1
            continue
        if not no_filter:
            if _is_multimodal(raw_messages):
                filtered_multimodal += 1
                continue
            if not _ends_with_assistant(raw_messages):
                filtered_no_assistant += 1
                continue

        converted = [convert_message_openai(message) for message in raw_messages]
        built = build_sft_tokenized_sample(active_tokenizer, converted, loss_norm=loss_norm, supervise=supervise)
        rows.append(
            {
                "messages": converted,
                "input_ids": built["input_ids"],
                "attention_mask": built["attention_mask"],
                "position_ids": built["position_ids"],
                "loss_mask": built["loss_mask"],
                "turn_lengths": built["turn_lengths"],
                "turn_offsets": built["turn_offsets"],
            }
        )
        total_tokens += len(built["input_ids"])

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(
            "SFT parquet has no trainable rows "
            f"(filtered_multimodal={filtered_multimodal}, "
            f"filtered_no_assistant={filtered_no_assistant}, skipped={skipped})"
        )
    pd.DataFrame(rows).to_parquet(output, index=False, engine="pyarrow")
    return SFTDatasetStats(
        path=str(output),
        rows=len(rows),
        skipped=skipped,
        filtered_multimodal=filtered_multimodal,
        filtered_no_assistant=filtered_no_assistant,
        total_tokens=total_tokens,
        loss_norm=loss_norm,
        supervise=supervise,
    )
