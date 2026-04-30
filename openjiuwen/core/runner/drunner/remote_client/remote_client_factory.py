# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import importlib
from importlib.metadata import entry_points

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import RemoteClientConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


REMOTE_CLIENT_ENTRY_POINT_GROUP = "openjiuwen.remote_clients"
REMOTE_CLIENT_A2A_NAME = "A2A"


class RemoteClientFactory:
    @staticmethod
    def create_a2a(config: RemoteClientConfig, card: AgentCard):
        try:
            remote_client_class = RemoteClientFactory._load_entry_point(REMOTE_CLIENT_A2A_NAME)
        except Exception as exc:
            raise build_error(
                StatusCode.REMOTE_AGENT_EXECUTION_ERROR,
                cause=exc,
                agent_id=config.id,
                reason="failed to load A2A remote client plugin",
            ) from exc

        try:
            return remote_client_class(config=config, card=card)
        except Exception as exc:
            raise build_error(
                StatusCode.REMOTE_AGENT_EXECUTION_ERROR,
                cause=exc,
                agent_id=config.id,
                reason="failed to instantiate A2A remote client plugin",
            ) from exc

    @staticmethod
    def _load_entry_point(name: str):
        for ep in entry_points(group=REMOTE_CLIENT_ENTRY_POINT_GROUP):
            if ep.name != name:
                continue
            return ep.load()

        if name == REMOTE_CLIENT_A2A_NAME:
            module = importlib.import_module("openjiuwen.extensions.a2a.a2a_remote_client")
            return getattr(module, "A2ARemoteClient")

        raise build_error(
            StatusCode.REMOTE_AGENT_EXECUTION_ERROR,
            reason=f"remote client plugin '{name}' not found in {REMOTE_CLIENT_ENTRY_POINT_GROUP}",
        )
