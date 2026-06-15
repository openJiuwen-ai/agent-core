# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine.qa_block.catalog import maybe_compact_catalog_l1
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.freezer import QABlockFreezer, allocate_qa_id
from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
from openjiuwen.core.context_engine.qa_block.layer import (
    QABlockLayer,
    inject_messages_before_last_user_message,
)
from openjiuwen.core.context_engine.qa_block.messages import (
    extract_qa_native_messages,
    is_other_qa_message,
    load_qa_l0,
    message_qa_id,
    strip_active_buffer,
    tag_messages_with_qa_id,
)
from openjiuwen.core.context_engine.qa_block.reconcile import reconcile_orphan_l0_blocks
from openjiuwen.core.context_engine.qa_block.registry import (
    load_registry,
    merge_registry_block,
    save_registry,
)
from openjiuwen.core.context_engine.qa_block.schema import (
    QA_ID_METADATA_KEY,
    REGISTRY_STATE_KEY,
    SOURCE_QA_ID_METADATA_KEY,
    L0Store,
    QABlockEntry,
    QABlockRegistry,
)
from openjiuwen.core.context_engine.qa_block.selector import (
    QABlockSelector,
    extract_next_user_query,
    fallback_rule_last_n,
    resolve_selector_model,
    resolve_summarizer_model,
)
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.context_engine.qa_block.summarizer import QABlockSummarizer
from openjiuwen.core.context_engine.qa_ref import QARef

__all__ = [
    "HistoryQABuffer",
    "L0Store",
    "QARef",
    "QABlockConfig",
    "QABlockEntry",
    "QABlockFreezer",
    "QABlockLayer",
    "QABlockRegistry",
    "QABlockStore",
    "QABlockSelector",
    "QABlockSummarizer",
    "allocate_qa_id",
    "extract_next_user_query",
    "fallback_rule_last_n",
    "resolve_selector_model",
    "resolve_summarizer_model",
    "QA_ID_METADATA_KEY",
    "REGISTRY_STATE_KEY",
    "SOURCE_QA_ID_METADATA_KEY",
    "extract_qa_native_messages",
    "inject_messages_before_last_user_message",
    "is_other_qa_message",
    "load_qa_l0",
    "load_registry",
    "merge_registry_block",
    "maybe_compact_catalog_l1",
    "message_qa_id",
    "reconcile_orphan_l0_blocks",
    "save_registry",
    "strip_active_buffer",
    "tag_messages_with_qa_id",
]
