# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pathlib import Path

from openjiuwen.core.context_engine.context.session_memory_manager import (
    DEFAULT_SESSION_MEMORY_TEMPLATE,
    SessionMemoryManager,
    _clean_session_memory_sections,
)

ZH_TEMPLATE = """# 会话标题
_用 5-10 个字写一个简短、明确、信息密度高的会话标题，不要空话。_

# 当前状态
_当前正在做什么？有哪些还没完成的任务？下一步最直接要做什么？_

# 任务说明
_用户的原始需求；交付物类型（Word/PPT/Excel/PDF/邮件/纪要等）；格式、风格、页数/篇幅、受众、截止时间等约束；用户已确认的方案。_

# 文件与素材
_输入文件、参考文档、附件路径；已生成的输出文件路径与版本；各文件的用途与处理状态。_

# 处理进度
_使用的技能/工具及阶段（如 pptx-craft Stage 1-9）；大纲/草稿/数据整理进度；待用户确认或审批的事项。_

# 问题与修正
_处理过程中的报错、重试与用户纠正；已被证明无效的做法。_

# 关键产物
_最终或阶段性交付物：文件路径、核心摘要、表格/结论/邮件正文要点；用户已确认的内容请完整保留。_

# 工作记录
_按时间顺序简要记录关键操作，每步一行，便于恢复上下文。_
"""

# 模拟 commit 后 active：仅 6 节有正文，缺「文件与素材」「问题与修正」
SPARSE_COMMITTED_NOTES = """# 会话标题
My session title

# 当前状态
Working on feature X

# 任务说明
User wants a report

# 处理进度
Stage 1 in progress

# 关键产物
DELIVERABLE_UNIQUE_BODY

# 工作记录
21:00 started task
"""


def _setup_workspace(
    tmp_path: Path,
    *,
    template_content: str,
    session_id: str = "test_session",
    create_template: bool = True,
) -> tuple[Path, Path]:
    context_dir = tmp_path / "context"
    notes_path = context_dir / f"{session_id}_context" / "session_memory" / "session_context.md"
    pending_path = notes_path.with_name(f"{notes_path.stem}.pending{notes_path.suffix}")
    if create_template:
        template_path = context_dir / "session_memory.md"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(template_content, encoding="utf-8")
    return notes_path, pending_path


def _section_block(merged: str, header: str) -> str:
    start = merged.index(header)
    rest = merged[start + len(header) + 1 :]
    next_header = rest.find("\n# ")
    return merged[start:] if next_header == -1 else merged[start : start + len(header) + 1 + next_header]


