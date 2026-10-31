"""Narrow supplementary authority cannot turn into later development scope."""
import pytest
from examples.adaptive_multi_agent_collab.cli import build_parser
from examples.adaptive_multi_agent_collab.operations.preflight import (
    SUPPLEMENT_SCOPE, stage_authorization_template, validate_stage_authorization,
)


def bindings():
    return dict(stage="development", manifest_fingerprint="a" * 64,
                condition_fingerprint="b" * 64, resource_profile_fingerprint="c" * 64,
                approved_budget_usd=80.0, pilot_fingerprint="d" * 64,
                execution_scope=SUPPLEMENT_SCOPE, supplement_plan_sha256="e" * 64,
                supplement_forecast_sha256="f" * 64)


def test_supplement_authority_requires_exact_plan_forecast_and_scope():
    expected = bindings()
    artifact = stage_authorization_template(**expected)
    with pytest.raises(ValueError, match="explicit approval"):
        validate_stage_authorization(artifact, **expected)
    artifact.update(decision="approve_stage", reviewer="offline test", rationale="synthetic fixture only")
    assert validate_stage_authorization(artifact, **expected) == artifact
    for key in ("condition_fingerprint", "supplement_plan_sha256", "supplement_forecast_sha256", "pilot_fingerprint"):
        with pytest.raises(ValueError, match="differs"):
            validate_stage_authorization({**artifact, key: "0" * 64}, **expected)
    with pytest.raises(ValueError):
        validate_stage_authorization({**artifact, "scope": "development_campaign"}, **expected)


@pytest.mark.parametrize("changes", [
    {"supplement_plan_sha256": None}, {"supplement_forecast_sha256": None},
    {"bootstrap_forecast_sha256": "1" * 64}, {"execution_scope": "development_campaign"},
])
def test_no_partial_or_unrelated_supplement_bindings(changes):
    with pytest.raises(ValueError):
        stage_authorization_template(**(bindings() | changes))


def test_cli_preserves_explicit_supplement_flags():
    parser = build_parser()
    args = parser.parse_args(["train", "manifest.json", "--output", "campaign", "--resume", "checkpoint.pt",
                              "--authorization", "authorization.json", "--supplement-plan", "plan.json",
                              "--supplement-forecast", "forecast.json"])
    assert args.supplement_plan == "plan.json" and not args.bootstrap_only
    preflight = parser.parse_args(["preflight", "manifest.json", "--operation", "train",
                                   "--supplement-plan", "plan.json", "--supplement-forecast", "forecast.json"])
    assert preflight.supplement_forecast == "forecast.json"
