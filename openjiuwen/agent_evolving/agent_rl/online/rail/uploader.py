# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Asynchronous rail-v1 upload batch uploader with bounded queue and WAL."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class TrajectoryUploader:
    """Upload rail-v1 batches to the online-RL gateway."""

    def __init__(
        self,
        gateway_endpoint: str,
        *,
        capacity: int = 256,
        max_retries: int = 5,
        backoff_base_sec: float = 0.2,
        wal_dir: str | Path = "records/rail_v1_wal",
        api_key: str = "",
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 30.0,
    ) -> None:
        self.gateway_endpoint = gateway_endpoint.rstrip("/")
        self.capacity = max(1, int(capacity))
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_sec = max(0.0, float(backoff_base_sec))
        self.wal_dir = Path(wal_dir)
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._queue: deque[dict[str, Any]] = deque()
        self._condition = asyncio.Condition()
        self._worker: Optional[asyncio.Task] = None
        self._closed = False
        self.queue_drop_total = 0
        self.http_4xx_total = 0

    async def enqueue(self, batch: Any) -> None:
        payload = batch.to_dict() if hasattr(batch, "to_dict") else dict(batch)
        async with self._condition:
            if len(self._queue) >= self.capacity:
                self._queue.popleft()
                self.queue_drop_total += 1
                logger.warning("[TrajectoryUploader] queue full; dropped oldest batch")
            self._queue.append(payload)
            self._ensure_worker_locked()
            self._condition.notify()

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        await self.replay_wal()
        while True:
            async with self._condition:
                while not self._queue and not self._closed:
                    await self._condition.wait()
                if not self._queue and self._closed:
                    return
                payload = self._queue.popleft()
            await self._send_or_wal(payload)

    async def replay_wal(self) -> None:
        for path in sorted(self.wal_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                ok = await self._post_with_retries(payload)
                if ok:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("[TrajectoryUploader] WAL replay failed file=%s err=%s", path, exc)

    async def _send_or_wal(self, payload: dict[str, Any]) -> None:
        ok = await self._post_with_retries(payload)
        if not ok:
            self._write_wal(payload)

    async def _post_with_retries(self, payload: dict[str, Any]) -> bool:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.post(
                    f"{self.gateway_endpoint}/v1/gateway/upload/batch",
                    json=payload,
                    headers=self._headers(),
                )
                if 400 <= resp.status_code < 500:
                    self.http_4xx_total += 1
                    logger.warning(
                        "[TrajectoryUploader] drop 4xx batch status=%d body=%s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return True
                resp.raise_for_status()
                return True
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base_sec * (2 ** attempt))
        logger.warning("[TrajectoryUploader] upload failed; writing WAL err=%s", last_exc)
        return False

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _write_wal(self, payload: dict[str, Any]) -> None:
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}.json"
        path = self.wal_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    async def shutdown(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()
            worker = self._worker
        if worker is not None:
            await worker
        leftovers: list[dict[str, Any]] = []
        async with self._condition:
            while self._queue:
                leftovers.append(self._queue.popleft())
        for payload in leftovers:
            self._write_wal(payload)
        if self._owned_client:
            await self._client.aclose()
