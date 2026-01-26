"""AgentCard Definition

Main classes included:
 - AgentCard: Agent card format definition

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from dataclasses import field
from typing import List

from openjiuwen.core.common.schema import Param
from openjiuwen.core.common.schema.card import BaseCard


class AgentCard(BaseCard):
    """Agent Card Data Class
    """
    input_params: List[Param] = field(default_factory=list)
    output_params: List[Param] = field(default_factory=list)