class TestSessionMemoryManagerBoundaryScenarios:
    def test_merge_restores_empty_sections_and_preserves_existing_bodies(self, tmp_path: Path):
        """空节恢复：sparse active 再次 merge 时补回 8 节骨架与描述，且已有正文按 header 精确保留、不错节挂载。"""
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=ZH_TEMPLATE)

        merged = SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, SPARSE_COMMITTED_NOTES
        )

        assert merged.count("# ") == 8
        assert pending_path.read_text(encoding="utf-8") == merged

        # 空节恢复：缺失节补回描述行骨架
        assert "# 文件与素材\n_输入文件" in merged
        assert "# 问题与修正\n_处理过程中" in merged

        # 已有内容保留
        assert "My session title" in merged
        assert "Working on feature X" in merged
        assert "DELIVERABLE_UNIQUE_BODY" in merged

        # 无 index 回退：关键产物正文不得挂到问题与修正
        issues_block = _section_block(merged, "# 问题与修正")
        assert "DELIVERABLE_UNIQUE_BODY" not in issues_block

    def test_two_round_merge_preserves_committed_content_and_restores_skeleton(self, tmp_path: Path):
        """已有内容保留 + 追加：第一轮 commit 后再 merge，历史正文仍在，空节骨架再次补回。"""
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=ZH_TEMPLATE)
        notes_path.parent.mkdir(parents=True, exist_ok=True)

        # 第一轮：sparse active → merge → 模拟 LLM 去掉描述行后 commit
        SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, SPARSE_COMMITTED_NOTES
        )
        pending_path.write_text(
            """# 会话标题
My session title

# 当前状态
Working on feature X

# 任务说明
User wants a report

# 处理进度
Stage 1 in progress

# 关键产物
DELIVERABLE_UNIQUE_BODY

# 工作记录
21:00 started task
21:30 appended after first update
""",
            encoding="utf-8",
        )
        assert SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path) is True
        round1_active = notes_path.read_text(encoding="utf-8")
        assert "21:30 appended after first update" in round1_active
        assert "_用 5-10 个字" not in round1_active

        # 第二轮：以 commit 结果为 current_notes 再 merge，模拟再次触发 session memory
        merged_round2 = SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, round1_active
        )

        assert merged_round2.count("# ") == 8
        assert "21:30 appended after first update" in merged_round2
        assert "DELIVERABLE_UNIQUE_BODY" in merged_round2
        assert "# 文件与素材\n_输入文件" in merged_round2
        assert "# 问题与修正\n_处理过程中" in merged_round2

    def test_commit_cleans_descriptions_drops_empty_sections_and_refuses_blank_overwrite(
        self, tmp_path: Path, caplog
    ):
        """描述行清理：commit 后正式文件仅 header+正文；空节丢弃；clean 后为空时拒绝覆盖 active。"""
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=ZH_TEMPLATE)
        notes_path.parent.mkdir(parents=True, exist_ok=True)

        # --- 描述行清理 + 空节丢弃 + 正常 commit ---
        pending_path.write_text(
            """# 会话标题
_描述_
Title body

# 任务说明
_仅描述无正文_

# 当前状态
_状态描述_
Working on X
_line2_
""",
            encoding="utf-8",
        )
        assert SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path) is True
        committed = notes_path.read_text(encoding="utf-8")
        assert "_描述_" not in committed
        assert "_状态描述_" not in committed
        assert "_仅描述" not in committed
        assert "# 会话标题\nTitle body" in committed
        assert "# 当前状态\nWorking on X\n_line2_" in committed
        assert "# 任务说明" not in committed

        # _clean 单元边界：混合空节与非空节
        cleaned = _clean_session_memory_sections(
            """# Session Title
_A short title guide._
My title

# Task Brief
_User request..._

# Current State
_What is active..._
Working on X
"""
        )
        assert "_short title guide_" not in cleaned
        assert "_User request" not in cleaned
        assert "# Session Title\nMy title" in cleaned
        assert "# Current State\nWorking on X" in cleaned
        assert "# Task Brief" not in cleaned

        # --- 空 commit 保护：不得用空内容覆盖已有 active ---
        notes_path.write_text("# 会话标题\nExisting active body\n", encoding="utf-8")
        pending_path.write_text(
            """# 会话标题
_仅描述无正文_

# 文件与素材
_另一个空节_
""",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            refused = SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path)

        assert refused is False
        assert notes_path.read_text(encoding="utf-8") == "# 会话标题\nExisting active body\n"
        assert any("refusing empty commit" in record.message for record in caplog.records)

    def test_header_mismatch_warns_and_template_fallback_stays_compatible(self, tmp_path: Path, caplog):
        """标题不一致回退兼容：header 与模板不一致时告警且不 silent 丢正文；缺 workspace 模板时回退 DEFAULT。"""
        # --- 中英 header 不一致：A3 告警，正文不进 pending ---
        notes_path, pending_path = _setup_workspace(
            tmp_path, template_content=DEFAULT_SESSION_MEMORY_TEMPLATE
        )
        current_notes = """# 会话标题
Chinese header only body
"""
        with caplog.at_level("WARNING"):
            merged = SessionMemoryManager._prepare_pending_session_memory(
                notes_path, pending_path, current_notes
            )

        assert merged.count("# ") == 8
        assert "# Session Title\n_A short and distinctive" in merged
        assert "Chinese header only body" not in merged
        assert any(
            "section body not merged into template" in record.message
            and "会话标题" in record.message
            for record in caplog.records
        )

        # --- workspace 模板缺失：回退 DEFAULT，英文 header 精确匹配 ---
        notes_path2, pending_path2 = _setup_workspace(
            tmp_path, template_content=ZH_TEMPLATE, create_template=False, session_id="fallback_session"
        )
        notes_path2.parent.mkdir(parents=True, exist_ok=True)

        merged_fallback = SessionMemoryManager._prepare_pending_session_memory(
            notes_path2,
            pending_path2,
            "# Session Title\nfallback body\n",
        )

        assert merged_fallback.count("# ") == 8
        assert "# Session Title\n_A short and distinctive" in merged_fallback
        assert "fallback body" in merged_fallback
