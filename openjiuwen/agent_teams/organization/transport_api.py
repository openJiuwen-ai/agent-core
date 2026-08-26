# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-facing TransportAPI for organization member messaging."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.organization.events import OrgEvent, OrgEventMessage, OrgTopic

logger = logging.getLogger(__name__)


def create_message_id() -> str:
    return f"org-msg-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class NegotiationRequest:
    from_team_id: str
    to_team_id: str


@dataclass(frozen=True)
class NegotiationResult:
    transport_type: str  # "inprocess" | "pyzmq"


@dataclass(frozen=True)
class TransportResult:
    success: bool
    message_id: str | None = None
    reason: str | None = None


class TransportAPI:
    """一个已加入 Organization 的 Team Leader 持有一个 TransportAPI。"""

    def __init__(
        self,
        *,
        organization_id: str,
        session_id: str,
        from_team_id: str,
        messager: Messager,
    ) -> None:
        self.organization_id = organization_id
        self.session_id = session_id
        self.from_team_id = from_team_id
        self.messager = messager

    async def negotiate(self, request: NegotiationRequest) -> NegotiationResult:
        """判断目标 Team 与当前 Team 的通信方式。

        当前版本：
        - 同进程：返回 inprocess；
        - 跨进程：返回 pyzmq。

        判断依据可以先放在 Team Runtime 的配置或 Team 注册信息中。
        现阶段 Organization 仅支持同进程协作，因此默认返回 inprocess。
        """

        if self._is_same_process(request.from_team_id, request.to_team_id):
            return NegotiationResult(transport_type="inprocess")
        return NegotiationResult(transport_type="pyzmq")

    async def deliver(
        self,
        content: str,
        to_team_id: str,
        *,
        message_id: str | None = None,
    ) -> TransportResult:
        """向目标 Team Leader 投递消息通知。

        先 ``negotiate`` 判断同进程 / 跨进程；当前实现只落地 inprocess（Messager）。
        正文应由调用方先写入 Store；inbox 事件只携带 ``message_id``，不含 content。
        """

        if not content:
            return TransportResult(success=False, reason="content is required")
        if not to_team_id:
            return TransportResult(success=False, reason="to_team_id is required")
        if self.messager is None:
            return TransportResult(success=False, reason="messager is not configured")

        negotiation = await self.negotiate(
            NegotiationRequest(
                from_team_id=self.from_team_id,
                to_team_id=to_team_id,
            )
        )
        message_id = message_id or create_message_id()

        if negotiation.transport_type == "inprocess":
            await self.messager.publish(
                OrgTopic.TEAM_INBOX.build(
                    self.session_id,
                    self.organization_id,
                    to_team_id,
                ),
                OrgEventMessage(
                    event_type=OrgEvent.LEADER_MESSAGE,
                    payload={
                        "message_id": message_id,
                        "organization_id": self.organization_id,
                        "from_team_id": self.from_team_id,
                        "to_team_id": to_team_id,
                    },
                    sender_id=self.from_team_id,
                ),
            )
            return TransportResult(success=True, message_id=message_id)

        if negotiation.transport_type == "pyzmq":
            return await self._deliver_by_pyzmq(
                message_id=message_id,
                content=content,
                to_team_id=to_team_id,
            )

        return TransportResult(
            success=False,
            message_id=message_id,
            reason=f"unsupported transport: {negotiation.transport_type}",
        )

    async def shutdown(self) -> None:
        """预留接口，暂不实现。"""

        return None

    def _is_same_process(self, from_team_id: str, to_team_id: str) -> bool:
        """当前 Organization 仅支持同进程成员，一律视为 inprocess。

        后续可改为查询 Team Runtime 注册信息 / 配置中的进程归属。
        """

        _ = (from_team_id, to_team_id)
        return True

    async def _deliver_by_pyzmq(
        self,
        *,
        message_id: str,
        content: str,
        to_team_id: str,
    ) -> TransportResult:
        """后续实现：将同一份 payload 发到目标 Team 所在节点。"""

        _ = (content, to_team_id)
        logger.warning("pyzmq transport is not implemented yet (message_id=%s)", message_id)
        return TransportResult(
            success=False,
            message_id=message_id,
            reason="pyzmq transport is not implemented yet",
        )


__all__ = [
    "NegotiationRequest",
    "NegotiationResult",
    "TransportAPI",
    "TransportResult",
    "create_message_id",
]
