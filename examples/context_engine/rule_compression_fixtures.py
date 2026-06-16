# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Synthetic oversized tool payloads for exercising every rule-compression content type."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from openjiuwen.core.context_engine.processor.offloader.rules.router import ContentType


@dataclass(frozen=True)
class CompressionScenario:
    name: str
    content_type: ContentType
    tool_name: str
    query: str
    build_content: Callable[[], str]
    expect_modified: bool = True
    expect_marker: str | None = None


def build_json_array_payload(row_count: int = 120) -> str:
    rows = [
        {
            "id": idx,
            "name": f"user-{idx}",
            "email": f"user{idx}@example.com",
            "status": "active" if idx % 5 else "error",
            "score": idx * 3,
        }
        for idx in range(1, row_count + 1)
    ]
    return json.dumps(rows, ensure_ascii=False)


def build_git_diff_payload(file_count: int = 8, hunk_lines: int = 36) -> str:
    chunks: list[str] = []
    for file_idx in range(1, file_count + 1):
        path = f"src/module_{file_idx}/handler.py"
        chunks.extend(
            [
                f"diff --git a/{path} b/{path}",
                "index abc1234..def5678 100644",
                f"--- a/{path}",
                f"+++ b/{path}",
                f"@@ -1,{hunk_lines} +1,{hunk_lines} @@",
            ]
        )
        for line_idx in range(hunk_lines):
            if line_idx % 12 == 0:
                chunks.append(f"-    old_line_{line_idx} in file {file_idx}")
                chunks.append(f"+    new_line_{line_idx} in file {file_idx}")
            else:
                chunks.append(f"     context line {line_idx} unchanged padding text")
    return "\n".join(chunks)


def build_html_payload(section_count: int = 40) -> str:
    sections = [
        f"<section id='s-{idx}'><h2>Section {idx}</h2>"
        f"<p>Detailed HTML body paragraph {idx} with enough text to inflate token count.</p></section>"
        for idx in range(1, section_count + 1)
    ]
    return (
        "<!doctype html><html><head><title>Probe Page</title>"
        "<style>body{display:none}</style><script>console.log('noise')</script></head>"
        f"<body><nav>skip me</nav><main>{''.join(sections)}</main><footer>skip</footer></body></html>"
    )


def build_search_results_payload(match_count: int = 80) -> str:
    lines: list[str] = []
    for idx in range(1, match_count + 1):
        file_path = f"openjiuwen/core/pkg_{idx % 12}/module.py"
        lines.append(f"{file_path}:{idx}: def handler_{idx}() -> None:")
    return "\n".join(lines)


def build_build_output_payload(line_count: int = 120) -> str:
    lines = [
        "============================= test session starts ==============================",
        "platform linux -- Python 3.11.0, pytest-8.0.0, pluggy-1.5.0",
        "collected 42 items",
    ]
    for idx in range(1, line_count - 10):
        if idx % 17 == 0:
            lines.append(f"ERROR tests/unit/test_module_{idx}.py::test_case - AssertionError: boom {idx}")
        elif idx % 11 == 0:
            lines.append(f"WARNING tests/unit/test_module_{idx}.py::{idx} deprecated API usage")
        else:
            lines.append(f"tests/unit/test_module_{idx}.py::test_case PASSED")
    lines.extend(
        [
            "=========================== short test summary info ============================",
            "FAILED tests/unit/test_module_17.py::test_case - AssertionError: boom 17",
            "============= 1 failed, 41 passed, 3 warnings in 12.34s =============",
        ]
    )
    return "\n".join(lines)


def build_source_code_payload(function_count: int = 40) -> str:
    lines = [
        '"""Synthetic Python module for source-code compression probing."""',
        "from __future__ import annotations",
        "import logging",
        "",
    ]
    for idx in range(1, function_count + 1):
        lines.extend(
            [
                f"def compute_value_{idx}(base: int) -> int:",
                f'    """Compute value for probe {idx}."""',
                "    total = base",
                "    for step in range(20):",
                "        total += step * 2",
                "        if total % 7 == 0:",
                "            logging.debug('step=%s total=%s', step, total)",
                "    return total",
                "",
            ]
        )
    return "\n".join(lines)


def build_plain_text_payload(repeat_count: int = 100) -> str:
    unique = [f"Unique narrative block {idx} with explanatory detail." for idx in range(1, 21)]
    repeated = [unique[idx % len(unique)] for idx in range(repeat_count)]
    return "\n".join(repeated)


SCENARIOS: list[CompressionScenario] = [
    CompressionScenario(
        name="json_array",
        content_type=ContentType.JSON_ARRAY,
        tool_name="probe_json_array",
        query="请调用 probe_json_array 拉取用户列表并总结异常状态数量。",
        build_content=build_json_array_payload,
        expect_marker='"_omitted"',
    ),
    CompressionScenario(
        name="git_diff",
        content_type=ContentType.GIT_DIFF,
        tool_name="probe_git_diff",
        query="请调用 probe_git_diff 查看多文件 diff 并列出改动文件。",
        build_content=build_git_diff_payload,
        expect_marker="unchanged/context diff lines omitted",
    ),
    CompressionScenario(
        name="html",
        content_type=ContentType.HTML,
        tool_name="probe_html_page",
        query="请调用 probe_html_page 抓取页面正文并概括章节数量。",
        build_content=build_html_payload,
        expect_marker="Section",
    ),
    CompressionScenario(
        name="search_results",
        content_type=ContentType.SEARCH_RESULTS,
        tool_name="probe_search_results",
        query="请调用 probe_search_results 查看代码搜索结果并统计命中文件数。",
        build_content=build_search_results_payload,
        expect_marker="SEARCH_RESULTS compressed",
    ),
    CompressionScenario(
        name="build_output",
        content_type=ContentType.BUILD_OUTPUT,
        tool_name="probe_build_output",
        query="请调用 probe_build_output 分析测试日志中的失败与告警。",
        build_content=build_build_output_payload,
        expect_marker="lines omitted",
    ),
    CompressionScenario(
        name="source_code",
        content_type=ContentType.SOURCE_CODE,
        tool_name="probe_source_code",
        query="请调用 probe_source_code 阅读源码并说明函数数量。",
        build_content=build_source_code_payload,
        expect_modified=True,
        expect_marker="def compute_value_",
    ),
    CompressionScenario(
        name="plain_text",
        content_type=ContentType.PLAIN_TEXT,
        tool_name="probe_plain_text",
        query="请调用 probe_plain_text 去重后统计唯一段落数量。",
        build_content=build_plain_text_payload,
        expect_modified=True,
    ),
]
