"""Registered extension CLI dispatches exact scopes through stage supervision."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import extension_experiment
from examples.adaptive_multi_agent_collab.infrastructure import shutdown


def test_extension_adapt_forwards_explicit_scope(monkeypatch, capsys):
    received = {}

    async def adapt(registration, output, **kwargs):
        received.update(registration=registration, output=output, **kwargs)
        return {"accepted": False}

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(extension_experiment, "adapt_extension", adapt)
    monkeypatch.setattr(shutdown, "run_stage", asyncio.run)
    assert cli.main(["extension-adapt", "registration", "--output", "target", "--resume", "checkpoint",
        "--authorization", "extension-approval", "--development-authorization", "development-approval",
        "--escalation-scope", "declared-scope", "--escalation-authorization", "scope-approval"]) == 1
    assert received == {"registration":"registration", "output":"target", "resume":"checkpoint",
        "authorization":"extension-approval", "development_authorization":"development-approval",
        "escalation_scope":"declared-scope", "escalation_authorization":"scope-approval"}
    assert '"accepted": false' in capsys.readouterr().out


@pytest.mark.parametrize("complete", [False, True])
def test_extension_test_requires_checkpoint_and_separate_approval(monkeypatch, capsys, complete):
    received = {}

    async def evaluate(registration, checkpoint, output, **kwargs):
        received.update(registration=registration, checkpoint=checkpoint, output=output, **kwargs)
        return {"stage_complete": complete}

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(extension_experiment, "evaluate_extension", evaluate)
    monkeypatch.setattr(shutdown, "run_stage", asyncio.run)
    assert cli.main(["extension-test", "registration", "--checkpoint", "selected", "--output", "target",
        "--authorization", "test-approval"]) == (0 if complete else 1)
    assert received == {"registration":"registration", "checkpoint":"selected", "output":"target",
                        "authorization":"test-approval"}
    assert '"stage_complete": ' + str(complete).lower() in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["extension-test", "registration", "--checkpoint", "selected",
                                      "--output", "target"])


def test_prospective_scope_preflight_keeps_explicit_approval(tmp_path, monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab.operations import preflight
    approval = tmp_path / "scope.json"
    approval.write_text(json.dumps({"decision":"approve", "scope_name":"declared"}))
    received = {}

    def inspect(specification, **kwargs):
        received.update(specification=specification, **kwargs)
        return SimpleNamespace(ready=False, to_dict=lambda:{"ready":False})

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(preflight, "inspect_preflight", inspect)
    assert cli.main(["preflight", "manifest", "--operation", "train", "--checkpoint", "selected",
        "--extension-scope", "declared", "--extension-scope-authorization", str(approval)]) == 2
    assert received["extension_scope"] == "declared"
    assert received["extension_scope_authorization"] == {"decision":"approve", "scope_name":"declared"}
    assert '"ready": false' in capsys.readouterr().out
