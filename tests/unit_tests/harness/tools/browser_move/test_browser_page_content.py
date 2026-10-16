# coding: utf-8

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime.page_content import fingerprint_snapshot

SNAPSHOT = """- main [ref=e1]:
  - article [ref=e2]:
    - link "Pinned tender" [ref=e3]:
      - /url: /pinned
  - article [ref=e4]:
    - link "Tender 1" [ref=e5]:
      - /url: /tender/1
    - paragraph: 2026-10-01
- button "Filters" [ref=e6] [expanded]
"""


@pytest.mark.parametrize(
    "wrap",
    [
        lambda text: text,
        lambda text: f"```yaml\n{text}```",
        lambda text: f"### Page\n- Page URL: https://example.test\n### Snapshot\n```yaml\n{text}```",
        lambda text: f"### Page state\n- Page Snapshot:\n```yaml\n{text}```\n### Events\n- Logged at 12:05",
        lambda text: {"snapshot": text},
        lambda text: json.dumps({"snapshot": text, "events": "Logged at 12:06"}),
        lambda text: {"content": [{"type": "text", "text": text}]},
        lambda text: {"result": {"data": {"text": text}}},
        lambda text: {"__browser_compact_rpc__": True, "payload": {"snapshot": text}},
    ],
)
def test_snapshot_envelopes_have_identical_content_fingerprints(wrap) -> None:
    expected = fingerprint_snapshot(SNAPSHOT)
    assert expected is not None and len(expected) == 64
    assert fingerprint_snapshot(wrap(SNAPSHOT)) == expected


def test_reference_and_yaml_format_changes_do_not_change_content() -> None:
    other = SNAPSHOT.replace("[ref=e", "[ref=frame9.e").replace("  ", "    ").replace("\n", "\r\n")
    assert fingerprint_snapshot(other) == fingerprint_snapshot(SNAPSHOT)
    assert fingerprint_snapshot('- paragraph: "2026-10-01"') == fingerprint_snapshot("- paragraph: 2026-10-01")
    assert fingerprint_snapshot('- paragraph: "yes"') == fingerprint_snapshot("- paragraph: yes")


@pytest.mark.parametrize(
    ("old", "new"),
    [("Tender 1", "Tender 2"), ("/tender/1", "/tender/2"), ("2026-10-01", "2026-10-02"), ("[expanded]", "")],
)
def test_later_result_and_control_changes_remain_visible(old: str, new: str) -> None:
    assert fingerprint_snapshot(SNAPSHOT.replace(old, new)) != fingerprint_snapshot(SNAPSHOT)


def test_complete_content_beyond_card_and_text_limits_is_hashed() -> None:
    cards = [f'- article: "Tender {index} {"x" * 2000}"' for index in range(25)]
    before = "\n".join(cards)
    cards[-1] = cards[-1][:-1] + ' updated"'
    assert len(before) > 12_000
    assert fingerprint_snapshot(before) is not None
    assert fingerprint_snapshot(before) != fingerprint_snapshot("\n".join(cards))


@pytest.mark.parametrize("result_index", [0, 1, 8])
def test_first_second_and_ninth_results_preserve_text_beyond_metadata_limit(result_index: int) -> None:
    prefix = "Tender description " * 20
    cards = [f"- article: {json.dumps(f'{prefix}result {index} original')}" for index in range(9)]
    before = "\n".join(cards)
    cards[result_index] = cards[result_index].replace("original", "updated")

    assert len(prefix) > 240
    assert fingerprint_snapshot(before) is not None
    assert fingerprint_snapshot("\n".join(cards)) != fingerprint_snapshot(before)


@pytest.mark.parametrize(
    "snapshot",
    [
        '- button "Tender [ref=e1]" [ref=e10]',
        '- paragraph: "Keep [ref=e1] literally"',
        '- link "Tender" [ref=e10]:\n  - /url: /tender?ref=e1',
        '- button "Escaped \\" [ref=e1]" [ref=e10]',
    ],
)
def test_reference_like_page_text_is_preserved(snapshot: str) -> None:
    before = fingerprint_snapshot(snapshot)
    assert before is not None
    assert fingerprint_snapshot(snapshot.replace("e1", "e2")) != before


def test_control_states_values_order_and_hierarchy_are_preserved() -> None:
    for before, after in [
        ('- checkbox "Accept" [checked]', '- checkbox "Accept"'),
        ('- option "A" [selected]', '- option "A"'),
        ('- textbox "Query": old', '- textbox "Query": new'),
        ("- paragraph: A\n- paragraph: B", "- paragraph: B\n- paragraph: A"),
        ('- main:\n  - button "Go"', '- main\n- button "Go"'),
        ('- paragraph: "a  b"', '- paragraph: "a b"'),
    ]:
        assert fingerprint_snapshot(before) is not None
        assert fingerprint_snapshot(before) != fingerprint_snapshot(after)


def test_page_text_that_looks_like_response_framing_is_preserved() -> None:
    snapshot = "- paragraph: |\n    ### Error\n    ```yaml\n    [ref=e1]\n    ```\n"
    assert fingerprint_snapshot(snapshot) is not None
    assert fingerprint_snapshot(snapshot.replace("Error", "Success")) != fingerprint_snapshot(snapshot)


def test_structured_json_preserves_content_but_ignores_node_refs_and_key_order() -> None:
    tree = {
        "role": "document",
        "ref": "e1",
        "children": [{"role": "link", "name": "Tender [ref=e2]", "ref": "e2", "href": "/tender/1"}],
    }
    other = dict(reversed(list(deepcopy(tree).items())))
    other["ref"] = "e100"
    other["children"][0]["ref"] = "e101"
    assert fingerprint_snapshot(tree) == fingerprint_snapshot({"snapshot": json.dumps(other)})
    other["children"][0]["href"] = "/tender/2"
    assert fingerprint_snapshot(tree) != fingerprint_snapshot(other)


@pytest.mark.parametrize("snapshot", ["", "\n  \n", "[]", [], {"snapshot": ""}, "### Snapshot\n```yaml\n```"])
def test_successfully_captured_empty_content_has_a_stable_fingerprint(snapshot) -> None:
    assert fingerprint_snapshot(snapshot) == fingerprint_snapshot("")
    assert fingerprint_snapshot(snapshot) is not None


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {"content": []},
        {"isError": True, "snapshot": SNAPSHOT},
        {"ok": False, "snapshot": SNAPSHOT},
        {"error": "timeout", "snapshot": SNAPSHOT},
        {"snapshot": {"file": "page.yml"}},
        "### Snapshot\n- [Snapshot](page.yml)",
        '### Error\nTimeout\n### Snapshot\n```yaml\n- button "Old"\n```',
        "### Page\n- Page URL: https://example.test",
        "### Snapshot\n```yaml\n- button",
        "### Snapshot\n```yaml\n- button\n```\n### Snapshot\n```yaml\n- link\n```",
        "- main:\n    - button\n   - link",
        "Could not capture snapshot",
        "# Missing snapshot",
        '{"snapshot":',
        "- &node main:\n    - *node",
    ],
)
def test_unavailable_captures_do_not_get_a_content_fingerprint(snapshot) -> None:
    assert fingerprint_snapshot(snapshot) is None


def test_recursive_transport_and_json_trees_are_unavailable() -> None:
    envelope = {}
    envelope["snapshot"] = envelope
    assert fingerprint_snapshot(envelope) is None
    tree = {"role": "document"}
    tree["children"] = [tree]
    assert fingerprint_snapshot(tree) is None
