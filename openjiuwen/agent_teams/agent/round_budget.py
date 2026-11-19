# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Round-scoped shared iteration budget ledger.

One ledger per team round (one user question -> terminal frame). Held by the
leader's coordination kernel; member/leader rails tick it once per task-loop
iteration via the ``budget_tick`` hook on ``TaskCompletionRail``.

Design contract:
- Dimension: ``Dict[member_name, rounds]``; leader counts under its own
  member name (no special case).
- Lifecycle bound to the round: opened at leader invoke start (zero-round
  opening counts), discarded at round end; never persisted.
- Member restarts keep accumulating under the same name (no deduction).
- In-process members only; external CLI / subprocess members do not tick
  (narrowing per design).
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from openjiuwen.core.common.logging import team_logger


class RoundBudgetLedger:
    """Per-round shared iteration budget for the whole team.

    Attributes:
        limit: Total round budget for this round (None = uncapped, ticks
            are still recorded for observability but never exhaust).
        session_id: Owning session, for log correlation.
        leader_name: Leader member name; its ticks may consume the
            reserved share below.
        leader_reserve: Tail of ``limit`` that members cannot push the
            *total* usage into. Members stop when ``used`` (leader +
            members) reaches ``limit - leader_reserve``; the leader may
            use the full ``limit``. This is a total-usage threshold, not a
            member-only bucket: the leader's own earlier ticks count
            toward it (they reduce the member headroom), and at the
            moment members stop the leader still has ``leader_reserve``
            remaining. Defaults to 0 (legacy shared pool).
    """

    def __init__(
            self,
            limit: Optional[int],
            session_id: str = "",
            *,
            leader_name: str = "",
            leader_reserve: int = 0,
            on_exhausted: Optional[Callable[[], None]] = None,
    ) -> None:
        self.limit = limit
        self.session_id = session_id
        self.leader_name = leader_name
        if limit is None:
            self.leader_reserve = 0
            self._member_limit: Optional[int] = None
        else:
            self.leader_reserve = max(
                0, min(int(leader_reserve), int(limit)),
            )
            self._member_limit = limit - self.leader_reserve
        self._counts: Dict[str, int] = {}
        self._exhausted_fired = False
        self._closed = False
        # 耗尽上升沿回调（leader kernel 用来唤醒空闲 leader 进入收尾）。
        # 与日志的 one-shot 闸共用 `_exhausted_fired`，每个账本至多一次。
        # 触发点 = 成员池到顶（leader 预留仍可用）。
        self._on_exhausted = on_exhausted

    def _cap_for(self, member_name: str) -> int:
        """Consumption cap for one producer (leader gets the reserve)."""
        if member_name and member_name == self.leader_name:
            return int(self.limit)
        return int(self._member_limit)

    def tick(self, member_name: str) -> Optional[int]:
        """Record one task-loop round for ``member_name``.

        Args:
            member_name: Producing member (leader included).

        Returns:
            Remaining budget under this producer's cap after the tick, or
            None when uncapped/closed. Members see the member cap; the
            leader sees the full limit (reserve included).
        """
        if self._closed:
            return None
        self._counts[member_name] = self._counts.get(member_name, 0) + 1
        if self.limit is None:
            return None
        remaining = self._cap_for(member_name) - sum(self._counts.values())
        if remaining <= 0 and not self._exhausted_fired:
            # One-shot gate: concurrent ticks must not multiply-fire.
            self._exhausted_fired = True
            total_used = sum(self._counts.values())
            leader_used = self._counts.get(self.leader_name, 0)
            team_logger.warning(
                "[RoundBudget] exhausted: scope=total used=%d/%d "
                "reserve=%d/%d member_used=%d leader_used=%d members=%s",
                total_used,
                self._cap_for(member_name),
                self.leader_reserve,
                self.limit,
                total_used - leader_used,
                leader_used,
                self._counts,
            )
            callback = self._on_exhausted
            if callback is not None:
                try:
                    callback()
                except Exception:  # noqa: BLE001 - 唤醒失败不得影响计数
                    team_logger.warning(
                        "[RoundBudget] exhaustion callback failed",
                        exc_info=True,
                    )
        return remaining

    def remaining(self, member_name: str = "") -> Optional[int]:
        """Non-mutating remaining budget probe (no tick).

        Settle-time consumers (wrap-up detection, stale sweeps) must be
        able to read the ledger without counting an iteration. ``tick``
        stays the only mutating entry — one call per successful inner
        ReAct iteration.

        Args:
            member_name: Producer whose cap to evaluate (leader gets the
                reserve; empty/others get the member cap).

        Returns:
            Remaining budget under that cap, or None when uncapped/closed.
        """
        if self._closed or self.limit is None:
            return None
        return self._cap_for(member_name) - sum(self._counts.values())

    @property
    def exhausted(self) -> bool:
        """Whether the total budget has been exhausted this round."""
        return self._exhausted_fired

    @property
    def used(self) -> int:
        """Total rounds consumed across all members this round."""
        return sum(self._counts.values())

    def detail(self) -> Dict[str, int]:
        """Return a copy of the per-member breakdown."""
        return dict(self._counts)

    def close(self, reason: str) -> None:
        """Discard the ledger at round end (idempotent).

        Args:
            reason: Close reason for the log line (``round_finalize`` /
                ``stream_end_fallback`` etc.).
        """
        if self._closed:
            return
        self._closed = True
        team_logger.info(
            "[RoundBudget] ledger close: session=%s used=%d/%s reason=%s",
            self.session_id,
            self.used,
            self.limit if self.limit is not None else "-",
            reason,
        )
