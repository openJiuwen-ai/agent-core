# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the design-v5 block-B content file store.

Covers the placeholder scheme: the DB ``content`` column keeps ``#file#``
only, ``SessionFileStore`` derives the file path from ``FileAddress`` fields,
and the DAOs dereference placeholder rows transparently (design-v5
30-block-b, §13 acceptance matrix).
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
    team_session_dir,
)
from openjiuwen.agent_teams.team_workspace.session_file_store import (
    CONTENT_IN_FILE,
    FileAddress,
    SessionFileStore,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.models import (
    _get_message_model,
    _get_task_model,
)
from openjiuwen.agent_teams.schema.task import NewTaskSpec


class TestSessionFileStore:
    """Store-level behavior: placeholder round-trip, derivation, safety."""

    @pytest.fixture
    def store(self, tmp_path):
        # SessionFileStore resolves paths through the shared ``agent_teams.paths``
        # helpers (no paths parameter); isolate FS state by pointing the global
        # home at ``tmp_path`` for the duration of the test.
        configure_openjiuwen_home(str(tmp_path))
        yield SessionFileStore()
        reset_openjiuwen_home()

    def _addr(self, **overrides):
        base = dict(
            team_name="T",
            session_id="S1",
            kind="task",
            object_id="t1",
            to_member=None,
        )
        base.update(overrides)
        return FileAddress(**base)

    def test_put_returns_placeholder_and_writes_derived_path(self, store, tmp_path):
        """put() stores the body at the derived path and returns ``#file#``."""
        addr = self._addr(kind="direct", object_id="m1", to_member="a")
        assert store.put("hello a", addr) == CONTENT_IN_FILE
        target = team_session_dir("T", "S1") / "messages" / "to_a" / "m1.md"
        assert target.read_text(encoding="utf-8") == "hello a"

    def test_get_derives_direct_broadcast_task_paths(self, store, tmp_path):
        """get() derives the path from the address for every kind."""
        store.put("d", self._addr(kind="direct", object_id="d1", to_member="a"))
        store.put("b", self._addr(kind="broadcast", object_id="b1"))
        store.put("t", self._addr(kind="task", object_id="t1"))
        assert store.get(self._addr(kind="direct", object_id="d1", to_member="a")) == "d"
        assert store.get(self._addr(kind="broadcast", object_id="b1")) == "b"
        assert store.get(self._addr(kind="task", object_id="t1")) == "t"
        root = team_session_dir("T", "S1")
        assert (root / "messages" / "to_a" / "d1.md").exists()
        assert (root / "messages" / "broadcast" / "b1.md").exists()
        assert (root / "tasks" / "t1.md").exists()

    def test_overwrite_same_object_id(self, store):
        """update_task semantics: rewriting the same task id overwrites in place."""
        addr = self._addr(kind="task", object_id="t1")
        store.put("v1", addr)
        store.put("v2", addr)
        assert store.get(addr) == "v2"

    def test_get_missing_file_raises(self, store):
        """A placeholder with no backing file must not surface silently."""
        with pytest.raises(FileNotFoundError, match="content file missing"):
            store.get(self._addr(kind="task", object_id="ghost"))

    def test_path_traversal_rejected(self, store):
        """An address that would escape the session root raises ValueError."""
        with pytest.raises(ValueError, match="escapes session root"):
            store.get(self._addr(kind="task", object_id="../../../evil"))

    def test_direct_requires_to_member(self, store):
        with pytest.raises(ValueError, match="requires to_member"):
            store.put("x", self._addr(kind="direct", object_id="d1", to_member=None))

    def test_remove_session_deletes_message_and_task_dirs(self, store, tmp_path):
        store.put("d", self._addr(kind="direct", object_id="d1", to_member="a"))
        store.put("t", self._addr(kind="task", object_id="t1"))
        store.remove_session(team_name="T", session_id="S1")
        root = team_session_dir("T", "S1")
        assert not (root / "messages").exists()
        assert not (root / "tasks").exists()


@pytest_asyncio.fixture
async def db(tmp_path):
    """TeamDatabase with an isolated home so session files stay in tmp."""
    token = set_session_id("fds_session")
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)
        reset_openjiuwen_home()


