# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""End-to-end system tests for the online skill evolution pipeline.

Exercises the full chain: SignalDetector → SkillExperienceOptimizer → EvolutionStore
without requiring a live LLM (uses a mock LLM that returns deterministic JSON).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionContext,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.signal import SignalDetector


def _prepare_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _build_conversation_with_script() -> list[dict]:
    """Build a realistic conversation containing a successful code execution."""
    return [
        {
            "role": "assistant",
            "content": "Let me read the skill first.",
            "tool_calls": [
                {
                    "id": "tc_read",
                    "name": "read_file",
                    "arguments": json.dumps({"file_path": "/skills/data-processor/SKILL.md"}),
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_read",
            "name": "read_file",
            "content": "# Data Processor Skill\n\nProcess CSV and JSON files.",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_code",
                    "name": "code",
                    "arguments": json.dumps({
                        "code": (
                            "import pandas as pd\n"
                            "import matplotlib.pyplot as plt\n\n"
                            "df = pd.read_csv('data.csv')\n"
                            "fig, ax = plt.subplots()\n"
                            "ax.bar(df['category'], df['value'])\n"
                            "ax.set_title('Category Distribution')\n"
                            "plt.savefig('chart.png')\n"
                            "print('Chart saved to chart.png')\n"
                        ),
                    }),
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_code",
            "name": "code",
            "content": "Chart saved to chart.png",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_bash",
                    "name": "bash",
                    "arguments": json.dumps({"command": "cat /etc/hosts | head"}),
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_bash",
            "name": "bash",
            "content": "Error: permission denied reading /etc/hosts",
        },
        {
            "role": "user",
            "content": "不对，应该用 sudo 读取系统文件",
        },
    ]


def _build_mock_llm_response_with_script() -> str:
    return json.dumps([
        {
            "action": "append",
            "target": "body",
            "section": "Troubleshooting",
            "content": "### Permission Denied on System Files\n- Use sudo when reading system files like /etc/hosts",
            "merge_target": None,
        },
        {
            "action": "append",
            "target": "script",
            "section": "Scripts",
            "content": (
                "import pandas as pd\n"
                "import matplotlib.pyplot as plt\n\n"
                "df = pd.read_csv('data.csv')\n"
                "fig, ax = plt.subplots()\n"
                "ax.bar(df['category'], df['value'])\n"
                "ax.set_title('Category Distribution')\n"
                "plt.savefig('chart.png')\n"
            ),
            "merge_target": None,
            "script_filename": "generate_bar_chart.py",
            "script_language": "python",
            "script_purpose": "Generate bar chart from CSV data",
        },
    ], ensure_ascii=False)


class TestOnlineEvolutionE2E:
    """End-to-end: conversation → signals → LLM → records → store → files."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_full_pipeline_signal_to_persist(tmp_path: Path):
        """Verify the complete pipeline from conversation signals through
        LLM generation to file-system persistence and Markdown rendering."""

        skill_name = "data-processor"
        skill_content = "# Data Processor Skill\n\nProcess CSV and JSON files.\n"
        _prepare_skill(tmp_path / "skills", skill_name, skill_content)

        # --- Phase 1: Signal Detection ---
        messages = _build_conversation_with_script()
        detector = SignalDetector(existing_skills={skill_name})
        signals = detector.detect(messages)

        assert len(signals) >= 2, f"Expected >=2 signals, got {len(signals)}: {[s.signal_type for s in signals]}"

        signal_types = {s.signal_type for s in signals}
        assert "script_artifact" in signal_types
        assert "execution_failure" in signal_types or "user_correction" in signal_types

        script_signals = [s for s in signals if s.signal_type == "script_artifact"]
        assert script_signals[0].skill_name == skill_name

        # --- Phase 2: LLM-based Experience Generation (mocked) ---
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(content=_build_mock_llm_response_with_script())
        )
        optimizer = SkillExperienceOptimizer(llm=llm, model="mock-model", language="en")

        store = EvolutionStore(str(tmp_path / "skills"))

        ctx = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content=skill_content,
            messages=messages,
            existing_desc_records=[],
            existing_body_records=[],
        )

        records = await optimizer.generate_records(ctx)

        assert len(records) == 2
        text_records = [r for r in records if r.change.target != EvolutionTarget.SCRIPT]
        script_records = [r for r in records if r.change.target == EvolutionTarget.SCRIPT]
        assert len(text_records) == 1
        assert len(script_records) == 1
        assert script_records[0].change.script_language == "python"
        assert script_records[0].change.script_filename == "generate_bar_chart.py"

        # --- Phase 3: Persistence ---
        for record in records:
            await store.append_record(skill_name, record)

        # Verify evolutions.json
        evo_log = await store.load_evolution_log(skill_name)
        assert len(evo_log.entries) == 2

        # Verify script file persisted
        scripts_dir = tmp_path / "skills" / skill_name / "evolution" / "scripts"
        assert scripts_dir.exists()
        py_files = list(scripts_dir.glob("*.py"))
        assert len(py_files) == 1
        script_content = py_files[0].read_text(encoding="utf-8")
        assert "matplotlib" in script_content
        assert "pandas" in script_content

        # Verify script index
        index_md = scripts_dir / "_index.md"
        assert index_md.exists()
        index_text = index_md.read_text(encoding="utf-8")
        assert "generate_bar_chart.py" in index_text
        assert "python" in index_text

        # Verify section markdown
        evo_dir = tmp_path / "skills" / skill_name / "evolution"
        ts_md = evo_dir / "troubleshooting.md"
        assert ts_md.exists()
        assert "Permission Denied" in ts_md.read_text(encoding="utf-8")

        # Verify SKILL.md index block
        skill_md = (tmp_path / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert "<!-- evolution-index-start -->" in skill_md
        assert "<!-- evolution-index-end -->" in skill_md
        assert "Evolution Experiences" in skill_md
        assert "**2**" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_data_fetch_false_positive_suppressed(tmp_path: Path):
        """Verify that failure keywords in data-fetch tool output do not
        generate false positive execution_failure signals."""

        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc_s", "name": "web_search", "arguments": "{}"}],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_s",
                "name": "web_search",
                "content": (
                    "Search results:\n"
                    "1. How to handle Python timeout errors\n"
                    "2. Common ValueError exceptions and fixes\n"
                    "3. ConnectionError troubleshooting guide\n"
                ),
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc_r", "name": "read_file", "arguments": "{}"}],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_r",
                "name": "read_file",
                "content": "File content: raise ValueError('failed validation')\n",
            },
        ]

        detector = SignalDetector()
        signals = detector.detect(messages)

        failure_signals = [s for s in signals if s.signal_type == "execution_failure"]
        assert len(failure_signals) == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_on_malformed_llm_output(tmp_path: Path):
        """Verify that the optimizer retries and recovers when the first LLM
        response is malformed JSON."""

        skill_name = "retry-skill"
        _prepare_skill(tmp_path / "skills", skill_name, "# Retry Skill\n")

        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=[
            SimpleNamespace(content="This is not JSON at all { broken"),
            SimpleNamespace(content=json.dumps([{
                "action": "append",
                "target": "body",
                "section": "Troubleshooting",
                "content": "### Recovered Fix\n- Retry succeeded",
            }])),
        ])

        optimizer = SkillExperienceOptimizer(llm=llm, model="mock", language="cn")
        detector = SignalDetector()
        signals = detector.detect([
            {"role": "tool", "name": "bash", "content": "Error: command timeout"},
        ])

        ctx = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content="# Retry Skill\n",
            messages=[{"role": "user", "content": "test"}],
            existing_desc_records=[],
            existing_body_records=[],
        )

        records = await optimizer.generate_records(ctx)

        assert llm.invoke.await_count == 2
        assert len(records) == 1
        assert "Recovered Fix" in records[0].change.content

    @staticmethod
    @pytest.mark.asyncio
    async def test_solidify_after_evolution(tmp_path: Path):
        """Verify that body records can be solidified into SKILL.md after
        the evolution pipeline completes."""

        skill_name = "solidify-skill"
        _prepare_skill(
            tmp_path / "skills", skill_name,
            "# Solidify Skill\n\n## Troubleshooting\n- existing item\n",
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps([{
            "action": "append",
            "target": "body",
            "section": "Troubleshooting",
            "content": "### New Finding\n- Always check permissions first",
        }])))

        optimizer = SkillExperienceOptimizer(llm=llm, model="mock", language="en")
        store = EvolutionStore(str(tmp_path / "skills"))

        signals = SignalDetector().detect([
            {"role": "tool", "name": "bash", "content": "Error: EACCES permission denied"},
        ])

        ctx = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content="# Solidify Skill\n\n## Troubleshooting\n- existing item\n",
            messages=[{"role": "user", "content": "fix it"}],
            existing_desc_records=[],
            existing_body_records=[],
        )

        records = await optimizer.generate_records(ctx)
        for record in records:
            await store.append_record(skill_name, record)

        pending_before = await store.get_pending_records(skill_name, EvolutionTarget.BODY)
        assert len(pending_before) == 1

        count = await store.solidify(skill_name)
        assert count == 1

        pending_after = await store.get_pending_records(skill_name, EvolutionTarget.BODY)
        assert len(pending_after) == 0

        skill_md = (tmp_path / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert "New Finding" in skill_md
        assert "existing item" in skill_md
        assert "Always check permissions first" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_merge_target_replaces_existing_record(tmp_path: Path):
        """Verify that a new record with merge_target replaces the old one
        instead of appending a duplicate."""

        skill_name = "merge-skill"
        _prepare_skill(tmp_path / "skills", skill_name, "# Merge Skill\n")

        store = EvolutionStore(str(tmp_path / "skills"))

        # First round: generate initial record
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps([{
            "action": "append",
            "target": "body",
            "section": "Troubleshooting",
            "content": "### Initial finding\n- v1 content",
        }])))

        optimizer = SkillExperienceOptimizer(llm=llm, model="mock", language="en")
        signals = SignalDetector().detect([
            {"role": "tool", "name": "bash", "content": "Error: timeout"},
        ])
        ctx = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content="# Merge Skill\n",
            messages=[{"role": "user", "content": "hi"}],
            existing_desc_records=[],
            existing_body_records=[],
        )
        records_v1 = await optimizer.generate_records(ctx)
        for r in records_v1:
            await store.append_record(skill_name, r)
        old_id = records_v1[0].id

        # Second round: generate merging record
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps([{
            "action": "append",
            "target": "body",
            "section": "Troubleshooting",
            "content": "### Updated finding\n- v2 content with more detail",
            "merge_target": old_id,
        }])))

        existing_body = await store.get_pending_records(skill_name, EvolutionTarget.BODY)
        ctx2 = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content="# Merge Skill\n",
            messages=[{"role": "user", "content": "hi again"}],
            existing_desc_records=[],
            existing_body_records=existing_body,
        )
        records_v2 = await optimizer.generate_records(ctx2)
        for r in records_v2:
            await store.append_record(skill_name, r)

        final_log = await store.load_evolution_log(skill_name)
        assert len(final_log.entries) == 1
        assert "v2 content" in final_log.entries[0].change.content
