# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Source, markup and document files read by path must never be rule-compressed."""

import json

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression import (
    ContentType,
    RuleContentRouter,
    RuleContext,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common import (
    extract_file_path_argument,
    is_source_file_path,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from tests.unit_tests.core.context_engine.rule_compression_fixtures import (
    build_build_output_payload,
    build_json_array_payload,
)


def _numbered(lines: list[str]) -> str:
    """Render lines the way read_file displays them."""
    return "\n".join(f"{line_number:6}\t{line}" for line_number, line in enumerate(lines, 1))


def _jsx_component_lines(block_count: int) -> list[str]:
    lines = [
        '"use client";',
        "",
        'import { useState } from "react";',
        "",
        "export function Timeline({ items }: { items: string[] }) {",
        "  const [open, setOpen] = useState(false);",
        "  return (",
    ]
    for index in range(block_count):
        lines.extend(
            [
                f'    <div className="row-{index}">',
                f"      <span>{{items[{index}]}}</span>",
                "    </div>",
            ]
        )
    lines.extend(["  );", "}"])
    return lines


def _markdown_guide_lines() -> list[str]:
    return [
        "# CLI and Agent Daemon Guide",
        "",
        "## Quick Start",
        "",
        "Running the daemon starts the agent loop on this machine.",
        "Pass `--to-id` to target one workspace.",
        "Info about login lives in the next section.",
        *[f"Paragraph {index} explains one more command in detail." for index in range(60)],
    ]


def _context(file_path: str | None = None) -> RuleContext:
    return RuleContext(
        max_tokens=1600,
        count_tokens=lambda text: max(len(text) // 3, 1),
        file_path=file_path,
    )


def test_jsx_source_read_by_path_is_not_routed_as_html():
    content = _numbered(_jsx_component_lines(80))
    router = RuleContentRouter()

    assert router.detect(content, _context()) == ContentType.HTML

    result = router.compress(content, _context("apps/web/src/timeline.tsx"))

    assert result.content_type == ContentType.SOURCE_FILE
    assert result.modified is False
    assert result.content == content


def test_go_source_with_error_line_read_by_path_is_not_routed_as_log():
    lines = [
        "package agent",
        "",
        "func run() error {",
        "\treturn fmt.Errorf(\"boom\")",
        "}",
        "Error: this doc comment line starts like a log line",
        *[f"func helper{index}() int {{ return {index} }}" for index in range(60)],
    ]
    content = _numbered(lines)
    router = RuleContentRouter()

    assert router.detect(content, _context()) == ContentType.LOG
    assert router.detect(content, _context("server/pkg/agent/claude.go")) == ContentType.SOURCE_FILE


def test_markdown_document_read_by_path_is_not_routed_as_log():
    content = _numbered(_markdown_guide_lines())
    router = RuleContentRouter()

    assert router.detect(content, _context()) == ContentType.LOG

    result = router.compress(content, _context("docs/CLI_AND_DAEMON.md"))

    assert result.content_type == ContentType.SOURCE_FILE
    assert result.modified is False


def test_data_files_read_by_path_keep_content_routing():
    router = RuleContentRouter()
    json_content = _numbered(build_json_array_payload().splitlines())
    log_content = _numbered(build_build_output_payload().splitlines())

    json_result = router.compress(json_content, _context("data/users.json"))
    log_result = router.compress(log_content, _context("reports/pytest.log"))

    assert json_result.content_type == ContentType.JSON_ARRAY
    assert json_result.modified is True
    assert log_result.content_type == ContentType.LOG
    assert log_result.modified is True


@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        ("src/app.tsx", True),
        ("C:\\repo\\server\\main.go", True),
        ("docs/README.MD", True),
        ("deploy/Dockerfile", True),
        (".env", True),
        ("site/index.html", True),
        ("data/users.json", False),
        ("logs/run.log", False),
        ("notes.txt", False),
        ("changes.patch", False),
        ("apps/web/src", False),
    ],
)
def test_is_source_file_path(file_path: str, expected: bool):
    assert is_source_file_path(file_path) is expected


@pytest.mark.parametrize(
    ("tool_arguments", "expected"),
    [
        ('{"file_path": "src/app.tsx", "offset": 1}', "src/app.tsx"),
        ({"path": " docs/guide.md "}, "docs/guide.md"),
        ({"command": "cat src/app.tsx"}, None),
        ({"file_path": ""}, None),
        ("not json", None),
        (None, None),
    ],
)
def test_extract_file_path_argument(tool_arguments: object, expected: str | None):
    assert extract_file_path_argument(tool_arguments) == expected


@pytest.mark.asyncio
@pytest.mark.usefixtures("refactored_context_processors")
async def test_offloader_keeps_source_file_verbatim_in_preview_and_offloads_original(tmp_path):
    engine = ContextEngine(
        ContextEngineConfig(context_window_tokens=1000, enable_tiktoken_counter=True),
        workspace=type("Workspace", (), {"root_path": str(tmp_path)})(),
    )
    context = await engine.create_context(
        "test_ctx",
        processors=[("MessageSummaryOffloader", MessageSummaryOffloaderConfig(protected_tool_names=[]))],
    )
    original = _numbered(_jsx_component_lines(200))
    tool_call = AssistantMessage(
        content="reading component",
        tool_calls=[
            ToolCall(
                id="tc-read-tsx",
                name="read_file",
                type="function",
                arguments=json.dumps({"file_path": "apps/web/src/timeline.tsx"}),
            ),
        ],
    )

    await context.add_messages([tool_call, ToolMessage(content=original, tool_call_id="tc-read-tsx")])

    message = context.get_messages()[1]
    assert isinstance(message, OffloadMixin)
    assert not message.metadata.get("rule_compressed_at")
    assert "[Content truncated and offloaded.]" in message.content
    assert '     8\t    <div className="row-0">' in message.content
    marker_path = message.content.rsplit("path=", 1)[1].rsplit("]]", 1)[0]
    with open(marker_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["messages"][0]["content"] == original
