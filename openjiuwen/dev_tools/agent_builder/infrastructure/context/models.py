#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from datetime import datetime
from typing import Dict


@dataclass
class DialogueMessage:
    """dialogue message model

    Attributes:
        content (str): The content of the message.
        role (str): The role of the message sender (e.g., 'user', 'assistant').
        timestamp (datetime): The timestamp when the message was created.
    """
    content: str
    role: str
    timestamp: datetime

    def to_dict(self) -> Dict:
        """Convert DialogueMessage to dictionary format.

        Returns:
            dict: A dictionary representation of the DialogueMessage.
        """
        return {
            'content': self.content,
            'role': self.role
        }