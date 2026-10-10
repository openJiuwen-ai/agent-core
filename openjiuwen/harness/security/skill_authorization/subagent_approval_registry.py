# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""子 Agent 委托审批注册表：保持子 Agent 协程存活，路由答案到 pending Future。"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

# 过期事件只是 UI 终态通知，不能反过来阻塞子 Agent 的超时/取消收口。
_EXPIRY_NOTIFY_TIMEOUT_SECONDS = 1.0

# 已取消 sender 的收尾上限。sender 走父会话 write_stream（websocket 背压时
# 可能长时间不 yield），被 cancel 后正常应立即返回；超时即放弃收尾——此时
# 卡片已被应答或请求已终态，sender 的结果没有消费价值，但等待它会持着
# session 交互锁把同会话后续审批全部卡死（2026-09-28 事故机制 A）。
_SENDER_DRAIN_TIMEOUT_SECONDS = 5.0


class SubagentApprovalCancelled(RuntimeError):
    """Pending delegated approval was withdrawn by lifecycle management."""


class SubagentApprovalCapacityError(RuntimeError):
    """A scope/session already reached its delegated approval capacity."""


class SubagentApprovalTimeout(asyncio.TimeoutError):
    """A displayed approval card reached its response TTL."""


class SubagentApprovalKind(str, Enum):
    """Delegated request types; answers may never cross these namespaces."""

    SKILL_LOAD = "skill_load"
    TOOL_PERMISSION = "tool_permission"


@dataclass(frozen=True)
class SubagentApprovalRequest:
    approval_id: str
    kind: SubagentApprovalKind
    session_id: str
    agent_scope_id: str
    tool_call_id: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)


@dataclass
class _PendingApproval:
    request: SubagentApprovalRequest
    future: asyncio.Future[Any]
    loop: asyncio.AbstractEventLoop


ApprovalSender = Callable[[SubagentApprovalRequest], Awaitable[None]]
ApprovalExpirySender = Callable[[SubagentApprovalRequest, str], Awaitable[None]]


