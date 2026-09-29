# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""F_15 trial switch (OPENJIUWEN_BROWSER_GUARDS=lean) against today's strict guards."""

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime import cart_verification as cart
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import unresolved_writes
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import (
    call,
    cart_runtime,
    cart_state,
    session_for,
)

# Real shape from 2026-09-29: 65 of 66 click timeouts stopped here, before "performing click action".
NEVER_PERFORMED = (
    "locator.click: Timeout 2500ms exceeded.\nCall log:\n  - waiting for locator('#add').first()\n"
    "  - locator resolved to <button>Add to cart</button>\n  - attempting click action\n"
    "  - waiting for element to be visible, enabled and stable"
)
PERFORMED = NEVER_PERFORMED + "\n  - element is visible, enabled and stable\n  - performing click action"


@pytest.fixture(params=["strict", "lean"])
def guards(request, monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_BROWSER_GUARDS", request.param)
    return request.param


def no_contract_state():
    state, _ = cart_state()
    state["phase_requirements"] = []
    return state


def test_add_to_cart_without_a_cart_contract(guards):
    # Lazada: every Add to cart was refused although the user was logged in.
    state = no_contract_state()
    session = session_for(state)
    if guards == "strict":
        with pytest.raises(ValueError, match="cart_baseline_required"):
            journal.prepare(session, call("jev_add"), cart_runtime({}), effect_adapter=cart.prepare_effects)
        return
    journal.prepare(session, call("jev_add"), cart_runtime({}), effect_adapter=cart.prepare_effects)
    assert state["execution_journal"][0]["cart_mutation"] is False


def test_click_timed_out_before_being_performed_is_not_an_uncertain_write(guards):
    state = no_contract_state()
    state["goal"] = "open the product page"  # No cart wording: isolate the timeout rule.
    session = session_for(state)
    first = call("llm_click")
    journal.prepare(session, first, cart_runtime({}))
    journal.record_result(session, first, {"success": False},
                          {"steps": [{"index": 0, "ok": False, "error": NEVER_PERFORMED}]})
    [entry] = state["execution_journal"]
    if guards == "strict":
        assert entry["execution_state"] == "dispatched_unknown" and unresolved_writes(state)
        with pytest.raises(ValueError, match="browser_write_requires_reconciliation"):
            journal.prepare(session, call("llm_click_again"), cart_runtime({}))
        return
    assert entry["execution_state"] == "rejected_before_dispatch" and not unresolved_writes(state)
    journal.prepare(session, call("llm_click_again"), cart_runtime({}))  # Not locked.


def test_click_that_was_performed_then_timed_out_stays_uncertain(guards):
    # The double-add risk the reconciliation lock exists for is kept in both modes.
    state = no_contract_state()
    state["goal"] = "open the product page"
    session = session_for(state)
    first = call("llm_click")
    journal.prepare(session, first, cart_runtime({}))
    journal.record_result(session, first, {"success": False},
                          {"steps": [{"index": 0, "ok": False, "error": PERFORMED}]})
    assert state["execution_journal"][0]["execution_state"] == "dispatched_unknown" and unresolved_writes(state)
    with pytest.raises(ValueError, match="browser_write_requires_reconciliation"):
        journal.prepare(session, call("llm_click_again"), cart_runtime({}))
