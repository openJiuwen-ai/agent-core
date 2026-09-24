"""Unit tests for the IM learning pipeline (OJ-02..OJ-05).

Covers, per the migration plan §7:
- corpus persist round-trip (normalize + persist + read-back);
- learning scope tagging (whitelist / since_ms);
- fetch paging (page_token style, msg_id+direction style, next_cursor=None);
- newest_seen watermark steady state, truncation/resume;
- stage run lease state machine (single active, takeover, renewal);
- FTS consumer drain + query-time eligible filtering (D8);
- end-to-end: Fake source -> fetch -> persist -> FTS search.
"""

from __future__ import annotations

import sqlite3

import pytest

from openjiuwen.harness.personal_context.im.backfill import (
    read_backfill_state,
    record_backfill_result,
)
from openjiuwen.harness.personal_context.im.fetch_depth import fetch_page_range
from openjiuwen.harness.personal_context.im.fetch_provider import ImLearningFetchProvider
from openjiuwen.harness.personal_context.im.fts_consumer import FtsConsumer
from openjiuwen.harness.personal_context.im.fts_index import FtsIndexRepository
from openjiuwen.harness.personal_context.im.learning_scope import (
    build_target_keys,
    compute_eligible_map,
    compute_learning_eligible,
)
from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningTarget,
    ImMessageBatch,
)
from openjiuwen.harness.personal_context.im.normalize import (
    derive_direction,
    normalize_batch,
)
from openjiuwen.harness.personal_context.im.persist import persist_batch
from openjiuwen.harness.personal_context.im.schema import init_im_schema
from openjiuwen.harness.personal_context.im.stage_runs import (
    begin_stage_run,
    finish_stage_run,
    latest_stage_run,
    list_active_runs,
    renew_lease,
    source_key_for,
)

BASE_MS = 1_700_000_000_000


def make_target(channel_id: str = "welink", external_id: str = "g1") -> ImLearningTarget:
    return ImLearningTarget(channel_id=channel_id, kind="group", external_id=external_id, title="项目群")


def make_message(
    msg_id: str,
    *,
    sent_at: int,
    channel_id: str = "welink",
    external_id: str = "g1",
    text: str = "消息内容",
    is_self: bool = False,
) -> ImLearningMessage:
    return ImLearningMessage(
        channel_id=channel_id,
        msg_id=msg_id,
        conversation_external_id=external_id,
        content_text=text,
        sent_at=sent_at,
        is_self=is_self,
    )


@pytest.fixture()
def db_conn() -> sqlite3.Connection:
    # check_same_thread=False matches the production connection factory
    # (open_im_context_db): the provider wraps sync sqlite work in
    # asyncio.to_thread, which hops threads.
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    init_im_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Fake sources covering the platform paging styles (plan §7)
# ---------------------------------------------------------------------------


class PageTokenSource:
    """Feishu/DingTalk style: pages by ``extra["page_token"]`` toward older."""

    def __init__(self, pages: list[list[ImLearningMessage]]) -> None:
        self._pages = list(pages)
        self.seen_cursors: list[dict | None] = []

    async def fetch_messages(self, target, cursor=None):
        self.seen_cursors.append(dict(cursor.extra) if cursor else None)
        if not self._pages:
            return ImMessageBatch(messages=(), next_cursor=None)
        page = self._pages.pop(0)
        has_more = bool(self._pages)
        next_cursor = (
            ImLearningCursor(message_id=None, query_direction=None, count=50, extra={"page_token": "tok"})
            if has_more
            else None
        )
        return ImMessageBatch(messages=tuple(page), next_cursor=next_cursor)