class TestDaoPlaceholderIntegration:
    """DAO-level behavior: DB keeps ``#file#``, reads dereference it."""

    @pytest.mark.asyncio
    async def test_message_roundtrip_keeps_placeholder(self, db):
        await db.team.create_team(team_name="T1", display_name="T1", leader_member_name="lead")
        assert await db.message.create_message(
            message_id="m1", team_name="T1", from_member_name="lead",
            content="body text", to_member_name="worker-a",
        )
        message_model = _get_message_model()
        async with db.session_local() as session:
            result = await session.execute(select(message_model).where(message_model.message_id == "m1"))
            row = result.scalar_one()
            assert row.content == CONTENT_IN_FILE

        message = await db.message.get_message("m1")
        assert message is not None
        assert message.content == "body text"

    @pytest.mark.asyncio
    async def test_broadcast_roundtrip(self, db):
        await db.team.create_team(team_name="T2", display_name="T2", leader_member_name="lead")
        await db.message.create_message(
            message_id="b1", team_name="T2", from_member_name="lead",
            content="broadcast body", broadcast=True,
        )
        message = await db.message.get_message("b1")
        assert message is not None
        assert message.content == "broadcast body"

    @pytest.mark.asyncio
    async def test_multicast_one_file_per_row(self, db, tmp_path):
        """create_direct_messages writes N files (one per row), all ``#file#``."""
        await db.team.create_team(team_name="T3", display_name="T3", leader_member_name="lead")
        created = await db.message.create_direct_messages(
            team_name="T3", from_member_name="lead",
            content="same content",
            recipients=[("id_a", "a"), ("id_b", "b"), ("id_c", "c")],
        )
        assert created == 3

        message_model = _get_message_model()
        async with db.session_local() as session:
            result = await session.execute(
                select(message_model).where(message_model.team_name == "T3").order_by(message_model.message_id)
            )
            rows = result.scalars().all()
            assert [r.message_id for r in rows] == ["id_a", "id_b", "id_c"]
            assert all(r.content == CONTENT_IN_FILE for r in rows)

        session_root = team_session_dir("T3", "fds_session")
        msgs_dir = session_root / "messages"
        for member, msg_id in (("a", "id_a"), ("b", "id_b"), ("c", "id_c")):
            target = msgs_dir / f"to_{member}" / f"{msg_id}.md"
            assert target.exists(), f"missing {target}"
            assert target.read_text(encoding="utf-8") == "same content"

        inbox = await db.message.get_messages(team_name="T3", to_member_name="b")
        assert [m.message_id for m in inbox] == ["id_b"]
        assert inbox[0].content == "same content"

    @pytest.mark.asyncio
    async def test_failed_insert_leaves_reclaimable_orphan_files(self, db):
        """Review #2: a failed insert leaves spilled files with no DB row.

        ``create_direct_messages`` spills every recipient before the write
        transaction; an ``IntegrityError`` (duplicate id) rolls back the rows
        but the files remain. They must be reclaimed by ``remove_session`` —
        the session-scoped lifecycle keeps them from leaking beyond teardown.
        """
        await db.team.create_team(team_name="T6", display_name="T6", leader_member_name="lead")
        # First batch creates id_a / id_b.
        assert await db.message.create_direct_messages(
            team_name="T6", from_member_name="lead",
            content="dup test", recipients=[("id_a", "a"), ("id_b", "b")],
        ) == 2
        # Second batch re-uses id_a → IntegrityError → whole batch rolls back.
        assert await db.message.create_direct_messages(
            team_name="T6", from_member_name="lead",
            content="dup test", recipients=[("id_a", "a"), ("id_c", "c")],
        ) == 0

        session_root = team_session_dir("T6", "fds_session")
        msgs_dir = session_root / "messages"
        # Orphan file from the failed batch still exists on disk.
        assert (msgs_dir / "to_c" / "id_c.md").exists()
        # And is reclaimed when the session is torn down.
        SessionFileStore().remove_session(team_name="T6", session_id="fds_session")
        assert not (msgs_dir / "to_c").exists()
        assert not (msgs_dir / "to_a").exists()

    @pytest.mark.asyncio
    async def test_templated_message_empty_content_not_dereferenced(self, db):
        """UC-B6: empty content (template in meta) must NOT spill to a file.

        The real invariant: the DB row stays ``""`` (inline, not the
        ``#file#`` placeholder) and no session file is created — so the
        delivery path expands the template from ``meta`` instead of reading
        a body file.
        """
        await db.team.create_team(team_name="T4", display_name="T4", leader_member_name="lead")
        await db.message.create_message(
            message_id="tpl1", team_name="T4", from_member_name="lead",
            content="", to_member_name="worker-a",
            meta={"template": "scheduler_task_start", "refs": {"task": "t_123"}},
        )
        message = await db.message.get_message("tpl1")
        assert message is not None
        assert message.content == ""

        # DB row is inline "" — not the placeholder, so no file read happened.
        message_model = _get_message_model()
        async with db.session_local() as session:
            result = await session.execute(select(message_model).where(message_model.message_id == "tpl1"))
            row = result.scalar_one()
            assert row.content == ""
            assert row.content != CONTENT_IN_FILE

        # No session file exists for this message.
        session_root = team_session_dir("T4", "fds_session")
        assert not (session_root / "messages" / "to_worker-a" / "tpl1.md").exists()

    @pytest.mark.asyncio
    async def test_task_roundtrip_and_update_overwrites_file(self, db):
        """UC-B3/B4/B5: task body round-trips and update_task overwrites in place."""
        await db.team.create_team(team_name="T5", display_name="T5", leader_member_name="lead")
        assert await db.task.create_task(
            task_id="task1", team_name="T5",
            title="login", content="implement login", status="pending",
        )
        task = await db.task.get_task("task1")
        assert task is not None
        assert task.content == "implement login"

        assert await db.task.update_task("task1", content="implement login v2")
        task = await db.task.get_task("task1")
        assert task.content == "implement login v2"

        task_model = _get_task_model()
        async with db.session_local() as session:
            result = await session.execute(select(task_model).where(task_model.task_id == "task1"))
            row = result.scalar_one()
            assert row.content == CONTENT_IN_FILE

    @pytest.mark.asyncio
    async def test_mutate_dependency_graph_spills_task_content(self, db):
        """The graph-mutation path (add_graph / leader create_task tool /
        external client create_task) must spill content too — not just the
        single-task ``create_task`` method.

        Before the fix, spill was wired only into ``TaskDao.create_task``,
        which every production entry point bypasses (they go through
        ``mutate_dependency_graph`` → ``_stage_new_tasks``). This test pins
        the graph path to the same invariant: the DB row keeps ``#file#``,
        the body lives at ``tasks/<task_id>.md``, and ``get_task``
        dereferences it transparently. Empty content stays inline (no file).
        """
        await db.team.create_team(team_name="T7", display_name="T7", leader_member_name="lead")

        result = await db.task.mutate_dependency_graph(
            team_name="T7",
            new_tasks=[
                NewTaskSpec(
                    task_id="g1",
                    title="graph task",
                    content="body via mutate_dependency_graph",
                    initial_status="pending",
                ),
                NewTaskSpec(
                    task_id="g2",
                    title="empty body",
                    content="",
                    initial_status="pending",
                ),
            ],
        )
        assert result.ok is True

        # Non-empty body → placeholder in the DB + a backing file.
        task_model = _get_task_model()
        async with db.session_local() as session:
            row = (
                await session.execute(select(task_model).where(task_model.task_id == "g1"))
            ).scalar_one()
            assert row.content == CONTENT_IN_FILE
            row2 = (
                await session.execute(select(task_model).where(task_model.task_id == "g2"))
            ).scalar_one()
            assert row2.content == ""  # empty stays inline, no placeholder

        session_root = team_session_dir("T7", "fds_session")
        assert (session_root / "tasks" / "g1.md").exists()
        assert (session_root / "tasks" / "g1.md").read_text(encoding="utf-8") == "body via mutate_dependency_graph"
        assert not (session_root / "tasks" / "g2.md").exists()

        # get_task dereferences the placeholder transparently.
        g1 = await db.task.get_task("g1")
        assert g1 is not None
        assert g1.content == "body via mutate_dependency_graph"
