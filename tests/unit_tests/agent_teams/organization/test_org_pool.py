# coding: utf-8

import pytest

from openjiuwen.agent_teams.organization.pool import (
    clear_process_org_managers,
    get_process_org_manager,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest.fixture(autouse=True)
def _reset_org_manager_registry():
    clear_process_org_managers()
    yield
    clear_process_org_managers()


@pytest.mark.asyncio
async def test_get_process_org_manager_reuses_manager_for_same_db_key():
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=":memory:org-pool-stable-key",
    )
    db_one = TeamDatabase(config)
    db_two = TeamDatabase(config)

    manager_one = get_process_org_manager(
        organization_id="org-stable",
        db=db_one,
        session_id="session-1",
    )
    manager_two = get_process_org_manager(
        organization_id="org-stable",
        db=db_two,
        session_id="session-1",
    )

    assert manager_one is manager_two

    await db_one.close()
    await db_two.close()


@pytest.mark.asyncio
async def test_db_close_clears_process_org_managers_for_db_key():
    config = DatabaseConfig(
        db_type=DatabaseType.SQLITE,
        connection_string=":memory:org-pool-close-key",
    )
    db = TeamDatabase(config)
    manager_before_close = get_process_org_manager(
        organization_id="org-close",
        db=db,
        session_id="session-1",
    )

    await db.close()

    db_reopened = TeamDatabase(config)
    manager_after_close = get_process_org_manager(
        organization_id="org-close",
        db=db_reopened,
        session_id="session-1",
    )

    assert manager_before_close is not manager_after_close

    await db_reopened.close()
