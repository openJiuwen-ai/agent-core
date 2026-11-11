# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session settings remain authoritative across independently connected members."""

from pathlib import Path

import pytest

from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase


@pytest.mark.asyncio
async def test_settings_persist_cas_and_isolate_sessions(tmp_path: Path) -> None:
    config = DatabaseConfig(connection_string=str(tmp_path / "team.db"))
    host = TeamDatabase(config)
    remote = TeamDatabase(config)
    try:
        await host.initialize()
        await remote.initialize()
        await host.session_settings.initialize("one", {"enabled": False})
        await remote.session_settings.initialize("one", {"enabled": True})
        assert (await remote.session_settings.get("one"))["settings"] == {"enabled": False}
        assert await host.session_settings.replace("one", {"enabled": True}, expected_version=0) == 1
        assert await remote.session_settings.get("one") == {"settings": {"enabled": True}, "version": 1}
        assert await remote.session_settings.get("two") is None
        with pytest.raises(ValueError, match="refresh"):
            await remote.session_settings.replace("one", {"enabled": False}, expected_version=0)
    finally:
        await remote.close()
        await host.close()
    recovered = TeamDatabase(config)
    try:
        await recovered.initialize()
        assert (await recovered.session_settings.get("one"))["version"] == 1
    finally:
        await recovered.close()
