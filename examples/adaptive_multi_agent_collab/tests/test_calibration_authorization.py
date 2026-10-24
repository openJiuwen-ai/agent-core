"""Calibration authorization and production CLI retain the exact bounded scope."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from examples.adaptive_multi_agent_collab.operations.preflight import CALIBRATION_SCOPE, stage_authorization_template, validate_stage_authorization


def bindings():
    return dict(stage="development", manifest_fingerprint="a"*64, condition_fingerprint="b"*64,
                resource_profile_fingerprint="c"*64, approved_budget_usd=80., pilot_fingerprint="d"*64,
                execution_scope=CALIBRATION_SCOPE, calibration_plan_sha256="e"*64,
                calibration_forecast_sha256="f"*64)


def test_exact_calibration_authority_rejects_changed_bindings_and_broader_scope():
    expected=bindings()
    artifact=stage_authorization_template(**expected)
    with pytest.raises(ValueError, match="explicit approval"):
        validate_stage_authorization(artifact, **expected)
    artifact.update(decision="approve_stage",reviewer="offline fixture",rationale="no production authority")
    assert validate_stage_authorization(artifact, **expected)==artifact
    for key in ("condition_fingerprint","resource_profile_fingerprint","calibration_plan_sha256","calibration_forecast_sha256"):
        with pytest.raises(ValueError, match="differs"):
            validate_stage_authorization({**artifact,key:"0"*64},**expected)
    with pytest.raises(ValueError):
        validate_stage_authorization(artifact,**{**expected,"execution_scope":"development_campaign"})


@pytest.mark.parametrize("change",[
    {"calibration_plan_sha256":None},{"calibration_forecast_sha256":None},
    {"calibration_plan_sha256":"bad"},{"bootstrap_forecast_sha256":"1"*64},
    {"supplement_plan_sha256":"1"*64},{"execution_scope":"development_campaign"},
])
def test_partial_or_unrelated_calibration_bindings_rejected(change):
    with pytest.raises(ValueError): stage_authorization_template(**(bindings()|change))


def test_cli_and_production_wrapper_forward_calibration_scope(monkeypatch,capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.infrastructure import shutdown
    received=[]
    async def execute(specification,output,**kwargs):
        received.append((specification,output,kwargs))
        return {"phase":"weighting","stage_complete":True,"execution_scope":CALIBRATION_SCOPE}
    monkeypatch.setattr(cli,"configure_logging",lambda:None)
    monkeypatch.setattr(experiment,"_execute_stage",execute)
    monkeypatch.setattr(shutdown,"run_stage",asyncio.run)
    assert cli.main(["train","fixture.json","--output","campaign","--resume","checkpoint.pt",
                     "--authorization","auth.json","--calibration-only","--calibration-plan","plan.json",
                     "--calibration-forecast","forecast.json"])==0
    call=received[0][2]
    assert call["calibration_only"] is True and call["stage"]=="development"
    assert call["calibration_plan_path"]=="plan.json" and call["calibration_forecast_path"]=="forecast.json"
    assert call["bootstrap_only"] is False and call.get("supplement_plan_path") is None
    assert len(received)==1


def test_preflight_cli_forwards_actual_checkpoint_and_scope(monkeypatch,capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import preflight
    received=[]
    def inspect(specification,**kwargs):
        received.append(kwargs)
        return SimpleNamespace(ready=False,to_dict=lambda:{"ready":False})
    monkeypatch.setattr(cli,"configure_logging",lambda:None)
    monkeypatch.setattr(preflight,"inspect_preflight",inspect)
    assert cli.main(["preflight","fixture.json","--operation","train","--checkpoint","checkpoint.pt",
                     "--authorization","auth.json","--calibration-only","--calibration-plan","plan.json",
                     "--calibration-forecast","forecast.json"])==2
    assert received[0]["checkpoint_path"]=="checkpoint.pt"
    assert received[0]["calibration_only"] and received[0]["calibration_plan_path"]=="plan.json"


@pytest.mark.parametrize("kwargs",[
    {"calibration_only":True}, {"calibration_plan_path":"plan.json"},
    {"calibration_only":True,"calibration_plan_path":"plan.json","calibration_forecast_path":"forecast.json","bootstrap_only":True,"bootstrap_forecast_path":"b.json"},
    {"calibration_only":True,"calibration_plan_path":"plan.json","calibration_forecast_path":"forecast.json","operation":"pilot"},
])
def test_incomplete_or_mixed_calibration_invocations_rejected_before_io(kwargs):
    from examples.adaptive_multi_agent_collab.operations.preflight import inspect_preflight
    with pytest.raises(ValueError,match="calibration-only"):
        inspect_preflight("must-not-read.json",**kwargs)
