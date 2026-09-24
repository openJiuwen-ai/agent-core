"""Unit tests for SqliteImCorpus over PersonalContext im_context.db."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.distill.analyzer import LlmAnalyzer
from openjiuwen.harness.personal_context.distill.runner import run_distill_job
from openjiuwen.harness.personal_context.distill.sqlite_corpus import SqliteImCorpus
from openjiuwen.harness.personal_context.distill.store import get_cursor_ms
from openjiuwen.harness.personal_context.im.models import ImLearningMessage, ImLearningTarget
from openjiuwen.harness.personal_context.im.normalize import normalize_batch
from openjiuwen.harness.personal_context.im.persist import persist_batch
from openjiuwen.harness.personal_context.im.scheduler import open_im_context_db

BASE_MS = 1_700_000_000_000
WINDOW_END_MS = BASE_MS + 10_000


class _FakeLlm:
    async def complete(self, *, system: str, user: str) -> str:
        if "Persona 分析 Prompt" in system:
            return '口头禅：["先看现有实现"]\n'
        if "Persona 生成模板" in system:
            return "# 本人 — Persona\n\n## Layer 2：表达风格\n- 先看现有实现再给建议\n"
        if "Work Skill 分析 Prompt" in system:
            return "负责领域：前端\n"
        if "Work Skill 生成模板" in system:
            return "# 本人 — Work Skill\n\n## 职责范围\n- 前端相关改动\n"
        return "fallback\n"


def _message(
    msg_id: str,
    *,
    sent_at: int,
    text: str,
    is_self: bool | None = True,
    sender_account: str | None = "me",
    sender_name: str | None = "我",
) -> ImLearningMessage:
    return ImLearningMessage(
        channel_id="welink",
        msg_id=msg_id,
        conversation_external_id="g1",
        content_text=text,
        sent_at=sent_at,
        is_self=is_self,
        sender_account=sender_account,
        sender_name=sender_name,
    )


def _seed_messages(
    home: Path,
    messages: list[ImLearningMessage],
    *,
    eligible_map: dict[str, int] | None = None,
) -> None:
    target = ImLearningTarget(
        channel_id="welink",
        kind="group",
        external_id="g1",
        title="项目群",
    )
    conn = open_im_context_db(home)
    try:
        batch = normalize_batch(
            target=target,
            messages=messages,
            fetched_at_ms=WINDOW_END_MS,
            learning_eligible_map=eligible_map,
        )
        persist_batch(conn, batch)
    finally:
        conn.close()


def test_sqlite_im_corpus_missing_db_returns_empty(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    corpus = SqliteImCorpus(str(home))
    messages, sampled = corpus.list_messages(
        window_start_ms=0,
        window_end_ms=WINDOW_END_MS,
        max_messages=800,
    )
    assert messages == []
    assert sampled is False
    assert corpus.count_eligible_since(cursor_ms=0, until_ms=WINDOW_END_MS) == 0
    assert not (home / "im" / "im_context.db").exists()


def test_sqlite_im_corpus_filters_eligible_window_empty_and_null(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    _seed_messages(
        home,
        [
            _message("m1", sent_at=BASE_MS + 1_000, text="这块前端页面我来改"),
            _message("m2", sent_at=BASE_MS + 2_000, text="闲聊", is_self=False, sender_account="a", sender_name="A"),
            _message("m3", sent_at=BASE_MS + 3_000, text="   "),
            _message("m4", sent_at=WINDOW_END_MS, text="窗外消息"),
            _message("m5", sent_at=BASE_MS + 4_000, text="可进蒸馏"),
        ],
        eligible_map={"m1": 1, "m2": 0, "m3": 1, "m4": 1, "m5": 1},
    )
    conn = open_im_context_db(home)
    try:
        conn.execute(
            "UPDATE im_messages SET learning_eligible = NULL WHERE external_id = ?",
            ("m5",),
        )
    finally:
        conn.close()

    corpus = SqliteImCorpus(str(home))
    messages, sampled = corpus.list_messages(
        window_start_ms=BASE_MS,
        window_end_ms=WINDOW_END_MS,
        max_messages=800,
    )
    assert sampled is False
    assert [message.content_text for message in messages] == ["这块前端页面我来改"]
    assert messages[0].id
    assert messages[0].channel_id == "welink"
    assert messages[0].conversation_id
    assert messages[0].sent_at_ms == BASE_MS + 1_000
    assert messages[0].is_self is True
    assert messages[0].learning_eligible == 1


def test_sqlite_im_corpus_half_open_excludes_end_bound(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    end = BASE_MS + 5_000
    _seed_messages(
        home,
        [
            _message("inside", sent_at=end - 1, text="窗内"),
            _message("bound", sent_at=end, text="右端点"),
        ],
        eligible_map={"inside": 1, "bound": 1},
    )
    corpus = SqliteImCorpus(str(home))
    messages, _ = corpus.list_messages(
        window_start_ms=BASE_MS,
        window_end_ms=end,
        max_messages=800,
    )
    assert [message.content_text for message in messages] == ["窗内"]


def test_sqlite_im_corpus_count_eligible_since_matches_filter(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    _seed_messages(
        home,
        [
            _message("m1", sent_at=BASE_MS + 1_000, text="一"),
            _message("m2", sent_at=BASE_MS + 2_000, text="二"),
            _message("m3", sent_at=BASE_MS + 3_000, text="三"),
        ],
        eligible_map={"m1": 1, "m2": 0, "m3": 1},
    )
    corpus = SqliteImCorpus(str(home))
    assert corpus.count_eligible_since(cursor_ms=BASE_MS + 1_000, until_ms=BASE_MS + 3_000) == 1
    assert corpus.count_eligible_since(cursor_ms=BASE_MS + 1_000, until_ms=BASE_MS + 3_001) == 2
    assert corpus.count_eligible_since(cursor_ms=BASE_MS, until_ms=WINDOW_END_MS) == 2


def test_sqlite_im_corpus_downsamples_uniformly(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    seeded = [
        _message(f"m{i}", sent_at=BASE_MS + i * 1_000, text=f"消息{i}")
        for i in range(1, 5)
    ]
    _seed_messages(
        home,
        seeded,
        eligible_map={f"m{i}": 1 for i in range(1, 5)},
    )
    corpus = SqliteImCorpus(str(home))
    messages, sampled = corpus.list_messages(
        window_start_ms=BASE_MS,
        window_end_ms=WINDOW_END_MS,
        max_messages=2,
    )
    assert sampled is True
    assert len(messages) == 2
    assert [message.content_text for message in messages] == ["消息1", "消息3"]


@pytest.mark.asyncio
async def test_run_distill_job_consumes_sqlite_im_corpus(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    _seed_messages(
        home,
        [
            _message("m1", sent_at=BASE_MS + 1_000, text="这块前端页面我来改，接口找后端对齐。"),
            _message(
                "m2",
                sent_at=BASE_MS + 2_000,
                text="帮我 review 一下这个 PR。",
                is_self=False,
                sender_account="alice",
                sender_name="Alice",
            ),
            _message("m3", sent_at=BASE_MS + 3_000, text="好的，我先看现有实现再给建议。"),
        ],
        eligible_map={"m1": 1, "m2": 1, "m3": 1},
    )
    home_str = str(home)
    result = await run_distill_job(
        home_str,
        window_end_ms=WINDOW_END_MS,
        force_full_window=True,
        corpus=SqliteImCorpus(home_str),
        analyzer=LlmAnalyzer(_FakeLlm()),
    )
    assert result.status == "success"
    assert result.message_count == 3
    assert result.distilled_dir is not None
    root = Path(result.distilled_dir)
    assert (root / "persona.md").is_file()
    assert (root / "work.md").is_file()
    assert get_cursor_ms(home_str) == WINDOW_END_MS
