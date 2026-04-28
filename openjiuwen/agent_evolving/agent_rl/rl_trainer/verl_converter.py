# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Convert trajectory samples into verl DataProto.

Shared by online gateway/scheduler and direct optimizer training paths.
"""

from __future__ import annotations

import importlib
from typing import Any, Optional, Sequence


class VerlDataProtoConverter:
    """Build a verl DataProto from trajectory samples."""

    def __init__(
        self,
        *,
        dataproto_cls: Optional[type] = None,
        pad_token_id: int = 0,
        default_score: float = 0.0,
        max_prompt_length: Optional[int] = None,
        max_response_length: Optional[int] = None,
        truncation: str = "truncate",
        filter_overlong_prompts: bool = False,
    ) -> None:
        """Initialize converter options.

        Args:
            dataproto_cls: Optional injected verl DataProto class for tests.
            pad_token_id: Padding token id used for tensor padding.
            default_score: Default judge score when sample score is missing.
            max_prompt_length: Optional prompt-token cap applied before padding.
            max_response_length: Optional response-token cap applied before padding.
            truncation: Truncation policy name from trainer config.
            filter_overlong_prompts: Whether to drop samples with overly long prompts.
        """
        self._dataproto_cls = dataproto_cls
        self.pad_token_id = int(pad_token_id)
        self.default_score = float(default_score)
        self.max_prompt_length = self._normalize_optional_limit(max_prompt_length)
        self.max_response_length = self._normalize_optional_limit(max_response_length)
        self.truncation = str(truncation or "truncate")
        self.filter_overlong_prompts = bool(filter_overlong_prompts)

    def convert_batch(self, batch: dict[str, Any]) -> Any:
        """Convert one trajectory batch payload to verl DataProto.

        Args:
            batch: Batch payload containing ``samples`` list.

        Returns:
            verl DataProto object.
        """
        samples = batch.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("batch.samples must be a non-empty list")
        return self.convert_samples(samples)

    def convert_samples(self, samples: Sequence[dict[str, Any]]) -> Any:
        """Convert trajectory sample list to verl DataProto.

        Args:
            samples: Trajectory sample sequence.

        Returns:
            verl DataProto object.
        """
        if not samples:
            raise ValueError("samples must be non-empty")

        rows: list[dict[str, Any]] = []
        dropped_samples = 0
        prompt_truncated = 0
        response_truncated = 0
        for i, sample in enumerate(samples):
            row = self._normalize_sample(sample=sample, idx=i)
            if row is None:
                dropped_samples += 1
                continue
            prompt_truncated += int(row.pop("_prompt_truncated", False))
            response_truncated += int(row.pop("_response_truncated", False))
            rows.append(row)
        if not rows:
            raise ValueError("all samples were dropped during normalization")

        prompt_max = max(max((len(r["prompt_ids"]) for r in rows), default=0), 1)
        response_max = max(max((len(r["response_ids"]) for r in rows), default=0), 1)
        input_max = prompt_max + response_max
        torch = self._load_torch()

        bs = len(rows)
        input_ids = torch.full((bs, input_max), self.pad_token_id, dtype=torch.long)
        attention_mask = torch.zeros((bs, input_max), dtype=torch.long)
        position_ids = torch.zeros((bs, input_max), dtype=torch.long)
        # Per-turn training (OpenClaw-RL style): response_mask covers only
        # the response portion.  prompt=0 (context, not in loss),
        # response=1 (current turn model output, in loss).
        response_mask = torch.zeros((bs, response_max), dtype=torch.long)
        old_log_probs = torch.zeros((bs, response_max), dtype=torch.float32)
        token_level_scores = torch.zeros((bs, response_max), dtype=torch.float32)
        prompts = torch.full((bs, prompt_max), self.pad_token_id, dtype=torch.long)
        responses = torch.full((bs, response_max), self.pad_token_id, dtype=torch.long)

        for row_idx, row in enumerate(rows):
            pl = len(row["prompt_ids"])
            rl = len(row["response_ids"])

            if pl > 0:
                prompt_tensor = torch.tensor(row["prompt_ids"], dtype=torch.long)
                prompts[row_idx, :pl] = prompt_tensor
                input_ids[row_idx, :pl] = prompt_tensor
                attention_mask[row_idx, :pl] = 1

            if rl > 0:
                response_tensor = torch.tensor(row["response_ids"], dtype=torch.long)
                responses[row_idx, :rl] = response_tensor
                input_ids[row_idx, prompt_max:prompt_max + rl] = response_tensor
                attention_mask[row_idx, prompt_max:prompt_max + rl] = 1
                response_mask[row_idx, :rl] = 1
                old_log_probs[row_idx, :rl] = torch.tensor(
                    row["response_logprobs"], dtype=torch.float32,
                )
                token_level_scores[row_idx, :rl] = row["judge_score"]

            active_positions = torch.cumsum(attention_mask[row_idx], dim=0) - 1
            position_ids[row_idx] = torch.clamp(active_positions, min=0)

        tensors = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "position_ids": position_ids,
            "prompts": prompts,
            "responses": responses,
            "response_mask": response_mask,
            "old_log_probs": old_log_probs,
            "token_level_scores": token_level_scores,
        }
        sample_ids = [r["sample_id"] for r in rows]
        non_tensors = {
            "sample_id": sample_ids,
            "data_id_list": sample_ids,
            "session_id": [r["session_id"] for r in rows],
            "turn_num": [r["turn_num"] for r in rows],
            "created_at": [r["created_at"] for r in rows],
            "mode": [r["mode"] for r in rows],
            "io_mode": [r["io_mode"] for r in rows],
            "model": [r["model"] for r in rows],
            "prompt_text": [r["prompt_text"] for r in rows],
            "response_text": [r["response_text"] for r in rows],
            "judge_score": [r["judge_score"] for r in rows],
        }
        meta_info = {
            "source": "agent-online-rl",
            "converter": "verl_dataproto",
            "num_samples": bs,
            "pad_token_id": self.pad_token_id,
            "dropped_samples": dropped_samples,
            "prompt_truncated_samples": prompt_truncated,
            "response_truncated_samples": response_truncated,
            "max_prompt_length": self.max_prompt_length,
            "max_response_length": self.max_response_length,
        }

        dataproto_cls = self._resolve_dataproto_cls()
        return dataproto_cls.from_dict(tensors=tensors, non_tensors=non_tensors, meta_info=meta_info)

    @staticmethod
    def _load_torch() -> Any:
        try:
            return importlib.import_module("torch")
        except Exception as exc:
            raise RuntimeError("Failed to import torch. Is torch installed?") from exc

    def _resolve_dataproto_cls(self) -> type:
        if self._dataproto_cls is not None:
            return self._dataproto_cls
        try:
            module = importlib.import_module("verl.protocol")
            cls = getattr(module, "DataProto")
        except Exception as exc:
            raise RuntimeError("Failed to import verl DataProto. Is verl installed?") from exc
        self._dataproto_cls = cls
        return cls

    def _normalize_sample(self, *, sample: dict[str, Any], idx: int) -> Optional[dict[str, Any]]:
        trajectory = sample.get("trajectory", {})
        if not isinstance(trajectory, dict):
            trajectory = {}

        prompt_ids = self._coerce_int_list(trajectory.get("prompt_ids"))
        response_ids = self._coerce_int_list(trajectory.get("response_ids"))
        input_ids = self._coerce_int_list(trajectory.get("input_ids"))

        if not input_ids:
            input_ids = prompt_ids + response_ids
        _has_response = bool(response_ids)
        _has_prompt = bool(prompt_ids)
        _input_longer_than_prompt = len(input_ids) >= len(prompt_ids) if _has_prompt else False
        _input_longer_than_response = len(input_ids) >= len(response_ids) if _has_response else False
        can_split_input_by_prompt = not _has_response and input_ids and _has_prompt and _input_longer_than_prompt
        can_split_input_by_response = (
            not _has_prompt and input_ids and _has_response and _input_longer_than_response
        )

        if can_split_input_by_prompt:
            response_ids = input_ids[len(prompt_ids):]
        if can_split_input_by_response:
            prompt_ids = input_ids[:len(input_ids) - len(response_ids)]
        if not input_ids:
            raise ValueError(f"sample[{idx}] has no valid token ids")

        prompt_truncated = False
        response_truncated = False
        if self.max_prompt_length is not None and len(prompt_ids) > self.max_prompt_length:
            if self.filter_overlong_prompts and self.truncation != "truncate":
                return None
            # Keep the tail of the prompt so the latest dialogue state survives.
            prompt_ids = prompt_ids[-self.max_prompt_length:]
            prompt_truncated = True
        if self.max_response_length is not None and len(response_ids) > self.max_response_length:
            response_ids = response_ids[:self.max_response_length]
            response_truncated = True
        input_ids = prompt_ids + response_ids

        response_len = len(response_ids)
        response_logprobs = self._coerce_float_list(trajectory.get("response_logprobs"))
        response_logprobs = response_logprobs[:response_len]
        if response_len > len(response_logprobs):
            response_logprobs.extend([0.0] * (response_len - len(response_logprobs)))

        judge_obj = sample.get("judge", {})
        judge_score = self.default_score
        if isinstance(judge_obj, dict):
            sv = judge_obj.get("score")
            if isinstance(sv, (int, float)):
                judge_score = float(sv)

        return {
            "sample_id": str(sample.get("sample_id") or ""),
            "session_id": str(sample.get("session_id") or "default"),
            "turn_num": int(sample.get("turn_num") or 0),
            "created_at": str(sample.get("created_at") or ""),
            "mode": str(sample.get("mode") or ""),
            "io_mode": str(sample.get("io_mode") or ""),
            "model": str(sample.get("model") or ""),
            "prompt_text": str(trajectory.get("prompt_text") or ""),
            "response_text": str(trajectory.get("response_text") or ""),
            "input_ids": input_ids,
            "prompt_ids": prompt_ids,
            "response_ids": response_ids,
            "response_logprobs": response_logprobs,
            "judge_score": judge_score,
            "_prompt_truncated": prompt_truncated,
            "_response_truncated": response_truncated,
        }

    @staticmethod
    def _normalize_optional_limit(value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return None
        return limit if limit > 0 else None

    @staticmethod
    def _coerce_int_list(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        return [int(x) for x in value if isinstance(x, (int, float))]

    @staticmethod
    def _coerce_float_list(value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        return [float(x) for x in value if isinstance(x, (int, float))]
