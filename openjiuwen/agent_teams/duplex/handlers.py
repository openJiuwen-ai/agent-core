# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Duplex variants of the mailbox handlers (F_117).

Imported lazily by ``EventDispatcher`` only when ``TeamAgentSpec.duplex`` is
active, so a disabled team never loads the router or the classifier stack.
"""

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.duplex.router import DuplexDeliveryMixin
from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler


class DuplexMessageHandler(DuplexDeliveryMixin, MessageHandler):
    """``MessageHandler`` whose mailbox deliveries go through the duplex router.

    Assembled by ``EventDispatcher`` instead of ``MessageHandler`` when
    ``TeamAgentSpec.duplex`` is active; otherwise never constructed.
    """


class DuplexGroupMessageHandler(DuplexDeliveryMixin, GroupMessageHandler):
    """``GroupMessageHandler`` whose deliveries go through the duplex router.

    A group post that @-mentions this member routes like a direct message; the
    classifier sees only the raw post, not the delivered excerpt.
    """
