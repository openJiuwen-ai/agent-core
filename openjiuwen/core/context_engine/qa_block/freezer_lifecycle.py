# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_block.freezer import QABlockFreezer

_FREEZERS: weakref.WeakSet[QABlockFreezer] = weakref.WeakSet()


def register_qa_block_freezer(freezer: QABlockFreezer) -> None:
    _FREEZERS.add(freezer)


async def cancel_qa_block_freeze_tasks_for_session(session_id: str | None) -> None:
    if session_id is None:
        for freezer in list(_FREEZERS):
            await freezer.cancel_all_session_tasks()
        return
    for freezer in list(_FREEZERS):
        await freezer.cancel_session_tasks(session_id)


__all__ = [
    "cancel_qa_block_freeze_tasks_for_session",
    "register_qa_block_freezer",
]
