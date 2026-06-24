from __future__ import annotations

from openjiuwen.core.context_engine.processor.offloader.rules.source_code_compressor import (
    SourceCodeCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rules.types import (
    ContentType,
    RuleContext,
)


def _ctx(**overrides):
    values = {
        "max_tokens": 1600,
        "source_min_lines": 1,
        "source_max_body_lines": 5,
        "min_savings_ratio": 0.0,
        "count_tokens": lambda text: max(len(text) // 3, 1),
    }
    values.update(overrides)
    return RuleContext(**values)


def _python_function(name: str, body_marker: str = "value") -> str:
    return "\n".join(
        [
            f"def {name}(base: int) -> int:",
            f'    """Compute {body_marker}."""',
            "    total = base",
            "    for step in range(20):",
            "        total += step",
            "        if total % 7 == 0:",
            "            total += 1",
            "    return total",
            "",
        ]
    )


def _numbered(content: str, *, start: int = 10) -> str:
    return "\n".join(f"{line_no:6}\t{line}" for line_no, line in enumerate(content.splitlines(), start))


def test_python_compression_outputs_compileable_omission_body():
    content = "\n".join(
        [
            "from __future__ import annotations",
            "",
            _python_function("compute_alpha"),
            _python_function("compute_beta"),
        ]
    )

    result = SourceCodeCompressor().compress(content, _ctx())

    assert result.modified is True
    assert result.lossy is True
    assert result.content_type == ContentType.SOURCE_CODE
    assert "# [function body omitted; reload original source for details]" in result.content
    assert "\n    pass" in result.content
    compile(result.content, "<compressed>", "exec")
    assert result.details == {
        "language": "python",
        "bodies_seen": 2,
        "bodies_compressed": 2,
        "query_protected_bodies": 0,
        "syntax_valid": True,
    }


def test_query_relevant_python_function_body_is_preserved():
    content = "\n".join(
        [
            _python_function("keep_me", "special_token"),
            _python_function("compress_me", "ordinary"),
        ]
    )

    result = SourceCodeCompressor().compress(
        content,
        _ctx(query_terms=frozenset({"SPECIAL_TOKEN"})),
    )

    assert result.modified is True
    assert '"""Compute special_token."""' in result.content
    assert '"""Compute ordinary."""' not in result.content
    assert result.details["bodies_seen"] == 2
    assert result.details["bodies_compressed"] == 1
    assert result.details["query_protected_bodies"] == 1


def test_parse_failure_returns_original_source():
    content = "def broken(:\n    return 1\n"

    result = SourceCodeCompressor().compress(content, _ctx())

    assert result.modified is False
    assert result.content == content
    assert result.content_type == ContentType.SOURCE_CODE


def test_utf8_offsets_preserve_non_ascii_source_around_replacements():
    content = "\n".join(
        [
            "# 中文注释 before",
            _python_function("compute_unicode", "普通值"),
            "CONSTANT = '结束'",
        ]
    )

    result = SourceCodeCompressor().compress(content, _ctx())

    assert result.modified is True
    assert "# 中文注释 before" in result.content
    assert "CONSTANT = '结束'" in result.content
    compile(result.content, "<compressed>", "exec")


def test_savings_gate_preserves_original_when_candidate_is_not_worthwhile():
    content = "\n".join(_python_function(f"compute_{idx}") for idx in range(2))

    result = SourceCodeCompressor().compress(
        content,
        _ctx(min_savings_ratio=0.99),
    )

    assert result.modified is False
    assert result.lossy is False
    assert result.content == content
    assert result.details["syntax_valid"] is True


def test_numbered_python_compression_preserves_original_line_numbers():
    content = _numbered(
        "\n".join(
            [
                "def keep_line_numbers(base: int) -> int:",
                "    total = base",
                "    for step in range(20):",
                "        total += step",
                "        if total % 7 == 0:",
                "            total += 1",
                "    return total",
                "",
                "def next_function() -> int:",
                "    return 1",
            ]
        ),
        start=40,
    )

    result = SourceCodeCompressor().compress(content, _ctx())

    assert result.modified is True
    assert "    40\tdef keep_line_numbers" in result.content
    assert "    41\t    # [function body omitted; original lines 41-46, reload original source for details]" in result.content
    assert "    47\t" in result.content
    assert "    48\tdef next_function" in result.content
    assert "    42\t" not in result.content
    assert "    46\t" not in result.content
    assert result.details["line_numbers_preserved"] is True
    assert result.details["bodies_compressed"] == 1


def test_numbered_python_without_ast_body_compression_returns_original():
    content = _numbered(
        "\n".join(
            [
                "def small() -> int:",
                "    return 1",
            ]
        ),
        start=80,
    )

    result = SourceCodeCompressor().compress(content, _ctx())

    assert result.modified is False
    assert result.content == content
    assert result.details["bodies_compressed"] == 0
