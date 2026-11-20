# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt contracts for the Summary Team delivery shortcuts."""

from types import SimpleNamespace

from openjiuwen.agent_teams.organization.runtime import (
    OrganizationRuntimeManager,
    _ORG_SUMMARY_TEAM_PROMPT,
)
from openjiuwen.agent_teams.paths import organization_workspace_dir
from openjiuwen.agent_teams.prompts.loader import load_template


def _summary_turn_prompt() -> str:
    """Return the one-shot prompt queued when a Summary Execution becomes ready."""
    runtime = OrganizationRuntimeManager(SimpleNamespace())
    captured: dict[str, str] = {}

    def capture_prompt(**kwargs):
        captured["prompt"] = kwargs["prompt"]

    runtime._schedule_leader_turn = capture_prompt
    runtime.schedule_summary_execution(
        team_id="summary-team",
        session_id="session-1",
        task_id="summary-task-1",
        organization_id="org-1",
        execution_id="execution-1",
        root_task_id="root-1",
    )
    return captured["prompt"]


def test_summary_turn_lets_the_leader_finish_short_text():
    """Short text is written and checked once; the two teammates are the complex path."""
    prompt = _summary_turn_prompt()

    assert "single short text deliverable" in prompt
    assert "check it once against the request" in prompt
    assert "call org_summary_complete in this turn" in prompt
    assert "Do not send that short text to source-integrator or delivery-drafter" in prompt
    assert "only when the bound sources contain a disagreement" in prompt
    assert "file, code, or a multi-part specialist deliverable" in prompt
    assert "Send the bound source facts to the two fixed" not in prompt


def test_summary_turn_reads_bound_text_before_opening_a_file():
    """The ready turn uses source text and opens a file only when that source cites it."""
    prompt = _summary_turn_prompt()

    assert "use only bound, accepted source outputs for facts" in prompt
    assert "When a source output already contains the needed text, use that text" in prompt
    assert "explicitly cites it and the summary needs its content" in prompt
    assert "Do not list directories or open files from earlier runs" in prompt


def test_summary_turn_keeps_the_outline_inside_the_request():
    """An outline is structure, attribution, and disagreements, not extra research."""
    prompt = _summary_turn_prompt()

    assert "structure the draft needs" in prompt
    assert "source attribution for each part" in prompt
    assert "disagreements that would change the conclusion" in prompt
    assert "Do not add research, risk lists, or verification items the user did not request" in prompt


def test_summary_turn_names_the_absolute_summary_directory():
    """The final file and result_uri use the directory supplied on this turn."""
    prompt = _summary_turn_prompt()
    summary_dir = str(organization_workspace_dir("org-1", "session-1") / "summary")

    assert summary_dir.startswith("/")
    assert f"The only directory for a final file is {summary_dir}" in prompt
    assert "pass that same absolute path as result_uri" in prompt
    assert "omit result_uri" in prompt


def test_summary_turn_keeps_message_dispatch_off_claim_task():
    """Message delivery does not require claim_task, and organization claim stays separate."""
    prompt = _summary_turn_prompt()

    assert "Do not also create an internal task for that same work" in prompt
    assert "A message reply completes a teammate's work when no internal task is assigned" in prompt
    assert "claim_task(status=completed)" not in prompt
    assert "org_claim_task" not in prompt

    runtime = OrganizationRuntimeManager(SimpleNamespace())
    captured: dict[str, str] = {}
    runtime._schedule_leader_turn = lambda **kwargs: captured.update(prompt=kwargs["prompt"])
    runtime._schedule_claim_turn(
        team_id="team-a",
        session_id="session-1",
        task_id="task-1",
        organization_id="org-1",
    )
    assert "you MUST call org_claim_task" in captured["prompt"]


def test_summary_team_section_repeats_the_delivery_rules():
    """The standing Summary Team section states the same five rules in both languages."""
    chinese = _ORG_SUMMARY_TEAM_PROMPT["cn"]
    english = _ORG_SUMMARY_TEAM_PROMPT["en"]

    assert "单篇短文本" in chinese
    assert "不要发给 source-integrator 或 delivery-drafter" in chinese
    assert "来源正文已经够用时直接使用正文" in chinese
    assert "只有来源明确引用了文件" in chinese
    assert "不要补充用户未要求的研究、风险清单或验证项" in chinese
    assert "summary 绝对目录" in chinese
    assert "省略 result_uri" in chinese
    assert "不要调用 `claim_task`" in chinese
    assert "org_claim_task" not in chinese

    assert "single short text deliverable" in english
    assert "Do not send that short text to source-integrator or delivery-drafter" in english
    assert "When a source output already contains the needed text, use that text" in english
    assert "explicitly cites it" in english
    assert "Do not add research, risk lists, or verification items the user did not request" in english
    assert "absolute summary directory given on this turn" in english
    assert "omit result_uri" in english
    assert "do not call claim_task" in english
    assert "org_claim_task" not in english


def test_summary_closeout_uses_the_supplied_summary_directory():
    """Recovery names the final directory and does not ask for a file just to fill result_uri."""
    summary_dir = "/organizations/org-1/summary"
    keywords = {
        "summary_task_id": "summary-1",
        "execution_id": "execution-1",
        "workspace_path": "/tmp/member-workspace",
        "summary_dir": summary_dir,
    }
    chinese = load_template("org_summary_closeout", "cn").format(keywords).content
    english = load_template("org_summary_closeout", "en").format(keywords).content

    assert summary_dir in chinese
    assert "最终文件目录" in chinese
    assert "同一绝对路径" in chinese
    assert "无需为了可选的 `result_uri` 再写文件" in chinese
    assert "{{summary_dir}}" not in chinese

    assert summary_dir in english
    assert "The only final-file directory is" in english
    assert "pass that same absolute path as `result_uri`" in english
    assert "Do not write a new file merely to fill `result_uri`" in english
    assert "{{summary_dir}}" not in english
