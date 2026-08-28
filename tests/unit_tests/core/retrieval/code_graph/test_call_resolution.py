# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Call-edge resolution: precision, evidence, and unresolved reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.indexing.symbol_extractor import (
    absolute_module_path,
    file_to_dotted_module,
)
from openjiuwen.core.retrieval.code_graph.models import (
    CallResolution,
    CodeGraphConfig,
    CodeGraphIndex,
    RelationKind,
)
from openjiuwen.core.retrieval.code_graph.query.expand_related import expand_related
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()

SAME_NAME = '''\
class Alpha:
    def run(self):
        return self.helper()

    def helper(self):
        return 1


class Beta:
    def run(self):
        return self.helper()

    def helper(self):
        return 2
'''

DISPATCH = '''\
def dispatch(obj):
    return obj.helper()
'''

RECEIVER_TYPED = '''\
from pkg.same_name import Alpha


def build():
    return Alpha().helper()
'''

UTIL = '''\
def compute_offset(value):
    return value + 1
'''

IMPORTER = '''\
from pkg.util import compute_offset


def entry(value):
    return compute_offset(value)
'''

RELATIVE_IMPORTER = '''\
from .util import compute_offset


def relative_entry(value):
    return compute_offset(value)
'''

INHERITED = '''\
from pkg.same_name import Alpha


class Gamma(Alpha):
    def run_twice(self):
        return self.helper() + self.helper()
'''

MUTUAL = '''\
def ping(n):
    if n <= 0:
        return 0
    return pong(n - 1)


def pong(n):
    return ping(n - 1)
'''

# astropy-13579 shape: world_to_pixel goes through a local wrapper variable.
# Without assignment tracking, `sliced.world_to_pixel_values` is an unresolved hop.
SLICED_WCS = '''\
class SlicedLowLevelWCS:
    def world_to_pixel_values(self, *world_arrays):
        return (0.0,)


def pixel_from_sliced(world):
    sliced = SlicedLowLevelWCS()
    return sliced.world_to_pixel_values(world)
'''


def _write_repo(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "same_name.py").write_text(SAME_NAME, encoding="utf-8")
    (pkg / "dispatch.py").write_text(DISPATCH, encoding="utf-8")
    (pkg / "receiver_typed.py").write_text(RECEIVER_TYPED, encoding="utf-8")
    (pkg / "util.py").write_text(UTIL, encoding="utf-8")
    (pkg / "importer.py").write_text(IMPORTER, encoding="utf-8")
    (pkg / "relative_importer.py").write_text(RELATIVE_IMPORTER, encoding="utf-8")
    (pkg / "inherited.py").write_text(INHERITED, encoding="utf-8")
    (pkg / "mutual.py").write_text(MUTUAL, encoding="utf-8")
    (pkg / "sliced_wcs.py").write_text(SLICED_WCS, encoding="utf-8")
    return root


@pytest.fixture
def index(tmp_path: Path) -> CodeGraphIndex:
    root = _write_repo(tmp_path / "repo")
    return build_index(root, CodeGraphConfig(max_files=200))


def _calls(index: CodeGraphIndex, caller: str) -> list[str]:
    return list(index.neighbors(caller, RelationKind.CALLS))


def _resolutions(index: CodeGraphIndex, source: str, target: str) -> list[str]:
    return [
        item.resolution
        for item in index.evidence_for(source, RelationKind.CALLS, target)
    ]


def test_self_call_resolves_to_own_class_not_the_same_named_sibling(
    index: CodeGraphIndex,
) -> None:
    alpha_run = "pkg/same_name.py::Alpha.run"
    beta_run = "pkg/same_name.py::Beta.run"

    assert _calls(index, alpha_run) == ["pkg/same_name.py::Alpha.helper"]
    assert _calls(index, beta_run) == ["pkg/same_name.py::Beta.helper"]


