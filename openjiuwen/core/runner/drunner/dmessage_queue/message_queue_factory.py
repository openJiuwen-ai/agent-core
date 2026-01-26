# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import importlib

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_fake import FakeMQ
from openjiuwen.core.runner.message_queue_base import MessageQueueBase
from openjiuwen.core.runner.runner_config import MessageQueueConfig, MessageQueueType


class MessageQueueFactory:
    @staticmethod
    def create(config: MessageQueueConfig) -> MessageQueueBase:
        mq_type = config.type.lower()

        if mq_type == MessageQueueType.FAKE:
            return FakeMQ()

        elif mq_type == MessageQueueType.PULSAR:
            try:
                module = importlib.import_module("openjiuwen.extensions.runner.pulsar_mq.message_queue_pulsar")
                mq_cls = getattr(module, "MessageQueuePulsar")
                return mq_cls(config.pulsar_config)
            except ImportError as e:
                logger.exception(f"[MessageQueueFactory] Failed to import Pulsar MQ: {e}")
                raise JiuWenBaseException(StatusCode.MESSAGE_QUEUE_INIT_ERROR.code,
                                          StatusCode.MESSAGE_QUEUE_INIT_ERROR.errmsg.format(f"{mq_type} import error"))
            except Exception as e:
                raise JiuWenBaseException(StatusCode.MESSAGE_QUEUE_INIT_ERROR.code,
                                          StatusCode.MESSAGE_QUEUE_INIT_ERROR.errmsg.format(f"{mq_type} import error"))
        else:
            raise JiuWenBaseException(StatusCode.MESSAGE_QUEUE_INIT_ERROR.code,
                                      StatusCode.MESSAGE_QUEUE_INIT_ERROR.errmsg.format(f"Unknown MQ type: {mq_type}"))
