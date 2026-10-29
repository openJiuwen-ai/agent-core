"""Candidate content must reach the model for every disclosure prompt variant."""

import json

import pytest

from openjiuwen.symphony.retrieval.common.models import RetrieverItem, RetrieverNode
from openjiuwen.symphony.retrieval.search.runtime.render.disclosure import (
    DisclosureConfig,
    build_disclosure_prompt_parts,
    build_exposed_fragment,
)


@pytest.mark.parametrize("top_k", [1, 2, 5])
@pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
@pytest.mark.parametrize("compact", [False, True], ids=["names", "codes"])
def test_full_messages_include_each_candidate_once(top_k, flat, compact):
    root = RetrieverNode(
        node_id="root",
        label="Root",
        items=(
            RetrieverItem(
                item_id="extract-alpha",
                payload="custom.capability_amber",
                label="Extraction label",
                description="Extract the document envelope into plain text.",
            ),
            RetrieverItem(
                item_id="summarize-beta",
                payload="custom.capability_cobalt",
                label="Summary label",
                description="Summarize the extracted text in English.",
            ),
        ),
    )
    fragment = build_exposed_fragment(
        root=root,
        branch_path=(root.node_id,),
        config=DisclosureConfig(
            max_exposure_depth_per_call=99,
            exposure_threshold=999,
            compact_boundary_codes_enabled=compact,
            compact_boundary_codebook=("ZX", "QJ"),
            flatten_full_tree_in_prompt=flat,
        ),
        subtree_item_count=lambda node: len(node.items),
    )
    query = "Extract my document and summarize its text."
    parts = build_disclosure_prompt_parts(
        fragment=fragment,
        query_messages=[{"role": "user", "content": query}],
        top_k=top_k,
    )
    content = "\n".join(message["content"] for message in parts.full_messages)

    assert content.count(fragment.rendered_tree) == 1
    assert content.count(query) == 1
    for item in root.items:
        assert content.count(item.description) == 1
    if compact and flat:
        for candidate in json.loads(fragment.rendered_tree):
            assert content.count(candidate["name"]) == 1
            assert content.count(f'"id": "{candidate["id"]}"') == 1
    else:
        for resolution in fragment.code_to_resolution.values():
            assert content.count(resolution.display_name) == 1
            if compact:
                assert content.count(f"[id: {resolution.code}]") == 1

    # Prefix-cache clients and ordinary clients receive the same candidates.
    prefix = "\n".join(message["content"] for message in parts.prefix_messages)
    assert prefix.count(fragment.rendered_tree) == 1
