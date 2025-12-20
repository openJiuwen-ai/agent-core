#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Iterator, Sequence, AsyncIterator

from openjiuwen.runnables import RunnableConfig
from openjiuwen.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    BaseCheckpointSaver
)

from jiuwen.core.runtime.runtime import BaseRuntime


class GraphCheckpointer(BaseCheckpointSaver[str]):
    def __init__(self, runtime: BaseRuntime, saver: BaseCheckpointSaver[str]):
        super().__init__()
        self.ctx = runtime
        self._inner = saver

    def reset(self, runtime: BaseRuntime):
        self.ctx = runtime

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._inner.get_tuple(config=config)

    def list(
            self,
            config: RunnableConfig | None,
            *,
            filter: dict[str, Any] | None = None,
            before: RunnableConfig | None = None,
            limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        return self._inner.list(config=config, filter=filter, before=before, limit=limit)

    def put(
            self,
            config: RunnableConfig,
            checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._inner.put(config=config, checkpoint=checkpoint, metadata=metadata, new_versions=new_versions)

    def put_writes(
            self,
            config: RunnableConfig,
            writes: Sequence[tuple[str, Any]],
            task_id: str,
            task_path: str = "",
    ) -> None:
        return self._inner.put_writes(config=config, writes=writes, task_id=task_id, task_path=task_path)

    def delete_thread(
            self,
            thread_id: str,
    ) -> None:
        self._inner.delete_thread(thread_id=thread_id)
        for key in list(self.state_blobs.keys()):
            if key[0] == thread_id:
                del self.state_blobs[key]

        for key in list(self.state_updates_blobs.keys()):
            if key[0] == thread_id:
                del self.state_updates_blobs[key]

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._inner.aget_tuple(config=config)

    async def alist(
            self,
            config: RunnableConfig | None,
            *,
            filter: dict[str, Any] | None = None,
            before: RunnableConfig | None = None,
            limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        return self._inner.alist(config=config, filter=filter, before=before, limit=limit)

    async def aput(
            self,
            config: RunnableConfig,
            checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await self._inner.aput(config=config, checkpoint=checkpoint, metadata=metadata,
                                      new_versions=new_versions)

    async def aput_writes(
            self,
            config: RunnableConfig,
            writes: Sequence[tuple[str, Any]],
            task_id: str,
            task_path: str = "",
    ) -> None:
        return await self._inner.aput_writes(config=config, writes=writes, task_id=task_id, task_path=task_path)

    async def adelete_thread(
            self,
            thread_id: str,
    ) -> None:
        return self.delete_thread(thread_id=thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self._inner.get_next_version(current=current, channel=channel)
