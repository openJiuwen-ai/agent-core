# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Duplex router: classify a mailbox row's scene, plan, and execute the action.

The router sits at the coordination mailbox drain (``MessageHandler``'s
``_deliver_message`` hook, swapped in at assembly time). It never blocks the
serial coordination loop on the classifier:

- rows whose action is fixed (user messages, system rows, group posts without
  an @, idle direct messages) are executed at once and the drain marks them
  read as before;
- rows that need a judgment are taken over: the scheduler judges them in the
  background, the router executes the actions, marks the rows read itself and
  re-drains the mailbox. A taken-over row is filtered out of later drains.
- broadcast read state is one per-member watermark that only advances, so
  while a broadcast is held, newer broadcasts stay unread (the drain runs
  oldest first and skips them) until the held one settles.

Actions map onto the SDK's existing delivery primitives (``deliver_input``)
plus the native safe interrupt. A failed delivery or interrupt leaves its rows
unread for the next periodic poll (no immediate re-drain) and never falls back
to a steer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.duplex.classifier import ChoiceClassifier, build_classifier
from openjiuwen.agent_teams.duplex.config import DuplexConfig
from openjiuwen.agent_teams.duplex.policy import (
    BROADCAST_WINDOW_SECONDS,
    Decision,
    DeliveryAction,
    MessageSource,
    final_action,
    plan,
    resolve,
)
from openjiuwen.agent_teams.duplex.scheduler import DuplexScheduler, Judged, RoutedMessage, work_context
from openjiuwen.agent_teams.group_chat.handler import group_metadata
from openjiuwen.agent_teams.harness.protocol import INTERRUPT_UNAVAILABLE, InterruptibleHarness
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.core.common.logging import team_logger

_TEXT_PROTOCOLS = frozenset({"", "plain", "text"})
_DEPARTING = frozenset({MemberStatus.SHUTDOWN.value, MemberStatus.SHUTDOWN_REQUESTED.value})


