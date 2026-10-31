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
from openjiuwen.agent_evolving.checkpointing.types import EvolutionTarget
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.signal import SignalDetector
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID, TRAJECTORY_SOURCE
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, write_llm_exchange
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
    SkillEvolutionRail,
    _SkillPreparedEvolutionInput,
)


def _prepare_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _trajectory(messages: list[dict]) -> Trajectory:
    """Wrap messages as the OTLP trajectory the rail drains after an invoke."""
    spans = []
    call_names = {
        call["id"]: call["function"]["name"] for message in messages for call in message.get("tool_calls", [])
    }
    for index, message in enumerate(messages):
        attrs: dict = {}
        if message["role"] == "tool":
            tool_name = call_names[message["tool_call_id"]]
            attrs[semconv.GEN_AI_TOOL_NAME] = tool_name
            attrs[semconv.GEN_AI_TOOL_CALL_RESULT] = json.dumps(message["content"], ensure_ascii=False)
            attrs[semconv.GEN_AI_TOOL_CALL_ID] = message["tool_call_id"]
            span_name = f"tool.{tool_name}"
        else:
            flat = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
            prompts, completions = ([], [flat]) if message["role"] == "assistant" else ([flat], [])
            attrs.update(write_llm_exchange(prompts, completions))
            attrs[semconv.GEN_AI_OPERATION_NAME] = "chat"
            span_name = "llm.call"
        spans.append({
            "name": span_name,
            "traceId": "trace-e2e",
            "spanId": f"span-{index}",
            "startTimeUnixNano": index * 10,
            "endTimeUnixNano": index * 10 + 1,
            "attributes": attributes_from_map(attrs),
        })
    resource = attributes_from_map({TRAJECTORY_ID: "e2e-task", TRAJECTORY_SOURCE: "online"})
    return Trajectory.from_otlp({
        "resourceSpans": [{"resource": {"attributes": resource}, "scopeSpans": [{"scope": {}, "spans": spans}]}]
    })


# A skill whose own text is full of failure words, as real skills are.
_SPREADSHEET_SKILL = (
    "---\nname: spreadsheet\ndescription: Edit .xlsx workbooks.\n---\n\n"
    "# Spreadsheet\n\n## Troubleshooting\n"
    "- If recalculation failed, read the error log; a #REF! error means a deleted range.\n"
    "- Timeout while saving: retry once.\n"
)


def _harness_tool_result(data: dict) -> str:
    """Tool result as the harness renders it into the conversation."""
    return f"success=True data={data!r} error=None"


def _shell(command: str, stdout: str, stderr: str, exit_code: int) -> str:
    """Shell tool output (harness/tools/shell/bash/_output.py)."""
    return _harness_tool_result(
        {"content": f"Command: {command}\nStdout: {stdout}\n\nStderr: {stderr}\nExit Code: {exit_code}"}
    )


def _task_with_shell_result(skills_root: Path, command: str, shell_output: str) -> list[dict]:
    """Load the skill with skill_tool, then run one shell command."""
    return [
        {"role": "user", "content": "Check /workspace/budget.xlsx for broken formulas."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "tc_load",
                "type": "function",
                "function": {"name": "skill_tool", "arguments": json.dumps({"skill_name": "spreadsheet"})},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_load",
            "content": _harness_tool_result(
                {"skill_directory": str(skills_root / "spreadsheet"), "skill_content": _SPREADSHEET_SKILL}
            ),
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc_run", "type": "function",
                            "function": {"name": "bash", "arguments": json.dumps({"command": command})}}],
        },
        {"role": "tool", "tool_call_id": "tc_run", "content": shell_output},
        {"role": "assistant", "content": "Done."},
    ]


def _passive_rail(skills_root: Path, llm) -> SkillEvolutionRail:
    return SkillEvolutionRail(
        skills_dir=str(skills_root),
        llm=llm,
        model="mock-model",
        signal_trigger=True,
        auto_save=True,
        language="en",
        review_runtime=EvolutionReviewRuntime(),
        trajectory_span_processor=TrajectorySpanProcessor(),
        async_evolution=False,
    )


async def _run_task(rail: SkillEvolutionRail, messages: list[dict]) -> None:
    await rail.run_evolution(
        _SkillPreparedEvolutionInput(
            trajectory=_trajectory(messages),
            messages=tuple(messages),
            skill_name="skill-evolution",
        )
    )


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
        optimizer = SkillExperienceOptimizer(llm=llm, model="mock-model", language="en", two_stage=False)

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

        optimizer = SkillExperienceOptimizer(llm=llm, model="mock", language="cn", two_stage=False)
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

        optimizer = SkillExperienceOptimizer(llm=llm, model="mock", language="en", two_stage=False)
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


class TestPassiveEvolutionOnSuccessfulToolResults:
    """Passive evolution must not learn from tool results that succeeded."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_clean_task_leaves_the_skill_unchanged(tmp_path: Path):
        """Loading a skill whose text mentions failures, then a command that exits 0
        while printing "0 errors", gives the optimizer nothing to learn from."""
        skills_root = tmp_path / "skills"
        skill_dir = _prepare_skill(skills_root, "spreadsheet", _SPREADSHEET_SKILL)
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps([{
            "action": "append", "target": "body", "section": "Troubleshooting",
            "content": "### Recalculation failed\n- Read the error log.", "summary": "Read the error log",
        }])))

        messages = _task_with_shell_result(
            skills_root, "make check", _shell("make check", "0 errors, 0 failed checks", "(empty)", 0),
        )
        await _run_task(_passive_rail(skills_root, llm), messages)

        llm.invoke.assert_not_awaited()
        assert not (skill_dir / "evolutions.json").exists()
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == _SPREADSHEET_SKILL

    @staticmethod
    @pytest.mark.asyncio
    async def test_failed_command_still_evolves_the_loaded_skill(tmp_path: Path):
        """A real crash after the same skill load reaches the optimizer as the only
        execution_failure, credited to the loaded skill, and becomes a record."""
        skills_root = tmp_path / "skills"
        skill_dir = _prepare_skill(skills_root, "spreadsheet", _SPREADSHEET_SKILL)
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps([{
            "action": "append", "target": "body", "section": "Troubleshooting",
            "content": "### Check sheet names\n- List wb.sheetnames before wb[name].",
            "summary": "List wb.sheetnames before indexing",
        }])))

        messages = _task_with_shell_result(
            skills_root,
            "make check",
            _shell("make check", "", "Traceback (most recent call last):\nKeyError: 'Worksheet Sheet1'", 1),
        )
        await _run_task(_passive_rail(skills_root, llm), messages)

        assert llm.invoke.await_count == 1
        prompt = llm.invoke.await_args.kwargs["messages"][0]["content"]
        assert prompt.count('"type": "execution_failure"') == 1
        assert "KeyError" in prompt
        entries = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))["entries"]
        assert [entry["source"] for entry in entries] == ["execution_failure"]
