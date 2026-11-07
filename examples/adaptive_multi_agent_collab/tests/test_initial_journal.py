"""The initialization-only publication exception cannot resume executed work."""

import asyncio

import pytest

from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal, PersistenceFailure
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger


@pytest.mark.parametrize("mutation", ["ledger", "progress", "halt", "entry", "policy"])
def test_empty_initialization_cannot_rebind_execution_or_mutated_state(tmp_path, mutation):
    state = ExpenditureLedger(Config().resources).state_dict()
    journal = DurableJournal([tmp_path], state)
    if mutation == "ledger":
        journal.changed({key: value for key, value in state.items() if key != "entries"}, [])
    elif mutation == "progress":
        asyncio.run(journal.progress("workflow", {"started": True}))
    else:
        state["halted" if mutation == "halt" else "entries" if mutation == "entry" else "failure_policy"] = True
    asyncio.run(journal.aclose())
    with pytest.raises(PersistenceFailure, match="unfinished"):
        DurableJournal([tmp_path], state, allow_empty_initialization=True)


def test_empty_initialization_needs_explicit_permission_and_preserves_wal(tmp_path):
    state = ExpenditureLedger(Config().resources).state_dict()
    journal = DurableJournal([tmp_path], state)
    asyncio.run(journal.aclose())
    raw = (tmp_path / "accounting_progress.jsonl").read_bytes()
    with pytest.raises(PersistenceFailure, match="unfinished"):
        DurableJournal([tmp_path], state)
    recovered = DurableJournal([tmp_path], state, allow_empty_initialization=True)
    asyncio.run(recovered.aclose())
    assert (tmp_path / "accounting_progress.jsonl").read_bytes() == raw