def test_same_class_edge_carries_call_site_evidence(index: CodeGraphIndex) -> None:
    evidence = index.evidence_for(
        "pkg/same_name.py::Alpha.run",
        RelationKind.CALLS,
        "pkg/same_name.py::Alpha.helper",
    )

    assert len(evidence) == 1
    hit = evidence[0]
    assert hit.resolution == CallResolution.SAME_CLASS.value
    assert hit.confidence == pytest.approx(0.95)
    assert hit.file == "pkg/same_name.py"
    assert hit.start_line == 3
    assert "self.helper()" in hit.expression


def test_evidence_is_readable_from_the_inverse_direction(index: CodeGraphIndex) -> None:
    forward = index.evidence_for(
        "pkg/same_name.py::Alpha.run",
        RelationKind.CALLS,
        "pkg/same_name.py::Alpha.helper",
    )
    inverse = index.evidence_for(
        "pkg/same_name.py::Alpha.helper",
        RelationKind.CALLED_BY,
        "pkg/same_name.py::Alpha.run",
    )

    assert inverse == forward


def test_unknown_receiver_with_ambiguous_name_produces_no_edge(
    index: CodeGraphIndex,
) -> None:
    assert _calls(index, "pkg/dispatch.py::dispatch") == []

    unresolved = [
        item for item in index.unresolved_calls if item.callee_name == "helper"
    ]
    assert any(item.file == "pkg/dispatch.py" for item in unresolved)


def test_receiver_class_name_resolves_to_that_class_method(
    index: CodeGraphIndex,
) -> None:
    targets = _calls(index, "pkg/receiver_typed.py::build")

    assert "pkg/same_name.py::Alpha.helper" in targets
    assert CallResolution.RECEIVER_TYPE.value in _resolutions(
        index,
        "pkg/receiver_typed.py::build",
        "pkg/same_name.py::Alpha.helper",
    )


def test_explicit_import_beats_a_repo_wide_name_lookup(index: CodeGraphIndex) -> None:
    target = "pkg/util.py::compute_offset"

    assert _calls(index, "pkg/importer.py::entry") == [target]
    assert _resolutions(index, "pkg/importer.py::entry", target) == [
        CallResolution.IMPORTED.value
    ]


def test_relative_import_links_the_imported_symbol(index: CodeGraphIndex) -> None:
    target = "pkg/util.py::compute_offset"
    importers = index.neighbors(target, RelationKind.IMPORTED_BY)

    assert "pkg/relative_importer.py" in importers
    assert _calls(index, "pkg/relative_importer.py::relative_entry") == [target]


@pytest.mark.parametrize(
    ("rel_path", "module", "expected"),
    [
        ("astropy/coordinates/builtin_frames/transforms.py", ".itrs", "astropy/coordinates/builtin_frames/itrs"),
        ("astropy/coordinates/builtin_frames/transforms.py", "..utils", "astropy/coordinates/utils"),
        ("pkg/mod.py", "pkg.util", "pkg/util"),
        ("pkg/mod.py", ".", "pkg"),
    ],
)
def test_absolute_module_path_resolves_relative_imports(
    rel_path: str,
    module: str,
    expected: str,
) -> None:
    assert absolute_module_path(rel_path, module) == expected


def test_file_to_dotted_module_matches_import_paths() -> None:
    assert (
        file_to_dotted_module("astropy/coordinates/builtin_frames/itrs.py")
        == "astropy.coordinates.builtin_frames.itrs"
    )
    assert file_to_dotted_module("pkg/__init__.py") == "pkg"


def test_inherited_method_resolves_through_the_base_class(
    index: CodeGraphIndex,
) -> None:
    targets = _calls(index, "pkg/inherited.py::Gamma.run_twice")

    assert targets == ["pkg/same_name.py::Alpha.helper"] * 2
    assert set(
        _resolutions(
            index,
            "pkg/inherited.py::Gamma.run_twice",
            "pkg/same_name.py::Alpha.helper",
        )
    ) == {CallResolution.SAME_CLASS.value}


def test_mutual_recursion_keeps_both_directions(index: CodeGraphIndex) -> None:
    assert _calls(index, "pkg/mutual.py::ping") == ["pkg/mutual.py::pong"]
    assert _calls(index, "pkg/mutual.py::pong") == ["pkg/mutual.py::ping"]


