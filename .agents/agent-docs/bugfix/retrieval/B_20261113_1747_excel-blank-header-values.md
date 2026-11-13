---
id: B_20261113_1747_excel-blank-header-values
title: Preserve tabular row values under blank headers
type: B
author: 待遇安然风
author_email: 206432729@qq.com
modules:
  - openjiuwen/core/retrieval
files:
  - openjiuwen/core/retrieval/indexing/processor/parser/excel_parser.py
  - tests/unit_tests/core/retrieval/indexing/processor/parser/test_excel_parser.py
designs: []
design_note: Local correction of the existing empty-header fallback contract; no independent design or public API change.
---

# Preserve tabular row values under blank headers

## Symptom and current contract

On develop baseline `279086272882a4d5fd59dcd7ac57d174f4c29281`, parsing headers `[None, "Score", ""]` and data `["Alice", 0, "pass"]` emits row text `Score: 0`. An entirely blank header can suppress the whole row. Column documents retain these values, but row-level retrieval loses the association between them.

`_rows_to_documents` already documents `Column N` fallback for empty headers. The existing XLSX fallback test only checked column names. This repair restores that contract for row documents too; it does not change signatures, IDs, source-type metadata, or `include_header=False` behavior. No dedicated retrieval spec or prior bugfix for this case was found in the managed document tree.

## Cause and implementation

Row generation skipped cells whose header normalized to an empty string. Fallback names were applied only later in the column-document loop. Normalize each header once, before either projection, using its one-based column position. This also handles `None` and whitespace-only headers consistently without mutating the caller's rows.

## Regression evidence

Environment: Windows x86_64, CPython 3.13.5, local virtual environment using installed SDK dependencies. Tests use actual temporary CSV/TSV/XLSX files and the public `ExcelParser.parse` entry point, without a model or vector database.

Command: `python -m pytest tests/unit_tests/core/retrieval/indexing/processor/parser/test_excel_parser.py -q --no-cov`.

- Baseline production code plus regression assertions: 6 failed, 30 passed. Failures exposed omitted values and the dropped all-blank-header row.
- Candidate: 36 passed, 2 existing Pydantic deprecation warnings. Coverage includes mixed/entirely blank headers, whitespace, zero-valued cells, header-free output, row ID/index preservation, and all three supported tabular formats.
- Related AutoFileParser and HybridChunker suites: 29 passed, 2 existing Pydantic deprecation warnings. Command: `python -m pytest tests/unit_tests/core/retrieval/indexing/processor/parser/test_auto_file_parser.py tests/unit_tests/core/retrieval/indexing/processor/chunker/test_hybrid_chunker.py -q --no-cov`.
- Full SDK suite and live knowledge-base/vector-store retrieval were not run. Changes to parser registration, document shape, or dependency versions would require revalidation.

## Documentation and follow-up

The English and Chinese ExcelParser API references now explain blank-header fallback and retention of the associated row values. Existing parser registration and HybridChunker table-unit handling remain unchanged. This is a local parser invariant; no new repository-wide rule is introduced. Maintainer review and platform CI remain separate from local verification.

## CI follow-up, 2026-11-13

The initial platform report contained 27,053 passing cases, 166 errors and 3 failures. All 169 unsuccessful cases included a Pulsar initialization error; the 36 ExcelParser cases passed. The existing A2A adapter test fixture replaced the global Runner configuration with `RunnerConfig()` during teardown, leaving distributed/Pulsar defaults behind for later tests. This fixture is unchanged between the upstream baseline and the original parser commit.

A serial local probe ran an actual adapter test followed by an independent assertion that Runner retained the default non-distributed/fake queue settings: 1 passed, 1 failed before the fixture correction. The fixture now saves the entry configuration and restores it in `finally`. The complete adapter module, the same independent probe and the parser module then produced 46 passed, with 2 existing Pydantic warnings. The probe is a local diagnostic outside the package; platform-wide results must be confirmed by a fresh pipeline.

This follow-up only changes test cleanup and does not change production Runner defaults. Local dependency setup for these additional suites installed the project-declared A2A SDK 1.0.0 and DashScope; Pydantic resolved to 2.14.3. Earlier 2.12.5 evidence above remains the original run's environment.