class MsgIdSource:
    """WeLink style: pages by ``message_id`` + ``query_direction``."""

    def __init__(self, pages: list[list[ImLearningMessage]]) -> None:
        self._pages = list(pages)
        self.seen_cursors: list[ImLearningCursor | None] = []

    async def fetch_messages(self, target, cursor=None):
        self.seen_cursors.append(cursor)
        if not self._pages:
            return ImMessageBatch(messages=(), next_cursor=None)
        page = self._pages.pop(0)
        if page:
            oldest = page[-1]
            next_cursor = ImLearningCursor(message_id=oldest.msg_id, query_direction=0, count=50)
        else:
            next_cursor = None
        return ImMessageBatch(messages=tuple(page), next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# OJ-03: normalize + persist
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_derives_direction_from_is_self(self) -> None:
        assert derive_direction(True) == "outbound"
        assert derive_direction(False) == "inbound"
        assert derive_direction(None) == "inbound"

    def test_normalizes_target_and_messages(self) -> None:
        target = make_target()
        batch = normalize_batch(
            target=target,
            messages=[
                make_message("m1", sent_at=BASE_MS, is_self=True),
                make_message("m2", sent_at=BASE_MS + 1),
            ],
            fetched_at_ms=BASE_MS + 10,
            learning_eligible_map={"m1": 1, "m2": 0},
        )
        assert batch.channel_id == "welink"
        assert len(batch.conversations) == 1
        conv = batch.conversations[0]
        assert (conv.channel_id, conv.external_id, conv.target_kind, conv.title) == (
            "welink",
            "g1",
            "group",
            "项目群",
        )
        assert [m.direction for m in batch.messages] == ["outbound", "inbound"]
        assert [m.learning_eligible for m in batch.messages] == [1, 0]

    def test_dedupes_by_channel_and_msg_id(self) -> None:
        batch = normalize_batch(
            target=make_target(),
            messages=[
                make_message("m1", sent_at=BASE_MS),
                make_message("m1", sent_at=BASE_MS),
            ],
            fetched_at_ms=BASE_MS,
        )
        assert len(batch.messages) == 1


class TestPersist:
    def test_round_trip_and_outbox(self, db_conn) -> None:
        target = make_target()
        batch = normalize_batch(
            target=target,
            messages=[
                make_message("m1", sent_at=BASE_MS, text="排期延期"),
                make_message("m2", sent_at=BASE_MS + 1, text="收到"),
            ],
            fetched_at_ms=BASE_MS + 10,
            learning_eligible_map={"m1": 1, "m2": 1},
        )
        result = persist_batch(db_conn, batch, ensure_schema=False)
        assert result.messages_upserted == 2
        assert result.changelog_entries_appended == 2
        rows = db_conn.execute("SELECT id, direction FROM im_messages ORDER BY sent_at").fetchall()
        assert len(rows) == 2
        # upsert idempotent: re-persist same batch appends changelog but no dupes
        result2 = persist_batch(db_conn, batch, ensure_schema=False)
        assert result2.messages_upserted == 2
        count = db_conn.execute("SELECT COUNT(*) FROM im_messages").fetchone()[0]
        assert count == 2

    def test_learning_eligible_only_decreases(self, db_conn) -> None:
        target = make_target()
        first = normalize_batch(
            target=target,
            messages=[make_message("m1", sent_at=BASE_MS, text="a")],
            fetched_at_ms=BASE_MS,
            learning_eligible_map={"m1": 0},
        )
        persist_batch(db_conn, first, ensure_schema=False)
        second = normalize_batch(
            target=target,
            messages=[make_message("m1", sent_at=BASE_MS, text="a")],
            fetched_at_ms=BASE_MS + 5,
            learning_eligible_map={"m1": 1},
        )
        persist_batch(db_conn, second, ensure_schema=False)
        row = db_conn.execute("SELECT learning_eligible FROM im_messages WHERE external_id='m1'").fetchone()
        assert row[0] == 0  # 只降不升


# ---------------------------------------------------------------------------
# OJ-02: learning scope
# ---------------------------------------------------------------------------


class TestLearningScope:
    def test_whitelist_and_since_rules(self) -> None:
        target = make_target()
        keys = build_target_keys([target])
        in_window = make_message("m1", sent_at=BASE_MS)
        out_of_window = make_message("m2", sent_at=BASE_MS - 1)
        assert compute_learning_eligible(target=target, message=in_window, whitelist_keys=keys, since_ms=BASE_MS) == 1
        assert (
            compute_learning_eligible(target=target, message=out_of_window, whitelist_keys=keys, since_ms=BASE_MS) == 0
        )
        other = make_target(channel_id="feishu")
        assert compute_learning_eligible(target=other, message=in_window, whitelist_keys=keys, since_ms=None) == 0

    def test_eligible_map(self) -> None:
        target = make_target()
        keys = build_target_keys([target])
        mapping = compute_eligible_map(
            target=target,
            messages=[make_message("m1", sent_at=1), make_message("m2", sent_at=2)],
            whitelist_keys=keys,
            since_ms=2,
        )
        assert mapping == {"m1": 0, "m2": 1}


# ---------------------------------------------------------------------------
# OJ-02: fetch paging
# ---------------------------------------------------------------------------


class TestFetchPaging:
    @pytest.mark.asyncio
    async def test_page_token_paging_style(self) -> None:
        pages = [
            [make_message("m3", sent_at=3), make_message("m2", sent_at=2)],
            [make_message("m1", sent_at=1)],
        ]
        source = PageTokenSource(pages)
        result = await fetch_page_range(source, make_target(), max_pages=5, count=50)
        assert result.pages == 2
        assert [m.msg_id for m in result.messages] == ["m3", "m2", "m1"]
        assert result.truncated is False
        assert source.seen_cursors[0] is None
        assert source.seen_cursors[1] == {"page_token": "tok"}

    @pytest.mark.asyncio
    async def test_msg_id_direction_paging_style(self) -> None:
        pages = [
            [make_message("m3", sent_at=3), make_message("m2", sent_at=2)],
            [make_message("m1", sent_at=1)],
        ]
        source = MsgIdSource(pages)
        result = await fetch_page_range(source, make_target(), max_pages=5, count=50)
        assert [m.msg_id for m in result.messages] == ["m3", "m2", "m1"]
        assert source.seen_cursors[1] is not None
        assert source.seen_cursors[1].message_id == "m2"

    @pytest.mark.asyncio
    async def test_max_pages_marks_truncated(self) -> None:
        class EndlessSource:
            def __init__(self) -> None:
                self.n = 0

            async def fetch_messages(self, target, cursor=None):
                self.n += 1
                return ImMessageBatch(
                    messages=(make_message(f"m{self.n}", sent_at=self.n),),
                    next_cursor=ImLearningCursor(message_id=f"m{self.n}", query_direction=0, count=50),
                )

        result = await fetch_page_range(EndlessSource(), make_target(), max_pages=3)
        assert result.pages == 3
        assert result.truncated is True
        assert len(result.messages) == 3

    @pytest.mark.asyncio
    async def test_start_cursor_anchors_first_call(self) -> None:
        pages = [[make_message("m5", sent_at=5)]]
        source = MsgIdSource(pages)
        start = ImLearningCursor(message_id="m6", query_direction=0, count=50)
        result = await fetch_page_range(source, make_target(), max_pages=2, start_cursor=start)
        # the loop passes the cursor values through (identity may be rebuilt)
        first = source.seen_cursors[0]
        assert first is not None
        assert (first.message_id, first.query_direction) == ("m6", 0)
        assert [m.msg_id for m in result.messages] == ["m5"]


# ---------------------------------------------------------------------------
# OJ-04: stage runs (lease state machine)
# ---------------------------------------------------------------------------


class TestStageRuns:
    def test_single_active_and_finish(self, db_conn) -> None:
        key = source_key_for("welink", "group", "g1")
        run1 = begin_stage_run(db_conn, stage="fetch", source_key=key, now_ms=1000)
        assert run1 is not None
        assert begin_stage_run(db_conn, stage="fetch", source_key=key, now_ms=2000) is None
        finish_stage_run(db_conn, run_id=run1, succeeded=True, now_ms=3000)
        run2 = begin_stage_run(db_conn, stage="fetch", source_key=key, now_ms=4000)
        assert run2 is not None
        assert run2 != run1

    def test_expired_lease_takeover(self, db_conn) -> None:
        key = source_key_for("welink", "group", "g1")
        run1 = begin_stage_run(db_conn, stage="fetch", source_key=key, lease_ttl_ms=1000, now_ms=1000)
        assert run1 is not None
        run2 = begin_stage_run(db_conn, stage="fetch", source_key=key, now_ms=5000)
        assert run2 is not None
        latest = latest_stage_run(db_conn, stage="fetch", source_key=key)
        assert latest is not None
        assert latest.id == run2
        row = db_conn.execute("SELECT status, last_error FROM im_stage_runs WHERE id = ?", (run1,)).fetchone()
        assert row[0] == "failed"
        assert row[1] == "lease expired"

    def test_renew_lease_extends(self, db_conn) -> None:
        run = begin_stage_run(db_conn, stage="index", source_key="-", lease_ttl_ms=1000, now_ms=1000)
        assert run is not None
        assert renew_lease(db_conn, run_id=run, lease_ttl_ms=10_000, now_ms=1500) is True
        assert begin_stage_run(db_conn, stage="index", source_key="-", now_ms=8000) is None

    def test_list_active_runs(self, db_conn) -> None:
        key = source_key_for("welink", "group", "g1")
        begin_stage_run(db_conn, stage="fetch", source_key=key, now_ms=1000)
        begin_stage_run(db_conn, stage="index", source_key="-", now_ms=1000)
        active = list_active_runs(db_conn)
        assert {(run.stage, run.source_key) for run in active} == {
            ("fetch", key),
            ("index", "-"),
        }


# ---------------------------------------------------------------------------
# OJ-04: backfill watermark
# ---------------------------------------------------------------------------


class TestBackfill:
    def test_watermark_monotonic(self, db_conn) -> None:
        target = make_target()
        record_backfill_result(
            db_conn,
            target=target,
            status="complete",
            truncated=False,
            newest_seen_msg_id="m9",
            newest_seen_sent_at=900,
            now_ms=1000,
        )
        record_backfill_result(
            db_conn,
            target=target,
            status="complete",
            truncated=False,
            newest_seen_msg_id="m5",
            newest_seen_sent_at=500,
            now_ms=2000,
        )
        state = read_backfill_state(db_conn, target=target)
        assert state is not None
        assert state.newest_seen_msg_id == "m9"
        assert state.newest_seen_sent_at == 900

    def test_never_downgrades_complete(self, db_conn) -> None:
        target = make_target()
        record_backfill_result(db_conn, target=target, status="complete", truncated=False, now_ms=1000)
        result = record_backfill_result(db_conn, target=target, status="truncated", truncated=True, now_ms=2000)
        assert result == "complete"
        state = read_backfill_state(db_conn, target=target)
        assert state is not None
        assert state.status == "complete"


# ---------------------------------------------------------------------------
# OJ-05: FTS pipeline
# ---------------------------------------------------------------------------


class TestFtsPipeline:
    def test_drain_once_indexes_and_searches(self, db_conn) -> None:
        target = make_target()
        batch = normalize_batch(
            target=target,
            messages=[
                make_message("m1", sent_at=BASE_MS, text="项目排期延期了"),
                make_message("m2", sent_at=BASE_MS + 1, text="收到，明天同步"),
            ],
            fetched_at_ms=BASE_MS + 10,
            learning_eligible_map={"m1": 1, "m2": 1},
        )
        persist_batch(db_conn, batch, ensure_schema=False)
        consumer = FtsConsumer(db_conn)
        processed = consumer.drain_once(now_ms=BASE_MS + 20)
        assert processed == 2
        fts = FtsIndexRepository(db_conn)
        hits = fts.search("排期")
        assert len(hits) >= 1
        # query-time eligible filtering (D8)
        db_conn.execute("UPDATE im_messages SET learning_eligible = 0 WHERE external_id = 'm2'")
        db_conn.commit()
        eligible_hits = fts.search("收到", learning_eligible_only=True)
        assert len(eligible_hits) == 0

    def test_drain_failure_does_not_advance_cursor(self, db_conn) -> None:
        target = make_target()
        batch = normalize_batch(
            target=target,
            messages=[make_message("m1", sent_at=BASE_MS, text="hello")],
            fetched_at_ms=BASE_MS,
        )
        persist_batch(db_conn, batch, ensure_schema=False)
        consumer = FtsConsumer(db_conn)
        acked_before = consumer.acked_seq()
        original = consumer._fts.upsert

        def _boom(**kwargs):
            raise RuntimeError("boom")

        consumer._fts.upsert = _boom
        # The consumer isolates per-entry failures: it logs, stops the drain,
        # and does NOT advance the ack cursor past the failed entry.
        assert consumer.drain_once(now_ms=BASE_MS + 5) == 0
        assert consumer.acked_seq() == acked_before
        consumer._fts.upsert = original
        assert consumer.drain_once(now_ms=BASE_MS + 6) == 1


# ---------------------------------------------------------------------------
# End-to-end: Fake source -> provider -> persist -> FTS
# ---------------------------------------------------------------------------


class TestProviderEndToEnd:
    @pytest.mark.asyncio
    async def test_backfill_then_steady(self, db_conn) -> None:
        target = make_target()
        source = PageTokenSource(
            [
                [make_message("m4", sent_at=4), make_message("m3", sent_at=3)],
                [make_message("m2", sent_at=2), make_message("m1", sent_at=1)],
            ]
        )
        provider = ImLearningFetchProvider(
            source=source,
            conn=db_conn,
            targets=(target,),
            since_ms=0,
            fetch_top_n=2,
            max_pages=1,
        )
        outcomes = await provider.run_once(now_ms=10_000)
        assert len(outcomes) == 1
        assert outcomes[0].mode == "backfill"
        assert outcomes[0].truncated is True
        state = read_backfill_state(db_conn, target=target)
        assert state is not None
        assert state.status == "truncated"

        source2 = PageTokenSource(
            [
                [make_message("m4", sent_at=4), make_message("m3", sent_at=3)],
                [make_message("m2", sent_at=2), make_message("m1", sent_at=1)],
            ]
        )
        provider2 = ImLearningFetchProvider(
            source=source2,
            conn=db_conn,
            targets=(target,),
            since_ms=0,
            fetch_top_n=2,
            max_pages=5,
        )
        outcomes2 = await provider2.run_once(now_ms=20_000)
        assert outcomes2[0].mode == "backfill"
        assert outcomes2[0].truncated is False
        state2 = read_backfill_state(db_conn, target=target)
        assert state2 is not None
        assert state2.status == "complete"

        source3 = PageTokenSource([[make_message("m5", sent_at=5), make_message("m4", sent_at=4)]])
        provider3 = ImLearningFetchProvider(source=source3, conn=db_conn, targets=(target,), since_ms=0, fetch_top_n=2)
        outcomes3 = await provider3.run_once(now_ms=30_000)
        assert outcomes3[0].mode == "steady"
        state3 = read_backfill_state(db_conn, target=target)
        assert state3 is not None
        assert state3.newest_seen_msg_id == "m5"
        count = db_conn.execute("SELECT COUNT(*) FROM im_messages").fetchone()[0]
        assert count == 5

    @pytest.mark.asyncio
    async def test_newest_seen_anchor_deleted_falls_back_to_sent_at(self, db_conn) -> None:
        target = make_target()
        record_backfill_result(
            db_conn,
            target=target,
            status="complete",
            truncated=False,
            newest_seen_msg_id="m2",
            newest_seen_sent_at=2,
            now_ms=1000,
        )
        source = PageTokenSource([[make_message("m1b", sent_at=1)]])
        provider = ImLearningFetchProvider(source=source, conn=db_conn, targets=(target,), since_ms=0)
        outcomes = await provider.run_once(now_ms=2000)
        assert outcomes[0].mode == "steady"
        assert outcomes[0].messages_persisted == 1

    @pytest.mark.asyncio
    async def test_fts_search_after_provider_cycle(self, db_conn) -> None:
        target = make_target()
        source = PageTokenSource([[make_message("m1", sent_at=1, text="预算超支需要评审")]])
        provider = ImLearningFetchProvider(source=source, conn=db_conn, targets=(target,), since_ms=0)
        await provider.run_once(now_ms=1000)
        consumer = FtsConsumer(db_conn)
        consumer.drain_once(now_ms=1100)
        fts = FtsIndexRepository(db_conn)
        hits = fts.search("预算")
        assert len(hits) == 1
