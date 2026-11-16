# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the swarmflow locale strings (runtime control verbs).

``resume_id`` previously claimed "接口已就位、执行推进中" — execution was
declared as still coming and the leader was told to use ``script_path``. The
tool now wires ``resume_id`` + ``action`` into pause/resume/stop control, so
the locale copy and the leader-facing description must teach the control verbs
instead of the stale "not supported" claims. These tests pin that copy down so
it cannot regress.
"""

import re

import pytest

from openjiuwen.agent_teams.tools.locales import cn as _cn
from openjiuwen.agent_teams.tools.locales import make_translator


@pytest.mark.level0
def test_cn_resume_id_has_no_stale_interface_claim():
    """cn resume_id copy no longer says execution is 'coming' / use script_path."""
    value = _cn.STRINGS["swarmflow.resume_id"]
    assert "执行推进中" not in value
    assert "action" in value


@pytest.mark.level0
def test_cn_action_param_string_exists_and_describes_pause():
    """cn defines swarmflow.action and it mentions the pause verb."""
    value = _cn.STRINGS["swarmflow.action"]
    assert "pause" in value


@pytest.mark.level0
def test_cn_swarmflow_md_teaches_runtime_control():
    """The leader-facing cn description explains pause/resume/stop control."""
    desc = make_translator("cn")("swarmflow")
    assert "运行态控制" in desc
    assert "action='stop'" in desc


@pytest.mark.level0
def test_swarmflow_md_documents_vote_contract_and_helper_import():
    """cn+en descriptions list the vote fields and the build_reviewers import rule.

    Issue #6306: scripts written per the description read ``v.label`` (crashed:
    the field did not exist) and copied a skeleton whose import lines were
    commented out (``NameError: build_reviewers``). The copy must now teach the
    real vote fields, the no-``name``-field rule, and the explicit import.
    """
    cn = make_translator("cn")("swarmflow")
    en = make_translator("en")("swarmflow")
    assert "没有 `name`" in cn
    assert "no `name`" in en
    for desc in (cn, en):
        assert "from openjiuwen.agent_teams.workflow.review import build_reviewers" in desc
        # Skeleton examples must show real imports. Match by pattern (any
        # commented `from swarmflow import ...` line) instead of one literal
        # string, so a re-worded commented import cannot slip past the check.
        assert re.search(r"(?m)^\s*#\s*from\s+swarmflow\s+import", desc) is None
