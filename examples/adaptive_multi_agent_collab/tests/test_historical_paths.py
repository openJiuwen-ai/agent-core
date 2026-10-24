"""Historical fixture resolution supports byte-preserving campaign archival."""

from pathlib import Path

import pytest

from examples.adaptive_multi_agent_collab.tests.historical import historical_campaign_path


def test_existing_historical_source_is_preferred(tmp_path: Path) -> None:
    source = tmp_path / "runs/strategyqa/checkpoint.pt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    archived = tmp_path / "archive/Pilot_1/runs/strategyqa/checkpoint.pt"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"archived")
    assert historical_campaign_path(source, root=tmp_path) == source


@pytest.mark.parametrize("absolute", [False, True])
def test_moved_historical_fixture_resolves_archive(tmp_path: Path, absolute: bool) -> None:
    relative = Path("runs/strategyqa/conditions/old/checkpoint.pt")
    archived = tmp_path / "archive/Pilot_1" / relative
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"unchanged historical bytes")
    requested = tmp_path / relative if absolute else relative
    assert historical_campaign_path(requested, root=tmp_path) == archived
    assert historical_campaign_path(requested, root=tmp_path).read_bytes() == b"unchanged historical bytes"


def test_missing_optional_historical_evidence_remains_missing(tmp_path: Path) -> None:
    requested = Path("runs/strategyqa/pilot_record.json")
    resolved = historical_campaign_path(requested, root=tmp_path)
    assert resolved == tmp_path / requested
    assert not resolved.exists()
