"""Retired policy workflows cannot regain execution through current entry points."""

import json
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.operations import policy_activation as policy


@pytest.mark.parametrize("protocol", ["bounded_failure_policy_plan_v1", "unknown"])
def test_retired_policy_plan_rejects_before_activation_or_mutation(tmp_path, monkeypatch, protocol):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"protocol": protocol}))
    before = plan.read_bytes()
    monkeypatch.setattr(
        policy, "_activate_independent_policy",
        lambda *_args: pytest.fail("a retired plan reached independent activation"),
    )
    with pytest.raises(ValueError, match="retired"):
        policy.activate_policy_plan(plan, tmp_path / "authorization.json")
    assert plan.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plan.json"]


@pytest.mark.parametrize("lineage", ["policy_transition", "revised_condition"])
@pytest.mark.parametrize("independent", [False, True])
def test_retired_or_mixed_lineage_rejects_all_current_policy_paths(tmp_path, monkeypatch, lineage, independent):
    specification = {lineage: {"historical": True}}
    if independent:
        specification["independent_campaign"] = {"policy_plan_path": "must-not-be-read.json"}
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(specification))
    before = source.read_bytes()
    monkeypatch.setattr(
        policy, "_validate_independent_activation",
        lambda *_args: pytest.fail("historical lineage reached current activation validation"),
    )
    operations = (
        lambda: policy.validate_policy_condition(specification, tmp_path),
        lambda: policy.registered_policy_directory(source),
        lambda: policy.bind_policy_dispatch(SimpleNamespace(), source, lambda: True),
        lambda: policy.policy_response_identity(SimpleNamespace(), source),
        lambda: policy.policy_compatibility_evidence(source),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="retired"):
            operation()
    assert source.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["manifest.json"]


def test_path_rebasing_preserves_plain_model_and_prompt_strings(tmp_path):
    """Retained campaign/extension helper, migrated from the retired workflow suite."""
    value = {"status": "complete", "prompt": "complete", "source_files": ["data.json"], "path": "receipt.json"}
    result = policy._absolute_paths(value, tmp_path)
    assert result["status"] == result["prompt"] == "complete"
    assert result["source_files"] == [str(tmp_path / "data.json")]
    assert result["path"] == str(tmp_path / "receipt.json")
