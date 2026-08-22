# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Custom veRL SFT dataset for pre-tokenized v1-compatible parquet."""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from omegaconf import DictConfig, ListConfig
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizer, ProcessorMixin
from verl.utils.dataset.dataset_utils import DatasetPadMode
from verl.utils.tokenizer import normalize_token_ids

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

try:
    from verl.utils.py_functional import convert_nested_value_to_list_recursive
except ImportError:
    from verl.utils.dataset.multiturn_sft_dataset import convert_nested_value_to_list_recursive

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("VERL_LOGGING_LEVEL", "WARN"))

_PAD_MODES = ("right", "no_padding")
_TRUNCATION_MODES = ("error", "left", "right")


class QwenMultiTurnSFTDataset(Dataset):
    """Read v1-style SFT parquet rows for veRL's custom dataset hook."""

    def __init__(
        self,
        parquet_files: str | list[str],
        tokenizer: PreTrainedTokenizer,
        config: DictConfig,
        processor: ProcessorMixin | None = None,
        max_samples: int = -1,
    ):
        self.tokenizer = tokenizer
        self.processor = processor
        self.max_samples = max_samples
        self._config = config or {}
        self.pad_mode = self._config.get("pad_mode", "no_padding")
        self.truncation = self._config.get("truncation", "left")
        self.max_length = self._config.get("max_length", 65536)
        self.messages_key = self._config.get("messages_key", "messages")
        self.loss_mask_key = self._config.get("loss_mask_key", "loss_mask")
        self.turn_offsets_key = self._config.get("turn_offsets_key", "turn_offsets")
        self.window_length = self._config.get("window_length", None)
        self.window_overlap_turns = self._config.get("window_overlap_turns", 2)
        self.sliding_window = self.window_length is not None and int(self.window_length) > 0
        self.enable_thinking_key = self._config.get("enable_thinking_key", "enable_thinking")
        self.enable_thinking_default = self._config.get("enable_thinking_default", None)
        self.apply_chat_template_kwargs = self._config.get("apply_chat_template_kwargs", {})
        if self.pad_mode not in _PAD_MODES:
            raise ValueError(f"Expect pad_mode to be 'right' or 'no_padding'. Got {self.pad_mode}")
        if self.truncation not in _TRUNCATION_MODES:
            raise ValueError(f"Expect truncation to be one of {_TRUNCATION_MODES}. Got {self.truncation}")

        self.parquet_files = parquet_files if isinstance(parquet_files, list | ListConfig) else [parquet_files]

        self._download()
        self._read_files_and_process()
        logger.info(
            "QwenMultiTurnSFTDataset ready: %d samples pre_tokenized=%s",
            len(self),
            self.pre_tokenized,
        )

    def _download(self) -> None:
        for i, parquet_file in enumerate(self.parquet_files):
            from verl.utils.fs import copy_local_path_from_hdfs

            self.parquet_files[i] = copy_local_path_from_hdfs(parquet_file, verbose=True)

    def _read_parquet(self, parquet_file: str) -> pd.DataFrame:
        try:
            return pd.read_parquet(parquet_file, dtype_backend="pyarrow")
        except TypeError:
            return pd.read_parquet(parquet_file)

    def _read_files_and_process(self) -> None:
        self.dataframe = pd.concat(map(self._read_parquet, self.parquet_files))
        total = len(self.dataframe)
        logger.info("QwenMultiTurnSFTDataset: %d samples loaded", total)

        if 0 < self.max_samples < total:
            seed = self._config.get("seed") if self._config else None
            selected = np.random.default_rng(seed).choice(
                total,
                size=self.max_samples,
                replace=False,
            )
            self.dataframe = self.dataframe.iloc[selected.tolist()]
            logger.info("Selected %d random samples out of %d", self.max_samples, total)

        self.pre_tokenized = all(column in self.dataframe.columns for column in ("input_ids", self.loss_mask_key))
        if self.pre_tokenized:
            logger.info("QwenMultiTurnSFTDataset: pre-tokenized mode (bypass runtime tokenization)")
            self._build_window_index()
            return

        message_rows = self.dataframe[self.messages_key]
        self.messages = message_rows.apply(convert_nested_value_to_list_recursive).tolist()
        self.enable_thinking = (
            self.dataframe[self.enable_thinking_key].tolist()
            if self.enable_thinking_key in self.dataframe.columns
            else None
        )

    def _build_window_index(self) -> None:
        self.window_index: list[tuple[int, int, int]] = []
        n_windowed = 0
        n_overlong = 0
        win_len = int(self.window_length) if self.window_length is not None else 0
        has_turn_offsets = self.turn_offsets_key in self.dataframe.columns
        input_rows = self.dataframe["input_ids"].tolist()
        offset_rows = (
            self.dataframe[self.turn_offsets_key].apply(convert_nested_value_to_list_recursive).tolist()
            if has_turn_offsets
            else [None] * len(input_rows)
        )
        for row_idx, (input_ids, offsets) in enumerate(zip(input_rows, offset_rows, strict=False)):
            seq_len = len(input_ids)

            if not self.sliding_window or seq_len <= win_len:
                self.window_index.append((row_idx, 0, seq_len))
                continue

            n_overlong += 1
            if not offsets:
                self.window_index.append((row_idx, 0, seq_len))
                continue

            n_windowed += 1
            start_idx = 0
            last_segment = len(offsets) - 1
            while start_idx <= last_segment:
                end_idx = self._window_end_index(offsets, start_idx, win_len)
                self.window_index.append((row_idx, offsets[start_idx][0], offsets[end_idx][1]))
                if end_idx >= last_segment:
                    break
                start_idx = max(start_idx + 1, end_idx + 1 - self.window_overlap_turns)

        logger.info(
            "QwenMultiTurnSFTDataset: %d window entries (%d overlong rows, %d windowed, window_length=%s)",
            len(self.window_index),
            n_overlong,
            n_windowed,
            self.window_length,
        )

    def _window_end_index(self, offsets: list[list[int]], start_idx: int, win_len: int) -> int:
        start_token = offsets[start_idx][0]
        end_idx = start_idx
        for candidate_idx, candidate in enumerate(offsets[start_idx:], start=start_idx):
            if candidate[1] - start_token > win_len:
                break
            end_idx = candidate_idx
        return end_idx

    def __len__(self) -> int:
        return len(self.window_index) if self.pre_tokenized else len(self.dataframe)

    def _get_pre_tokenized(self, item: int) -> dict[str, torch.Tensor]:
        row_idx, start_token, end_token = self.window_index[item]
        row = self.dataframe.iloc[row_idx]
        input_ids = self._slice_tensor(row["input_ids"], start_token, end_token, dtype=torch.long)
        attention_mask = self._slice_tensor(row["attention_mask"], start_token, end_token, dtype=torch.long)
        loss_mask = self._slice_tensor(row[self.loss_mask_key], start_token, end_token, dtype=torch.float32)
        position_ids = torch.arange(end_token - start_token, dtype=torch.long)
        return self._apply_length_policy(
            input_ids,
            attention_mask,
            loss_mask,
            position_ids,
            right_pad_error_truncates=True,
        )

    @staticmethod
    def _slice_tensor(value: Any, start: int, end: int, *, dtype: torch.dtype) -> torch.Tensor:
        as_py = getattr(value, "as_py", None)
        if callable(as_py):
            value = as_py()
        try:
            sliced = value[slice(start, end)]
        except TypeError:
            sliced = list(value)[slice(start, end)]
        to_pylist = getattr(sliced, "to_pylist", None)
        if callable(to_pylist):
            sliced = to_pylist()
        tolist = getattr(sliced, "tolist", None)
        if callable(tolist):
            sliced = tolist()
        return torch.as_tensor(sliced, dtype=dtype)

    def _truncate(self, input_ids, attention_mask, loss_mask, position_ids):
        return (
            self._slice_to_max_length(input_ids),
            self._slice_to_max_length(attention_mask),
            self._slice_to_max_length(loss_mask),
            self._slice_position_ids_to_max_length(position_ids),
        )

    def _slice_to_max_length(self, tensor: torch.Tensor) -> torch.Tensor:
        if self.truncation == "left":
            return tensor[slice(-self.max_length, None)]
        return tensor[slice(None, self.max_length)]

    def _slice_position_ids_to_max_length(self, position_ids: torch.Tensor) -> torch.Tensor:
        if self.truncation == "left":
            return position_ids[..., slice(-self.max_length, None)]
        return position_ids[..., slice(None, self.max_length)]

    def _apply_length_policy(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        loss_mask: torch.Tensor,
        position_ids: torch.Tensor,
        *,
        right_pad_error_truncates: bool = False,
    ) -> dict[str, torch.Tensor]:
        sequence_length = input_ids.shape[0]
        if self.pad_mode == DatasetPadMode.RIGHT:
            if sequence_length < self.max_length:
                pad_len = self.max_length - sequence_length
                pad_token_id = self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else 0
                pad_shape = (pad_len,)
                input_ids = torch.cat([input_ids, torch.full(pad_shape, pad_token_id, dtype=torch.long)])
                attention_mask = torch.cat([attention_mask, torch.zeros(pad_shape, dtype=torch.long)])
                loss_mask = torch.cat([loss_mask, torch.zeros(pad_shape, dtype=torch.float32)])
                position_ids = F.pad(position_ids, (0, pad_len), value=0)
            elif sequence_length > self.max_length:
                if self.truncation == "error" and not right_pad_error_truncates:
                    raise ValueError(f"{sequence_length=} is larger than {self.max_length=}")
                input_ids, attention_mask, loss_mask, position_ids = self._truncate(
                    input_ids,
                    attention_mask,
                    loss_mask,
                    position_ids,
                )
            return {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "position_ids": position_ids,
                "loss_mask": loss_mask,
            }

        if self.pad_mode == DatasetPadMode.NO_PADDING:
            if sequence_length > self.max_length and self.truncation == "error":
                raise ValueError(f"{sequence_length=} is larger than {self.max_length=}")
            if sequence_length > self.max_length:
                input_ids, _, loss_mask, position_ids = self._truncate(
                    input_ids,
                    attention_mask,
                    loss_mask,
                    position_ids,
                )
            return {"input_ids": input_ids, "position_ids": position_ids, "loss_mask": loss_mask}

        raise ValueError(f"Unknown pad mode {self.pad_mode}")

    def _tokenize_single_message(self, message: dict[str, Any]) -> list[int]:
        try:
            return normalize_token_ids(
                self.tokenizer.apply_chat_template([message], add_generation_prompt=False, tokenize=True)
            )
        except _TOKENIZER_TEMPLATE_ERRORS:
            if message["role"] == "system":
                text = f"<|im_start|>system\n{self._flatten_content(message.get('content', ''))}<|im_end|>\n"
                return self.tokenizer.encode(text, add_special_tokens=False)

            dummy = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
            try:
                dummy_prefix = normalize_token_ids(
                    self.tokenizer.apply_chat_template(dummy, add_generation_prompt=False, tokenize=True)
                )
                combined = normalize_token_ids(
                    self.tokenizer.apply_chat_template(dummy + [message], add_generation_prompt=False, tokenize=True)
                )
                return combined[slice(len(dummy_prefix), None)]
            except _TOKENIZER_TEMPLATE_ERRORS:
                text = f"<|im_start|>{message['role']}\n{self._flatten_content(message.get('content', ''))}<|im_end|>\n"
                return self.tokenizer.encode(text, add_special_tokens=False)

    @staticmethod
    def _flatten_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                parts.append(str(item.get("text", item.get("value", ""))) if isinstance(item, dict) else str(item))
            return "".join(parts)
        return "" if content is None else str(content)

    def _build_inputs(self, item: int) -> tuple[torch.Tensor, torch.Tensor]:
        row_dict = self.dataframe.iloc[item].to_dict()
        messages = convert_nested_value_to_list_recursive(row_dict[self.messages_key])
        input_ids: list[int] = []
        loss_mask: list[float] = []
        assistant_prompt_len: int | None = None

        def get_assistant_prompt_len() -> int:
            nonlocal assistant_prompt_len
            if assistant_prompt_len is None:
                assistant_prompt_len = len(self._tokenize_single_message({"role": "assistant", "content": ""}))
            return assistant_prompt_len

        for message in messages:
            tokens = self._tokenize_single_message(message)
            if message["role"] == "assistant":
                prompt_len = get_assistant_prompt_len()
                input_ids.extend(tokens)
                if prompt_len < len(tokens):
                    loss_mask.extend([0.0] * prompt_len + [1.0] * (len(tokens) - prompt_len))
                else:
                    loss_mask.extend([1.0] * len(tokens))
            else:
                input_ids.extend(tokens)
                loss_mask.extend([0.0] * len(tokens))

        return torch.tensor(input_ids, dtype=torch.long), torch.tensor(loss_mask, dtype=torch.float32)

    def __getitem__(self, item: int) -> dict[str, torch.Tensor]:
        if self.pre_tokenized:
            return self._get_pre_tokenized(item)

        input_ids, loss_mask = self._build_inputs(item)
        position_ids = torch.arange(len(input_ids), dtype=torch.long)
        attention_mask = torch.ones(len(input_ids), dtype=torch.long)
        return self._apply_length_policy(input_ids, attention_mask, loss_mask, position_ids)
