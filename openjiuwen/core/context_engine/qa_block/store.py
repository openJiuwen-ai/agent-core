# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.foundation.llm import (
    BaseMessage,
)
from openjiuwen.core.foundation.llm.schema.message import message_from_dict

L0_SCHEMA_VERSION = 1


class QABlockStore:
    """Filesystem-backed L0 store under {workspace}/context/{session_id}_context/qa_blocks/."""

    def __init__(self, workspace_root: str, session_id: str, sys_operation=None):
        self._workspace_root = workspace_root
        self._session_id = session_id
        self._sys_operation = sys_operation

    @staticmethod
    def l0_relative_path(qa_id: str) -> str:
        return f"qa_blocks/{qa_id}/messages.json"

    def _context_root(self) -> Path:
        return Path(self._workspace_root) / "context" / f"{self._session_id}_context"

    def _absolute_path(self, qa_id: str) -> Path:
        return self._context_root() / self.l0_relative_path(qa_id)

    def _list_l0_qa_ids_local(self) -> list[str]:
        root = self._context_root() / "qa_blocks"
        if not root.is_dir():
            return []
        qa_ids: list[str] = []
        for child in root.iterdir():
            if not child.is_dir() or not child.name.startswith("qa_"):
                continue
            if (child / "messages.json").is_file():
                qa_ids.append(child.name)
        return sorted(qa_ids)

    async def _list_l0_qa_ids_via_sys_operation(self) -> list[str]:
        qa_blocks_path = str(self._context_root() / "qa_blocks")
        result = await self._sys_operation.fs().list_directories(qa_blocks_path, recursive=False)
        if getattr(result, "code", 0) != 0:
            return []
        data = getattr(result, "data", None)
        items = getattr(data, "list_items", None) or []
        qa_ids: list[str] = []
        for item in items:
            name = getattr(item, "name", None) or Path(getattr(item, "path", "")).name
            if not name or not name.startswith("qa_"):
                continue
            msg_path = str(self._context_root() / "qa_blocks" / name / "messages.json")
            read_result = await self._sys_operation.fs().read_file(msg_path)
            if getattr(read_result, "code", 0) == 0:
                qa_ids.append(name)
        return sorted(qa_ids)

    async def list_l0_qa_ids(self) -> list[str]:
        local_ids = self._list_l0_qa_ids_local()
        if local_ids or self._sys_operation is None:
            return local_ids
        return await self._list_l0_qa_ids_via_sys_operation()

    async def write_l0(
        self,
        qa_id: str,
        messages: list[BaseMessage],
        *,
        l0_content_mode: Literal["delta", "compact_summary_tail"] = "delta",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        rel_path = self.l0_relative_path(qa_id)
        payload = {
            "qa_id": qa_id,
            "schema_version": L0_SCHEMA_VERSION,
            "l0_content_mode": l0_content_mode,
            "messages": [message.model_dump(mode="json") for message in messages],
            "metadata": metadata or {},
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)

        if self._sys_operation is not None:
            abs_path = str(self._absolute_path(qa_id))
            result = await self._sys_operation.fs().write_file(abs_path, content)
            if getattr(result, "code", 0) != 0:
                raise OSError(f"write_l0 failed qa_id={qa_id} path={abs_path} code={result.code}")
        else:
            target = self._absolute_path(qa_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        logger.info(
            "[QABlockStore] write_l0 session_id=%s qa_id=%s path=%s message_count=%s mode=%s",
            self._session_id,
            qa_id,
            rel_path,
            len(messages),
            l0_content_mode,
        )
        return rel_path

    async def read_l0(self, qa_id: str) -> list[BaseMessage]:
        rel_path = self.l0_relative_path(qa_id)
        raw_text: str | None = None

        if self._sys_operation is not None:
            abs_path = str(self._absolute_path(qa_id))
            result = await self._sys_operation.fs().read_file(abs_path)
            if getattr(result, "code", 0) == 0 and getattr(result, "data", None):
                raw_text = result.data.content
        else:
            target = self._absolute_path(qa_id)
            if target.is_file():
                raw_text = target.read_text(encoding="utf-8")

        if not raw_text:
            logger.info(
                "[QABlockStore] read_l0 miss session_id=%s qa_id=%s path=%s",
                self._session_id,
                qa_id,
                rel_path,
            )
            return []

        payload = json.loads(raw_text)
        messages_data = payload.get("messages", [])
        messages: list[BaseMessage] = []
        for item in messages_data:
            messages.append(message_from_dict(item))

        logger.info(
            "[QABlockStore] read_l0 hit session_id=%s qa_id=%s path=%s message_count=%s",
            self._session_id,
            qa_id,
            rel_path,
            len(messages),
        )
        return messages
