# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read/write team-level ``TEAM_MEMORY.md`` under ``team-memory/``."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING, Optional

from openjiuwen.core.common.logging import memory_logger as logger

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

TEAM_MEMORY_FILENAME = "TEAM_MEMORY.md"
TEAM_MEMORY_MAX_READ_LINES = 200


class SharedMemoryManager:
    """管理 ``{team_home}/team-memory/`` 目录下的团队摘要文件。

    - 所有成员只读访问 :meth:`read_team_summary`
    - 提取 agent（leader ``extract_after_round``）通过工具或 :meth:`write_team_summary` 写入

    **写入语义**

    - 若使用 ``sys_operation.fs().write_file``：为 **单次覆盖写**，是否原子取决于底层 FS 抽象实现。
    - 若 fallback 本地文件系统：使用 ``tempfile.NamedTemporaryFile(delete=False)`` + ``os.replace`` **原子替换**。
    - 两种方式语义不完全一致；高并发场景优先依赖单一写入者（提取 agent）。

    **追加**

    :meth:`append_entry` 为读-改-写，**非原子**；并发追加可能丢失更新，适合低频或单 writer。
    """

    def __init__(
        self,
        team_memory_dir: str,
        sys_operation: Optional["SysOperation"] = None,
    ) -> None:
        self._dir = team_memory_dir
        self._sys_operation = sys_operation

    async def ensure_dir(self) -> None:
        """确保 ``team-memory/`` 目录存在。"""
        os.makedirs(self._dir, exist_ok=True)

    async def read_team_summary(self) -> str:
        """读取团队记忆摘要文件。

        Returns:
            文件内容字符串（最多前 ``TEAM_MEMORY_MAX_READ_LINES`` 行），不存在或错误时返回空字符串。
        """
        file_path = os.path.join(self._dir, TEAM_MEMORY_FILENAME)

        if self._sys_operation:
            try:
                result = await self._sys_operation.fs().read_file(file_path)
                if result and hasattr(result, "data") and result.data:
                    content = result.data.content
                    lines = content.split("\n")[:TEAM_MEMORY_MAX_READ_LINES]
                    return "\n".join(lines).strip()
                return ""
            except Exception:
                return ""

        try:
            if not os.path.exists(file_path):
                return ""
            with open(file_path, "r", encoding="utf-8") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= TEAM_MEMORY_MAX_READ_LINES:
                        break
                    lines.append(line.rstrip("\n"))
                return "\n".join(lines).strip()
        except Exception:
            return ""

    async def write_team_summary(self, content: str) -> None:
        """写入团队记忆摘要（覆盖整个文件）。

        优先 ``sys_operation``；失败则回退为本地 **原子** 写入（见类文档）。
        """
        await self.ensure_dir()
        target = os.path.join(self._dir, TEAM_MEMORY_FILENAME)

        if self._sys_operation:
            try:
                await self._sys_operation.fs().write_file(
                    target,
                    content=content,
                    create_if_not_exist=True,
                    prepend_newline=False,
                )
                return
            except Exception as e:
                logger.warning(
                    f"[SharedMemoryManager] sys_operation write failed, fallback: {e}"
                )

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._dir,
                suffix=".tmp",
                prefix="team_memory_",
                delete=False,
            ) as f:
                tmp_path = f.name
                f.write(content)
            os.replace(tmp_path, target)
        except Exception as e:
            logger.error(f"[SharedMemoryManager] Atomic write failed: {e}")
            raise
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def append_entry(self, entry: str) -> None:
        """Append one team memory entry(meth:`write_team_summary`)"""
        existing = await self.read_team_summary()
        if existing:
            new_content = existing + "\n\n---\n\n" + entry
        else:
            new_content = entry
        await self.write_team_summary(new_content)


__all__ = ["SharedMemoryManager"]
