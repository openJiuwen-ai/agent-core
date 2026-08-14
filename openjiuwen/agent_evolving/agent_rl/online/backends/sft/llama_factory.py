# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LLaMA-Factory dataset conversion and trainer adapter for online SFT."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_evolving.agent_rl.online.core.training_process import ManagedTrainingProcess

LLAMA_FACTORY_DATASET_NAME = "agent_sft"
logger = logging.getLogger("online_rl.scheduler.sft.llama_factory")


@dataclass(frozen=True)
class LLaMAFactoryDatasetPaths:
    """Files written for one LLaMA-Factory training dataset."""

    dataset_dir: Path
    train_file: Path
    dataset_info_file: Path
    train_yaml_file: Path
    stats_file: Path


@dataclass(frozen=True)
class LLaMAFactoryTrainConfig:
    """Minimal LLaMA-Factory LoRA SFT config derived from env and scheduler state."""

    model_name_or_path: str
    output_dir: str
    cutoff_len: int = 32768
    template: str = "qwen"
    dataset_name: str = LLAMA_FACTORY_DATASET_NAME
    learning_rate: str = "1e-5"
    num_train_epochs: float = 1.0
    per_device_train_batch_size: int = 1
    gradient_accumulation_steps: int = 1
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_target: str = "all"
    bf16: bool = True
    fp16: bool = False
    save_strategy: str = "no"
    max_steps: int = -1
    save_steps: int = 100000
    save_total_limit: int = 0
    logging_steps: int = 1
    extra_args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, model_name_or_path: str, output_dir: str) -> LLaMAFactoryTrainConfig:
        """Read LLaMA-Factory trainer knobs from process env."""

        return cls(
            model_name_or_path=model_name_or_path,
            output_dir=output_dir,
            cutoff_len=_env_int("SFT_LLAMAFACTORY_CUTOFF_LEN", _env_int("SFT_MAX_LENGTH", 32768)),
            template=os.getenv("SFT_LLAMAFACTORY_TEMPLATE", os.getenv("SFT_TEMPLATE", "qwen")).strip() or "qwen",
            dataset_name=os.getenv("SFT_LLAMAFACTORY_DATASET_NAME", LLAMA_FACTORY_DATASET_NAME).strip()
            or LLAMA_FACTORY_DATASET_NAME,
            learning_rate=os.getenv("SFT_LLAMAFACTORY_LR", os.getenv("SFT_LR", "1e-5")).strip() or "1e-5",
            num_train_epochs=_env_float("SFT_LLAMAFACTORY_EPOCHS", _env_float("SFT_TOTAL_EPOCHS", 1.0)),
            per_device_train_batch_size=_env_int("SFT_LLAMAFACTORY_PER_DEVICE_BATCH", 1),
            gradient_accumulation_steps=_env_int("SFT_LLAMAFACTORY_GRAD_ACCUM", 1),
            lora_rank=_env_int("SFT_LORA_RANK", 16),
            lora_alpha=_env_int("SFT_LORA_ALPHA", 32),
            lora_target=os.getenv("SFT_TARGET_MODULES", os.getenv("SFT_LORA_TARGET", "all")).strip() or "all",
            bf16=_env_bool("SFT_BF16", True),
            fp16=_env_bool("SFT_FP16", False),
            save_strategy=os.getenv("SFT_LLAMAFACTORY_SAVE_STRATEGY", "no").strip() or "no",
            max_steps=_env_int("SFT_LLAMAFACTORY_MAX_STEPS", -1),
            save_steps=_env_int("SFT_LLAMAFACTORY_SAVE_STEPS", 100000),
            save_total_limit=_env_int("SFT_LLAMAFACTORY_SAVE_TOTAL_LIMIT", 0),
            logging_steps=_env_int("SFT_LLAMAFACTORY_LOGGING_STEPS", 1),
            extra_args=_env_json_dict("SFT_LLAMAFACTORY_EXTRA_ARGS"),
        )

    def to_yaml_dict(self, *, dataset_dir: Path) -> dict[str, Any]:
        config: dict[str, Any] = {
            "stage": "sft",
            "do_train": True,
            "model_name_or_path": self.model_name_or_path,
            "dataset": self.dataset_name,
            "dataset_dir": str(dataset_dir),
            "template": self.template,
            "finetuning_type": "lora",
            "output_dir": self.output_dir,
            "overwrite_output_dir": True,
            "cutoff_len": self.cutoff_len,
            "learning_rate": float(self.learning_rate),
            "num_train_epochs": self.num_train_epochs,
            "per_device_train_batch_size": self.per_device_train_batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "lr_scheduler_type": "constant",
            "logging_steps": self.logging_steps,
            "save_steps": self.save_steps,
            "save_strategy": self.save_strategy,
            "bf16": self.bf16,
            "fp16": self.fp16,
            "report_to": "none",
            "trust_remote_code": True,
            "lora_rank": self.lora_rank,
            "lora_alpha": self.lora_alpha,
            "lora_target": self.lora_target,
            "gradient_checkpointing": True,
            "ddp_timeout": 180000000,
        }
        if self.max_steps >= 0:
            config["max_steps"] = self.max_steps
        if self.save_total_limit > 0:
            config["save_total_limit"] = self.save_total_limit
        config.update(self.extra_args)
        return config


