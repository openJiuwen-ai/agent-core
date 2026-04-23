# coding: utf-8

import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

import torch
from verl.utils.dataset.rl_dataset import RLHFDataset, collate_fn

from openjiuwen.core.common.logging import logger


class AgentDataset(RLHFDataset):
    """RLHFDataset variant tailored for agent-RL training.

    - Disables ``filter_overlong_prompts`` (agent mode handles truncation itself)
    - Adds ``index`` and ``fake_ids`` fields to each row (required by Verl DataProto)
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.filter_overlong_prompts = False

    def __getitem__(self, item: int) -> dict:
        row_dict: dict = self.dataframe[item]
        index = row_dict.get("extra_info", {}).get("index", 0)
        row_dict["index"] = index
        row_dict["fake_ids"] = torch.ones(1, dtype=torch.int)
        return row_dict


@dataclass
class DatasetBundle:
    train_dataset: Any
    val_dataset: Any
    collate_fn: Callable
    train_sampler: Optional[Any] = None
    cleanup_fn: Optional[Callable[[], None]] = None


def _build_agent_datasets(
    *,
    data_cfg,
    tokenizer,
    processor,
    train_files,
    val_files,
) -> Tuple[Any, Any]:
    common = {
        "tokenizer": tokenizer,
        "processor": processor,
        "config": data_cfg,
    }
    train_ds = AgentDataset(data_files=train_files, **common)
    val_ds = AgentDataset(data_files=val_files, **common)
    return train_ds, val_ds


def _set_train_val_files(data_cfg, *, train_files, val_files) -> None:
    # Keep the side-effect behavior (some callers rely on config.data.*_files),
    # but centralize it so online/offline share the same update pathway.
    data_cfg.train_files = train_files
    data_cfg.val_files = val_files


def create_offline_datasets(config, tokenizer, processor) -> DatasetBundle:
    from verl.trainer.main_ppo import create_rl_sampler

    data_cfg = config.data
    train_ds, val_ds = _build_agent_datasets(
        data_cfg=data_cfg,
        tokenizer=tokenizer,
        processor=processor,
        train_files=data_cfg.train_files,
        val_files=data_cfg.val_files,
    )
    sampler = create_rl_sampler(data_cfg, train_ds)
    return DatasetBundle(train_dataset=train_ds, val_dataset=val_ds, collate_fn=collate_fn, train_sampler=sampler)


def create_online_datasets(config, tokenizer, processor) -> DatasetBundle:
    tmp_path = _create_dummy_parquet()
    data_cfg = config.data
    _set_train_val_files(data_cfg, train_files=tmp_path, val_files=tmp_path)

    train_ds, val_ds = _build_agent_datasets(
        data_cfg=data_cfg,
        tokenizer=tokenizer,
        processor=processor,
        train_files=data_cfg.train_files,
        val_files=data_cfg.val_files,
    )

    def _cleanup_tmp() -> None:
        if not tmp_path:
            return
        if "online_ppo_dummy_" not in str(tmp_path):
            return
        try:
            os.remove(tmp_path)
        except OSError:
            return

    return DatasetBundle(train_dataset=train_ds, val_dataset=val_ds, collate_fn=collate_fn, cleanup_fn=_cleanup_tmp)


def _create_dummy_parquet() -> str:
    try:
        import pandas as pd
    except ImportError as e:
        raise RuntimeError("pandas is required: pip install pandas") from e

    dummy_msg = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    df = pd.DataFrame({"messages": [dummy_msg] * 16})
    tmp = tempfile.NamedTemporaryFile(
        suffix=".parquet", delete=False, prefix="online_ppo_dummy_"
    )
    df.to_parquet(tmp.name)
    tmp.close()
    return tmp.name