class SubagentApprovalRegistry:
    """Process-local, one-shot approval router with per-session serialization."""

    _instance: ClassVar["SubagentApprovalRegistry | None"] = None

    def __init__(
        self,
        *,
        max_pending_per_session: int = 5,
        max_queued_per_scope: int = 8,
        turn_wait_timeout: float | None = None,
    ) -> None:
        if max_pending_per_session <= 0:
            raise ValueError("max_pending_per_session 必须大于 0")
        if max_queued_per_scope <= 0:
            raise ValueError("max_queued_per_scope 必须大于 0")
        if turn_wait_timeout is not None and turn_wait_timeout <= 0:
            raise ValueError("turn_wait_timeout 必须大于 0 或为 None")
        self._guard = threading.RLock()
        self._pending: dict[str, _PendingApproval] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._max_pending_per_session = max_pending_per_session
        # 同 scope 排队深度（持卡者之外的额外注册数）。默认 8 是给调大了
        # session 上限的部署的 scope 维度护栏；默认 max_pending_per_session=5
        # 下同 scope 有效深度本就受 session 配额约束。
        self._max_queued_per_scope = max_queued_per_scope
        # 排队等锁（等前一个审批 settle）的上限；None = 按调用方 timeout×2。
        # 单个前驱正常最坏占用 ≈ TTL + 发卡 + 有界收尾，2×TTL 覆盖并留裕量。
        self._turn_wait_timeout = turn_wait_timeout

    @classmethod
    def get_instance(cls) -> "SubagentApprovalRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def peek_instance(cls) -> "SubagentApprovalRegistry | None":
        """返回已创建的单例；未创建时返回 ``None``（不触发初始化）。"""
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        instance = cls._instance
        cls._instance = None
        if instance is not None:
            instance.clear_all()

    @property
    def pending_count(self) -> int:
        with self._guard:
            return len(self._pending)

    def pending_requests(self) -> tuple[SubagentApprovalRequest, ...]:
        with self._guard:
            return tuple(item.request for item in self._pending.values())

    async def request(
        self,
        *,
        kind: SubagentApprovalKind,
        session_id: str,
        agent_scope_id: str,
        tool_call_id: str,
        payload: dict[str, Any],
        sender: ApprovalSender,
        expiry_sender: ApprovalExpirySender | None = None,
        timeout: float,
    ) -> Any:
        sid = str(session_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        call_id = str(tool_call_id or "").strip()
        if not sid or not scope or not call_id:
            raise ValueError("session_id、agent_scope_id、tool_call_id 均不能为空")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        approval_id = f"subagent_{kind.value}_{uuid.uuid4().hex}"
        request = SubagentApprovalRequest(
            approval_id=approval_id,
            kind=kind,
            session_id=sid,
            agent_scope_id=scope,
            tool_call_id=call_id,
            payload=dict(payload),
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._guard:
            # 同 scope 并发请求排队而非硬拒：排队者就是 session_lock 的 FIFO
            # 等待者，_pending 仍是唯一路由表（resolve/cancel 对排队者同样生效，
            # Step-3 的三路竞速保证排队者被 settle 时立即返回、被 cancel 冲刷时
            # 不再傻等锁）。仅当同 scope 注册数（持卡者 + 排队者）超过
            # 1 + max_queued_per_scope 时才拒绝（2026-09-28 事故：子代理单轮
            # N 个工具调用并发请求审批，硬拒 ×5 直接丢卡）。
            scope_registered = sum(
                1
                for item in self._pending.values()
                if item.request.session_id == sid and item.request.agent_scope_id == scope
            )
            if scope_registered >= 1 + self._max_queued_per_scope:
                raise SubagentApprovalCapacityError(
                    f"scope approval queue limit reached: "
                    f"session={sid!r} scope={scope!r} limit={self._max_queued_per_scope}",
                )
            session_count = sum(item.request.session_id == sid for item in self._pending.values())
            if session_count >= self._max_pending_per_session:
                raise SubagentApprovalCapacityError(
                    f"session pending approval limit reached: session={sid!r} limit={self._max_pending_per_session}",
                )
            self._pending[approval_id] = _PendingApproval(request, future, loop)
            session_lock = self._session_locks.setdefault(sid, asyncio.Lock())
        sender_task: asyncio.Task[None] | None = None
        # 锁获取三路竞速：等锁 / future 先完成（排队期间被 resolve 或被取消
        # 冲刷，直接返回不再傻等锁）/ turn 超时（前驱卡死的安全阀——本 MR 已
        # 把前驱各等待全部有界化，正常排队不应触到这里）。锁的释放统一收敛
        # 到外层 finally：排队者被 settle 时若 acquire 恰好已完成，锁也必须
        # 释放，否则下一个排队者永久等待。
        turn_timeout = self._turn_wait_timeout if self._turn_wait_timeout is not None else timeout * 2.0
        acquire_task = asyncio.ensure_future(session_lock.acquire())
        lock_held = False
        try:
            done, _ = await asyncio.wait(
                {acquire_task, future},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=turn_timeout,
            )
            if acquire_task in done and not acquire_task.cancelled() and acquire_task.exception() is None:
                lock_held = True
            if future in done:
                # 排队期间已 settle：直接返回，锁由外层 finally 释放。
                return await future
            if not done:
                # turn 超时：卡片从未展示，无 UI 终态需要通知。
                self._discard_pending(approval_id, future)
                raise SubagentApprovalTimeout("delegated approval turn wait timed out")
            if future.done():
                return await future
            # sender 耗时（前端展示）不计入响应 TTL，但生命周期取消必须能
            # 立即打断一个尚未返回的 sender，不能等发送链路自己结束。
            sender_task = asyncio.create_task(sender(request))
            done, _pending = await asyncio.wait(
                {sender_task, future},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                sender_completed = sender_task in done
                sender_succeeded = False
                if sender_completed and not sender_task.cancelled():
                    sender_succeeded = sender_task.exception() is None
                if not sender_task.done():
                    sender_task.cancel()
                await self._drain_sender_task(sender_task, request)
                try:
                    return await future
                except SubagentApprovalCancelled:
                    if sender_succeeded:
                        await self._notify_expiry(
                            expiry_sender,
                            request,
                            "cancelled",
                        )
                    raise
            await sender_task
            # future 已完成时 wait_for 会立即返回结果或重抛其异常，
            # 由下方 except 分支统一收口，无需单独的 done() 快路径。
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError as exc:
                # 先让迟到点击变为无效，再通知前端移除卡片；通知期间仍持有
                # 当前 session 交互锁，避免下一张卡先于过期终态展示。
                self._discard_pending(approval_id, future)
                await self._notify_expiry(expiry_sender, request, "timeout")
                raise SubagentApprovalTimeout(
                    "delegated approval response timed out",
                ) from exc
            except SubagentApprovalCancelled:
                await self._notify_expiry(expiry_sender, request, "cancelled")
                raise
            except asyncio.CancelledError:
                self._discard_pending(approval_id, future)
                await self._notify_expiry(expiry_sender, request, "cancelled")
                raise
        finally:
            if not acquire_task.done():
                acquire_task.cancel()
                await asyncio.gather(acquire_task, return_exceptions=True)
                if not acquire_task.cancelled() and acquire_task.exception() is None:
                    # cancel 与完成赛跑时可能恰好已拿到锁。
                    lock_held = True
            if lock_held:
                session_lock.release()
            if sender_task is not None and not sender_task.done():
                sender_task.cancel()
                await self._drain_sender_task(sender_task, request)
            with self._guard:
                current = self._pending.get(approval_id)
                if current is not None and current.future is future:
                    self._pending.pop(approval_id, None)
                # 该 session 已无 pending 时回收锁，避免长跑进程泄漏。
                if not any(item.request.session_id == sid for item in self._pending.values()):
                    self._session_locks.pop(sid, None)
            if not future.done():
                future.cancel()

    def resolve(
        self,
        *,
        session_id: str,
        approval_id: str,
        kind: SubagentApprovalKind,
        answer: Any,
        agent_scope_id: str | None = None,
    ) -> bool:
        sid = str(session_id or "").strip()
        rid = str(approval_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        # approval_id is a unique uuid key; agent_scope_id is an optional
        # cross-check. OfficeClaw / relay often omit it on resume chat.send.
        if not sid or not rid:
            return False
        with self._guard:
            pending = self._pending.get(rid)
            if pending is None:
                return False
            request = pending.request
            if request.session_id != sid or request.kind != kind:
                return False
            if scope and request.agent_scope_id != scope:
                logger.warning(
                    "[skill_authorization] subagent.approval_scope_mismatch "
                    "session=%s expected_scope=%s got_scope=%s kind=%s",
                    sid,
                    request.agent_scope_id,
                    scope,
                    kind.value,
                )
                return False
            self._pending.pop(rid, None)
        # call_soon_threadsafe 只收位置参数；带 kwargs 的完成回调用 partial 包装。
        pending.loop.call_soon_threadsafe(
            functools.partial(
                self._complete_future,
                pending.future,
                answer,
                None,
                request=request,
                via="resolve",
            )
        )
        logger.info(
            "[skill_authorization] subagent.approval_resolved kind=%s session=%s scope=%s",
            kind.value,
            sid,
            request.agent_scope_id,
        )
        return True

    def cancel_scope(self, session_id: str, agent_scope_id: str) -> int:
        sid = str(session_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        return self._cancel_matching(
            lambda item: item.request.session_id == sid and item.request.agent_scope_id == scope,
        )

    def cancel_session(self, session_id: str) -> int:
        sid = str(session_id or "").strip()
        return self._cancel_matching(lambda item: item.request.session_id == sid)

    def clear_all(self) -> int:
        return self._cancel_matching(lambda _item: True)

    def _cancel_matching(self, predicate: Callable[[_PendingApproval], bool]) -> int:
        with self._guard:
            matches = [rid for rid, item in self._pending.items() if predicate(item)]
            pending = [self._pending.pop(rid) for rid in matches]
            # 不在这里移除 session lock：被取消的 request 可能仍持有该锁并需要
            # 先发送终态事件。request.finally 会在协程退出后安全回收。
        for item in pending:
            item.loop.call_soon_threadsafe(
                functools.partial(
                    self._complete_future,
                    item.future,
                    None,
                    SubagentApprovalCancelled("delegated approval was cancelled"),
                    request=item.request,
                    via="cancel",
                )
            )
        return len(pending)

    def _discard_pending(
        self,
        approval_id: str,
        future: asyncio.Future[Any],
    ) -> None:
        """Remove a timed-out request before emitting its terminal UI event."""
        with self._guard:
            current = self._pending.get(approval_id)
            if current is not None and current.future is future:
                self._pending.pop(approval_id, None)

    async def _drain_sender_task(
        self,
        sender_task: asyncio.Task[None],
        request: SubagentApprovalRequest,
    ) -> None:
        """收尾已取消的 sender；超时即放弃并留下可观测告警。

        sender 只负责把卡片送到前端；请求已终态（应答/超时/取消）后它的
        结果没有任何消费价值，但无界等待它返回会持有 session 交互锁，把
        同会话的全部后续审批卡死。注意不能用 ``wait_for``：它超时后会在
        内部等待取消真正完成，sender 吞掉 CancelledError 时它自己就挂死；
        ``asyncio.wait`` 带超时只返回、不取消被等待者。放弃收尾后挂一个
        done_callback 消费异常，避免 "exception was never retrieved" 噪音。
        """
        done, pending = await asyncio.wait({sender_task}, timeout=_SENDER_DRAIN_TIMEOUT_SECONDS)
        if not pending:
            return
        logger.warning(
            "[skill_authorization] subagent.approval_sender_drain_abandoned "
            "approval_id=%s scope=%s kind=%s timeout=%.1fs",
            request.approval_id,
            request.agent_scope_id,
            request.kind.value,
            _SENDER_DRAIN_TIMEOUT_SECONDS,
        )

        def _consume(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                logger.warning(
                    "[skill_authorization] subagent.approval_sender_final_failure approval_id=%s",
                    request.approval_id,
                    exc_info=task.exception(),
                )

        sender_task.add_done_callback(_consume)

    @staticmethod
    async def _notify_expiry(
        expiry_sender: ApprovalExpirySender | None,
        request: SubagentApprovalRequest,
        reason: str,
    ) -> None:
        if expiry_sender is None:
            return
        try:
            await asyncio.wait_for(
                expiry_sender(request, reason),
                timeout=_EXPIRY_NOTIFY_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — UI终态通知失败不能覆盖原超时/取消语义
            logger.warning(
                "[skill_authorization] subagent.approval_expiry_notify_failed kind=%s session=%s scope=%s reason=%s",
                request.kind.value,
                request.session_id,
                request.agent_scope_id,
                reason,
                exc_info=True,
            )

    @staticmethod
    def _complete_future(
        future: asyncio.Future[Any],
        result: Any,
        error: BaseException | None,
        *,
        request: SubagentApprovalRequest | None = None,
        via: str = "",
    ) -> None:
        """只在 Future 所属事件循环完成终态，避免跨线程直接变更。

        Future 已终态（等待协程被取消/超时先收口）时，答案会凭空蒸发且
        调用方（resolve/cancel）仍返回成功——这正是审批结果丢失事故的
        静默形态。这里至少留下可归因的 warning（含 approval_id/scope/kind），
        供线上统计丢失率并驱动后续是否需要答案缓存。
        """
        if future.done():
            if request is not None:
                logger.warning(
                    "[skill_authorization] subagent.approval_completion_dropped "
                    "future_already_done via=%s approval_id=%s scope=%s kind=%s",
                    via or "<unknown>",
                    request.approval_id,
                    request.agent_scope_id,
                    request.kind.value,
                )
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)


def get_subagent_approval_registry() -> SubagentApprovalRegistry:
    return SubagentApprovalRegistry.get_instance()