class LLaMAFactoryTrainerAdapter:
    """Prepare LLaMA-Factory files and run one LoRA SFT job."""

    def __init__(
        self,
        *,
        base_model_path: str,
        training_gpu_ids: str = "",
        cli_bin: str = "",
        process_runner: ManagedTrainingProcess | None = None,
    ) -> None:
        self.base_model_path = base_model_path
        self.training_gpu_ids = training_gpu_ids
        self.cli_bin = cli_bin or os.getenv("LLAMAFACTORY_CLI", "llamafactory-cli")
        self._process_runner = process_runner or ManagedTrainingProcess("llama-factory")

    def train(
        self,
        *,
        samples: list[dict[str, Any]],
        dataset_dir: Path,
        output_dir: Path,
        run_dir: Path,
    ) -> LLaMAFactoryDatasetPaths:
        paths = prepare_llama_factory_sft_run(
            samples,
            dataset_dir=dataset_dir,
            train_config=LLaMAFactoryTrainConfig.from_env(
                model_name_or_path=self.base_model_path,
                output_dir=str(output_dir),
            ),
        )
        self._run_cli(paths.train_yaml_file, run_dir=run_dir)
        return paths

    def _run_cli(self, train_yaml_file: Path, *, run_dir: Path) -> None:
        run_llama_factory_train_cli(
            train_yaml_file,
            run_dir=run_dir,
            cli_bin=self.cli_bin,
            training_gpu_ids=self.training_gpu_ids,
            process_runner=self._process_runner,
        )


def run_llama_factory_train_cli(
    train_yaml_file: Path,
    *,
    run_dir: Path,
    cli_bin: str = "",
    training_gpu_ids: str = "",
    process_runner: ManagedTrainingProcess | None = None,
) -> None:
    """Run ``llamafactory-cli train`` with isolated training GPU visibility."""

    selected_cli = cli_bin or os.getenv("LLAMAFACTORY_CLI", "llamafactory-cli")
    executable = shutil.which(selected_cli)
    if executable:
        command = [executable, "train", str(train_yaml_file)]
    else:
        command = [sys.executable, "-m", "llamafactory.cli", "train", str(train_yaml_file)]
    env = os.environ.copy()
    if executable:
        # LLaMA-Factory launches distributed workers through ``torchrun`` from
        # PATH. When the scheduler runs in a different conda env from
        # ``llamafactory-cli``, keep torchrun and worker Python in the same env
        # as the selected CLI.
        cli_bin_dir = str(Path(executable).resolve().parent)
        env["PATH"] = cli_bin_dir + os.pathsep + env.get("PATH", "")
    gpu_ids = [item.strip() for item in training_gpu_ids.split(",") if item.strip()]
    if training_gpu_ids:
        env["CUDA_VISIBLE_DEVICES"] = training_gpu_ids
    if gpu_ids:
        env["NPROC_PER_NODE"] = str(len(gpu_ids))
    runner = process_runner or ManagedTrainingProcess("llama-factory")
    runner.run(command, cwd=run_dir, env=env)