class DuplexRouter:
    """Routes one member's mailbox rows through the duplex policy.

    Args:
        host: The owning TeamAgent (``deliver_input`` / ``is_agent_running`` /
            ``has_pending_interrupt`` / ``harness``).
        infra: Per-process infra (message manager and team backend).
        member_name: The target member.
        config: The active duplex configuration.
        redrain: Re-queues a mailbox sweep after taken-over rows settled.
        classifier_factory: Builds the classifier once, on first use (tests inject).
    """

    def __init__(
        self,
        host: Any,
        infra: Any,
        member_name: str,
        config: DuplexConfig,
        *,
        redrain: Callable[[], Awaitable[None]],
        classifier_factory: Callable[[], ChoiceClassifier] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._host = host
        self._infra = infra
        self._member = member_name
        self._redrain = redrain
        self._owned: dict[str, Any] = {}
        extra = {"clock": clock} if clock is not None else {}
        self.scheduler = DuplexScheduler(
            classifier_factory=classifier_factory or (lambda: build_classifier(config)),
            context=lambda: work_context(self._host.harness, member_name, running=not self._idle()),
            execute=self._execute_batch,
            **extra,
        )

    def owns(self, message_id: str) -> bool:
        """Whether the router took this row over (it is being judged)."""
        return message_id in self._owned

    def blocks(self, row: Any) -> bool:
        """Whether ``row`` is a broadcast newer than a held one.

        Marking it read would advance the broadcast watermark past the held
        row and lose it if its delivery fails.
        """
        if not getattr(row, "broadcast", False):
            return False
        return any(
            getattr(held, "broadcast", False) and held.timestamp <= row.timestamp and held.message_id != row.message_id
            for held in self._owned.values()
        )

    async def submit(self, item: RoutedMessage) -> bool:
        """Route one row. True: handled now, the caller marks it read.

        False: the row must not be marked read by the caller — either the
        router took it over (it settles the read state itself) or the delivery
        failed (the row stays unread and is redelivered).
        """
        idle = self._idle()
        planned = plan(item.source, idle=idle)
        if planned.questions and item.source is MessageSource.BROADCAST:
            if not self.scheduler.allow_broadcast(item.sender, item.message_id):
                decision = Decision(
                    DeliveryAction.VISIBLE_ONLY,
                    f"scene=broadcast rule=rate_limited window={BROADCAST_WINDOW_SECONDS:.0f}s",
                )
                return await self._run(item, decision.action, decision.reason)
        if not planned.questions:
            decision = resolve(item.source, planned, None, idle=idle)
            return await self._run(item, decision.action, decision.reason)
        self._owned[item.message_id] = item.row
        self.scheduler.enqueue(Judged(item=item, plan=planned, idle=idle))
        return False

    async def aclose(self) -> None:
        """Stop judging; taken-over rows stay unread for the next drain."""
        await self.scheduler.aclose()
        self._owned.clear()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _idle(self) -> bool:
        return not self._host.is_agent_running()

    async def _execute_batch(self, batch: list[Judged]) -> None:
        """Execute one judged batch in arrival order; re-read state first.

        Each row's final action is decided once. Non-interrupt rows run first
        and are marked read and released at once, so a long safe interrupt
        does not hold them; the interrupt runs last. A delivered broadcast
        newer than a held interrupting broadcast stays held until the
        interrupt settles (the watermark must not pass the held row).
        Re-drains only when nothing failed; failed rows wait for the periodic poll.
        """
        failed = False
        try:
            if await self._must_release():
                return
            actions = [(judged, self._final(judged)) for judged in batch]
            interrupting = [judged for judged, action in actions if action is DeliveryAction.INTERRUPT]
            held = [j.item.row for j in interrupting if getattr(j.item.row, "broadcast", False)]
            released: list[Judged] = []
            behind: list[Judged] = []
            for judged, action in actions:
                if action is DeliveryAction.INTERRUPT:
                    continue
                if not await self._run(judged.item, action, judged.decision.reason):
                    failed = True
                elif _behind(judged.item.row, held):
                    behind.append(judged)
                else:
                    released.append(judged)
            if released:
                await self._infra.message_manager.mark_messages_read([j.item.row for j in released], self._member)
                for judged in released:
                    self._owned.pop(judged.item.message_id, None)
            if not interrupting:
                return
            if released and not failed:
                await self._redrain()  # rows blocked by the released ones need not wait for the interrupt
            if await self._interrupt(interrupting):
                settled = [j.item.row for j in interrupting + behind]
                await self._infra.message_manager.mark_messages_read(settled, self._member)
            else:
                failed = True  # the delivered rows behind it stay unread rather than pass it
        finally:
            for judged in batch:
                self._owned.pop(judged.item.message_id, None)
            if not failed:
                await self._redrain()

    def _final(self, judged: Judged) -> DeliveryAction:
        return final_action(judged.decision.action, idle_now=self._idle())

    async def _must_release(self) -> bool:
        """Hand rows back to the drain gates (HITL pending, member departing)."""
        if self._host.has_pending_interrupt():
            return True
        backend = self._infra.team_backend
        status = await backend.get_member_status(self._member) if backend is not None else None
        return status in _DEPARTING and not self._host.is_agent_running()

    async def _run(self, item: RoutedMessage, action: DeliveryAction, reason: str) -> bool:
        """Execute one non-interrupt action; False when the delivery failed."""
        team_logger.info(
            "duplex decision member={} message_id={} sender={} action={} {}",
            self._member,
            item.message_id,
            item.sender,
            action.value,
            reason,
        )
        if action is DeliveryAction.VISIBLE_ONLY:
            return True
        use_steer = {
            DeliveryAction.DELIVER_NOW: True,
            DeliveryAction.STEER: True,
            DeliveryAction.APPEND: False,
            DeliveryAction.PASSTHROUGH: item.use_steer,
        }[action]
        try:
            await self._host.deliver_input(item.text, use_steer=use_steer)
        except Exception:  # noqa: BLE001 - row stays unread and is redelivered
            team_logger.error("duplex: delivery failed message_id={}", item.message_id, exc_info=True)
            return False
        return True

    async def _interrupt(self, items: list[Judged]) -> bool:
        """One safe interrupt carrying every interrupting message, in order.

        Returns False when the interrupt failed: the rows stay unread and are
        redelivered. No steer fallback — it would override an explicit
        pause / stop, or deliver the replan twice.
        """
        content = t("duplex.replan", messages="\n\n".join(j.item.text for j in items))
        ids = [j.item.message_id for j in items]
        runtime = self._host.harness
        try:
            result = (
                await runtime.interrupt(content) if isinstance(runtime, InterruptibleHarness) else INTERRUPT_UNAVAILABLE
            )
            if result == INTERRUPT_UNAVAILABLE:
                await self._host.deliver_input("\n\n".join(j.item.text for j in items), use_steer=True)
        except Exception:  # noqa: BLE001 - rows stay unread and are redelivered
            team_logger.warning("duplex: interrupt failed member={} message_ids={}", self._member, ids, exc_info=True)
            return False
        team_logger.info("duplex interrupt member={} message_ids={} result={}", self._member, ids, result)
        return True


def _behind(row: Any, held: list[Any]) -> bool:
    """Whether ``row`` is a broadcast at or after a held broadcast."""
    return getattr(row, "broadcast", False) and any(h.timestamp <= row.timestamp for h in held)


class DuplexDeliveryMixin:
    """Mailbox-handler mixin that routes the drain's deliveries through a router.

    Mixed in front of ``MessageHandler`` / ``GroupMessageHandler`` by the
    dispatcher when ``TeamAgentSpec.duplex`` is active (assembly-time swap,
    no mode check inside handler methods).
    """

    def __init__(self, *args: Any, router: DuplexRouter, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._router = router

    async def _deliver_message(self, msg: Any, text: str, *, expanded: Any, use_steer: bool) -> bool:
        if self._router.blocks(msg):
            return False  # stays unread until the held broadcast settles
        source = await self._message_source(msg, expanded)
        body = str(msg.content or "") if source is MessageSource.GROUP_MENTION else expanded.body
        item = RoutedMessage(
            row=msg,
            text=text,
            source=source,
            sender=str(msg.from_member_name),
            body=body,
            use_steer=use_steer,
        )
        return await self._router.submit(item)

    async def _read_all_unread(self, member_name: str) -> list[Any]:
        rows = await super()._read_all_unread(member_name)
        rows = [row for row in rows if not self._router.owns(str(row.message_id)) and not self._router.blocks(row)]
        # Oldest first: a broadcast taken over must be decided before any newer
        # broadcast is marked read past it (watermark read state).
        return sorted(rows, key=lambda row: row.timestamp)

    async def _message_source(self, msg: Any, expanded: Any) -> MessageSource:
        """Scene of a row, from SDK signals only."""
        if expanded.is_template or (msg.protocol or "") not in _TEXT_PROTOCOLS:
            return MessageSource.SYSTEM
        sender = str(msg.from_member_name)
        if await self._is_user_side(sender):
            return MessageSource.USER
        meta = group_metadata(msg) if msg.broadcast else {}
        if meta:
            mentioned = self._blueprint.member_name in (meta.get("mentions") or ())
            return MessageSource.GROUP_MENTION if mentioned else MessageSource.GROUP_PLAIN
        if not msg.broadcast:
            return MessageSource.DIRECT
        team_spec = self._blueprint.team_spec
        if team_spec is not None and sender == team_spec.leader_member_name:
            return MessageSource.LEADER_BROADCAST
        return MessageSource.BROADCAST

    async def _is_user_side(self, sender: str) -> bool:
        """The user, a HITT controller's avatar, or a passive human member."""
        if sender == USER_PSEUDO_MEMBER_NAME:
            return True
        backend = self._infra.team_backend
        if backend is None:
            return False
        return await backend.is_human_agent(sender) or await backend.is_passive_human(sender)
