# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team rules variants of the mailbox handlers (F_130).

Imported lazily by ``EventDispatcher`` only when ``TeamAgentSpec.team_rules`` is
active, so a disabled team never loads the router or the classifier stack.
"""

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.group_chat.message_handler import GroupMessageHandler
from openjiuwen.agent_teams.team_rules.router import TeamRulesDeliveryMixin


class TeamRulesMessageHandler(TeamRulesDeliveryMixin, MessageHandler):
    """``MessageHandler`` whose mailbox deliveries go through the team rules router.

    Assembled by ``EventDispatcher`` instead of ``MessageHandler`` when
    ``TeamAgentSpec.team_rules`` is active; otherwise never constructed.
    """


class TeamRulesGroupMessageHandler(TeamRulesDeliveryMixin, GroupMessageHandler):
    """``GroupMessageHandler`` whose deliveries go through the team rules router.

    A group post that @-mentions this member routes like a direct message; the
    classifier sees only the raw post, not the delivered excerpt.
    """