def test_direct_self_recursion_does_not_create_a_self_loop(
    index: CodeGraphIndex,
) -> None:
    for relation in index.relations:
        assert relation.source_id != relation.target_id


def test_local_wrapper_assignment_does_not_break_the_call_chain(
    index: CodeGraphIndex,
) -> None:
    """13579-shaped hop: local `sliced = SlicedLowLevelWCS()` then method call."""
    caller = "pkg/sliced_wcs.py::pixel_from_sliced"
    target = "pkg/sliced_wcs.py::SlicedLowLevelWCS.world_to_pixel_values"

    assert target in _calls(index, caller)
    assert CallResolution.LOCAL_ASSIGNMENT.value in _resolutions(index, caller, target)
    assert not any(
        item.callee_name == "world_to_pixel_values" and item.file.endswith("sliced_wcs.py")
        for item in index.unresolved_calls
    )


SCHEMA_EDITOR = '''\
class SchemaEditor:
    def alter_db_table(self, old, new):
        return old

    def _alter_many_to_many(self):
        self.alter_db_table("a", "b")
'''

SQLITE_EDITOR = '''\
from pkg.schema import SchemaEditor


class DatabaseSchemaEditor(SchemaEditor):
    def alter_db_table(self, old, new):
        return super().alter_db_table(old, new)
'''

TYPED_PARAM_CALLER = '''\
from pkg.schema import SchemaEditor


class RenameModel:
    def database_forwards(self, schema_editor: SchemaEditor, from_state):
        schema_editor.alter_db_table("old", "new")
'''

UNTYPED_PARAM_CALLER = '''\
class RenameModel:
    def database_forwards(self, schema_editor, from_state):
        schema_editor.alter_db_table("old", "new")
'''


def _schema_repo(root: Path, *, typed: bool) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "schema.py").write_text(SCHEMA_EDITOR, encoding="utf-8")
    (pkg / "sqlite.py").write_text(SQLITE_EDITOR, encoding="utf-8")
    (pkg / "ops.py").write_text(
        TYPED_PARAM_CALLER if typed else UNTYPED_PARAM_CALLER,
        encoding="utf-8",
    )
    return root


def test_typed_parameter_receiver_resolves_to_annotated_class(tmp_path: Path) -> None:
    index = build_index(_schema_repo(tmp_path / "repo", typed=True), CodeGraphConfig(max_files=50))
    caller = "pkg/ops.py::RenameModel.database_forwards"
    target = "pkg/schema.py::SchemaEditor.alter_db_table"

    assert target in _calls(index, caller)
    assert CallResolution.LOCAL_ASSIGNMENT.value in _resolutions(index, caller, target)


def test_untyped_parameter_call_stays_unresolved_when_the_name_is_ambiguous(
    tmp_path: Path,
) -> None:
    index = build_index(_schema_repo(tmp_path / "repo", typed=False), CodeGraphConfig(max_files=50))
    caller = "pkg/ops.py::RenameModel.database_forwards"
    target = "pkg/schema.py::SchemaEditor.alter_db_table"

    assert target not in _calls(index, caller)
    assert any(
        item.caller_id == caller and item.callee_name == "alter_db_table"
        for item in index.unresolved_calls
    )


def test_find_callers_does_not_surface_unresolved_name_matches(
    tmp_path: Path,
) -> None:
    """Backup expand_related only returns graph edges, matching run11."""
    index = build_index(_schema_repo(tmp_path / "repo", typed=False), CodeGraphConfig(max_files=50))
    target = "pkg/schema.py::SchemaEditor.alter_db_table"
    related = {
        hit.symbol_id
        for hit in expand_related(index, target, relations=["called_by"], limit=10)
    }

    assert "pkg/schema.py::SchemaEditor._alter_many_to_many" in related
    assert "pkg/ops.py::RenameModel.database_forwards" not in related
    assert any(
        item.caller_id == "pkg/ops.py::RenameModel.database_forwards"
        and item.callee_name == "alter_db_table"
        for item in index.unresolved_calls
    )

