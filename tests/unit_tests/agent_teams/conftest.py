# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fixtures for the agent_teams unit tests."""

import pytest

from openjiuwen.agent_teams.spawn.shared_resources import cleanup_shared_resources


@pytest.fixture(autouse=True)
def _clean_shared_team_resources():
    """Reset process-global team singletons around every test.

    ``get_shared_db`` caches one ``TeamDatabase`` per db config for the whole
    worker, and a sqlite ``:memory:`` instance keeps every earlier test's
    dynamic session tables alive for the worker lifetime. A test whose spawn
    path creates session tables before the runtime binds a session (e.g. the
    manifest flush in ``test_runner_team_runtime``) resolves the target
    session id from the ambient context, so it can collide with tables a
    previous test left under a fixed session id and fail with
    ``CREATE TABLE ... already exists``. Reset the singletons so every test
    starts from an empty shared database.
    """
    cleanup_shared_resources()
    yield
    cleanup_shared_resources()
