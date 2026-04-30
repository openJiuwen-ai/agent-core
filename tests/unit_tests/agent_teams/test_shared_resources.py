from types import SimpleNamespace

from openjiuwen.agent_teams.spawn.shared_resources import (
    cleanup_shared_resources,
    get_shared_db,
)


class _DummyTeamDatabase:
    def __init__(self, config):
        self.config = config


def _config(db_type: str, connection_string: str = "") -> SimpleNamespace:
    return SimpleNamespace(db_type=db_type, connection_string=connection_string)


def test_get_shared_db_reuses_same_non_memory_config(monkeypatch):
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.TeamDatabase", _DummyTeamDatabase)
    cleanup_shared_resources()
    try:
        cfg1 = _config("sqlite", "team_data/team.db")
        cfg2 = _config("sqlite", "team_data/team.db")
        db1 = get_shared_db(cfg1)
        db2 = get_shared_db(cfg2)
        assert db1 is db2
    finally:
        cleanup_shared_resources()


def test_get_shared_db_distinguishes_db_type_with_same_connection(monkeypatch):
    monkeypatch.setattr("openjiuwen.agent_teams.tools.database.TeamDatabase", _DummyTeamDatabase)
    cleanup_shared_resources()
    try:
        sqlite_cfg = _config("sqlite", "shared-conn")
        postgresql_cfg = _config("postgresql", "shared-conn")
        sqlite_db = get_shared_db(sqlite_cfg)
        postgresql_db = get_shared_db(postgresql_cfg)
        assert sqlite_db is not postgresql_db
    finally:
        cleanup_shared_resources()


def test_get_shared_db_memory_uses_singleton():
    cleanup_shared_resources()
    try:
        db1 = get_shared_db(_config("memory"))
        db2 = get_shared_db(_config("memory"))
        assert db1 is db2
    finally:
        cleanup_shared_resources()

