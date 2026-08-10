# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for templated tool descriptions (``tools/locales``).

A ``_desc`` Markdown file may declare ``{{slot}}`` placeholders filled from
shared fragments. The whole point of the mechanism is that a missing fragment
fails loudly at tool-construction time instead of shipping a raw ``{{slot}}``
literal to the model, so most of these tests are about the failure modes.
"""

import pytest

from openjiuwen.agent_teams.tools.locales import (
    _load_fragment,
    _slots_of,
    make_translator,
)

_SLOTTED = (
    "send_message",
    "send_message_scheduled",
    "create_task",
    "create_task_scheduled",
    "update_task",
    "build_team",
)
_SLOTLESS = ("view_task", "claim_task")

#: Capability slots and the description each one is declared in. A capability
#: slot is the only kind a caller may omit, so these are the pairs where the
#: rendered spacing has to hold with the slot both on and off.
_CAPABILITY_SLOTS = (
    ("spawn_teammate", "fork_usage"),
    ("update_task", "update_task_verify_gate"),
    ("build_team", "build_team_verify_gate"),
)


@pytest.mark.level0
def test_slots_of_extracts_ordered_unique_names():
    """Slot extraction is ordered and de-duplicated."""
    assert _slots_of("a {{x}} b {{y}} c {{x}}") == ("x", "y")
    assert _slots_of("no placeholders here") == ()


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("tool", _SLOTLESS)
def test_slotless_descriptions_render_verbatim(lang, tool):
    """A description without slots is returned untouched (no regression)."""
    t = make_translator(lang)
    desc = t(tool)
    assert desc
    assert "{{" not in desc


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("desc_key", _SLOTTED)
def test_slotted_descriptions_are_fully_filled(lang, desc_key):
    """Every declared slot resolves; no placeholder survives."""
    t = make_translator(lang)
    desc = t(desc_key)
    assert "{{" not in desc, f"unresolved placeholder in {desc_key}/{lang}"
    # The shared handoff fragment is the one both send_message variants pull in.
    if desc_key.startswith("send_message"):
        assert ".team/" in desc


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
def test_variants_share_a_fragment_verbatim(lang):
    """Both send_message variants embed the identical shared fragment."""
    t = make_translator(lang)
    fragment = _load_fragment("artifact_handoff_policy", lang)
    assert fragment in t("send_message")
    assert fragment in t("send_message_scheduled")


@pytest.mark.level0
def test_missing_fragment_raises_with_expected_path():
    """A missing fragment names the file it looked for — no silent fallback."""
    with pytest.raises(FileNotFoundError) as exc:
        _load_fragment("definitely_not_a_fragment", "cn")
    message = str(exc.value)
    assert "definitely_not_a_fragment" in message
    assert "fragments" in message


@pytest.mark.level0
def test_missing_description_raises():
    """A tool with neither Markdown nor a STRINGS entry fails at construction."""
    t = make_translator("cn")
    with pytest.raises(FileNotFoundError):
        t("no_such_tool_at_all")


@pytest.mark.level0
def test_missing_param_key_raises():
    """A missing parameter key is a KeyError, not an empty string."""
    t = make_translator("cn")
    with pytest.raises(KeyError):
        t("create_task", "task.no_such_param")


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
def test_param_descriptions_are_shared_across_variants(lang):
    """Variants reuse the same parameter strings — reuse costs no mechanism."""
    t = make_translator(lang)
    # Same key namespace: the scheduled create_task variant reads these too.
    assert t("create_task", "task.title")
    assert t("create_task", "task.assignee")
    # send_message_scheduled redefines only ``to``; content/summary are reused.
    assert t("send_message_scheduled", "to") != t("send_message", "to")


@pytest.mark.level0
def test_runtime_error_strings_still_interpolate():
    """STRINGS values keep their ``{key}`` / format_map path (runtime errors)."""
    t = make_translator("cn")
    rendered = t("update_task", "error_human_agent_locked_edit", task_id="T-42")
    assert "T-42" in rendered


# ---------------------------------------------------------------------------
# Capability slots: omitting one must leave no trace of where it sat
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize(("desc_key", "slot"), _CAPABILITY_SLOTS)
def test_omitted_capability_slot_drops_its_fragment(lang, desc_key, slot):
    """Omitting a capability slot removes its prose and leaks no placeholder."""
    t = make_translator(lang)
    fragment = _load_fragment(slot, lang)

    assert fragment in t(desc_key)
    rendered = t(desc_key, omit=frozenset({slot}))
    assert fragment not in rendered
    assert "{{" not in rendered


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize(("desc_key", "slot"), _CAPABILITY_SLOTS)
def test_omitted_capability_slot_leaves_no_blank_hole(lang, desc_key, slot):
    """A description reads the same with a slot omitted as if it never existed.

    Templates put a blank line on each side of a section-level slot, so
    collapsing one to an empty string would leave a two-blank-line hole in the
    middle of the prose. The model reads this text raw — a hole is a signal
    that something was removed, which invites it to ask about a capability the
    team does not have.
    """
    t = make_translator(lang)
    for rendered in (t(desc_key), t(desc_key, omit=frozenset({slot}))):
        assert "\n\n\n" not in rendered
        assert not rendered.startswith("\n")
        assert not rendered.endswith("\n")
