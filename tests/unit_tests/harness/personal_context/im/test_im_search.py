"""Unit tests for the IM search port, SQLite implementation, and tool (OJ-06).

Matrix per ``DigitalAvatar/OJ06-IM检索方案.md`` §11.2:

- contract purity: ``im/search.py`` has no storage imports (D10);
- tool layer (FakeSearchPort, no DB): keyword required (D9), time-string
  parsing (ISO 8601 / relative), ToolOutput shape, port errors;
- SQLite implementation: keyword (bigram / English / strict->relaxed),
  conversation refs (D12: exact, title LIKE, escape, not found), sender
  filter, closed time interval, learning-eligible invisibility (D11/D8),
  paging, SQL-side filter semantics, read-only behavior, empty/missing DB,
  content truncation;
- prompts metadata registration.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.im import search as search_contract_module
from openjiuwen.harness.personal_context.im.fts_consumer import FtsConsumer
from openjiuwen.harness.personal_context.im.models import (
    ImLearningMessage,
    ImLearningTarget,
)
from openjiuwen.harness.personal_context.im.normalize import normalize_batch
from openjiuwen.harness.personal_context.im.persist import persist_batch
from openjiuwen.harness.personal_context.im.scheduler import open_im_context_db
from openjiuwen.harness.personal_context.im.schema import init_im_schema
from openjiuwen.harness.personal_context.im.search import (
    ImSearchHit,
    ImSearchQuery,
)
from openjiuwen.harness.personal_context.im.search_tool import (
    ImSearchTool,
    _parse_time_to_ms,
)
from openjiuwen.harness.personal_context.im.sqlite_search import (
    TRUNCATION_SUFFIX,
    SqliteImSearchStore,
)
from openjiuwen.harness.prompts.tools import (
    build_tool_card,
    get_tool_description,
    get_tool_input_params,
)
from openjiuwen.harness.prompts.tools.im_search import (
    IM_SEARCH_DESCRIPTION,
    ImSearchMetadataProvider,
)

BASE_MS = 1_700_000_000_000


# ---------------------------------------------------------------------------
# Corpus fixture: real pipeline (normalize -> persist -> FTS drain)
# ---------------------------------------------------------------------------


def _msg(
    msg_id: str,
    *,
    sent_at: int,
    text: str,
    external_id: str = "g1",
    account: str | None = None,
    name: str | None = None,
    is_self: bool = False,
) -> ImLearningMessage:
    return ImLearningMessage(
        channel_id="welink",
        msg_id=msg_id,
        conversation_external_id=external_id,
        content_text=text,
        sent_at=sent_at,
        sender_account=account,
        sender_name=name,
        is_self=is_self,
    )


def _target(external_id: str, title: str) -> ImLearningTarget:
    return ImLearningTarget(channel_id="welink", kind="group", external_id=external_id, title=title)


SEED: list[tuple[ImLearningTarget, list[ImLearningMessage], dict[str, int]]] = [
    (
        _target("g1", "项目群"),
        [
            _msg("m1", sent_at=BASE_MS + 1000, text="排期下周发布", account="alice", name="Alice"),
            _msg("m2", sent_at=BASE_MS + 2000, text="budget approval pending", account="bob", name="Bob"),
            _msg("m3", sent_at=BASE_MS + 3000, text="排期确认，预算已通", account="me", name="我", is_self=True),
            _msg("m4", sent_at=BASE_MS + 3500, text="机密排期内容", account="alice", name="Alice"),
        ],
        {"m4": 0},  # out of learning scope (D11)
    ),
    (
        _target("g2", "闲聊群"),
        [
            _msg("m5", sent_at=BASE_MS + 4000, external_id="g2", text="午饭吃啥", account="alice", name="Alice"),
        ],
        {},
    ),
    (
        _target("g3", "100%进度群"),
        [
            _msg("m6", sent_at=BASE_MS + 5000, external_id="g3", text="排期同步", account="bob", name="张三丰"),
        ],
        {},
    ),
    (
        _target("g4", "100大进度群"),
        [
            _msg("m8", sent_at=BASE_MS + 6000, external_id="g4", text="排期对齐", account="bob", name="Bob"),
        ],
        {},
    ),
]


@pytest.fixture()
def corpus(tmp_path: Path):
    """Seeded im_context.db; returns (home, external_id -> internal message_id)."""
    conn = open_im_context_db(tmp_path)
    try:
        init_im_schema(conn)
        for target, messages, eligible_map in SEED:
            batch = normalize_batch(
                target=target,
                messages=messages,
                fetched_at_ms=BASE_MS,
                learning_eligible_map=eligible_map or None,
            )
            persist_batch(conn, batch, now_ms=BASE_MS)
        FtsConsumer(conn).drain_once(now_ms=BASE_MS)
        rows = conn.execute("SELECT id, external_id FROM im_messages").fetchall()
        ext_to_id = {str(row["external_id"]): str(row["id"]) for row in rows}
    finally:
        conn.close()
    return tmp_path, ext_to_id


# ---------------------------------------------------------------------------
# Contract purity (D10)
# ---------------------------------------------------------------------------


class TestContractPurity:
    def test_search_module_has_no_storage_imports(self) -> None:
        source = Path(search_contract_module.__file__).read_text(encoding="utf-8")
        assert "import sqlite" not in source
        assert "sqlite3." not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("sqlite") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith("sqlite")


# ---------------------------------------------------------------------------
# SQLite implementation: keyword
# ---------------------------------------------------------------------------


class TestSqliteKeyword:
    def test_chinese_keyword_matches(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期"))
        assert total == 4  # m4 (eligible=0) invisible
        assert {h.message_id for h in hits} == {ext_to_id["m1"], ext_to_id["m3"], ext_to_id["m6"], ext_to_id["m8"]}

    def test_english_keyword_matches(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="budget"))
        assert total == 1
        assert hits[0].message_id == ext_to_id["m2"]
        assert hits[0].sender_account == "bob"

    def test_strict_to_relaxed_fallback(self, corpus) -> None:
        home, ext_to_id = corpus
        # strict tier = [预, 预算, 算, 算通, 通]; m3 only has 算已 (no 算通) -> strict miss.
        # relaxed tier = [预, 算, 通] singles, all present in m3 -> fallback hit.
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="预算通"))
        assert total == 1
        assert hits[0].message_id == ext_to_id["m3"]

    def test_punctuation_only_keyword_returns_empty(self, corpus) -> None:
        home, _ = corpus
        assert SqliteImSearchStore(home).search(ImSearchQuery(keyword="！！！")) == ([], 0, False)

    def test_missing_keyword_raises_status_error(self, corpus) -> None:
        home, _ = corpus
        with pytest.raises(BaseError):
            SqliteImSearchStore(home).search(ImSearchQuery(keyword=""))
        with pytest.raises(BaseError):
            SqliteImSearchStore(home).search(ImSearchQuery(keyword="   "))


# ---------------------------------------------------------------------------
# SQLite implementation: filters (conversation D12 / sender / time)
# ---------------------------------------------------------------------------


class TestSqliteFilters:
    def test_conversation_filter_by_external_id(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", conversation_refs=("g1",)))
        assert total == 2
        assert {h.message_id for h in hits} == {ext_to_id["m1"], ext_to_id["m3"]}

    def test_conversation_filter_by_title_substring(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", conversation_refs=("项目",)))
        assert total == 2
        assert {h.message_id for h in hits} == {ext_to_id["m1"], ext_to_id["m3"]}
        assert all(h.conversation_title == "项目群" for h in hits)

    def test_conversation_title_like_escape_percent(self, corpus) -> None:
        home, ext_to_id = corpus
        # "100%进度" must match only the literal-% title (g3), not g4 "100大进度群".
        hits, total, _ = SqliteImSearchStore(home).search(
            ImSearchQuery(keyword="排期", conversation_refs=("100%进度",))
        )
        assert total == 1
        assert hits[0].message_id == ext_to_id["m6"]

    def test_conversation_not_found_returns_empty(self, corpus) -> None:
        home, _ = corpus
        result = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", conversation_refs=("不存在",)))
        assert result == ([], 0, False)

    def test_sender_filter_by_account(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", sender="alice"))
        assert total == 1
        assert hits[0].message_id == ext_to_id["m1"]

    def test_sender_filter_case_insensitive_name(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", sender="Alice"))
        assert total == 1
        assert hits[0].message_id == ext_to_id["m1"]

    def test_sender_filter_by_name_substring(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", sender="三"))
        assert total == 1
        assert hits[0].message_id == ext_to_id["m6"]

    def test_sender_combo_keyword(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", sender="bob"))
        assert total == 2
        assert {h.message_id for h in hits} == {ext_to_id["m6"], ext_to_id["m8"]}

    def test_time_closed_interval_boundaries(self, corpus) -> None:
        home, ext_to_id = corpus
        # [BASE+1000, BASE+3000] closed on both ends: m1 and m3 hit the edges.
        hits, total, _ = SqliteImSearchStore(home).search(
            ImSearchQuery(keyword="排期", since_ms=BASE_MS + 1000, until_ms=BASE_MS + 3000)
        )
        assert total == 2
        assert {h.message_id for h in hits} == {ext_to_id["m1"], ext_to_id["m3"]}

    def test_conversation_and_time_combo(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(
            ImSearchQuery(keyword="排期", conversation_refs=("g1",), since_ms=BASE_MS + 2000)
        )
        assert total == 1
        assert hits[0].message_id == ext_to_id["m3"]


# ---------------------------------------------------------------------------
# SQLite implementation: semantics (D11 / paging / read-only / truncation)
# ---------------------------------------------------------------------------


class TestSqliteSemantics:
    def test_learning_eligible_invisible_but_indexed(self, corpus) -> None:
        home, ext_to_id = corpus
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="机密"))
        assert total == 0
        assert hits == []
        # D8/D11: the message IS in the FTS index; filtering happens at query time.
        conn = open_im_context_db(home)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM im_messages_fts_state WHERE message_id = ?",
                (ext_to_id["m4"],),
            ).fetchone()
            assert row[0] == 1
        finally:
            conn.close()

    def test_sql_filter_semantics_total_matches_hits(self, corpus) -> None:
        home, _ = corpus
        # keyword hits 4 rows; sender keeps 2: total must be 2 (SQL-side filter,
        # not in-memory post-filtering of a limit-50 page).
        hits, total, _ = SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期", sender="bob", limit=50))
        assert total == 2
        assert len(hits) == 2

    def test_paging_limit_offset_truncated(self, corpus) -> None:
        home, _ = corpus
        store = SqliteImSearchStore(home)
        hits, total, truncated = store.search(ImSearchQuery(keyword="排期", limit=2, offset=0))
        assert (total, len(hits), truncated) == (4, 2, True)
        hits, total, truncated = store.search(ImSearchQuery(keyword="排期", limit=2, offset=2))
        assert (total, len(hits), truncated) == (4, 2, False)
        hits, total, truncated = store.search(ImSearchQuery(keyword="排期", limit=2, offset=3))
        assert (total, len(hits), truncated) == (4, 1, False)
        # limit above the cap is clamped to 50.
        hits, total, truncated = store.search(ImSearchQuery(keyword="排期", limit=100))
        assert (total, len(hits), truncated) == (4, 4, False)

    def test_content_truncation(self, corpus) -> None:
        home, _ = corpus
        store = SqliteImSearchStore(home, max_content_chars=3)
        hits, _, _ = store.search(ImSearchQuery(keyword="budget"))
        assert len(hits) == 1
        assert hits[0].content_text == "bud" + TRUNCATION_SUFFIX

    def test_empty_db_returns_empty_for_all_filters(self, tmp_path: Path) -> None:
        conn = open_im_context_db(tmp_path)
        try:
            init_im_schema(conn)
        finally:
            conn.close()
        store = SqliteImSearchStore(tmp_path)
        assert store.search(ImSearchQuery(keyword="排期")) == ([], 0, False)
        assert store.search(
            ImSearchQuery(keyword="排期", sender="alice", since_ms=0, until_ms=BASE_MS, conversation_refs=("g1",))
        ) == ([], 0, False)

    def test_missing_db_returns_empty_and_creates_nothing(self, tmp_path: Path) -> None:
        missing_home = tmp_path / "no_such_home"
        store = SqliteImSearchStore(missing_home)
        assert store.search(ImSearchQuery(keyword="排期")) == ([], 0, False)
        assert not (missing_home / "im" / "im_context.db").exists()

    def test_search_is_read_only(self, corpus) -> None:
        home, _ = corpus

        def count_rows() -> int:
            conn = open_im_context_db(home)
            try:
                return int(conn.execute("SELECT COUNT(*) FROM im_messages").fetchone()[0])
            finally:
                conn.close()

        before = count_rows()
        SqliteImSearchStore(home).search(ImSearchQuery(keyword="排期"))
        assert count_rows() == before


# ---------------------------------------------------------------------------
# Tool layer (FakeSearchPort, no DB)
# ---------------------------------------------------------------------------

FIXTURE_HIT = ImSearchHit(
    message_id="mid",
    channel_id="welink",
    conversation_id="cid",
    conversation_title="项目群",
    sender_account="alice",
    sender_name="Alice",
    is_self=False,
    sent_at=BASE_MS,
    content_text="排期下周发布",
)


class FakeSearchPort:
    def __init__(self) -> None:
        self.queries: list[ImSearchQuery] = []

    def search(self, query: ImSearchQuery) -> tuple[list[ImSearchHit], int, bool]:
        self.queries.append(query)
        return [FIXTURE_HIT], 1, False


class ExplodingPort:
    def search(self, query: ImSearchQuery) -> tuple[list[ImSearchHit], int, bool]:
        raise RuntimeError("boom")


class TestImSearchTool:
    @pytest.mark.asyncio
    async def test_missing_or_blank_keyword_rejected(self) -> None:
        tool = ImSearchTool(FakeSearchPort())
        for inputs in ({}, {"keyword": None}, {"keyword": ""}, {"keyword": "   "}):
            result = await tool.invoke(inputs)
            assert result.success is False
            assert "keyword" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_since_rejected(self) -> None:
        tool = ImSearchTool(FakeSearchPort())
        for bad in ("7x", "not-a-date", ""):
            result = await tool.invoke({"keyword": "排期", "since": bad})
            assert result.success is False
            assert "since" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_until_rejected(self) -> None:
        tool = ImSearchTool(FakeSearchPort())
        result = await tool.invoke({"keyword": "排期", "until": "yesterday-ish"})
        assert result.success is False
        assert "until" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_limit_rejected(self) -> None:
        tool = ImSearchTool(FakeSearchPort())
        result = await tool.invoke({"keyword": "排期", "limit": "abc"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_iso_times_parsed_into_query(self) -> None:
        port = FakeSearchPort()
        result = await ImSearchTool(port).invoke(
            {"keyword": "排期", "since": "2026-09-01", "until": "2026-09-10T12:00:00"}
        )
        assert result.success is True
        query = port.queries[0]
        assert query.since_ms == int(datetime(2026, 9, 1).astimezone().timestamp() * 1000)
        assert query.until_ms == int(datetime(2026, 9, 10, 12, 0, 0).astimezone().timestamp() * 1000)

    @pytest.mark.asyncio
    async def test_query_fields_passed_to_port_with_limit_clamp(self) -> None:
        port = FakeSearchPort()
        result = await ImSearchTool(port).invoke(
            {"keyword": " 排期 ", "conversation": "项目群", "sender": "alice", "limit": 100, "offset": 5}
        )
        assert result.success is True
        query = port.queries[0]
        assert query.keyword == "排期"
        assert query.conversation_refs == ("项目群",)
        assert query.sender == "alice"
        assert query.limit == 50
        assert query.offset == 5
        assert query.since_ms is None and query.until_ms is None

    @pytest.mark.asyncio
    async def test_output_shape(self) -> None:
        result = await ImSearchTool(FakeSearchPort()).invoke({"keyword": "排期"})
        assert result.success is True
        data = result.data
        assert set(data.keys()) == {"total", "truncated", "hits"}
        assert data["total"] == 1
        assert data["truncated"] is False
        hit = data["hits"][0]
        assert set(hit.keys()) == {
            "message_id",
            "channel_id",
            "conversation_id",
            "conversation_title",
            "sender_account",
            "sender_name",
            "is_self",
            "sent_at",
            "content_text",
        }
        assert hit["conversation_title"] == "项目群"

    @pytest.mark.asyncio
    async def test_port_error_becomes_tool_error(self) -> None:
        result = await ImSearchTool(ExplodingPort()).invoke({"keyword": "排期"})
        assert result.success is False
        assert "boom" in (result.error or "")


class TestTimeParsing:
    def test_relative_expressions(self) -> None:
        now = 1_000_000
        assert _parse_time_to_ms("7d", now_ms=now) == now - 7 * 86_400_000
        assert _parse_time_to_ms("24h", now_ms=now) == now - 86_400_000
        assert _parse_time_to_ms("30m", now_ms=now) == now - 1_800_000
        assert _parse_time_to_ms("48H", now_ms=now) == now - 172_800_000

    def test_iso_utc_and_naive(self) -> None:
        assert _parse_time_to_ms("2026-09-01T00:00:00Z") == 1_788_220_800_000
        expected = int(datetime(2026, 9, 1).astimezone().timestamp() * 1000)
        assert _parse_time_to_ms("2026-09-01") == expected

    def test_invalid_returns_none(self) -> None:
        assert _parse_time_to_ms("7x") is None
        assert _parse_time_to_ms("not-a-date") is None
        assert _parse_time_to_ms("") is None


# ---------------------------------------------------------------------------
# Prompts metadata
# ---------------------------------------------------------------------------


class TestImSearchMetadata:
    def test_registered_and_bilingual(self) -> None:
        assert get_tool_description("im_search", "cn") == IM_SEARCH_DESCRIPTION["cn"]
        assert get_tool_description("im_search", "en") == IM_SEARCH_DESCRIPTION["en"]

    def test_input_params_schema(self) -> None:
        params = get_tool_input_params("im_search")
        assert params["type"] == "object"
        assert params["required"] == ["keyword"]
        assert set(params["properties"].keys()) == {
            "keyword",
            "conversation",
            "sender",
            "since",
            "until",
            "limit",
            "offset",
        }
        assert params["properties"]["keyword"]["type"] == "string"

    def test_provider_validate(self) -> None:
        ImSearchMetadataProvider().validate()

    def test_build_tool_card(self) -> None:
        card = build_tool_card("im_search", "ImSearchTool")
        assert card.name == "im_search"
        assert card.description == IM_SEARCH_DESCRIPTION["cn"]
        assert card.input_params["required"] == ["keyword"]
