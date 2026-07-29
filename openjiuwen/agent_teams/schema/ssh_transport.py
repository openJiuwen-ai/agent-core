# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SSH transport schema for external CLI members."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error


class SshTransportConfig(BaseModel):
    """Connection details for one reachable ssh endpoint.

    Attributes:
        host: Hostname or IP of the ssh endpoint.
        port: SSH port.
        username: Login user; ``None`` lets asyncssh pick from config/agent.
        key_file: Path to a private key file.
        password: Password auth value.
        agent: Use the local ssh-agent for keys.
        known_hosts: Optional path to a known_hosts file. ``None`` preserves
            asyncssh's strict default lookup unless ``disable_host_key_check``
            is enabled.
        disable_host_key_check: Skip host-key verification entirely.
        connect_timeout_s: Connect timeout in seconds.
    """

    model_config = ConfigDict(protected_namespaces=())

    host: str
    port: int = 22
    username: str | None = None
    key_file: str | None = None
    password: str | None = None
    agent: bool = False
    known_hosts: str | None = None
    disable_host_key_check: bool = False
    connect_timeout_s: float = 15.0

    @model_validator(mode="after")
    def _require_auth(self) -> "SshTransportConfig":
        """Ensure at least one auth method is configured.

        Raises:
            BaseError: ``AGENT_TEAM_CONFIG_INVALID`` when no auth is set.
        """
        if not self.key_file and not self.password and not self.agent:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="SshTransportConfig requires at least one auth method (key_file / password / agent=True)",
            )
            raise AssertionError  # pragma: no cover - raise_error always raises
        return self


__all__ = ["SshTransportConfig"]
