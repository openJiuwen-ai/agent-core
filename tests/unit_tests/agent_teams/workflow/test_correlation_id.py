# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Correlation ID: ``_branch_disambig`` encoding and ``_correlation_id`` with disambig."""

from openjiuwen.agent_teams.workflow.engine.primitives import _branch_disambig
from openjiuwen.agent_teams.workflow.engine import primitives as _p


def test_disambig_no_branch():
    assert _branch_disambig(()) == ""
    assert _branch_disambig((("call", 0),)) == ""
    assert _branch_disambig((("wf", 0, "intro"),)) == ""


def test_disambig_single_parallel():
    assert _branch_disambig((("par", 5, 0),)) == "p0"
    assert _branch_disambig((("par", 5, 2),)) == "p2"


def test_disambig_single_pipeline():
    assert _branch_disambig((("pipe", 3, 0, 1),)) == "i0s1"


def test_disambig_nested_par_in_pipe():
    path = (("pipe", 0, 0, 1), ("par", 0, 0))
    assert _branch_disambig(path) == "i0s1.p0"
    path2 = (("pipe", 0, 0, 1), ("par", 0, 1))
    assert _branch_disambig(path2) == "i0s1.p1"


def test_disambig_skips_call_and_wf_segments():
    path = (("par", 1, 0), ("wf", 0, "sub"), ("par", 0, 1), ("call", 0))
    assert _branch_disambig(path) == "p0.p1"


class _FakeSession:
    """Minimal stand-in for AgentSession to test _correlation_id in isolation."""
    _history = []
    _correlation_id = _p.AgentSession._correlation_id


def _set_path(path):
    _p._path.set(path)


def _make_sess():
    return _FakeSession()


def test_corr_serial_no_branch():
    _set_path(())
    s = _make_sess()
    assert s._correlation_id({"phase": "review", "label": "reviewer"}) == "review:reviewer:0"


def test_corr_single_parallel():
    _set_path((("par", 5, 0),))
    s = _make_sess()
    assert s._correlation_id({"phase": "review", "label": "reviewer"}) == "review#p0:reviewer:0"


def test_corr_three_parallel_unique():
    _set_path((("par", 5, 0),))
    s0 = _make_sess()
    c0 = s0._correlation_id({"phase": "review", "label": "reviewer"})
    _set_path((("par", 5, 1),))
    s1 = _make_sess()
    c1 = s1._correlation_id({"phase": "review", "label": "reviewer"})
    _set_path((("par", 5, 2),))
    s2 = _make_sess()
    c2 = s2._correlation_id({"phase": "review", "label": "reviewer"})
    assert len({c0, c1, c2}) == 3
    assert c0 == "review#p0:reviewer:0" and c1 == "review#p1:reviewer:0" and c2 == "review#p2:reviewer:0"


def test_corr_subworkflow_human_always_spliced():
    # Inside sub-workflow, phase = _current_phase (set by inner phase()), not display name.
    _set_path((("par", 5, 0), ("wf", 0, "review")))
    s = _make_sess()
    corr = s._correlation_id({"phase": "peer-review", "label": "reviewer"})
    assert corr == "peer-review#p0:reviewer:0"


def test_corr_nested_fanout_serial_concat():
    _set_path((("par", 5, 0), ("wf", 0, "review"), ("par", 0, 0)))
    s0 = _make_sess()
    c0 = s0._correlation_id({"phase": "peer-review", "label": "reviewer"})
    _set_path((("par", 5, 0), ("wf", 0, "review"), ("par", 0, 1)))
    s1 = _make_sess()
    c1 = s1._correlation_id({"phase": "peer-review", "label": "reviewer"})
    assert c0 == "peer-review#p0.p0:reviewer:0"
    assert c1 == "peer-review#p0.p1:reviewer:0"
    assert c0 != c1


def test_corr_pipeline_branch():
    _set_path((("pipe", 3, 0, 1),))
    s = _make_sess()
    assert s._correlation_id({"phase": "review", "label": "reviewer"}) == "review#i0s1:reviewer:0"
