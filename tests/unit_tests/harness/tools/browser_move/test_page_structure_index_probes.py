#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

from openjiuwen.harness.tools.browser_move.playwright_runtime.page_structure_index import (
    PAGE_INDEX_RUNTIME_KEY,
    PAGE_INDEX_SCHEMA_VERSION,
    PAGE_INDEX_STATE_KEY,
    build_page_index_configuration,
    build_page_index_configure_js,
    build_page_index_install_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)


def test_installer_and_both_probes_share_the_same_versioned_page_index() -> None:
    configuration = build_page_index_configuration()
    install_js = build_page_index_install_js(configuration)
    interactive_js = build_interactive_probe_js(query="search")
    card_js = build_card_probe_js(
        query="flight",
        configuration_revision=configuration["revision"],
    )

    assert PAGE_INDEX_STATE_KEY in install_js
    assert PAGE_INDEX_RUNTIME_KEY in install_js
    assert f"const SCHEMA_VERSION = {PAGE_INDEX_SCHEMA_VERSION}" in install_js
    assert "state.index = buildIndex(state)" in install_js
    assert "cache_hit" in install_js
    assert "page_version" in install_js
    assert configuration["revision"] in install_js

    for query_js in (interactive_js, card_js):
        assert PAGE_INDEX_RUNTIME_KEY in query_js
        assert "page_index_runtime_missing" in query_js
        assert "document.createTreeWalker" not in query_js
        assert "buildIndex" not in query_js


def test_page_index_uses_one_dom_traversal_and_bottom_up_aggregation() -> None:
    install_js = build_page_index_install_js()

    assert install_js.count("document.createTreeWalker") == 1
    assert "NodeFilter.SHOW_ELEMENT" in install_js
    assert "for (let index = nodes.length - 1; index >= 0; index -= 1)" in install_js
    assert "node.descendantCount += child.descendantCount" in install_js
    assert "node.structuralSignature = hashText" in install_js
    assert "node.shapeSignature = hashText" in install_js
    assert "document.querySelectorAll(selectors.join(','))" not in install_js


def test_page_index_detects_exact_and_approximate_repeated_sibling_groups() -> None:
    install_js = build_page_index_install_js()

    assert "const discoverRepeatedGroups" in install_js
    assert "index.childrenByParent" in install_js
    assert "exactBuckets" in install_js
    assert "shapeBuckets" in install_js
    assert "scoreGroup(index, ids, 'exact')" in install_js
    assert "scoreGroup(index, chosen, 'approximate')" in install_js
    assert "groupsOverlap" in install_js
    assert "candidate.score < 18" in install_js


def test_approximate_signature_tolerates_optional_repeated_child_types() -> None:
    install_js = build_page_index_install_js()

    assert "Array.from(childShapeCounts.keys())" in install_js
    assert "Array.from(childShapeCounts.entries())" not in install_js
    assert "Math.ceil(node.descendantCount / 10)" in install_js
    assert "const chosen = novel.length >= 2 ? novel : ids" in install_js


def test_card_probe_parses_only_bounded_representatives_then_compiles_paths() -> None:
    install_js = build_page_index_install_js()

    assert "const REPRESENTATIVE_LIMIT = 3" in install_js
    assert "const MAX_GROUP_ATTEMPTS = 3" in install_js
    assert "candidateGroups.slice(0, MAX_GROUP_ATTEMPTS)" in install_js
    assert "chooseRepresentativeIds" in install_js
    assert "inferGroupSchema" in install_js
    assert "relativePath" in install_js
    assert "resolveRelativePath" in install_js
    assert "schema.fields[field]" in install_js
    assert "schema.buttonPaths" in install_js
    assert "state.schemaCache[schemaKey]" in install_js
    assert "schemaReused" in install_js


def test_card_output_is_extracted_from_schema_not_reparsed_per_candidate() -> None:
    install_js = build_page_index_install_js()
    extraction_start = install_js.index("const cardFromSchema")
    extraction_end = install_js.index("const queryCards", extraction_start)
    extraction = install_js[extraction_start:extraction_end]

    assert "fieldNode(index, rootId, schema, 'title')" in extraction
    assert "fieldNode(index, rootId, schema, 'price')" in extraction
    assert "resolveRelativePath(index, rootId, path)" in extraction
    assert ".querySelectorAll(" not in extraction
    assert "descendantIds(" not in extraction


def test_selector_configuration_is_sent_once_not_embedded_in_card_query() -> None:
    profiles = [
        {
            "id": "shop",
            "domains": ["example.com"],
            "card_container_selectors": [".product-card"],
        }
    ]
    records = [
        {
            "domain": "example.com",
            "route_signature": "/",
            "kind": "card_probe",
            "selectors": {"card_container_selectors": [".cached-card"]},
        }
    ]
    configuration = build_page_index_configuration(
        site_profiles=profiles,
        selector_cache_records=records,
    )
    configure_js = build_page_index_configure_js(configuration)
    query_js = build_card_probe_js(
        configuration_revision=configuration["revision"],
    )

    assert ".product-card" in configure_js
    assert ".cached-card" in configure_js
    assert configuration["revision"] in configure_js
    assert configuration["revision"] in query_js
    assert ".product-card" not in query_js
    assert ".cached-card" not in query_js
    assert '"site_profiles"' not in query_js
    assert '"selector_cache_records"' not in query_js
    assert len(query_js) < 1500


