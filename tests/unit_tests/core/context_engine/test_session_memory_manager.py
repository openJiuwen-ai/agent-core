# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pathlib import Path

from openjiuwen.core.context_engine.context.session_memory_manager import (
    DEFAULT_SESSION_MEMORY_TEMPLATE,
    SessionMemoryManager,
    _clean_session_memory_sections,
)

SPARSE_COMMITTED_NOTES = """# Session Title
My session title

# Active Task and Success Criteria
Implement feature X

# Current Execution State
Working on feature X

# Relevant Files
src/main.py
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
    rest = merged[start + len(header) + 1:]
    next_header = rest.find("\n# ")
    return merged[start:] if next_header == -1 else merged[start:start + len(header) + 1 + next_header]


class TestSessionMemoryManagerBoundaryScenarios:
    def test_merge_restores_empty_sections_and_preserves_existing_bodies(self, tmp_path: Path):
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=DEFAULT_SESSION_MEMORY_TEMPLATE)

        merged = SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, SPARSE_COMMITTED_NOTES
        )

        assert merged.count("# ") == 15
        assert pending_path.read_text(encoding="utf-8") == merged

        assert "# Immediate Resume Point and Next Useful Step\n_What should the next agent" in merged
        assert "# Repository and Codebase Understanding\n_Record durable understanding" in merged

        assert "My session title" in merged
        assert "Working on feature X" in merged
        assert "src/main.py" in merged

        resume_block = _section_block(merged, "# Immediate Resume Point and Next Useful Step")
        assert "src/main.py" not in resume_block

    def test_two_round_merge_preserves_committed_content_and_restores_skeleton(self, tmp_path: Path):
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=DEFAULT_SESSION_MEMORY_TEMPLATE)
        notes_path.parent.mkdir(parents=True, exist_ok=True)

        SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, SPARSE_COMMITTED_NOTES
        )
        pending_path.write_text(
            """# Session Title
My session title

# Active Task and Success Criteria
Implement feature X

# Current Execution State
Working on feature X

# Relevant Files
src/main.py

# Historical Work Performed
21:00 started task
21:30 appended after first update
""",
            encoding="utf-8",
        )
        assert SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path) is True
        round1_active = notes_path.read_text(encoding="utf-8")
        assert "21:30 appended after first update" in round1_active
        assert "_A short and distinctive" not in round1_active

        merged_round2 = SessionMemoryManager._prepare_pending_session_memory(
            notes_path, pending_path, round1_active
        )

        assert merged_round2.count("# ") == 15
        assert "21:30 appended after first update" in merged_round2
        assert "src/main.py" in merged_round2
        assert "# Immediate Resume Point and Next Useful Step\n_What should the next agent" in merged_round2

    def test_commit_cleans_descriptions_drops_empty_sections_and_refuses_blank_overwrite(
        self, tmp_path: Path, caplog
    ):
        notes_path, pending_path = _setup_workspace(tmp_path, template_content=DEFAULT_SESSION_MEMORY_TEMPLATE)
        notes_path.parent.mkdir(parents=True, exist_ok=True)

        pending_path.write_text(
            """# Session Title
_A short title guide._
Title body

# Current Execution State
_state description only_

# Active Task and Success Criteria
_What is the active task..._
Implement feature X
""",
            encoding="utf-8",
        )
        assert SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path) is True
        committed = notes_path.read_text(encoding="utf-8")
        assert "_A short title guide._" not in committed
        assert "_state description only_" not in committed
        assert "_What is the active task_" not in committed
        assert "# Session Title\nTitle body" in committed
        assert "# Active Task and Success Criteria\nImplement feature X" in committed
        assert "# Current Execution State" not in committed

        cleaned = _clean_session_memory_sections(
            """# Session Title
_A short title guide._
My title

# Active Task and Success Criteria
_What is the active task..._

# Current Execution State
_What is actively being worked on..._
Working on X
"""
        )
        assert "_short title guide_" not in cleaned
        assert "_What is the active task" not in cleaned
        assert "# Session Title\nMy title" in cleaned
        assert "# Current Execution State\nWorking on X" in cleaned
        assert "# Active Task and Success Criteria" not in cleaned

        notes_path.write_text("# Session Title\nExisting active body\n", encoding="utf-8")
        pending_path.write_text(
            """# Session Title
_描述无正文_

# Current Execution State
_另一个空节_
""",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            refused = SessionMemoryManager._commit_pending_session_memory(pending_path, notes_path)

        assert refused is False
        assert notes_path.read_text(encoding="utf-8") == "# Session Title\nExisting active body\n"
        assert any("refusing empty commit" in record.message for record in caplog.records)

    def test_header_mismatch_warns_and_template_fallback_stays_compatible(self, tmp_path: Path, caplog):
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

        assert merged.count("# ") == 15
        assert "# Session Title\n_A short and distinctive" in merged
        assert "Chinese header only body" not in merged
        assert any(
            "section body not merged into template" in record.message
            and "会话标题" in record.message
            for record in caplog.records
        )

        notes_path2, pending_path2 = _setup_workspace(
            tmp_path, template_content=DEFAULT_SESSION_MEMORY_TEMPLATE, create_template=False, session_id="fallback_session"
        )
        notes_path2.parent.mkdir(parents=True, exist_ok=True)

        merged_fallback = SessionMemoryManager._prepare_pending_session_memory(
            notes_path2,
            pending_path2,
            "# Session Title\nfallback body\n",
        )

        assert merged_fallback.count("# ") == 15
        assert "# Session Title\n_A short and distinctive" in merged_fallback
        assert "fallback body" in merged_fallback