def prepare_llama_factory_sft_run(
    samples: list[dict[str, Any]],
    *,
    dataset_dir: Path,
    train_config: LLaMAFactoryTrainConfig,
) -> LLaMAFactoryDatasetPaths:
    """Write LLaMA-Factory openai-format dataset, dataset_info, config, and stats."""

    records = convert_samples_to_llama_factory_openai(samples)
    if not records:
        raise ValueError("no valid SFT samples after LLaMA-Factory conversion")
    records = truncate_records_to_cutoff(records, train_config=train_config)
    return prepare_llama_factory_records_run(records, dataset_dir=dataset_dir, train_config=train_config)


def prepare_llama_factory_records_run(
    records: list[dict[str, Any]],
    *,
    dataset_dir: Path,
    train_config: LLaMAFactoryTrainConfig,
) -> LLaMAFactoryDatasetPaths:
    """Write already-converted LLaMA-Factory openai-format records."""

    if not records:
        raise ValueError("no valid LLaMA-Factory records to train")

    dataset_dir.mkdir(parents=True, exist_ok=True)
    train_file = dataset_dir / "train.json"
    dataset_info_file = dataset_dir / "dataset_info.json"
    train_yaml_file = dataset_dir / "train.yaml"
    stats_file = dataset_dir / "stats.json"

    train_file.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dataset_info_file.write_text(
        json.dumps(_dataset_info(train_config.dataset_name, train_file.name), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    train_yaml_file.write_text(
        yaml.safe_dump(train_config.to_yaml_dict(dataset_dir=dataset_dir), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    stats_file.write_text(json.dumps(_stats(records), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LLaMAFactoryDatasetPaths(
        dataset_dir=dataset_dir,
        train_file=train_file,
        dataset_info_file=dataset_info_file,
        train_yaml_file=train_yaml_file,
        stats_file=stats_file,
    )


def load_sft_samples_from_path(path: str | Path, *, limit: int = 0) -> list[dict[str, Any]]:
    """Load V1/SFT samples from a JSON file, JSONL file, or directory tree."""

    source = Path(path)
    files: list[Path]
    if source.is_dir():
        files = sorted([*source.rglob("*.jsonl"), *source.rglob("*.json")])
    else:
        files = [source]

    samples: list[dict[str, Any]] = []
    for file_path in files:
        for sample in _load_sft_samples_file(file_path):
            samples.append(sample)
            if limit > 0 and len(samples) >= limit:
                return samples
    return samples


def convert_samples_to_llama_factory_openai(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert V1/sft-sample-v1 records to LLaMA-Factory openai-format rows."""

    records: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        messages, tools = _messages_and_tools(sample)
        if not messages:
            continue
        target_index = _target_message_index(messages)
        if target_index < 0:
            continue
        normalized = _normalize_openai_messages(messages[: target_index + 1])
        if not _valid_llama_factory_dialog(normalized):
            continue
        records.append(
            {
                "messages": normalized,
                "tools": _json_text(tools) if tools else "",
                "metadata": _metadata(sample, idx),
            }
        )
    return records


def truncate_records_to_cutoff(
    records: list[dict[str, Any]],
    *,
    train_config: LLaMAFactoryTrainConfig,
) -> list[dict[str, Any]]:
    """Drop leading text from overlength records before LLaMA-Factory sees them.

    LLaMA-Factory's runtime truncation is hard to audit from scheduler logs. The
    online SFT path does an explicit tokenizer pass here so long supervisor
    replays keep their final assistant target while older context is removed.
    """

    if train_config.cutoff_len <= 0 or not _env_bool("SFT_LLAMAFACTORY_TRUNCATE_TO_CUTOFF", True):
        return records

    try:
        tokenizer = _load_tokenizer(train_config.model_name_or_path)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Skip explicit SFT cutoff truncation because tokenizer cannot be loaded model=%s error=%s",
            train_config.model_name_or_path,
            exc,
        )
        return records
    truncated: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        truncated.append(
            _truncate_record_to_cutoff(
                record,
                tokenizer=tokenizer,
                cutoff_len=train_config.cutoff_len,
                record_index=idx,
            )
        )
    return truncated


def _truncate_record_to_cutoff(
    record: dict[str, Any],
    *,
    tokenizer: Any,
    cutoff_len: int,
    record_index: int,
) -> dict[str, Any]:
    before = _estimate_record_tokens(record, tokenizer=tokenizer)
    if before <= cutoff_len:
        record.setdefault("metadata", {})["token_count"] = before
        return record

    out = {
        **record,
        "messages": [dict(message) for message in list(record.get("messages") or [])],
        "metadata": dict(record.get("metadata") or {}),
    }
    removed_tokens = 0
    trimmed_message_count = 0
    tools_dropped = False
    target_trimmed = False
    target_index = _last_assistant_index(out["messages"])

    while True:
        current = _estimate_record_tokens(out, tokenizer=tokenizer)
        if current <= cutoff_len:
            break
        overage = current - cutoff_len
        message_index = _next_trimmable_message_index(out["messages"], target_index=target_index)
        if message_index < 0:
            if out.get("tools"):
                out["tools"] = ""
                tools_dropped = True
                logger.warning(
                    "SFT record tools dropped for cutoff record=%s tokens=%s cutoff=%s",
                    _record_log_id(out, record_index),
                    current,
                    cutoff_len,
                )
                continue
            message_index = _next_trimmable_message_index(
                out["messages"],
                target_index=target_index,
                include_target=True,
            )
            if message_index < 0:
                logger.warning(
                    "SFT record remains over cutoff after all truncation record=%s tokens=%s cutoff=%s",
                    _record_log_id(out, record_index),
                    current,
                    cutoff_len,
                )
                break
        removed = _trim_message_content_from_start(
            out["messages"][message_index],
            tokenizer=tokenizer,
            min_tokens=max(overage + 32, 64),
        )
        if removed <= 0:
            break
        if message_index == target_index:
            target_trimmed = True
        removed_tokens += removed
        trimmed_message_count += 1

    after = _estimate_record_tokens(out, tokenizer=tokenizer)
    out["metadata"].update(
        {
            "truncated_to_cutoff": True,
            "token_count_before_truncate": before,
            "token_count_after_truncate": after,
            "truncate_cutoff_len": cutoff_len,
            "truncate_removed_content_tokens": removed_tokens,
            "truncate_trimmed_message_count": trimmed_message_count,
            "truncate_tools_dropped": tools_dropped,
            "truncate_target_trimmed": target_trimmed,
        }
    )
    logger.warning(
        "SFT record truncated record=%s tokens_before=%s tokens_after=%s cutoff=%s removed_content_tokens=%s",
        _record_log_id(out, record_index),
        before,
        after,
        cutoff_len,
        removed_tokens,
    )
    return out


def _load_tokenizer(model_name_or_path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_name_or_path, trust_remote_code=True)


def _estimate_record_tokens(record: dict[str, Any], *, tokenizer: Any) -> int:
    messages = list(record.get("messages") or [])
    tools = _parse_tools(record.get("tools"))
    try:
        if tools:
            return len(
                tokenizer.apply_chat_template(
                    messages,
                    tools=tools,
                    tokenize=True,
                    add_generation_prompt=False,
                )
            )
        return len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False))
    except (KeyError, TypeError, ValueError, RuntimeError):
        text = "\n".join(f"{message.get('role')}: {message.get('content')}" for message in messages)
        if tools:
            text += "\nTOOLS: " + json.dumps(tools, ensure_ascii=False, sort_keys=True)
        return len(tokenizer.encode(text, add_special_tokens=True))


def _parse_tools(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _last_assistant_index(messages: list[dict[str, Any]]) -> int:
    for idx in range(len(messages) - 1, -1, -1):
        if str(messages[idx].get("role") or "") in {"assistant", "function_call"}:
            return idx
    return -1


def _next_trimmable_message_index(
    messages: list[dict[str, Any]],
    *,
    target_index: int,
    include_target: bool = False,
) -> int:
    for idx, message in enumerate(messages):
        if idx == target_index and not include_target:
            continue
        if str(message.get("content") or ""):
            return idx
    return -1


def _trim_message_content_from_start(
    message: dict[str, Any],
    *,
    tokenizer: Any,
    min_tokens: int,
) -> int:
    content = str(message.get("content") or "")
    if not content:
        return 0
    token_ids = tokenizer.encode(content, add_special_tokens=False)
    if not token_ids:
        message["content"] = ""
        return 0
    remove_count = min(len(token_ids), max(1, min_tokens))
    message["content"] = tokenizer.decode(token_ids[remove_count:], skip_special_tokens=False)
    return remove_count


def _record_log_id(record: dict[str, Any], record_index: int) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return str(metadata.get("source_sample_id") or metadata.get("session_id") or record_index)


def convert_samples_to_llama_factory_openai_prefix(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert V1/sft-sample-v1 records into prefix-split LLaMA-Factory rows."""

    records: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        messages, tools = _messages_and_tools(sample)
        if not messages:
            continue
        target_indices = _prefix_target_indices(messages)
        if not target_indices:
            continue
        target_total = len(target_indices)
        for prefix_index, target_index in enumerate(target_indices, start=1):
            normalized = _normalize_openai_messages(messages[: target_index + 1])
            if not _valid_llama_factory_dialog(normalized):
                continue
            records.append(
                {
                    "messages": normalized,
                    "tools": _json_text(tools) if tools else "",
                    "metadata": {
                        **_metadata(sample, idx),
                        "prefix_split": True,
                        "prefix_index": prefix_index,
                        "prefix_total": target_total,
                        "source_message_count": len(messages),
                        "target_message_index": target_index,
                    },
                }
            )
    return records


def _load_sft_samples_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [
            item
            for item in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            if isinstance(item, dict)
        ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("samples") or payload.get("trajectories") or payload.get("data") or [payload]
    if not isinstance(payload, list):
        raise TypeError(f"unsupported SFT payload shape: {path}")
    return [item for item in payload if isinstance(item, dict)]


def _messages_and_tools(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    messages = sample.get("messages")
    if not isinstance(messages, list):
        messages = []
    messages = [dict(message) for message in messages if isinstance(message, dict)]
    assistant_message = sample.get("assistant_message")
    if isinstance(assistant_message, dict):
        messages.append({**assistant_message, "loss_weight": 1.0})
    tools = sample.get("tools")
    return messages, tools


def _target_message_index(messages: list[dict[str, Any]]) -> int:
    target_indices = _prefix_target_indices(messages)
    if target_indices:
        return target_indices[-1]
    return -1


def _prefix_target_indices(messages: list[dict[str, Any]]) -> list[int]:
    supervised: list[int] = []
    fallback: list[int] = []
    for idx, message in enumerate(messages):
        if _role(message) not in {"assistant", "function_call"}:
            continue
        fallback.append(idx)
        if _is_supervised(message):
            supervised.append(idx)
    if supervised:
        return supervised
    return fallback


def _normalize_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for raw in messages:
        role = _role(raw)
        content = _content_text(raw.get("content"))
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if not content and raw.get("tool_calls"):
            content = ""
        item: dict[str, Any] = {"role": role, "content": content}
        if role == "assistant" and raw.get("tool_calls"):
            item["tool_calls"] = raw.get("tool_calls")
        _append_alternating_message(out, item)

    if system_parts:
        out.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return out


def _append_alternating_message(out: list[dict[str, Any]], item: dict[str, Any]) -> None:
    role = str(item.get("role") or "")
    if role not in {"user", "assistant", "tool", "function_call"}:
        return
    if not out:
        if role in {"assistant", "function_call"}:
            out.append({"role": "user", "content": ""})
        out.append(item)
        return

    last_role = str(out[-1].get("role") or "")
    if _same_turn_parity(last_role, role):
        out[-1] = _merge_messages(out[-1], item)
        return
    out.append(item)


def _same_turn_parity(left: str, right: str) -> bool:
    odd = {"user", "tool"}
    even = {"assistant", "function_call"}
    return (left in odd and right in odd) or (left in even and right in even)


def _merge_messages(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    text_parts = [str(left.get("content") or ""), str(right.get("content") or "")]
    merged["content"] = "\n\n".join(part for part in text_parts if part)
    if right.get("tool_calls"):
        merged["tool_calls"] = right["tool_calls"]
        merged["role"] = right.get("role") or merged.get("role")
    return merged


def _valid_llama_factory_dialog(messages: list[dict[str, Any]]) -> bool:
    body = messages[1:] if messages and messages[0].get("role") == "system" else messages
    if len(body) < 2 or len(body) % 2 != 0:
        return False
    odd = {"user", "tool"}
    even = {"assistant", "function_call"}
    for idx, message in enumerate(body):
        if idx % 2 == 0 and message.get("role") not in odd:
            return False
        if idx % 2 == 1 and message.get("role") not in even:
            return False
    return True


def _role(message: dict[str, Any]) -> str:
    role = str(message.get("role") or message.get("from") or "").strip().lower()
    if role in {"human"}:
        return "user"
    if role in {"gpt"}:
        return "assistant"
    if role in {"observation"}:
        return "tool"
    if role in {"function", "function_call"}:
        return "function_call"
    return role


def _is_supervised(message: dict[str, Any]) -> bool:
    if message.get("loss_mask") is not None:
        return bool(int(message.get("loss_mask") or 0))
    if message.get("loss_weight") is not None:
        try:
            return float(message.get("loss_weight") or 0.0) > 0.0
        except (TypeError, ValueError):
            return False
    return False


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("value") or item.get("content")
                if text is not None:
                    parts.append(str(text))
                elif item.get("type") in {"tool_call", "function_call"}:
                    parts.append(_json_text(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        text = value.get("text") or value.get("value") or value.get("content")
        if text is not None:
            return str(text)
        return _json_text(value)
    return str(value)


def _metadata(sample: dict[str, Any], idx: int) -> dict[str, Any]:
    base = sample.get("metadata") if isinstance(sample.get("metadata"), dict) else {}
    return {
        **base,
        "source_sample_id": sample.get("sample_id") or sample.get("raw_id") or sample.get("trajectory_id") or idx,
        "source_protocol_version": sample.get("protocol_version") or "v1-messages",
        "session_id": sample.get("session_id") or base.get("session_id") or "",
        "scenario": sample.get("scenario") or base.get("scenario") or "",
    }


def _dataset_info(dataset_name: str, file_name: str) -> dict[str, Any]:
    return {
        dataset_name: {
            "file_name": file_name,
            "formatting": "openai",
            "columns": {
                "messages": "messages",
                "tools": "tools",
            },
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "observation_tag": "tool",
                "function_tag": "function_call",
                "system_tag": "system",
            },
        }
    }


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    token_counts = [
        int(
            record.get("metadata", {}).get("token_count_after_truncate")
            or record.get("metadata", {}).get("token_count")
            or 0
        )
        for record in records
        if isinstance(record.get("metadata"), dict)
    ]
    return {
        "record_count": len(records),
        "message_count": sum(len(record.get("messages") or []) for record in records),
        "with_tools": sum(1 for record in records if record.get("tools")),
        "truncated_records": sum(
            1
            for record in records
            if isinstance(record.get("metadata"), dict) and record["metadata"].get("truncated_to_cutoff")
        ),
        "max_token_count": max(token_counts) if token_counts else 0,
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_json_dict(key: str) -> dict[str, Any]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
