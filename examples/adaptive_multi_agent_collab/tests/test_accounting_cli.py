"""Saved-receipt settlement has no credential or network prerequisite."""

import json

import pytest

from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import reconciliation


def test_saved_settlement_cli_uses_only_local_evidence(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    received = []

    async def settle(campaign, audit, **kwargs):
        received.append((campaign, audit, kwargs))
        return {"fully_resolved": False, "unresolved_dispatch_ids": [400], "additional_lookup_attempts": 9}

    monkeypatch.setattr(reconciliation, "settle_cached_receipts", settle)
    result = cli.main(
        [
            "settle-accounting",
            "--campaign",
            "campaign",
            "--audit",
            "audit",
            "--model-catalog",
            "models.json",
            "--endpoint-catalog",
            "openai/gpt-4.1-mini=openai.json",
            "--endpoint-catalog",
            "anthropic/claude-sonnet-4.6=anthropic.json",
        ]
    )
    assert result == 1
    assert received == [
        (
            "campaign",
            "audit",
            {
                "model_catalog_path": "models.json",
                "endpoint_catalog_paths": {
                    "openai/gpt-4.1-mini": "openai.json",
                    "anthropic/claude-sonnet-4.6": "anthropic.json",
                },
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["unresolved_dispatch_ids"] == [400]


@pytest.mark.parametrize("endpoints", [["missing-separator"], ["model=first.json", "model=second.json"]])
def test_saved_settlement_cli_rejects_ambiguous_inputs_before_operation(monkeypatch, endpoints):
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid evidence must not reach settlement")

    monkeypatch.setattr(reconciliation, "settle_cached_receipts", forbidden)
    args = ["settle-accounting", "--campaign", "campaign", "--audit", "audit", "--model-catalog", "models.json"]
    for endpoint in endpoints:
        args.extend(["--endpoint-catalog", endpoint])
    with pytest.raises(ValueError, match="unique MODEL=PATH"):
        cli.main(args)


def test_accounting_cli_only_dispatches_bounded_metadata_function(tmp_path, monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import reconciliation
    from examples.adaptive_multi_agent_collab.providers import gateway

    authorization = tmp_path / "authorization.json"
    authorization.write_text('{"fixture":true}')
    seen = {}

    async def reconcile(campaign, audit, **kwargs):
        seen.update(kwargs)
        assert campaign == "fixture-campaign" and audit == "fixture-audit"
        return {"fully_resolved": False}

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(reconciliation, "reconcile_campaign", reconcile)
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-only")
    assert (
        cli.main(
            [
                "reconcile-accounting",
                "--campaign",
                "fixture-campaign",
                "--audit",
                "fixture-audit",
                "--authorization",
                str(authorization),
                "--disposition",
                "retain original evidence",
                "--dispatch-id", "10", "--dispatch-id", "11",
            ]
        )
        == 1
    )
    assert seen["dispatch_ids"] == (10, 11)
    assert seen["lookup"].func is gateway.lookup_generation_metadata
    assert seen["authorization"] == {"fixture": True}
    assert json.loads(capsys.readouterr().out)["fully_resolved"] is False