def test_configuration_revision_ignores_cache_counters_and_timestamps() -> None:
    first = build_page_index_configuration(
        selector_cache_records=[
            {
                "domain": "example.com",
                "route_signature": "/search",
                "kind": "card_probe",
                "selectors": {"card_container_selectors": [".card"]},
                "success_count": 1,
                "last_success_at": 10,
                "quality_score": 0.5,
            }
        ]
    )
    second = build_page_index_configuration(
        selector_cache_records=[
            {
                "domain": "example.com",
                "route_signature": "/search",
                "kind": "card_probe",
                "selectors": {"card_container_selectors": [".card"]},
                "success_count": 99,
                "last_success_at": 999,
                "quality_score": 1.0,
            }
        ]
    )

    assert first["revision"] == second["revision"]
    assert "success_count" not in first["selector_cache_records"][0]
    assert "last_success_at" not in first["selector_cache_records"][0]


def test_group_identity_is_canonical_across_cache_profile_and_repeated_sources() -> None:
    install_js = build_page_index_install_js()

    assert "const canonicalGroupId" in install_js
    assert "id: canonicalGroupId(index, memberIds)" in install_js
    canonical_start = install_js.index("const canonicalGroupId")
    canonical_end = install_js.index("const scoreGroup", canonical_start)
    canonical = install_js[canonical_start:canonical_end]
    assert "source" not in canonical
    assert "group.id = canonical.id" in install_js
    assert "registerGroupContext(index, selected.group, true)" in install_js


def test_interactive_probe_can_scope_to_card_group_and_match_group_text() -> None:
    install_js = build_page_index_install_js()
    query_js = build_interactive_probe_js(
        query="Soumission",
        scope_group_id="group_abc",
        scope_item_index=2,
    )

    assert "scopeGroupId" in install_js
    assert "scopeItemIndex" in install_js
    assert "const groupText = groupMatch ? groupMatch.root.aggregateText : ''" in install_js
    assert '"scope_group_id":"group_abc"' in query_js
    assert '"scope_item_index":2' in query_js


def test_mutations_navigation_scroll_and_viewport_invalidate_the_index() -> None:
    install_js = build_page_index_install_js()

    assert "new MutationObserver" in install_js
    assert "state.domVersion += 1" in install_js
    assert "state.index = null" in install_js
    assert "state.index.url === location.href" in install_js
    assert "state.index.scrollX === Math.round(window.scrollX)" in install_js
    assert "state.index.scrollY === Math.round(window.scrollY)" in install_js
    assert "state.index.viewportWidth === window.innerWidth" in install_js
    assert "state.index.viewportHeight === window.innerHeight" in install_js


def test_document_lifecycle_diagnostics_distinguish_rebuild_and_bfcache_restore() -> None:
    install_js = build_page_index_install_js()

    assert "createDocumentId" in install_js
    assert "document_id" in install_js
    assert "index_rebuilt" in install_js
    assert "restored_from_bfcache" in install_js
    assert "window.addEventListener('pageshow'" in install_js
    assert "event.persisted" in install_js


def test_compact_card_output_omits_debug_payload_but_preserves_cache_samples() -> None:
    install_js = build_page_index_install_js()
    compact_js = build_card_probe_js(diagnostics_level="compact")
    debug_js = build_card_probe_js(diagnostics_level="debug")

    assert "const compactCard" in install_js
    assert "_cache_cards: result.cards.slice(0, 3)" in install_js
    assert "if (diagnosticsLevel !== 'compact') response.groups = result.groups" in install_js
    assert "if (diagnosticsLevel === 'debug')" in install_js
    assert '"diagnostics_level":"compact"' in compact_js
    assert '"diagnostics_level":"debug"' in debug_js


def test_probe_diagnostics_expose_algorithm_and_cache_behavior() -> None:
    install_js = build_page_index_install_js()

    for key in (
        "build_ms",
        "query_ms",
        "nodes_indexed",
        "repeated_groups",
        "truncated",
    ):
        assert key in install_js

    assert "representatives_parsed" in install_js
    assert "schema_reused" in install_js
    assert "groups_examined" in install_js
    assert "signature_kind" in install_js
    assert "feature_counts" in install_js


def test_probe_requests_are_embedded_as_compact_json() -> None:
    interactive_js = build_interactive_probe_js(
        max_items=17,
        viewport_only=False,
        query='price "low"',
    )
    card_js = build_card_probe_js(
        max_cards=9,
        viewport_only=False,
        include_buttons=False,
        query="nonstop",
        diagnostics_level="compact",
        configuration_revision="abc123",
    )

    assert '"mode":"interactives"' in interactive_js
    assert '"max_items":17' in interactive_js
    assert '"viewport_only":false' in interactive_js
    assert 'price \\"low\\"' in interactive_js
    assert '"mode":"cards"' in card_js
    assert '"max_cards":9' in card_js
    assert '"include_buttons":false' in card_js
    assert '"query":"nonstop"' in card_js
    assert '"configuration_revision":"abc123"' in card_js
    assert len(interactive_js) < 1500
    assert len(card_js) < 1500
