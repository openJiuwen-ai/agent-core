# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Counter for model-provided ``tiktoken.model`` vocabularies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.token.native_tokenizer_counter import NativeTokenizerCounter
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo


# Kimi K2/K2.5/K2.6/K2.7/K3 use the same base BPE pre-tokenization rule. The
# model repositories provide the special-token ID map in tokenizer_config.json;
# this pattern is the safe local-only fallback when that file is unavailable.
KIMI_TIKTOKEN_PAT_STR = "|".join(
    [
        r"""[\p{Han}]+""",
        (
            r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]*"""
            r"""[\p{Ll}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?"""
        ),
        (
            r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]+"""
            r"""[\p{Ll}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?"""
        ),
        r"""\p{N}{1,3}""",
        r""" ?[^\s\p{L}\p{N}]+[\r\n]*""",
        r"""\s*[\r\n]+""",
        r"""\s+(?!\S)""",
        r"""\s+""",
    ]
)

_K2_SPECIAL_TOKEN_IDS = {
    "[BOS]": 163584,
    "[EOS]": 163585,
    "<|im_end|>": 163586,
    "<|im_user|>": 163587,
    "<|im_assistant|>": 163588,
    "<|start_header_id|>": 163590,
    "<|end_header_id|>": 163591,
    "[EOT]": 163593,
    "<|im_system|>": 163594,
    "<|tool_calls_section_begin|>": 163595,
    "<|tool_calls_section_end|>": 163596,
    "<|tool_call_begin|>": 163597,
    "<|tool_call_argument_begin|>": 163598,
    "<|tool_call_end|>": 163599,
    "<|im_middle|>": 163601,
    "<|media_begin|>": 163602,
    "<|media_content|>": 163603,
    "<|media_end|>": 163604,
    "<|media_pad|>": 163605,
    "<think>": 163606,
    "</think>": 163607,
    "[UNK]": 163838,
    "[PAD]": 163839,
}

_K3_SPECIAL_TOKEN_IDS = {
    "[BOS]": 163584,
    "[EOS]": 163585,
    "<|end_of_msg|>": 163586,
    "<|open|>": 163587,
    "<|close|>": 163588,
    "<|sep|>": 163589,
    "[start_header_id]": 163590,
    "[end_header_id]": 163591,
    "[EOT]": 163593,
    "<|media_begin|>": 163602,
    "<|media_content|>": 163603,
    "<|media_end|>": 163604,
    "<|media_pad|>": 163605,
    "<osagent_mode>": 163649,
    "[UNK]": 163838,
    "[PAD]": 163839,
}


class TiktokenModelCounter(TokenCounter):
    """Count text with a downloaded model-native tiktoken BPE vocabulary."""

    def __init__(
        self,
        tokenizer_path: str | Path,
        *,
        model: str = "",
        tokenizer_model: str | None = None,
        measurement_source: str = "native_tokenizer",
        fallback_reason: str | None = None,
        fallback_tokenizer_model: str | None = None,
    ) -> None:
        try:
            import tiktoken
            from tiktoken.load import load_tiktoken_bpe
        except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
            raise RuntimeError("tiktoken is required for model-native tokenizer counting") from exc

        path = Path(tokenizer_path)
        mergeable_ranks = load_tiktoken_bpe(str(path))
        special_tokens = _load_special_tokens(
            path,
            tokenizer_model=tokenizer_model,
            base_token_count=len(mergeable_ranks),
        )
        self._encoding = tiktoken.Encoding(
            name=path.stem,
            pat_str=KIMI_TIKTOKEN_PAT_STR,
            mergeable_ranks=mergeable_ranks,
            special_tokens=special_tokens,
        )
        self._model = model
        self.measurement_source = measurement_source
        self.measurement_estimated = measurement_source != "native_tokenizer"
        self.measurement_tokenizer = tokenizer_model or path.name
        self.measurement_fallback_reason = fallback_reason
        self.measurement_fallback_tokenizer_model = fallback_tokenizer_model

    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        return len(self._encoding.encode(str(text), allowed_special="all"))

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for message in messages:
            content = NativeTokenizerCounter.content_text(message.content)
            piece = f"<|start|>{message.role}\n{content}<|end|>"
            total += self.count(piece, model=model, **kwargs)
            if isinstance(message, AssistantMessage) and message.tool_calls:
                total += self.count(
                    json.dumps([call.model_dump() for call in message.tool_calls], ensure_ascii=False),
                    model=model,
                    **kwargs,
                )
        return total + 3

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        if not tools:
            return 0
        total = 0
        for index, tool in enumerate(tools):
            function_obj = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            }
            piece = f"<|start|>functions.{tool.name}:{index}\n"
            piece += json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))
            piece += "<|end|>"
            total += self.count(piece, model=model, **kwargs)
        return total + 3


def _load_special_tokens(
    path: Path,
    *,
    tokenizer_model: str | None,
    base_token_count: int,
) -> dict[str, int]:
    """Load repository special-token IDs, with a Kimi-compatible local fallback."""
    config_path = path.parent / "tokenizer_config.json"
    configured: dict[str, int] = {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        config = {}
    added_tokens_decoder = config.get("added_tokens_decoder") if isinstance(config, dict) else None
    if isinstance(added_tokens_decoder, dict):
        for raw_id, item in added_tokens_decoder.items():
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                continue
            try:
                token_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            configured[item["content"]] = token_id

    if configured:
        return _fill_reserved_special_tokens(configured, base_token_count)

    model_name = str(tokenizer_model or "").casefold()
    defaults = (
        _K3_SPECIAL_TOKEN_IDS if "kimi-k3" in model_name or model_name.endswith("/kimi-k3") else _K2_SPECIAL_TOKEN_IDS
    )
    return _fill_reserved_special_tokens(dict(defaults), base_token_count)


def _fill_reserved_special_tokens(tokens: dict[str, int], base_token_count: int) -> dict[str, int]:
    used_ids = set(tokens.values())
    for token_id in range(base_token_count, base_token_count + 256):
        if token_id not in used_ids:
            tokens[f"<|reserved_token_{token_id}|>"] = token_id
    return tokens


__all__ = ["KIMI_TIKTOKEN_PAT_STR", "TiktokenModelCounter"]
